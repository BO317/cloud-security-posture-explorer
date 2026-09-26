"""Entirely invented fixtures. Timestamp is fixed, never presented as live."""

from app.checks import BPA_FLAGS

OBSERVED_AT = "2026-09-25T12:00:00Z"


def sample_resources():
    enabled = dict.fromkeys(BPA_FLAGS, True)
    examples = [
        ("s3", "demo-bucket-protected", enabled, None),
        ("s3", "demo-bucket-review", {**enabled, "BlockPublicPolicy": False}, None),
        ("s3", "demo-bucket-unavailable", None, "Simulated AccessDenied"),
        ("s3", "demo-bucket-incomplete", {"BlockPublicAcls": True}, None),
        ("security_group", "demo-group-restricted", [{"protocol": "tcp", "from_port": 22, "to_port": 22, "cidrs": ["192.0.2.0/24"]}], None),
        ("security_group", "demo-group-worldwide", [{"protocol": "tcp", "from_port": 3389, "to_port": 3389, "cidrs": ["::/0"]}], None),
        ("security_group", "demo-group-unavailable", None, "Simulated API timeout"),
        ("security_group", "demo-group-incomplete", None, None),
    ]
    return [
        {"kind": kind, "name": name, "observation": {"observed_at": OBSERVED_AT, "data": data, "error": error}}
        for kind, name, data, error in examples
    ]
