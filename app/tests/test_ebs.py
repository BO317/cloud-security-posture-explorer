"""Offline EBS collection, completeness, evaluation, and audit tests."""

import json
import unittest
from unittest.mock import Mock, call, patch

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.stub import Stubber

from app.aws_provider import AWSProvider
from app.checks import check
from app.config import Settings
from app.server import create_application
from app.tests.test_server import request

NAME = "cloud-security-posture-explorer"
OTHER_NAME = "lab-worker"
INSTANCE = "i-0123456789abcdef0"
OTHER = "i-0123456789abcdef1"
VOLUME = "vol-0123456789abcdef0"
SECOND_VOLUME = "vol-0123456789abcdef1"
STAMP = "2026-09-26T12:00:00+00:00"


def instance_request(name):
    return {"Filters": [{"Name": "tag:Name", "Values": [name]},
                        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}],
            "MaxResults": 5}


def instance_response(instance_id=INSTANCE, volume_ids=(VOLUME,), name=NAME):
    return {"Reservations": [{"Instances": [{"InstanceId": instance_id,
            "Tags": [{"Key": "Name", "Value": name}], "State": {"Name": "running"},
            "BlockDeviceMappings": [{"Ebs": {"VolumeId": value, "Status": "attached"}} for value in volume_ids]}]}],
            "ResponseMetadata": {"RequestId": "instance-request"}}


def volume_response(encrypted=True, volume_id=VOLUME, instance_id=INSTANCE):
    return {"Volumes": [{"VolumeId": volume_id, "Encrypted": encrypted,
            "Attachments": [{"InstanceId": instance_id, "VolumeId": volume_id, "State": "attached"}]}],
            "ResponseMetadata": {"RequestId": "volume-request"}}


