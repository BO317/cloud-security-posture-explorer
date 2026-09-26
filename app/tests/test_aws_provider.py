import json
import unittest
from unittest.mock import Mock, patch

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError, ReadTimeoutError
from botocore.stub import Stubber

from app.aws_provider import AWSProvider, SDK_CONFIG
from app.checks import BPA_FLAGS, check
from app.config import Settings

TIMESTAMP = "2026-09-26T12:00:00+00:00"
GROUP = "sg-12345678"


def permission(protocol="tcp", start=22, end=22, cidr="0.0.0.0/0", ipv6=False):
    return {"IpProtocol": protocol, "FromPort": start, "ToPort": end,
            "IpRanges": [] if ipv6 else [{"CidrIp": cidr}],
            "Ipv6Ranges": [{"CidrIpv6": cidr}] if ipv6 else [],
            "UserIdGroupPairs": [], "PrefixListIds": []}


def group_response(rules):
    return {"SecurityGroups": [{"GroupId": GROUP, "IpPermissions": rules}]}


class ProviderTest(unittest.TestCase):
    def setUp(self):
        # Fail if a test accidentally contacts AWS or IMDS.
        blocker = patch("socket.socket.connect", side_effect=AssertionError("Unexpected network access"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.s3, self.ec2, self.session = Mock(), Mock(), Mock()
        self.session.client.side_effect = lambda service, **kwargs: {"s3": self.s3, "ec2": self.ec2}[service]
        self.factory = Mock(return_value=self.session)
        self.settings = Settings("us-east-1", ("demo-bucket",), (GROUP,))
        self.provider = AWSProvider(self.settings, self.factory, clock=lambda: TIMESTAMP)
        self.s3.get_public_access_block.return_value = {"PublicAccessBlockConfiguration": dict.fromkeys(BPA_FLAGS, True)}
        self.ec2.describe_security_groups.return_value = group_response([])

    def statuses(self):
        return [check(r["kind"], r["observation"]).status for r in self.provider.collect().resources]

    def test_success_and_explicit_request_scope(self):
        self.assertEqual(self.statuses(), ["PASS", "PASS"])
        self.factory.assert_called_once_with()
        self.s3.get_public_access_block.assert_called_once_with(Bucket="demo-bucket")
        self.ec2.describe_security_groups.assert_called_once_with(GroupIds=[GROUP])
        for call in self.session.client.call_args_list:
            self.assertEqual(call.kwargs, {"region_name": "us-east-1", "config": SDK_CONFIG})
        self.assertEqual(SDK_CONFIG.retries["total_max_attempts"], 2)

    def test_each_disabled_s3_setting_is_review(self):
        for flag in BPA_FLAGS:
            self.s3.get_public_access_block.return_value = {"PublicAccessBlockConfiguration": {**dict.fromkeys(BPA_FLAGS, True), flag: False}}
            self.assertEqual(self.statuses()[0], "REVIEW")

    def test_s3_missing_malformed_and_partial_data(self):
        for response in (None, [], {}, {"PublicAccessBlockConfiguration": None}, {"PublicAccessBlockConfiguration": {}}, {"PublicAccessBlockConfiguration": dict.fromkeys(BPA_FLAGS, "true")}, {"PublicAccessBlockConfiguration": {"BlockPublicAcls": False}}):
            with self.subTest(response=response):
                self.s3.get_public_access_block.return_value = response
                self.assertEqual(self.statuses(), ["UNKNOWN", "PASS"])

    def test_access_denied_and_other_api_errors(self):
        for code in ("AccessDenied", "UnauthorizedOperation", "Throttling", "NoSuchPublicAccessBlockConfiguration", "InvalidGroup.NotFound"):
            for method in (self.s3.get_public_access_block, self.ec2.describe_security_groups):
                method.side_effect = ClientError({"Error": {"Code": code, "Message": "private details"}}, "ReadOperation")
            scan = self.provider.collect()
            for resource in scan.resources:
                result = check(resource["kind"], resource["observation"])
                self.assertEqual(result.status, "UNKNOWN")
                self.assertNotIn("private details", result.reason)
                self.assertEqual(result.observed_at, TIMESTAMP)

    def test_timeout_connection_credentials_and_unexpected_errors(self):
        for error in (ReadTimeoutError(endpoint_url="https://example.invalid"), EndpointConnectionError(endpoint_url="https://example.invalid"), NoCredentialsError(), RuntimeError("private")):
            self.s3.get_public_access_block.side_effect = error
            self.ec2.describe_security_groups.side_effect = error
            self.assertEqual(self.statuses(), ["UNKNOWN", "UNKNOWN"])

    def test_session_initialization_failure(self):
        self.factory.side_effect = NoCredentialsError()
        self.assertEqual(self.statuses(), ["UNKNOWN", "UNKNOWN"])

    def test_client_initialization_failure_is_isolated(self):
        self.session.client.side_effect = [NoCredentialsError(), self.ec2]
        self.assertEqual(self.statuses(), ["UNKNOWN", "PASS"])

    def test_empty_scope_never_calls_aws(self):
        provider = AWSProvider(Settings("us-east-1", (), ()), self.factory)
        self.assertEqual(provider.collect().resources, [])
        self.factory.assert_not_called()

    def test_empty_response_is_not_empty_permissions(self):
        for response in ({}, {"SecurityGroups": []}, {"SecurityGroups": None}, None, [], {"SecurityGroups": [{}]}, group_response(None), {"SecurityGroups": [{"GroupId": GROUP}]}):
            self.ec2.describe_security_groups.return_value = response
            self.assertEqual(self.statuses()[1], "UNKNOWN")
        self.ec2.describe_security_groups.return_value = group_response([])
        self.assertEqual(self.statuses()[1], "PASS")

    def test_wrong_duplicate_and_partial_groups(self):
        for response in ({"SecurityGroups": [{"GroupId": "sg-87654321", "IpPermissions": []}]}, {"SecurityGroups": [{"GroupId": GROUP, "IpPermissions": []}] * 2}, {**group_response([]), "NextToken": "more"}):
            self.ec2.describe_security_groups.return_value = response
            self.assertEqual(self.statuses()[1], "UNKNOWN")

    def test_worldwide_tcp_numeric_protocol_and_all_protocols(self):
        for protocol in ("tcp", "6", "-1"):
            for cidr, ipv6 in (("0.0.0.0/0", False), ("::/0", True)):
                for start, end in ((22, 22), (3300, 3400), (0, 65535)):
                    rule = permission(protocol, start, end, cidr, ipv6)
                    if protocol == "-1":
                        del rule["FromPort"], rule["ToPort"]
                    self.ec2.describe_security_groups.return_value = group_response([rule])
                    self.assertEqual(self.statuses()[1], "REVIEW")

    def test_tcp_only_and_private_cidr_pass(self):
        for rule in (permission("udp", 3389, 3389), permission("17", 22, 22), permission(cidr="192.0.2.0/24"), permission(start=443, end=443), permission("icmp", -1, -1), permission("58", -1, -1, "::/0", True)):
            self.ec2.describe_security_groups.return_value = group_response([rule])
            self.assertEqual(self.statuses()[1], "PASS")

    def test_icmpv6_optional_type_code_and_invalid_icmp(self):
        rule = permission("icmpv6", cidr="::/0", ipv6=True)
        del rule["FromPort"], rule["ToPort"]
        self.ec2.describe_security_groups.return_value = group_response([rule])
        self.assertEqual(self.statuses()[1], "PASS")
        for protocol, start, end in (("icmp", None, None), ("icmp", -1, 2), ("icmpv6", None, 0), ("icmp", 256, 0)):
            self.ec2.describe_security_groups.return_value = group_response([permission(protocol, start, end)])
            self.assertEqual(self.statuses()[1], "UNKNOWN")

    def test_incomplete_rule_overrides_review_and_pass(self):
        variants = [None, {}, permission(start=None), permission(start=True), permission(start=23, end=22), permission(end=65536), permission(cidr="invalid"), permission(cidr="::/0"), permission(protocol="garbage")]
        for field in ("IpProtocol", "FromPort", "ToPort", "IpRanges", "Ipv6Ranges", "UserIdGroupPairs", "PrefixListIds"):
            rule = permission()
            del rule[field]
            variants.append(rule)
        for field in ("IpRanges", "Ipv6Ranges", "UserIdGroupPairs", "PrefixListIds"):
            rule = permission()
            rule[field] = None
            variants.append(rule)
        for invalid in variants:
            with self.subTest(rule=invalid):
                for preceding in (permission(), permission(start=443, end=443)):
                    self.ec2.describe_security_groups.return_value = group_response([preceding, invalid])
                    self.assertEqual(self.statuses()[1], "UNKNOWN")

    def test_missing_cidr_and_unsupported_references(self):
        for field, entries in (("IpRanges", [{}]), ("IpRanges", [None]), ("IpRanges", [{"CidrIp": 123}]), ("UserIdGroupPairs", [{"GroupId": GROUP}]), ("PrefixListIds", [{"PrefixListId": "pl-12345678"}])):
            rule = permission()
            rule[field] = entries
            self.ec2.describe_security_groups.return_value = group_response([rule])
            self.assertEqual(self.statuses()[1], "UNKNOWN")

    def test_one_failed_target_does_not_hide_other_targets(self):
        provider = AWSProvider(Settings("us-east-1", ("demo-one", "demo-two"), (GROUP,)), self.factory)
        self.s3.get_public_access_block.side_effect = [NoCredentialsError(), {"PublicAccessBlockConfiguration": dict.fromkeys(BPA_FLAGS, True)}]
        results = provider.collect().resources
        self.assertEqual([check(r["kind"], r["observation"]).status for r in results], ["UNKNOWN", "PASS", "PASS"])
        self.assertEqual([r["name"] for r in results], ["demo-one", "demo-two", GROUP])

    def test_audit_logs_have_correlation_without_inventory_payloads(self):
        self.s3.get_public_access_block.return_value["ResponseMetadata"] = {"RequestId": "request-123"}
        with self.assertLogs("app.aws_provider", level="INFO") as captured:
            scan = self.provider.collect()
        entries = [json.loads(record.getMessage()) for record in captured.records]
        self.assertEqual(entries[0]["request_id"], "request-123")
        self.assertEqual(entries[0]["scan_id"], scan.scan_id)
        self.assertEqual(entries[0]["observed_at"], TIMESTAMP)
        self.assertNotIn("demo-bucket", " ".join(captured.output))
        self.assertNotIn("SecurityGroups", " ".join(captured.output))

    def test_real_sdk_stubs_validate_operation_names_and_parameters(self):
        # Unsigned clients, mocked credential resolution, no credentials/IMDS.
        sdk_session = boto3.Session()
        with patch.object(sdk_session._session, "get_credentials", return_value=None):
            config = Config(signature_version=UNSIGNED)
            s3 = sdk_session.client("s3", region_name="us-east-1", config=config)
            ec2 = sdk_session.client("ec2", region_name="us-east-1", config=config)
        self.addCleanup(s3.close)
        self.addCleanup(ec2.close)
        self.session.client.side_effect = lambda service, **kwargs: {"s3": s3, "ec2": ec2}[service]
        with Stubber(s3) as s3_stub, Stubber(ec2) as ec2_stub:
            s3_stub.add_response("get_public_access_block", {"PublicAccessBlockConfiguration": dict.fromkeys(BPA_FLAGS, True)}, {"Bucket": "demo-bucket"})
            ec2_stub.add_response("describe_security_groups", group_response([permission()]), {"GroupIds": [GROUP]})
            self.assertEqual(self.statuses(), ["PASS", "REVIEW"])
            s3_stub.add_client_error("get_public_access_block", service_error_code="AccessDenied", expected_params={"Bucket": "demo-bucket"})
            ec2_stub.add_client_error("describe_security_groups", service_error_code="UnauthorizedOperation", expected_params={"GroupIds": [GROUP]})
            self.assertEqual(self.statuses(), ["UNKNOWN", "UNKNOWN"])
            s3_stub.assert_no_pending_responses()
            ec2_stub.assert_no_pending_responses()
