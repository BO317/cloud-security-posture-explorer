import json
import unittest
from unittest.mock import Mock, patch

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.stub import Stubber

from app.config import ConfigurationError, Settings
from app.config_provider import PARAMETER_NAME, load_configuration
from app.aws_provider import collect_from_environment
from app.server import create_application
from app.tests.test_server import request

DOCUMENT = {"region": "us-west-2", "allowed_buckets": ["demo-bucket"],
            "allowed_security_groups": [], "allowed_instances": []}
ENV = {"AWS_REGION": "us-east-1", "ALLOWED_SECURITY_GROUPS": "sg-12345678"}


def response(document=DOCUMENT):
    return {"Parameter": {"Name": PARAMETER_NAME, "Type": "String", "Version": 3,
                          "Value": json.dumps(document)},
            "ResponseMetadata": {"RequestId": "request-123"}}


class ParameterConfigurationTest(unittest.TestCase):
    def setUp(self):
        blocker = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.client = Mock()
        self.client.get_parameter.return_value = response()
        self.session = Mock()
        self.session.client.return_value = self.client
        self.factory = Mock(return_value=self.session)

    def load(self, env=ENV):
        return load_configuration(env, self.factory, scan_id="scan-test")

    def test_valid_parameter_overrides_environment_without_merging(self):
        result = self.load()
        self.assertEqual(result.settings, Settings.from_document(DOCUMENT))
        self.assertEqual((result.source, result.version), ("ssm", 3))
        self.factory.assert_called_once_with()
        self.assertEqual(self.session.client.call_args.kwargs["region_name"], "us-east-1")
        self.client.get_parameter.assert_called_once_with(Name=PARAMETER_NAME, WithDecryption=False)
        self.client.close.assert_called_once_with()

    def test_ssm_region_and_invalid_local_scope_do_not_override_valid_parameter(self):
        self.assertEqual(self.load({"SSM_REGION": "us-west-2", "ALLOWED_BUCKETS": "*"}).source, "ssm")

    def test_missing_parameter_access_denied_and_timeout_use_valid_fallback(self):
        errors = [ClientError({"Error": {"Code": code, "Message": "private diagnostic"}}, "GetParameter")
                  for code in ("ParameterNotFound", "AccessDeniedException", "ThrottlingException")]
        errors.append(ReadTimeoutError(endpoint_url="https://ssm.us-east-1.amazonaws.com"))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.client.get_parameter.side_effect = error
                with self.assertLogs("app.config_provider", level="WARNING") as logs:
                    result = self.load()
                self.assertEqual(result.settings, Settings.from_environment(ENV))
                self.assertEqual((result.source, result.version), ("environment", None))
                self.assertNotIn("private diagnostic", str(logs.output))

    def test_malformed_json_and_response_fall_back(self):
        invalid = [None, {}, {"Parameter": {}}, response()]
        invalid[-1]["Parameter"]["Value"] = "{bad-json"
        for field, value in (("Name", "/other"), ("Type", "SecureString"), ("Version", True), ("Value", None)):
            item = response()
            item["Parameter"][field] = value
            invalid.append(item)
        for item in invalid:
            with self.subTest(response=item):
                self.client.get_parameter.return_value = item
                self.assertEqual(self.load().source, "environment")

    def test_invalid_documents_cannot_merge_with_local_config(self):
        documents = [[], {}, {**DOCUMENT, "allowed_instances": None},
                     {**DOCUMENT, "allowed_buckets": "demo-bucket"},
                     {**DOCUMENT, "allowed_buckets": [True]},
                     {**DOCUMENT, "allowed_buckets": ["demo-bucket,other-bucket"]},
                     {**DOCUMENT, "allowed_buckets": []},
                     {**DOCUMENT, "allowed_instances": ["*"]},
                     {**DOCUMENT, "extra": "unsupported"},
                     {**DOCUMENT, "allowed_buckets": [f"demo-{i}" for i in range(101)]}]
        for document in documents:
            with self.subTest(document=document):
                self.client.get_parameter.return_value = response(document)
                self.assertEqual(self.load().settings, Settings.from_environment(ENV))

    def test_duplicate_json_keys_are_rejected(self):
        item = response()
        item["Parameter"]["Value"] = json.dumps(DOCUMENT)[:-1] + ',"region":"us-east-1"}'
        self.client.get_parameter.return_value = item
        self.assertEqual(self.load().source, "environment")

    def test_shared_validation_trims_and_deduplicates(self):
        settings = Settings.from_document({**DOCUMENT, "allowed_instances": [" i-12345678 ", "i-12345678"]})
        self.assertEqual(settings.instances, ("i-12345678",))

    def test_both_sources_invalid_fail_closed(self):
        self.client.get_parameter.return_value = {}
        with self.assertRaises(ConfigurationError):
            self.load({"AWS_REGION": "us-east-1"})

    def test_sdk_client_creation_failure_falls_back(self):
        self.session.client.side_effect = ReadTimeoutError(endpoint_url="https://example.invalid")
        self.assertEqual(self.load().source, "environment")

    def test_sdk_contract_with_stubber(self):
        with patch("botocore.session.Session.get_credentials", return_value=None):
            client = boto3.Session().client("ssm", region_name="us-east-1", config=Config(signature_version=UNSIGNED))
        self.addCleanup(client.close)
        self.session.client.return_value = client
        with Stubber(client) as stub:
            stub.add_response("get_parameter", response(), {"Name": PARAMETER_NAME, "WithDecryption": False})
            self.assertEqual(self.load().version, 3)
            stub.assert_no_pending_responses()

    def test_configuration_and_posture_share_audit_correlation(self):
        s3 = Mock()
        s3.get_public_access_block.return_value = {"PublicAccessBlockConfiguration": {
            key: True for key in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")}}
        self.session.client.side_effect = lambda service, **kwargs: self.client if service == "ssm" else s3
        with patch.dict("os.environ", ENV, clear=True), patch("boto3.Session", self.factory), self.assertLogs(level="INFO") as logs:
            scan = collect_from_environment()
        events = [json.loads(record.message) for record in logs.records]
        self.assertEqual(len(events), 2)
        self.assertTrue(all(event["scan_id"] == scan.scan_id for event in events))
        self.assertEqual(events[1]["config_version"], 3)
        self.assertNotIn("demo-bucket", str(logs.output))

    def test_health_bypasses_ssm_and_invalid_config_returns_unknown(self):
        self.client.get_parameter.return_value = {}
        app = create_application(collect_from_environment)
        with patch.dict("os.environ", {"AWS_REGION": "us-east-1"}, clear=True), patch("boto3.Session", self.factory):
            result = request(app, "/healthz")
            self.assertTrue(result["status"].startswith("200"))
            self.assertEqual(json.loads(result["body"]), {"liveness": "ok"})
            self.client.get_parameter.assert_not_called()
            result = request(app, "/")
            self.assertTrue(result["status"].startswith("503"))
            self.assertIn("UNKNOWN", result["body"])
        self.assertEqual(self.session.client.call_count, 1)
