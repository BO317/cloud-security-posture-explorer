"""Primary SSM configuration with an independently validated environment fallback."""
from dataclasses import dataclass
import json
import logging
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.aws_support import SDK_CONFIG, safe_request_id, utc_now
from app.config import ConfigurationError, Settings, validate_region

PARAMETER_NAME = "/cloud-security-posture-explorer/lab/config"
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedConfiguration:
    settings: Settings
    source: str
    version: int | None = None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError("Duplicate JSON configuration key.")
        result[key] = value
    return result


def _parse_response(response):
    parameter = response.get("Parameter") if isinstance(response, dict) else None
    if (not isinstance(parameter, dict) or parameter.get("Name") != PARAMETER_NAME
            or parameter.get("Type") != "String"
            or type(parameter.get("Version")) is not int or parameter["Version"] < 1
            or not isinstance(parameter.get("Value"), str)):
        raise ConfigurationError("Parameter response missing or malformed.")
    value = parameter["Value"]
    if len(value.encode("utf-8")) > 8192:
        raise ConfigurationError("Configuration exceeds the parameter size limit.")
    try:
        document = json.loads(value, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise ConfigurationError("Invalid JSON configuration.") from exc
    return LoadedConfiguration(Settings.from_document(document), "ssm", parameter["Version"])


def load_configuration(environ=None, session_factory=None, *, scan_id=None):
    """Never merge sources or retain stale configuration across scans.

    systemd loads the local EnvironmentFile; this module reads its process values.
    AWS_REGION (or SSM_REGION) bootstraps SSM independently of the JSON scan region.
    """
    env = dict(os.environ if environ is None else environ)
    client = None
    request_id = None
    reason = None
    try:
        region = validate_region(env.get("SSM_REGION", env.get("AWS_REGION", env.get("AWS_DEFAULT_REGION", ""))))
        session = (session_factory or boto3.Session)()
        client = session.client("ssm", region_name=region, config=SDK_CONFIG)
        response = client.get_parameter(Name=PARAMETER_NAME, WithDecryption=False)
        request_id = safe_request_id(response)
        loaded = _parse_response(response)
    except ClientError as exc:
        request_id = safe_request_id(exc.response)
        code = exc.response.get("Error", {}).get("Code")
        reason = {"ParameterNotFound": "parameter_not_found", "AccessDeniedException": "access_denied",
                  "AccessDenied": "access_denied"}.get(code, "api_error")
    except BotoCoreError:
        reason = "sdk_error"
    except ConfigurationError:
        reason = "invalid_configuration"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                LOGGER.warning("ssm_client_cleanup_failed")
    if reason is not None:
        try:
            loaded = LoadedConfiguration(Settings.from_environment(env), "environment")
        except ConfigurationError as exc:
            LOGGER.error(json.dumps({"event": "configuration_load", "scan_id": scan_id,
                                    "loaded_at": utc_now(), "source": None, "outcome": "failed",
                                    "fallback_reason": reason, "request_id": request_id}))
            raise ConfigurationError("Parameter Store unavailable or invalid and local fallback invalid.") from exc
    event = {"event": "configuration_load", "scan_id": scan_id, "loaded_at": utc_now(),
             "source": loaded.source, "version": loaded.version, "outcome": "loaded",
             "fallback_reason": reason, "request_id": request_id}
    LOGGER.log(logging.WARNING if reason else logging.INFO, json.dumps(event))
    return loaded
