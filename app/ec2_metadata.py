"""Small IMDSv2-only bootstrap reads with fixed endpoints and bounded timeouts."""
import re
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from app.config import ConfigurationError, validate_region


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_metadata(path):
    """IMDSv2 only, fixed link-local endpoint, no proxies or redirects."""
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request("http://169.254.169.254/latest/api/token", method="PUT",
                      headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
    with opener.open(request, timeout=2) as response:
        token = response.read(4097).decode("ascii")
    if not token or len(token) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise ValueError("Invalid metadata token")
    request = Request("http://169.254.169.254/latest/meta-data/" + path,
                      headers={"X-aws-ec2-metadata-token": token})
    with opener.open(request, timeout=2) as response:
        value = response.read(65).decode("ascii").strip()
    return value


def instance_id():
    value = _read_metadata("instance-id")
    if not re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", value):
        raise ValueError("Invalid metadata instance identity")
    return value


def instance_region():
    try:
        return validate_region(_read_metadata("placement/region"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError("EC2 metadata region unavailable or invalid.") from exc


