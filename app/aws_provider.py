"""Bounded AWS reads and strict normalization into the check evidence contract."""

from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import ip_network
import json
import logging
import re
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.checks import check
from app.config import Settings

LOGGER = logging.getLogger(__name__)
SDK_CONFIG = Config(
    connect_timeout=3, read_timeout=5,
    retries={"mode": "standard", "total_max_attempts": 2},
    ignore_configured_endpoint_urls=True,
)
PROTOCOLS = {"tcp": "tcp", "6": "tcp", "udp": "udp", "17": "udp",
             "icmp": "icmp", "1": "icmp", "icmpv6": "icmpv6", "58": "icmpv6", "-1": "all"}


class EvidenceError(ValueError):
    """Missing, unsupported, or malformed AWS evidence."""


@dataclass(frozen=True)
class Scan:
    region: str
    scan_id: str
    started_at: str
    completed_at: str
    resources: list


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_permissions(permissions):
    if not isinstance(permissions, list):
        raise EvidenceError("Inbound permissions missing or malformed.")
    normalized = []
    for permission in permissions:
        if not isinstance(permission, dict):
            raise EvidenceError("Inbound rule malformed.")
        raw_protocol = permission.get("IpProtocol")
        if not isinstance(raw_protocol, str) or raw_protocol not in PROTOCOLS:
            raise EvidenceError("Protocol missing or unsupported.")
        rule = {"protocol": PROTOCOLS[raw_protocol], "cidrs": [],
                "from_port": permission.get("FromPort"), "to_port": permission.get("ToPort")}
        # Require all source collections, including explicitly empty ones. Never
        # silently discard unsupported sources or turn a partial rule into PASS.
        for field in ("IpRanges", "Ipv6Ranges", "UserIdGroupPairs", "PrefixListIds"):
            if not isinstance(permission.get(field), list):
                raise EvidenceError("Source collections missing or malformed.")
        if permission["UserIdGroupPairs"] or permission["PrefixListIds"]:
            raise EvidenceError("Referenced security groups or prefix lists are not evaluated.")
        for field, cidr_key, version in (("IpRanges", "CidrIp", 4), ("Ipv6Ranges", "CidrIpv6", 6)):
            for source in permission[field]:
                if not isinstance(source, dict) or not isinstance(source.get(cidr_key), str):
                    raise EvidenceError("CIDR missing or malformed.")
                try:
                    network = ip_network(source[cidr_key], strict=True)
                except ValueError as exc:
                    raise EvidenceError("Invalid CIDR.") from exc
                if network.version != version:
                    raise EvidenceError("CIDR address family mismatch.")
                rule["cidrs"].append(source[cidr_key])
        # ICMPv6 permits omitted type/code together; ICMP does not.
        if rule["protocol"] == "icmpv6" and "FromPort" not in permission and "ToPort" not in permission:
            rule["from_port"] = rule["to_port"] = -1
        normalized.append(rule)
    return normalized


def security_group_data(response, expected_id):
    if not isinstance(response, dict):
        raise EvidenceError("Response malformed.")
    # One explicit GroupId without MaxResults requests its complete result.
    # An unexpected continuation token is incomplete evidence, never PASS.
    if response.get("NextToken") not in (None, ""):
        raise EvidenceError("Unexpected partial response.")
    groups = response.get("SecurityGroups")
    if not isinstance(groups, list) or len(groups) != 1:
        raise EvidenceError("Requested security group missing or response ambiguous.")
    group = groups[0]
    if not isinstance(group, dict) or group.get("GroupId") != expected_id:
        raise EvidenceError("Returned security group does not match requested scope.")
    return normalize_permissions(group.get("IpPermissions"))


def safe_request_id(response):
    if not isinstance(response, dict) or not isinstance(response.get("ResponseMetadata"), dict):
        return None
    value = response["ResponseMetadata"].get("RequestId")
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9+=/_-]{1,200}", value) else None


class AWSProvider:
    def __init__(self, settings, session_factory=None, clock=utc_now):
        self.settings = settings
        self.session_factory = session_factory or boto3.Session
        self.clock = clock

    def collect(self):
        started = self.clock()
        scan_id = str(uuid4())
        resources = []
        session = None
        clients = {}
        targets = [("s3", name) for name in self.settings.buckets]
        targets += [("security_group", name) for name in self.settings.security_groups]
        for ordinal, (kind, name) in enumerate(targets):
            data, error, request_id = None, None, None
            diagnostic = None
            try:
                if session is None:
                    # No explicit credentials or profile: EC2 uses its instance role
                    # through the SDK default provider chain, including refresh.
                    session = self.session_factory()
                service = "s3" if kind == "s3" else "ec2"
                if service not in clients:
                    clients[service] = session.client(service, region_name=self.settings.region, config=SDK_CONFIG)
                client = clients[service]
                if kind == "s3":
                    response = client.get_public_access_block(Bucket=name)
                    request_id = safe_request_id(response)
                    if not isinstance(response, dict):
                        raise EvidenceError("Response malformed.")
                    data = response.get("PublicAccessBlockConfiguration")
                else:
                    response = client.describe_security_groups(GroupIds=[name])
                    request_id = safe_request_id(response)
                    data = security_group_data(response, name)
            except ClientError as exc:
                request_id = safe_request_id(exc.response)
                code = exc.response.get("Error", {}).get("Code")
                error = "access_denied" if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "AuthFailure") else "api_error"
            except BotoCoreError as exc:
                error = "sdk_error"
                diagnostic = type(exc).__name__
            except EvidenceError as exc:
                error = "invalid_response"
                diagnostic = str(exc)  # Internal constant messages only.
            except Exception as exc:
                # Fail closed at the integration boundary; never log response
                # payloads, exception messages, credentials, or resource names.
                error = "unexpected_error"
                diagnostic = type(exc).__name__
            observed_at = self.clock()
            observation = {"observed_at": observed_at, "data": data, "error": error}
            result = check(kind, observation)
            LOGGER.info(json.dumps({"event": "posture_observation", "scan_id": scan_id,
                                    "target_index": ordinal, "kind": kind, "observed_at": observed_at,
                                    "status": result.status, "error": error,
                                    "reason": result.reason, "diagnostic": diagnostic, "request_id": request_id}))
            resources.append({"kind": kind, "name": name, "observation": observation})
        for client in clients.values():
            try:
                client.close()
            except Exception:
                LOGGER.warning("aws_client_cleanup_failed")
        return Scan(self.settings.region, scan_id, started, self.clock(), resources)


def collect_from_environment():
    return AWSProvider(Settings.from_environment()).collect()
