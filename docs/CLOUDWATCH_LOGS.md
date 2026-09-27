# CloudWatch Logs deployment

Implemented and tested offline. No AWS resources or IAM policies were changed by
this code update. Docker bootstrap preserves the host journal using the journald
logging driver. See [current deployment instructions](DEPLOYMENT_EC2.md); systemd
service commands below describe the legacy source deployment. No CloudWatch Agent or
new Python dependency is required; the application uses its existing boto3 SDK.

## Destination and events

- Log group: `/cloud-security-posture-explorer/app` (fixed).
- Log stream: the **hosting EC2 instance ID**, obtained using IMDSv2; not the Name
  target being scanned. Instance replacement automatically selects a new stream.
- Forwarded events: `configuration_load`, `posture_observation`, and
  `application_error`. Each CloudWatch message is the original JSON string,
  without a text prefix, extra envelope, or reserialization.
- Existing observation/configuration fields and `scan_id` are preserved. Scan
  correlation begins before configuration loading, including configuration errors.
  Application errors have a constant safe `code`, UTC `timestamp`, and `scan_id`;
  startup errors outside a scan have null `scan_id`. No raw exception or traceback
  is uploaded. HTTP access lines and startup console text remain journal-only.

The root stderr handler is retained for systemd journal. CloudWatch attaches a
second handler only to application loggers. Its own delivery diagnostics use a
separate journal-only logger, preventing recursive uploads. The service name can
be `cloud-security-posture-explorer` (bootstrap deployment) or the earlier
`cloud-security-posture` example: use your actual installed service name.

## Configuration

Runtime settings live in the existing SSM String parameter
`/cloud-security-posture-explorer/lab/config`. Keep the resource lists and add:

```json
"cloudwatch_logs_enabled": true
```

This optional boolean defaults to true for existing four-field documents. Use
false to disable cloud copies. The document's `region` selects both posture and
CloudWatch APIs. The group name remains fixed and the stream ID is automatic;
there are no separate local logging variables. Old `CLOUDWATCH_LOGS_ENABLED` and
`CLOUDWATCH_LOGS_REGION` environment variables are ignored.

Each successful SSM load applies these settings before emitting that scan's
configuration event. Changes take effect on the next dashboard scan, without a
service restart. Previously queued events retain their original region and can
finish sending after logging is disabled. The journal always retains the events.

On EC2 with no environment file, IMDSv2 `placement/region` provides the initial
SSM region. Put the parameter in that region. The optional legacy `SSM_REGION`,
`AWS_REGION`, or `AWS_DEFAULT_REGION` override is still supported for local
workflows or a parameter in a different region. The supplied systemd unit makes
the environment file optional; no per-instance app settings need to be written.

If SSM fails or returns invalid JSON, retain the last successful logging switch
and region for this process only. This is not a cached posture scope: normal
UNKNOWN/fallback behavior remains. Before any successful SSM load, error logs
are attempted in the hosting instance's region with logging enabled. This lets
initial configuration failures be reported without needing remote config first.
A failed disable edit cannot be applied until SSM can be read successfully; the
journal remains available even when cloud logging/metadata fails. Non-EC2 local
runs without metadata retain console logging and may report delivery warnings.

CloudWatch authentication has an explicit **EC2 role-only** credential resolver.
It uses botocore's refreshable instance metadata provider; environment credentials,
profiles, SSO, web identity, and container credential providers are not registered
for this client. No AWS access keys are accepted as logging configuration.
Retain the systemd unit's credential isolation for the other application clients.
The new resolver uses botocore interfaces tested with `requirements-lock.txt`;
rerun the suite before changing the dependency lock.

## AWS prerequisites and permissions

1. As the deployment operator, create the log group in the logging region and
   configure the desired retention. For example, a lab may choose seven days;
   retention management stays with the operator. Log ingestion/storage incur
   charges; review the account budget. Keep the group across instance replacements
   if centralized logs must survive those replacements.
2. Grant the existing EC2 application role exactly these additional actions:

   | Action | Resource scope |
   | --- | --- |
   | `logs:CreateLogStream` | `arn:aws:logs:<REGION>:<ACCOUNT_ID>:log-group:/cloud-security-posture-explorer/app:log-stream:i-*` |
   | `logs:PutLogEvents` | Same stream ARN pattern |

   Substitute account, region, and partition. The `i-*` pattern supports instance
   replacement; it permits the role to write any matching stream in this group,
   not only its own instance stream. The application itself selects its IMDS ID.
   No `CreateLogGroup`, `DescribeLogStreams`, retention/delete, or read permission
   is required on the application role. Review existing broader attached policies
   separately. Existing posture/SSM permissions remain required.
