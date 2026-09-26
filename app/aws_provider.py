"""Bounded AWS reads and strict normalization into the check evidence contract."""

from dataclasses import dataclass
from ipaddress import ip_network
import json
import logging
import re
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.checks import check
from app.aws_support import SDK_CONFIG, safe_request_id, utc_now
from app.config_provider import load_configuration

LOGGER = logging.getLogger(__name__)
INSTANCE_STATES = ("pending", "running", "stopping", "stopped")
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


def read_with_audit(client, operation, api_requests, **parameters):
    """Record each attempted operation, without its inventory-bearing parameters."""
    entry = {"operation": operation, "request_id": None}
    api_requests.append(entry)
    response = getattr(client, operation)(**parameters)
    entry["request_id"] = safe_request_id(response)
    return response


def resolve_named_instance(response, expected_name):
    """Require one complete, unambiguous match; never pick the first match."""
    if not isinstance(response, dict) or response.get("NextToken") not in (None, ""):
        raise EvidenceError("Instance response missing, malformed, or partial.")
    reservations = response.get("Reservations")
    if not isinstance(reservations, list) or len(reservations) != 1:
        raise EvidenceError("Requested instance missing or response ambiguous.")
    reservation = reservations[0]
    instances = reservation.get("Instances") if isinstance(reservation, dict) else None
    if not isinstance(instances, list) or len(instances) != 1:
        raise EvidenceError("Requested instance missing or response ambiguous.")
    instance = instances[0]
    if not isinstance(instance, dict):
        raise EvidenceError("Instance data malformed.")
    instance_id = instance.get("InstanceId")
    if not isinstance(instance_id, str) or not re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", instance_id):
        raise EvidenceError("Resolved instance ID missing or malformed.")
    state = instance.get("State")
    if not isinstance(state, dict) or state.get("Name") not in INSTANCE_STATES:
        raise EvidenceError("Instance state missing, malformed, or outside scope.")
    tags = instance.get("Tags")
    if not isinstance(tags, list) or any(
            not isinstance(tag, dict) or not isinstance(tag.get("Key"), str)
            or not isinstance(tag.get("Value"), str) for tag in tags):
        raise EvidenceError("Instance tags missing or malformed.")
    names = [tag["Value"] for tag in tags if tag["Key"] == "Name"]
    if names != [expected_name]:
        raise EvidenceError("Instance Name tag missing, duplicated, or outside scope.")
    return instance


def attached_volume_ids(instance):
    """Read mappings only after validating the instance identity and scope."""
    mappings = instance.get("BlockDeviceMappings")
    if not isinstance(mappings, list) or not mappings:
        raise EvidenceError("No complete attached EBS volume mappings available.")
    volume_ids = []
    for mapping in mappings:
        ebs = mapping.get("Ebs") if isinstance(mapping, dict) else None
        if not isinstance(ebs, dict):
            raise EvidenceError("EBS mapping missing or malformed.")
        volume_id = ebs.get("VolumeId")
        if (not isinstance(volume_id, str)
                or not re.fullmatch(r"vol-(?:[0-9a-f]{8}|[0-9a-f]{17})", volume_id)
                or volume_id in volume_ids or ebs.get("Status") != "attached"):
            raise EvidenceError("EBS volume identity or stable attachment missing, invalid, or duplicated.")
        volume_ids.append(volume_id)
    return volume_ids


def normalize_volumes(response, instance_id, expected_ids):
    """Match all requested volumes; never evaluate just the returned subset."""
    if not isinstance(response, dict) or response.get("NextToken") not in (None, ""):
        raise EvidenceError("Volume response missing, malformed, or partial.")
    volumes = response.get("Volumes")
    if not isinstance(volumes, list) or len(volumes) != len(expected_ids):
        raise EvidenceError("Attached volume results incomplete or ambiguous.")
    expected = set(expected_ids)
    seen = set()
    normalized = []
    for volume in volumes:
        if not isinstance(volume, dict):
            raise EvidenceError("Volume data malformed.")
        volume_id = volume.get("VolumeId")
        if not isinstance(volume_id, str) or volume_id not in expected or volume_id in seen:
            raise EvidenceError("Returned volume does not match requested scope or is duplicated.")
        attachments = volume.get("Attachments")
        if not isinstance(attachments, list) or any(not isinstance(item, dict) for item in attachments):
            raise EvidenceError("Volume attachments missing or malformed.")
        matches = [item for item in attachments if item.get("InstanceId") == instance_id]
        if (len(matches) != 1 or matches[0].get("VolumeId") != volume_id
                or matches[0].get("State") != "attached"):
            raise EvidenceError("Volume attachment to the requested instance could not be confirmed.")
        if type(volume.get("Encrypted")) is not bool:
            raise EvidenceError("Volume encryption flag missing or invalid.")
        seen.add(volume_id)
        normalized.append({"volume_id": volume_id, "encrypted": volume["Encrypted"]})
    return normalized


