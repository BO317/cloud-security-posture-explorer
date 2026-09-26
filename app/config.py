"""Explicit collection scope. Empty allowlists never mean discovery."""

from dataclasses import dataclass
import os
import re


class ConfigurationError(ValueError):
    """Safe configuration diagnostic, containing no configured values."""


def _allowlist(environ, name, pattern):
    raw = environ.get(name, "").strip()
    if not raw:
        return ()
    values = tuple(dict.fromkeys(part.strip() for part in raw.split(",")))
    if len(values) > 100 or any(not re.fullmatch(pattern, value) for value in values):
        raise ConfigurationError(f"{name} must contain at most 100 explicit, valid identifiers; no wildcards or empty entries.")
    return values


@dataclass(frozen=True)
class Settings:
    region: str
    buckets: tuple[str, ...]
    security_groups: tuple[str, ...]

    @classmethod
    def from_environment(cls, environ=None):
        env = os.environ if environ is None else environ
        region = env.get("AWS_REGION", env.get("AWS_DEFAULT_REGION", "")).strip()
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region):
            raise ConfigurationError("Set AWS_REGION to an explicit AWS region.")
        buckets = _allowlist(env, "ALLOWED_BUCKETS", r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
        groups = _allowlist(env, "ALLOWED_SECURITY_GROUPS", r"sg-(?:[0-9a-f]{8}|[0-9a-f]{17})")
        if not buckets and not groups:
            raise ConfigurationError("Configure ALLOWED_BUCKETS and/or ALLOWED_SECURITY_GROUPS; empty scope is UNKNOWN.")
        return cls(region, buckets, groups)
