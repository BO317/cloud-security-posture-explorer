"""Offline CloudWatch transport, role isolation, and application correlation tests."""
import io
import json
import logging
from threading import Event
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.credentials import InstanceMetadataProvider
from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.stub import Stubber

from app.audit import SCAN_ID
from app.config import ConfigurationError, Settings
from app.ec2_metadata import instance_region
from app.cloudwatch_logs import CloudWatchHandler, LOG_GROUP, configure_cloudwatch, apply_cloudwatch_settings, instance_id, role_client
from app.server import create_application
from app.tests.test_server import request, fixture_scan

INSTANCE = "i-0123456789abcdef0"


class AlreadyExists(ClientError):
    pass


def record(event="posture_observation", **fields):
    value = json.dumps({"event": event, "scan_id": "scan-test", **fields})
    return logging.LogRecord("app.test", logging.INFO, "", 0, value, (), None)


class CloudWatchTest(unittest.TestCase):
    def setUp(self):
        blocker = patch("socket.socket.connect", side_effect=AssertionError("Live network forbidden"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.client = Mock()
        self.client.exceptions.ResourceAlreadyExistsException = AlreadyExists
        self.client.put_log_events.return_value = {}
        self.factory = Mock(return_value=self.client)
        self.identity = Mock(return_value=INSTANCE)
        self.handler = CloudWatchHandler("us-east-1", client_factory=self.factory,
                                         identity_factory=self.identity, start=False, cooldown=0)
        self.addCleanup(self.handler.close)

    def send(self, entry=None):
        entry = entry or record()
        self.handler.emit(entry)
        event, scan_id, region = self.handler.queue.get_nowait()
        self.handler._send(event, scan_id, region)
        self.handler.queue.task_done()
        return entry

    def test_required_events_keep_exact_json_and_scan_id(self):
        for event_name in ("configuration_load", "posture_observation", "application_error"):
            entry = self.send(record(event_name))
            self.client.put_log_events.assert_called_with(
                logGroupName=LOG_GROUP, logStreamName=INSTANCE,
                logEvents=[{"timestamp": int(entry.created * 1000), "message": entry.getMessage()}])
        self.client.create_log_stream.assert_called_once_with(logGroupName=LOG_GROUP, logStreamName=INSTANCE)
        self.factory.assert_called_once_with("us-east-1")
        self.identity.assert_called_once_with()

    def test_existing_stream_is_reused_without_sequence_token_or_describe(self):
        self.client.create_log_stream.side_effect = AlreadyExists(
            {"Error": {"Code": "ResourceAlreadyExistsException"}}, "CreateLogStream")
        self.send()
        self.client.put_log_events.assert_called_once()
        self.client.describe_log_streams.assert_not_called()
        self.client.create_log_group.assert_not_called()

    def test_errors_are_sanitized_and_next_event_can_recover(self):
        for operation in ("create_log_stream", "put_log_events"):
            for error in (ClientError({"Error": {"Code": "AccessDeniedException", "Message": "private-detail"}}, operation),
                          ClientError({"Error": {"Code": "ResourceNotFoundException"}}, operation),
                          ClientError({"Error": {"Code": "ThrottlingException"}}, operation),
                          ReadTimeoutError(endpoint_url="https://private-detail.invalid")):
                with self.subTest(operation=operation, error=type(error).__name__):
                    self.handler.stream_ready = False
                    method = getattr(self.client, operation)
                    method.side_effect = error
                    with self.assertLogs("cloudwatch_delivery", level="WARNING") as logs:
                        self.send()
                    self.assertNotIn("private-detail", str(logs.output))
                    self.assertEqual(json.loads(logs.records[0].getMessage())["scan_id"], "scan-test")
                    method.side_effect = None
                    self.send()
                    self.assertTrue(self.handler.stream_ready)

    def test_http_success_with_rejected_event_is_not_silent(self):
        self.client.put_log_events.return_value = {"rejectedLogEventsInfo": {"tooOldLogEventEndIndex": 0}}
        with self.assertLogs("cloudwatch_delivery", level="WARNING") as logs:
            self.send()
        self.assertIn("event_rejected", logs.output[0])

    def test_malformed_response_and_metadata_failure_stay_local(self):
        self.client.put_log_events.return_value = None
        with self.assertLogs("cloudwatch_delivery", level="WARNING"):
            self.send()
        self.handler.stream = None
        self.identity.side_effect = URLError("private-detail")
        self.client.reset_mock()
        with self.assertLogs("cloudwatch_delivery", level="WARNING") as logs:
            self.send()
        self.client.put_log_events.assert_not_called()
        self.assertNotIn("private-detail", str(logs.output))

    def test_unavailable_role_credentials_recover_on_next_event(self):
        self.factory.side_effect = RuntimeError("private credential detail")
        with self.assertLogs("cloudwatch_delivery", level="WARNING") as logs:
            self.send()
        self.client.put_log_events.assert_not_called()
        self.assertNotIn("private credential detail", str(logs.output))
        self.factory.side_effect = None
        self.send()
        self.client.put_log_events.assert_called_once()

    def test_queue_overflow_and_oversize_are_bounded_and_local(self):
        handler = CloudWatchHandler("us-east-1", capacity=1, start=False)
        self.addCleanup(handler.close)
        handler.emit(record())
        with self.assertLogs("cloudwatch_delivery", level="WARNING") as logs:
            handler.emit(record())
            handler.emit(record(detail="x" * 8192))
        self.assertEqual(handler.queue.qsize(), 1)
        self.assertIn("queue_full", logs.output[0])
        self.assertIn("event_too_large", logs.output[1])

    def test_non_json_or_unapproved_events_are_not_uploaded(self):
        for message in ("private text", "[]", '{"event":"sdk_debug","secret":"private"}'):
            entry = record()
            entry.msg = message
            self.handler.emit(entry)
        self.assertTrue(self.handler.queue.empty())

    def test_cooldown_prevents_repeated_network_calls(self):
        self.handler.cooldown = 30
        self.client.put_log_events.side_effect = RuntimeError()
        with self.assertLogs("cloudwatch_delivery", level="WARNING"):
            self.send()
        self.send()
        self.assertEqual(self.client.put_log_events.call_count, 1)

    def test_background_network_wait_does_not_block_health_or_journal(self):
        entered, release = Event(), Event()

        def put(**kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test timed out")
            return {}

        self.client.put_log_events.side_effect = put
        handler = CloudWatchHandler("us-east-1", client_factory=self.factory, identity_factory=self.identity)
        logger = logging.getLogger("app.cloudwatch_test")
        output = io.StringIO()
        journal = logging.StreamHandler(output)
        old_level, old_propagate = logger.level, logger.propagate
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        logger.addHandler(journal)
        try:
            logger.info(record().getMessage())
            self.assertTrue(entered.wait(2))
            app = create_application(Mock(return_value=fixture_scan()))
            self.assertEqual(request(app, "/healthz")["status"], "200 OK")
            self.assertEqual(request(app)["status"], "200 OK")
            logger.info(record("configuration_load").getMessage())
            self.assertEqual(len(output.getvalue().splitlines()), 2)
        finally:
            release.set()
            logger.removeHandler(handler)
            logger.removeHandler(journal)
            logger.setLevel(old_level)
            logger.propagate = old_propagate
            handler.close()
        self.assertFalse(handler.thread.is_alive())
        self.client.close.assert_called_once()

    def test_sdk_contract_uses_fixed_group_and_instance_stream(self):
        with patch("botocore.session.Session.get_credentials", return_value=None):
            client = boto3.Session().client("logs", region_name="us-east-1", config=Config(signature_version=UNSIGNED))
        self.addCleanup(client.close)
        self.factory.return_value = client
        entry = record()
        with Stubber(client) as stub:
            stub.add_response("create_log_stream", {}, {"logGroupName": LOG_GROUP, "logStreamName": INSTANCE})
            stub.add_response("put_log_events", {}, {"logGroupName": LOG_GROUP, "logStreamName": INSTANCE,
                "logEvents": [{"timestamp": int(entry.created * 1000), "message": entry.getMessage()}]})
            self.send(entry)
            stub.assert_no_pending_responses()

    def test_imdsv2_identity_uses_token_no_proxy_and_no_v1_fallback(self):
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(b"test-token"), io.BytesIO(INSTANCE.encode())]
        with patch("app.ec2_metadata.build_opener", return_value=opener) as factory:
            self.assertEqual(instance_id(), INSTANCE)
        self.assertEqual(factory.call_args.args[0].proxies, {})
        token_request = opener.open.call_args_list[0].args[0]
        id_request = opener.open.call_args_list[1].args[0]
        self.assertEqual(token_request.method, "PUT")
        self.assertEqual(id_request.get_header("X-aws-ec2-metadata-token"), "test-token")
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 2})
        opener.reset_mock()
        opener.open.side_effect = URLError("metadata denied")
        with patch("app.ec2_metadata.build_opener", return_value=opener), self.assertRaises(URLError):
            instance_id()
        self.assertEqual(opener.open.call_count, 1)

    def test_malformed_metadata_identity_is_rejected(self):
        for responses in ([b"bad\ntoken"], [b"token", b"not-an-instance"]):
            opener = Mock()
            opener.open.side_effect = [io.BytesIO(value) for value in responses]
            with patch("app.ec2_metadata.build_opener", return_value=opener), self.assertRaises(ValueError):
                instance_id()

    def test_only_ec2_role_provider_is_registered(self):
        sdk = Mock()
        with patch("app.cloudwatch_logs.boto3.Session", return_value=sdk) as factory:
            role_client("us-east-1")
        session = factory.call_args.kwargs["botocore_session"]
        providers = session.get_component("credential_provider").providers
        self.assertEqual(len(providers), 1)
        self.assertIsInstance(providers[0], InstanceMetadataProvider)
        sdk.client.assert_called_once()
        self.assertEqual(sdk.client.call_args.args, ("logs",))
        self.assertEqual(sdk.client.call_args.kwargs["region_name"], "us-east-1")

    def test_configuration_failure_and_application_error_share_scan_id(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch("app.config_provider.instance_region", side_effect=ConfigurationError("Metadata unavailable")), \
                self.assertLogs("app", level="INFO") as logs:
            self.assertEqual(request(create_application())["status"], "503 Service Unavailable")
        events = [json.loads(item.getMessage()) for item in logs.records]
        self.assertEqual([item["event"] for item in events], ["configuration_load", "application_error"])
        self.assertIsNotNone(events[0]["scan_id"])
        self.assertEqual(events[0]["scan_id"], events[1]["scan_id"])
        self.assertIsNone(SCAN_ID.get())

    def test_enabled_setup_is_idempotent_and_does_not_call_aws(self):
        logger = logging.getLogger("app")
        with patch("app.cloudwatch_logs.role_client", side_effect=AssertionError("No AWS at setup")):
            handler = configure_cloudwatch()
            try:
                self.assertIs(configure_cloudwatch(), handler)
                self.assertIsNone(handler.region)
                self.assertTrue(handler.queue.empty())
                self.assertIsNone(handler.client)
            finally:
                logger.removeHandler(handler)
                handler.close()

    def test_server_start_failure_is_reported_and_handler_closed(self):
        from app.server import main
        handler = Mock()
        with patch("sys.argv", ["server"]), patch("app.server.logging.basicConfig"), \
                patch("app.server.configure_cloudwatch", return_value=handler), \
                patch("app.server.make_server", side_effect=OSError("private bind details")), \
                self.assertLogs("app.server", level="ERROR") as logs, self.assertRaises(SystemExit) as result:
            main()
        self.assertEqual(result.exception.code, 1)
        self.assertEqual(json.loads(logs.records[0].getMessage())["code"], "application_server_failed")
        self.assertNotIn("private bind details", str(logs.output))
        handler.close.assert_called_once()

    def test_unexpected_application_error_is_json_and_redacted(self):
        app = create_application(Mock(side_effect=RuntimeError("private failure")))
        with self.assertLogs("app.server", level="ERROR") as logs:
            self.assertEqual(request(app)["status"], "503 Service Unavailable")
        event = json.loads(logs.records[0].getMessage())
        self.assertEqual(event["event"], "application_error")
        self.assertIsNotNone(event["scan_id"])
        self.assertNotIn("private failure", str(logs.output))

    def test_ssm_toggle_applies_without_restarting_or_removing_journal(self):
        logger = logging.getLogger("app")
        logger.addHandler(self.handler)
        output = io.StringIO()
        journal = logging.StreamHandler(output)
        logger.addHandler(journal)
        try:
            apply_cloudwatch_settings(Settings("us-west-2", (), (), ("lab",), False))
            logger.handle(record())
            self.assertTrue(self.handler.queue.empty())
            self.assertIn("posture_observation", output.getvalue())
            apply_cloudwatch_settings(Settings("us-west-2", (), (), ("lab",), True))
            logger.handle(record())
            event, scan_id, region = self.handler.queue.get_nowait()
            self.assertEqual(region, "us-west-2")
            self.handler._send(event, scan_id, region)
            self.handler.queue.task_done()
            self.factory.assert_called_once_with("us-west-2")
        finally:
            logger.removeHandler(self.handler)
            logger.removeHandler(journal)

    def test_queued_events_keep_region_at_enqueue_when_ssm_region_changes(self):
        first, second = Mock(), Mock()
        for client in (first, second):
            client.put_log_events.return_value = {}
            client.exceptions.ResourceAlreadyExistsException = AlreadyExists
        self.factory.side_effect = [first, second]
        self.handler.emit(record())
        self.handler.region = "us-west-2"
        self.handler.emit(record())
        for _ in range(2):
            event, scan_id, region = self.handler.queue.get_nowait()
            self.handler._send(event, scan_id, region)
            self.handler.queue.task_done()
        self.assertEqual([call.args[0] for call in self.factory.call_args_list], ["us-east-1", "us-west-2"])
        first.close.assert_called_once()
        first.put_log_events.assert_called_once()
        second.put_log_events.assert_called_once()

    def test_bootstrap_failure_logging_discovers_region_without_environment(self):
        self.handler.region = None
        with patch.dict("os.environ", {}, clear=True), patch("app.cloudwatch_logs.instance_region", return_value="us-west-2"):
            self.send(record("application_error"))
        self.factory.assert_called_once_with("us-west-2")

    def test_region_metadata_uses_imdsv2_and_rejects_invalid_response(self):
        for value in (b"us-east-1", b"https://bad-endpoint", b""):
            opener = Mock()
            opener.open.side_effect = [io.BytesIO(b"token"), io.BytesIO(value)]
            with patch("app.ec2_metadata.build_opener", return_value=opener):
                if value == b"us-east-1":
                    self.assertEqual(instance_region(), "us-east-1")
                    self.assertTrue(opener.open.call_args.args[0].full_url.endswith("/placement/region"))
                else:
                    with self.assertRaises(ConfigurationError):
                        instance_region()
