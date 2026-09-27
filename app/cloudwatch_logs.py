"""Best-effort CloudWatch copy of structured application logs; journal stays primary."""
import json
import logging
from queue import Empty, Full, Queue
import re
from threading import Event, Thread
import time

import boto3
import botocore.session
from botocore.credentials import CredentialResolver, InstanceMetadataProvider
from botocore.exceptions import ClientError, NoCredentialsError
from botocore.utils import InstanceMetadataFetcher

from app.aws_support import SDK_CONFIG, utc_now
from app.ec2_metadata import instance_id, instance_region

LOG_GROUP = "/cloud-security-posture-explorer/app"
EVENTS = {"configuration_load", "posture_observation", "application_error"}
MAX_MESSAGE_BYTES = 8192
LOCAL_LOGGER = logging.getLogger("cloudwatch_delivery")  # Outside app: never re-enqueue.


def local_failure(code, *, scan_id=None):
    LOCAL_LOGGER.warning(json.dumps({"event": "cloudwatch_delivery_error", "code": code,
                                     "scan_id": scan_id, "timestamp": utc_now()}))


def role_client(region):
    """Use only refreshable EC2 instance-role credentials, never local profiles/keys."""
    session = botocore.session.get_session()
    provider = InstanceMetadataProvider(iam_role_fetcher=InstanceMetadataFetcher(
        timeout=2, num_attempts=1, base_url="http://169.254.169.254/",
        config={"ec2_metadata_v1_disabled": True}))
    session.register_component("credential_provider", CredentialResolver([provider]))
    sdk = boto3.Session(botocore_session=session)
    if sdk.get_credentials() is None:
        raise NoCredentialsError()
    return sdk.client("logs", region_name=region, config=SDK_CONFIG)


class CloudWatchHandler(logging.Handler):
    """Nonblocking enqueue; a single daemon owns metadata and all AWS calls.

    Each request contains one event, preserving the original JSON message exactly.
    Failed events are not replayed: journal is the durable local copy. SDK retries
    can duplicate an event if AWS accepted it before a response was lost.
    """

    def __init__(self, region=None, *, client_factory=role_client, identity_factory=instance_id,
                 capacity=256, cooldown=30, start=True):
        super().__init__(logging.INFO)
        self.region = region
        self.enabled = True
        self.client_region = None
        self.bootstrap_region = None
        self.client_factory = client_factory
        self.identity_factory = identity_factory
        self.queue = Queue(maxsize=capacity)
        self.stop = Event()
        self.client = None
        self.stream = None
        self.stream_ready = False
        self.cooldown = cooldown
        self.retry_after = 0
        self.thread = Thread(target=self._run, name="cloudwatch-logs", daemon=True)
        if start:
            self.thread.start()

    def emit(self, record):
        if self.stop.is_set() or not self.enabled:
            return
        try:
            message = record.getMessage()
            document = json.loads(message)
            if not isinstance(document, dict) or document.get("event") not in EVENTS:
                return
            scan_id = document.get("scan_id")
            if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
                local_failure("event_too_large", scan_id=scan_id)
                return
            self.queue.put_nowait(({"timestamp": int(record.created * 1000), "message": message}, scan_id, self.region))
        except Full:
            local_failure("queue_full", scan_id=scan_id)
        except (ValueError, TypeError, RecursionError):
            # Ignore non-JSON records; do not upload arbitrary text or stack traces.
            return

    def _send(self, event, scan_id, region=None):
        if time.monotonic() < self.retry_after:
            return  # The initial failure warning announces this cooldown loss window.
        try:
            if region is None:
                if self.bootstrap_region is None:
                    self.bootstrap_region = instance_region()
                region = self.bootstrap_region
            if self.client is not None and self.client_region != region:
                self.client.close()
                self.client = None
                self.stream_ready = False
            if self.stream is None:
                self.stream = self.identity_factory()
                if not isinstance(self.stream, str) or not re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", self.stream):
                    self.stream = None
                    raise ValueError("Invalid instance identity")
            if self.client is None:
                self.client = self.client_factory(region)
                self.client_region = region
            if not self.stream_ready:
                try:
                    self.client.create_log_stream(logGroupName=LOG_GROUP, logStreamName=self.stream)
                except self.client.exceptions.ResourceAlreadyExistsException:
                    pass
                self.stream_ready = True
            response = self.client.put_log_events(logGroupName=LOG_GROUP, logStreamName=self.stream,
                                                 logEvents=[event])
            if not isinstance(response, dict):
                raise ValueError("Malformed logging response")
            if response.get("rejectedLogEventsInfo"):
                local_failure("event_rejected", scan_id=scan_id)
        except Exception as exc:
            # Logging is an isolation boundary: no SDK/metadata/programming error
            # may crash the worker or propagate into application requests.
            code = "delivery_failed"
            if isinstance(exc, ClientError):
                aws_code = exc.response.get("Error", {}).get("Code")
                code = {"AccessDeniedException": "access_denied", "AccessDenied": "access_denied",
                        "ResourceNotFoundException": "resource_not_found",
                        "ThrottlingException": "throttled"}.get(aws_code, "api_error")
            self.stream_ready = False
            self.retry_after = time.monotonic() + self.cooldown
            local_failure(code, scan_id=scan_id)

    def _run(self):
        try:
            while not self.stop.is_set() or not self.queue.empty():
                try:
                    event, scan_id, region = self.queue.get(timeout=0.1)
                except Empty:
                    continue
                try:
                    self._send(event, scan_id, region)
                finally:
                    self.queue.task_done()
        finally:
            if self.client is not None:
                try:
                    self.client.close()
                except Exception:
                    local_failure("client_cleanup_failed")

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                local_failure("shutdown_flush_incomplete")
        super().close()


def apply_cloudwatch_settings(settings):
    """Update an installed handler only; never start threads or perform network I/O."""
    for handler in logging.getLogger("app").handlers:
        if isinstance(handler, CloudWatchHandler):
            handler.acquire()
            try:
                handler.enabled = settings.cloudwatch_logs_enabled
                handler.region = settings.region
            finally:
                handler.release()


def configure_cloudwatch():
    """Install once at startup. SSM controls delivery on each successful scan load.

    Before the first valid SSM load, configuration/startup failures can still be
    sent to the fixed group in the IMDS-discovered region. No local config needed.
    """
    logger = logging.getLogger("app")
    for handler in logger.handlers:
        if isinstance(handler, CloudWatchHandler) and not handler.stop.is_set():
            return handler
    handler = CloudWatchHandler()
    logger.addHandler(handler)  # Root stderr handler remains in place for journal.
    return handler