class EBSProviderTest(unittest.TestCase):
    def setUp(self):
        blocker = patch("socket.socket.connect", side_effect=AssertionError("Unexpected network access"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.ec2, self.session = Mock(), Mock()
        self.session.client.return_value = self.ec2
        self.provider = AWSProvider(Settings("us-east-1", (), (), (NAME,)),
                                    Mock(return_value=self.session), clock=lambda: STAMP)
        self.ec2.describe_instances.return_value = instance_response()
        self.ec2.describe_volumes.return_value = volume_response()

    def result(self):
        resources = self.provider.collect().resources
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0]["name"], NAME)
        return check(resources[0]["kind"], resources[0]["observation"])

    def test_encrypted_volume_passes_and_requests_are_bounded(self):
        result = self.result()
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.observed_at, STAMP)
        self.ec2.describe_instances.assert_called_once_with(**instance_request(NAME))
        self.ec2.describe_volumes.assert_called_once_with(VolumeIds=[VOLUME])
        self.session.client.assert_called_once()

    def test_unencrypted_volume_is_review(self):
        self.ec2.describe_volumes.return_value = volume_response(False)
        self.assertEqual(self.result().status, "REVIEW")

    def test_replacement_instance_is_resolved_without_configuration_change(self):
        self.assertEqual(self.result().status, "PASS")
        self.ec2.describe_instances.return_value = instance_response(OTHER, (SECOND_VOLUME,))
        self.ec2.describe_volumes.return_value = volume_response(False, SECOND_VOLUME, OTHER)
        self.assertEqual(self.result().status, "REVIEW")
        self.assertEqual(self.provider.settings.instance_name_tags, (NAME,))
        self.ec2.describe_volumes.assert_called_with(VolumeIds=[SECOND_VOLUME])
        self.assertEqual(self.ec2.describe_instances.call_args_list,
                         [call(**instance_request(NAME)), call(**instance_request(NAME))])

    def test_duplicate_name_matches_are_unknown_without_volume_reads(self):
        for separate_reservations in (False, True):
            response = instance_response()
            other = instance_response(OTHER)
            if separate_reservations:
                response["Reservations"] += other["Reservations"]
            else:
                response["Reservations"][0]["Instances"] += other["Reservations"][0]["Instances"]
            self.ec2.describe_instances.return_value = response
            self.assertEqual(self.result().status, "UNKNOWN")
        self.ec2.describe_volumes.assert_not_called()

    def test_tags_identity_and_state_must_be_complete_and_in_scope(self):
        for key, value in (
                ("Tags", None), ("Tags", []), ("Tags", [{}]),
                ("Tags", [{"Key": "Name", "Value": NAME}] * 2),
                ("Tags", [{"Key": "name", "Value": NAME}]),
                ("Tags", [{"Key": "Name", "Value": NAME.upper()}]),
                ("InstanceId", None), ("InstanceId", "invalid"),
                ("State", None), ("State", {}),
                ("State", {"Name": "terminated"}), ("State", {"Name": "shutting-down"})):
            with self.subTest(key=key, value=value):
                response = instance_response()
                response["Reservations"][0]["Instances"][0][key] = value
                self.ec2.describe_instances.return_value = response
                self.assertEqual(self.result().status, "UNKNOWN")
        self.ec2.describe_volumes.assert_not_called()

    def test_stopped_instance_remains_in_encryption_scope(self):
        response = instance_response()
        response["Reservations"][0]["Instances"][0]["State"] = {"Name": "stopped"}
        self.ec2.describe_instances.return_value = response
        self.assertEqual(self.result().status, "PASS")

    def test_all_volumes_evaluated_regardless_of_return_order(self):
        self.ec2.describe_instances.return_value = instance_response(volume_ids=(VOLUME, SECOND_VOLUME))
        for encrypted, expected in ((True, "PASS"), (False, "REVIEW")):
            response = volume_response(encrypted, SECOND_VOLUME)
            response["Volumes"] += volume_response()["Volumes"]
            self.ec2.describe_volumes.return_value = response
            self.assertEqual(self.result().status, expected)

    def test_missing_invalid_or_partial_instance_data_is_unknown(self):
        cases = [None, {}, {"Reservations": []}, {"Reservations": [None]},
                 {"Reservations": [{"Instances": []}]}, instance_response(name=OTHER_NAME),
                 instance_response(volume_ids=()), {**instance_response(), "NextToken": "more"}]
        for replacement in (None, {}, [None], [{}], [{"Ebs": {}}],
                            [{"Ebs": {"VolumeId": VOLUME}}],
                            [{"Ebs": {"VolumeId": VOLUME, "Status": "attaching"}}],
                            [{"Ebs": {"VolumeId": "invalid", "Status": "attached"}}]):
            response = instance_response()
            response["Reservations"][0]["Instances"][0]["BlockDeviceMappings"] = replacement
            cases.append(response)
        response = instance_response()
        del response["Reservations"][0]["Instances"][0]["BlockDeviceMappings"]
        cases.append(response)
        cases.append(instance_response(volume_ids=(VOLUME, VOLUME)))
        for response in cases:
            with self.subTest(response=response):
                self.ec2.describe_instances.return_value = response
                self.assertEqual(self.result().status, "UNKNOWN")
        self.ec2.describe_volumes.assert_not_called()

    def test_missing_invalid_or_partial_volume_data_is_unknown(self):
        cases = [None, {}, {"Volumes": []}, {"Volumes": None}, {"Volumes": [None]},
                 volume_response(volume_id=SECOND_VOLUME), {**volume_response(), "NextToken": "more"}]
        for key in ("VolumeId", "Encrypted", "Attachments"):
            response = volume_response()
            del response["Volumes"][0][key]
            cases.append(response)
        for encrypted in (None, 1, "true"):
            cases.append(volume_response(encrypted))
        for attachments in (None, [], [None], [{}], [{"InstanceId": OTHER}],
                            [{"InstanceId": INSTANCE, "VolumeId": VOLUME, "State": "detaching"}],
                            [{"InstanceId": INSTANCE, "State": "attached"}]):
            response = volume_response()
            response["Volumes"][0]["Attachments"] = attachments
            cases.append(response)
        for response in cases:
            with self.subTest(response=response):
                self.ec2.describe_volumes.return_value = response
                self.assertEqual(self.result().status, "UNKNOWN")

    def test_partial_results_override_known_unencrypted_volume(self):
        self.ec2.describe_instances.return_value = instance_response(volume_ids=(VOLUME, SECOND_VOLUME))
        for response in (volume_response(False), {"Volumes": volume_response(False)["Volumes"] * 2}):
            self.ec2.describe_volumes.return_value = response
            self.assertEqual(self.result().status, "UNKNOWN")
        response = volume_response(False)
        response["Volumes"] += volume_response(True, SECOND_VOLUME)["Volumes"]
        del response["Volumes"][1]["Encrypted"]
        self.ec2.describe_volumes.return_value = response
        self.assertEqual(self.result().status, "UNKNOWN")

    def test_access_denied_on_either_api_is_unknown(self):
        for operation in ("describe_instances", "describe_volumes"):
            with self.subTest(operation=operation):
                method = getattr(self.ec2, operation)
                method.side_effect = ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "private details"},
                                                 "ResponseMetadata": {"RequestId": "denied-request"}}, operation)
                result = self.result()
                self.assertEqual(result.status, "UNKNOWN")
                self.assertIn("access denied", result.reason)
                self.assertNotIn("private details", result.reason)
                method.side_effect = None

    def test_api_timeout_on_either_api_is_unknown(self):
        for operation in ("describe_instances", "describe_volumes"):
            method = getattr(self.ec2, operation)
            method.side_effect = ReadTimeoutError(endpoint_url="https://example.invalid")
            self.assertEqual(self.result().status, "UNKNOWN")
            method.side_effect = None

    def test_one_instance_failure_does_not_hide_the_next(self):
        self.provider.settings = Settings("us-east-1", (), (), (NAME, OTHER_NAME))
        self.ec2.describe_instances.side_effect = [ReadTimeoutError(endpoint_url="https://example.invalid"), instance_response(OTHER, name=OTHER_NAME)]
        self.ec2.describe_volumes.return_value = volume_response(instance_id=OTHER)
        scan = self.provider.collect()
        self.assertEqual([check(row["kind"], row["observation"]).status for row in scan.resources], ["UNKNOWN", "PASS"])
        self.assertEqual(self.ec2.describe_instances.call_args_list, [call(**instance_request(NAME)), call(**instance_request(OTHER_NAME))])

    def test_empty_instance_allowlist_makes_no_instance_or_volume_calls(self):
        self.provider.settings = Settings("us-east-1", (), ("sg-12345678",))
        self.ec2.describe_security_groups.return_value = {"SecurityGroups": [{"GroupId": "sg-12345678", "IpPermissions": []}]}
        self.provider.collect()
        self.ec2.describe_instances.assert_not_called()
        self.ec2.describe_volumes.assert_not_called()

    def test_audit_preserves_scan_id_and_all_request_ids_without_identifiers(self):
        with self.assertLogs("app.aws_provider", level="INFO") as captured:
            scan = self.provider.collect()
        event = json.loads(captured.records[0].getMessage())
        self.assertEqual(event["scan_id"], scan.scan_id)
        self.assertEqual(event["request_id"], "volume-request")
        self.assertEqual(event["api_requests"], [
            {"operation": "describe_instances", "request_id": "instance-request"},
            {"operation": "describe_volumes", "request_id": "volume-request"}])
        self.assertEqual(event["kind"], "ebs_encryption")
        for identifier in (NAME, INSTANCE, VOLUME):
            self.assertNotIn(identifier, " ".join(captured.output))

    def test_failed_volume_call_retains_both_audit_requests(self):
        self.ec2.describe_volumes.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "private details"},
                                                           "ResponseMetadata": {"RequestId": "denied-request"}}, "DescribeVolumes")
        with self.assertLogs("app.aws_provider", level="INFO") as captured:
            self.result()
        event = json.loads(captured.records[0].getMessage())
        self.assertEqual(event["api_requests"][0]["request_id"], "instance-request")
        self.assertEqual(event["request_id"], "denied-request")
        self.assertEqual(event["api_requests"][1]["request_id"], "denied-request")
        self.assertNotIn("private details", " ".join(captured.output))

    def test_timeout_does_not_reuse_previous_api_request_id(self):
        self.ec2.describe_volumes.side_effect = ReadTimeoutError(endpoint_url="https://example.invalid")
        with self.assertLogs("app.aws_provider", level="INFO") as captured:
            self.result()
        event = json.loads(captured.records[0].getMessage())
        self.assertIsNone(event["request_id"])
        self.assertEqual(event["api_requests"][0]["request_id"], "instance-request")
        self.assertIsNone(event["api_requests"][1]["request_id"])

    def test_dashboard_uses_existing_columns_and_health_remains_independent(self):
        app = create_application(self.provider.collect)
        response = request(app)
        for text in ("EBS Volume Encryption", NAME, "PASS", STAMP):
            self.assertIn(text, response["body"])
        self.assertEqual(response["body"].count("<tr>"), 2)
        self.ec2.describe_instances.reset_mock()
        self.assertEqual(json.loads(request(app, "/healthz")["body"]), {"liveness": "ok"})
        self.ec2.describe_instances.assert_not_called()

    def test_sdk_stubber_validates_operations_and_scope(self):
        session = boto3.Session()
        with patch.object(session._session, "get_credentials", return_value=None):
            client = session.client("ec2", region_name="us-east-1", config=Config(signature_version=UNSIGNED))
        self.addCleanup(client.close)
        self.session.client.return_value = client
        with Stubber(client) as stub:
            stub.add_response("describe_instances", instance_response(), instance_request(NAME))
            stub.add_response("describe_volumes", volume_response(), {"VolumeIds": [VOLUME]})
            self.assertEqual(self.result().status, "PASS")
            stub.assert_no_pending_responses()


class EBSCheckTest(unittest.TestCase):
    def test_strict_flags_empty_evidence_and_unknown_precedence(self):
        valid = {"volume_id": VOLUME, "encrypted": False}
        for data in (None, [], {}, [None], [{}], [valid, valid],
                     [valid, {"volume_id": SECOND_VOLUME, "encrypted": "true"}],
                     [valid, {"volume_id": SECOND_VOLUME}]):
            result = check("ebs_encryption", {"observed_at": STAMP, "data": data})
            self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(check("ebs_encryption", {"observed_at": STAMP, "data": [valid]}).status, "REVIEW")
        self.assertEqual(check("ebs_encryption", {"observed_at": STAMP, "data": [valid], "error": "access_denied"}).status, "UNKNOWN")
