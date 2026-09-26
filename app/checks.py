"""Pure checks over normalized observations; no AWS access."""

from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_network
import re


BPA_FLAGS = (
    "BlockPublicAcls", "IgnorePublicAcls",
    "BlockPublicPolicy", "RestrictPublicBuckets",
)

ERROR_REASONS = {
    "access_denied": "AWS access denied; configuration could not be verified.",
    "api_error": "AWS API error; configuration could not be verified.",
    "sdk_error": "AWS SDK, credentials, connection, or timeout error; collection unavailable.",
    "invalid_response": "AWS evidence missing, malformed, partial, or unsupported.",
    "unexpected_error": "Unexpected collection failure; configuration could not be verified.",
}


@dataclass(frozen=True)
class Result:
    status: str
    reason: str
    observed_at: str | None


def check(kind, observation):
    """Errors and incomplete evidence take precedence over any PASS/REVIEW."""
    timestamp = None
    if isinstance(observation, dict):
        raw = observation.get("observed_at")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.utcoffset() is not None:
                timestamp = raw
        except (AttributeError, TypeError, ValueError):
            pass

    def result(status, reason):
        return Result(status, reason, timestamp)

    if not isinstance(observation, dict):
        return result("UNKNOWN", "Observation missing or malformed.")
    if observation.get("error") is not None:
        error = observation["error"]
        reason = ERROR_REASONS.get(error) if isinstance(error, str) else None
        return result("UNKNOWN", reason or "Collection/API error; configuration could not be verified.")
    if timestamp is None:
        return result("UNKNOWN", "Observation timestamp missing or invalid.")
    data = observation.get("data")
    if kind == "ebs_encryption":
        if not isinstance(data, list) or not data:
            return result("UNKNOWN", "No complete attached EBS volume evidence available.")
        seen = set()
        unencrypted = 0
        for volume in data:
            if not isinstance(volume, dict):
                return result("UNKNOWN", "Attached EBS volume evidence malformed.")
            volume_id = volume.get("volume_id")
            if (not isinstance(volume_id, str)
                    or not re.fullmatch(r"vol-(?:[0-9a-f]{8}|[0-9a-f]{17})", volume_id)
                    or volume_id in seen or type(volume.get("encrypted")) is not bool):
                return result("UNKNOWN", "Volume identity or encryption flag missing, invalid, or duplicated.")
            seen.add(volume_id)
            unencrypted += volume["encrypted"] is False
        if unencrypted:
            return result("REVIEW", f"{unencrypted} of {len(data)} attached EBS volumes report Encrypted=False.")
        return result("PASS", f"All {len(data)} attached EBS volumes report Encrypted=True.")
    if kind == "s3":
        if not isinstance(data, dict) or any(type(data.get(k)) is not bool for k in BPA_FLAGS):
            return result("UNKNOWN", "All four bucket-level settings must be present as booleans.")
        disabled = [k for k in BPA_FLAGS if not data[k]]
        if disabled:
            return result("REVIEW", "Bucket-level settings disabled: " + ", ".join(disabled) + ".")
        return result("PASS", "All four bucket-level Block Public Access settings enabled; effective exposure is not assessed.")
    if kind != "security_group":
        return result("UNKNOWN", "Unsupported check type.")
    if not isinstance(data, list):
        return result("UNKNOWN", "Complete inbound-rule list missing or malformed.")
    flagged = set()
    for rule in data:
        if not isinstance(rule, dict):
            return result("UNKNOWN", "Inbound rule missing or malformed.")
        protocol = rule.get("protocol")
        if protocol not in ("tcp", "udp", "icmp", "icmpv6", "all"):
            return result("UNKNOWN", "Protocol missing or unsupported.")
        sources = rule.get("cidrs")
        if not isinstance(sources, list) or not sources:
            return result("UNKNOWN", "CIDR sources missing; non-CIDR sources are not supported.")
        try:
            if any(not isinstance(source, str) for source in sources):
                raise ValueError
            networks = [ip_network(source, strict=True) for source in sources]
        except ValueError:
            return result("UNKNOWN", "Invalid CIDR source.")
        if protocol in ("tcp", "udp"):
            start, end = rule.get("from_port"), rule.get("to_port")
            if type(start) is not int or type(end) is not int or not 0 <= start <= end <= 65535:
                return result("UNKNOWN", "Port range missing or invalid.")
        elif protocol in ("icmp", "icmpv6"):
            start, end = rule.get("from_port"), rule.get("to_port")
            if (type(start) is not int or type(end) is not int
                    or not -1 <= start <= 255 or not -1 <= end <= 255
                    or (start == -1 and end != -1)):
                return result("UNKNOWN", "ICMP type/code missing or invalid.")
        else:
            start, end = 0, 65535
        if protocol in ("tcp", "all") and any(n.prefixlen == 0 for n in networks):
            flagged.update(port for port in (22, 3389) if start <= port <= end)
    if flagged:
        return result("REVIEW", "Worldwide inbound source includes TCP management port(s): " + ", ".join(map(str, sorted(flagged))) + "; reachability is not established.")
    return result("PASS", "No worldwide TCP SSH (22) or RDP (3389) access in the supplied inbound rules; other exposure is not assessed.")
