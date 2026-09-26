"""Shared bounded SDK configuration and safe audit metadata."""
from datetime import datetime, timezone
import re
from botocore.config import Config

SDK_CONFIG = Config(
    connect_timeout=3, read_timeout=5,
    retries={"mode": "standard", "total_max_attempts": 2},
    ignore_configured_endpoint_urls=True,
)

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_request_id(response):
    if not isinstance(response, dict) or not isinstance(response.get("ResponseMetadata"), dict):
        return None
    value = response["ResponseMetadata"].get("RequestId")
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9+=/_-]{1,200}", value) else None