def instance_volume_data(client, name_tag, api_requests):
    # One bounded page suffices only when it proves a unique complete match.
    # Any NextToken is UNKNOWN; do not follow it or assume the first match wins.
    response = read_with_audit(client, "describe_instances", api_requests,
                               Filters=[{"Name": "tag:Name", "Values": [name_tag]},
                                        {"Name": "instance-state-name", "Values": list(INSTANCE_STATES)}],
                               MaxResults=5)
    instance = resolve_named_instance(response, name_tag)
    instance_id = instance["InstanceId"]
    volume_ids = attached_volume_ids(instance)
    # Never call DescribeVolumes with an empty list (which could broaden scope).
    response = read_with_audit(client, "describe_volumes", api_requests, VolumeIds=volume_ids)
    return normalize_volumes(response, instance_id, volume_ids)


class AWSProvider:
    def __init__(self, settings, session_factory=None, clock=utc_now):
        self.settings = settings
        self.session_factory = session_factory or boto3.Session
        self.clock = clock

    def collect(self, *, scan_id=None, started_at=None, config_source=None, config_version=None):
        started = started_at or self.clock()
        scan_id = scan_id or str(uuid4())
        resources = []
        session = None
        clients = {}
        targets = [("s3", name) for name in self.settings.buckets]
        targets += [("security_group", name) for name in self.settings.security_groups]
        targets += [("ebs_encryption", name) for name in self.settings.instance_name_tags]
        for ordinal, (kind, name) in enumerate(targets):
            data, error, request_id = None, None, None
            diagnostic = None
            api_requests = []
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
                    response = read_with_audit(client, "get_public_access_block", api_requests, Bucket=name)
                    request_id = safe_request_id(response)
                    if not isinstance(response, dict):
                        raise EvidenceError("Response malformed.")
                    data = response.get("PublicAccessBlockConfiguration")
                elif kind == "security_group":
                    response = read_with_audit(client, "describe_security_groups", api_requests, GroupIds=[name])
                    request_id = safe_request_id(response)
                    data = security_group_data(response, name)
                else:
                    data = instance_volume_data(client, name, api_requests)
            except ClientError as exc:
                request_id = safe_request_id(exc.response)
                if api_requests:
                    api_requests[-1]["request_id"] = request_id
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
            if api_requests:
                request_id = api_requests[-1]["request_id"]
            observation = {"observed_at": observed_at, "data": data, "error": error}
            result = check(kind, observation)
            LOGGER.info(json.dumps({"event": "posture_observation", "scan_id": scan_id,
                                    "target_index": ordinal, "kind": kind, "observed_at": observed_at,
                                    "status": result.status, "error": error,
                                    "reason": result.reason, "diagnostic": diagnostic, "request_id": request_id,
                                    "api_requests": api_requests, "config_source": config_source,
                                    "config_version": config_version}))
            resources.append({"kind": kind, "name": name, "observation": observation})
        for client in clients.values():
            try:
                client.close()
            except Exception:
                LOGGER.warning("aws_client_cleanup_failed")
        return Scan(self.settings.region, scan_id, started, self.clock(), resources)


def collect_from_environment():
    """Resolve primary/fallback configuration inside the existing scan boundary."""
    started, scan_id = utc_now(), str(uuid4())
    loaded = load_configuration(scan_id=scan_id)
    return AWSProvider(loaded.settings).collect(
        scan_id=scan_id, started_at=started,
        config_source=loaded.source, config_version=loaded.version)