3. Enable IMDSv2 access on the instance. Identity retrieval uses fixed link-local
   URLs, a two-second timeout, no proxy/redirect, and no IMDSv1 fallback. Role
   credentials also use IMDSv2 with bounded metadata attempts. Keep
   `AWS_EC2_METADATA_DISABLED=false` in the service environment.
4. Allow outbound HTTPS/DNS to the regional CloudWatch Logs endpoint using existing
   routing or an appropriate endpoint. Keep metadata access local. Do not expose
   the dashboard publicly or add inbound access for logging.

These two narrowly scoped telemetry writes are the only new AWS mutations in
the application's behavior. Security posture collection remains read-only; no
resource remediation, tagging, policy modification, or AWS provisioning is added.

## Deploy and verify

Deploy the reviewed application code and run:

```sh
cd /opt/cloud-security-posture-explorer
.venv/bin/python -m unittest discover -s app/tests -v
sudo systemctl restart cloud-security-posture-explorer
curl --fail http://127.0.0.1:8000/healthz
sudo journalctl -u cloud-security-posture-explorer -n 100 --no-pager
```

The one-time restart above loads the new code. Subsequent SSM edits do not need
a restart. After setting up AWS prerequisites and enabling logging in SSM, open the dashboard
through the existing authenticated access path. In the CloudWatch console select
the logging region, the fixed log group, and the hosting instance's ID stream.
Verify `configuration_load` and `posture_observation` have the same `scan_id` as
the dashboard and journal. Configuration/server failures generate
`application_error`; verify failure paths offline rather than breaking a live
deployment. Health 200 does not prove logging delivery or scan success.

Use the updated unit (or cloud-init bootstrap) with
`EnvironmentFile=-/etc/cloud-security-posture.env`: the minus sign makes the local
fallback optional. Existing deployments that retain the file can keep running
without changing it. To disable the integration, set `cloudwatch_logs_enabled`
to false in SSM and request a fresh scan. No SSH or local env edit is needed.

## Delivery limits and diagnosis

The logging path only enqueues: logging metadata/role lookup, stream creation,
and PutLogEvents run on one daemon worker. SSM bootstrap metadata/config loading
occurs inside the existing scan boundary; health bypasses both. The queue holds at most 256 events;
messages over 8 KiB and new messages arriving at a full queue are dropped from
the CloudWatch copy with a local warning. Original journal logging is unchanged.
Each request sends one event, avoiding cross-event timestamp ordering constraints.
This is suitable for a small manual lab, not a high-volume logging pipeline.

SDK connect/read timeouts are 3/5 seconds with two total attempts. After delivery
failure, the worker drops the failed event, logs a sanitized warning, and skips
CloudWatch copies during a 30-second cooldown. A new event after cooldown retries
initialization/delivery. There is no disk spool or replay from journal. SDK retries
can produce duplicates if the service accepted an event before a response was
lost. SIGTERM/Ctrl+C shutdown waits at most two seconds for the worker; a warning
reports incomplete flushing. Abrupt termination can lose queued copies. Configure
journald persistence/retention separately if local logs must survive reboot.

Inspect journal-only `cloudwatch_delivery_error` codes:

- `access_denied`: review role permissions, region, group ARN, and explicit denies.
- `resource_not_found`: ensure the group exists in the logging region.
- `throttled`, `api_error`, `delivery_failed`: check API/network/IMDS/role availability;
  raw exceptions and credentials are deliberately omitted.
- `event_rejected`: AWS returned rejected event metadata; check clock sync and retention.
- `queue_full`, `event_too_large`, `shutdown_flush_incomplete`: the cloud copy is
  incomplete; consult journal and reduce load or use a durable collector for a
  future higher-volume design.

CloudWatch failure never changes PASS/REVIEW/UNKNOWN or the `/healthz` response.
The integration is best effort, not an exactly-once or tamper-proof audit service.

## References

- [PutLogEvents: batching, rejections, and ignored sequence tokens](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_PutLogEvents.html)
- [IAM actions and stream resource ARNs](https://docs.aws.amazon.com/service-authorization/latest/reference/list_logs.html)
- [CreateLogStream](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_CreateLogStream.html)
- [IMDSv2 token flow](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html)
