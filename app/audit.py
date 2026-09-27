"""Request-local correlation and sanitized application error events."""
from contextvars import ContextVar
import json

from app.aws_support import utc_now

SCAN_ID = ContextVar("scan_id", default=None)


def log_application_error(logger, level, code, *, scan_id=None):
    """Call with internal constant codes only; never include raw exceptions."""
    logger.log(level, json.dumps({"event": "application_error", "code": code,
                                 "scan_id": scan_id or SCAN_ID.get(), "timestamp": utc_now()}))
