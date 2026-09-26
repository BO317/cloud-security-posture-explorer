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
    return _identifiers(raw.split(","), name, pattern)


def _identifiers(items, name, pattern):
    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
        raise ConfigurationError(f"{name} must be an array of identifiers.")
    values = tuple(dict.fromkeys(part.strip() for part in items))
    if len(values) > 100 or any(not re.fullmatch(pattern, value) for value in values):
        raise ConfigurationError(f"{name} must contain at most 100 explicit, valid identifiers; no wildcards or empty entries.")
    return values


def validate_region(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", value.strip()):
        raise ConfigurationError("Set AWS_REGION to an explicit AWS region.")
    return value.strip()


@dataclass(frozen=True)
class Settings:
    region: str
    buckets: tuple[str, ...]
    security_groups: tuple[str, ...]
    instances: tuple[str, ...] = ()

    @classmethod
    def from_document(cls, document):
        fields = {"region", "allowed_buckets", "allowed_security_groups", "allowed_instances"}
        if not isinstance(document, dict) or set(document) != fields:
            raise ConfigurationError("Configuration JSON must contain exactly the four supported fields.")
        region = validate_region(document["region"])
        buckets = _identifiers(document["allowed_buckets"], "allowed_buckets", r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
        groups = _identifiers(document["allowed_security_groups"], "allowed_security_groups", r"sg-(?:[0-9a-f]{8}|[0-9a-f]{17})")
        instances = _identifiers(document["allowed_instances"], "allowed_instances", r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})")
        if not buckets and not groups and not instances:
            raise ConfigurationError("Empty scope is UNKNOWN; configure at least one explicit resource.")
        return cls(region, buckets, groups, instances)

    @classmethod
    def from_environment(cls, environ=None):
        env = os.environ if environ is None else environ
        region = validate_region(env.get("AWS_REGION", env.get("AWS_DEFAULT_REGION", "")))
        buckets = _allowlist(env, "ALLOWED_BUCKETS", r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
        groups = _allowlist(env, "ALLOWED_SECURITY_GROUPS", r"sg-(?:[0-9a-f]{8}|[0-9a-f]{17})")
        instances = _allowlist(env, "ALLOWED_INSTANCES", r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})")
        if not buckets and not groups and not instances:
            raise ConfigurationError("Configure ALLOWED_BUCKETS, ALLOWED_SECURITY_GROUPS, or ALLOWED_INSTANCES; empty scope is UNKNOWN.")
        return cls(region, buckets, groups, instances)
