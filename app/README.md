# Cloud Security Posture Explorer application

Read-only AWS collection with boto3, Python 3.10+, standard-library `wsgiref`,
server-rendered HTML, and `unittest`. No JavaScript framework or remediation.
The application remains a portfolio workload, not a production security product.

## Local development: install, configure, and run

These source/venv commands are for a development checkout, not the current EC2
container host. From the repository root on Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r app/requirements-lock.txt
export AWS_REGION=us-east-1
export ALLOWED_BUCKETS=your-personal-lab-bucket
export ALLOWED_SECURITY_GROUPS=sg-0123456789abcdef0
export ALLOWED_INSTANCE_NAME_TAGS=cloud-security-posture-explorer
.venv/bin/python -m app.server --port 8000
```

Replace example resource identifiers. On Windows use `.venv\Scripts\python.exe`
and PowerShell environment assignments such as `$env:AWS_REGION='us-east-1'`.
Use the EC2 instance role for live collection. No static keys are needed or
accepted as application arguments. The SDK uses its default credential chain;
Docker bootstrap forwards only supported configuration variables and disables
shared credential/config files. Keep credentials out of the image. Local execution
still uses the default SDK chain; it does not enforce EC2-only identity for posture reads.

Open http://127.0.0.1:8000/ locally, or through authenticated SSH forwarding.
Stop with Ctrl+C. See [EC2 deployment](../docs/DEPLOYMENT_EC2.md) for Docker/ECR
bootstrap and the GitHub Actions to SSM deployment flow.

| Setting | Meaning |
| --- | --- |
| `SSM_REGION` | Optional SSM bootstrap region override |
| `AWS_REGION` | SSM bootstrap and fallback posture region; `AWS_DEFAULT_REGION` is a fallback only when `AWS_REGION` is absent |
| `ALLOWED_BUCKETS` | Comma-separated bucket names; no wildcards, ARNs, or empty entries |
| `ALLOWED_SECURITY_GROUPS` | Comma-separated security group IDs; no discovery or filters from the browser |
| `ALLOWED_INSTANCE_NAME_TAGS` | Optional comma-separated literal EC2 Name tag values for attached EBS volume encryption checks; empty disables this check |
| `HOST` | Bind address, default `127.0.0.1` when unset; use `0.0.0.0` inside a container |
| `--port` | HTTP port, default 8000 |

Allowlist entries are trimmed and deduplicated, with a maximum of 100 per list.
Any list may be empty to skip those targets. All three empty, an absent region, or
invalid configuration in both sources makes `/` return 503 with UNKNOWN and
performs no posture reads. Parameter Store is primary at
`/cloud-security-posture-explorer/lab/config`; its JSON has `region`,
`allowed_buckets`, `allowed_security_groups`, and `allowed_instance_name_tags`. The complete
environment is the fallback on SSM/JSON/validation failure, without merging fields.
See [configuration and rollout](../docs/PARAMETER_STORE_MIGRATION.md) and the
[JSON example](deploy/parameter-store-config.example.json). Recreate the container
through bootstrap after editing the host fallback file; `docker restart` alone
does not reload it. Remote changes apply on the next scan. SDK timeouts are fixed at 3 seconds connect,
5 seconds read, and two total attempts in standard retry mode. Credential metadata
timeouts are separate (see deployment example). These are not a whole-scan deadline.

Name values use a deliberately restricted syntax: 1-256 characters, starting
with an ASCII letter/digit, followed by ASCII letters/digits, spaces, or
`._/@+=-`. Leading/trailing whitespace is trimmed. Wildcards (`*`, `?`), commas,
control characters, backslashes, and ARNs are rejected. Keep each Name unique
among non-terminated instances in the configured region/account. Names are
application scope selectors, not an IAM authorization boundary.

`allowed_instances` and nonempty `ALLOWED_INSTANCES` are no longer accepted.
Migrate both primary and fallback configuration with the code release; see the
[deployment migration steps](../docs/DEPLOYMENT_EC2.md#name-tag-migration).

## Server binding

Direct Python execution retains `127.0.0.1:8000` when HOST is unset. EC2 bootstrap
explicitly sets HOST and publishes port 8000 on all host interfaces.
For a container, set `HOST=0.0.0.0` in its runtime environment. For example,
with an existing image:

```sh
docker run --rm -e HOST=0.0.0.0 -p 127.0.0.1:8000:8000 your-app-image
```

The container listens on all IPv4 interfaces; this example publishes the port
only on the Docker host's loopback interface. Retain authenticated access for
live inventory. Startup output reports the actual bound address and port.
HOST is a process-start setting, not a Parameter Store field; restart/recreate
the process to change it. Port selection still uses `--port`. AWS credentials,
metadata access, and logging requirements are unchanged by this binding change.

## Endpoints and collection lifecycle

| Method / path | Behavior |
| --- | --- |
| `GET /` | One live collection of configured resources and rendered results |
| `GET /healthz` | HTTP 200, `{"liveness": "ok"}`; no configuration lookup or AWS collection |
| `HEAD /`, `HEAD /healthz` | Same route processing, headers only; HEAD `/` also collects |
| Other paths | 404 |
| Other methods | 405; no write routes |

Resources are read sequentially, one explicit bucket or security group per API
request. Each allowlisted Name tag adds a DescribeInstances request and, when its
volume mappings are valid, a DescribeVolumes request for only those volume IDs.
One failed resource does not suppress the others. The scan has a UUID,
start/end timestamps, and per-resource observation/failed-attempt times in UTC.
Timestamps are collection times, not configuration modification times. The UI is
a snapshot: reload to collect again. There is no background polling, historical
store, cached PASS fallback, or claim that an old browser tab remains fresh.

`ThreadingMixIn` keeps the wsgiref listener responsive during SDK waits. Only one
posture collection may run at a time; concurrent dashboard requests receive 503
and `Retry-After: 5`. Health requests do not acquire that lock. Missing SDK packages
or a dead process can prevent startup; liveness independence concerns AWS/config
failures in an otherwise running installation.

Per-resource failures render UNKNOWN with HTTP 200 so partial evidence remains
visible. A configuration or unexpected whole-page failure returns 503. Health
200 proves neither scan completeness nor a security PASS. COMPLETE means every
configured observation is evaluable; it may include REVIEW. Any UNKNOWN (or no
observations) makes evidence INCOMPLETE.

## Check contract and conservative UNKNOWN behavior

- **EBS Volume Encryption:** one result per `ALLOWED_INSTANCE_NAME_TAGS` entry. Read the
  unique instance using `describe_instances` with an exact `tag:Name` filter and
  `instance-state-name` values pending/running/stopping/stopped. Zero matches or
  multiple matches are UNKNOWN; no instance is arbitrarily selected. Returned
  ID, state, and case-sensitive Name tag must be complete and valid. Extract EBS
  mappings from that resolved instance, and call `describe_volumes(VolumeIds=[...])` for those IDs only. Every
  volume must be returned exactly once with a strict boolean `Encrypted` and a
  confirmed `attached` association to that instance. All true is PASS; any false
  with otherwise complete evidence is REVIEW. Missing/malformed fields, permission
  errors, API failures, partial responses, duplicate/unexpected IDs, and changing
  attachment states are UNKNOWN. UNKNOWN overrides even a known unencrypted volume.
  An empty mapping list is explicitly UNKNOWN (no attached EBS evidence), never a
  vacuous PASS; no unscoped volume query is issued. Instance-store disks, unattached
  volumes, snapshots, KMS key policies, and encryption-by-default settings are not
  assessed. These sequential reads are a point-in-time observation, not an atomic
  snapshot; detected attachment inconsistencies require a fresh collection.
  DescribeInstances requests one bounded page (`MaxResults=5`). Any continuation
  token is UNKNOWN: this intentionally refuses to resolve identity from a partial
  result. DescribeVolumes requests explicit IDs without a pagination limit; any
  unexpected continuation token is likewise UNKNOWN. Each scan resolves the Name
  again; replacing an instance with the same Name requires no EC2 config change.
  The dashboard resource column shows the configured Name value.
- **S3:** `get_public_access_block(Bucket=...)` reads four strict boolean fields.
  All true is PASS, any false with otherwise complete evidence is REVIEW, and
  API errors, missing flags, or invalid values are UNKNOWN. Even
  `NoSuchPublicAccessBlockConfiguration` is UNKNOWN, not an assumed set of flags.
  This says nothing conclusive about effective public exposure: policies, ACLs,
  account/organization controls, and directory buckets are outside the check.
- **Security groups:** `describe_security_groups(GroupIds=[...])` must return
  exactly the requested group with an explicit `IpPermissions` list. A complete
  empty rule list passes; an empty `SecurityGroups` result is UNKNOWN. An unexpected
  `NextToken` is rejected as incomplete rather than interpreting a partial result.
- TCP 22/3389 reachable by an individual rule from `0.0.0.0/0` or `::/0` is REVIEW.
  Port ranges and all-protocol `-1` rules are included. Numeric protocol `6` is
  recognized as TCP. UDP alone does not trigger this TCP-only check.
- Missing/malformed protocols, port ranges, source lists, or CIDRs produce UNKNOWN.
  All four AWS source arrays (`IpRanges`, `Ipv6Ranges`, `UserIdGroupPairs`,
  `PrefixListIds`) must be explicitly present, even if empty. This deliberately
  rejects partial evidence. CIDRs must be canonical and match their address family.
  Nonempty group references or prefix lists, and unsupported protocols, produce
  UNKNOWN instead of being silently dropped; no extra AWS lookup permissions are
  requested. ICMP type/code is validated; omission of both is valid for ICMPv6.
- UNKNOWN takes precedence over both PASS and REVIEW within a resource. A known
  risky rule followed by an incomplete rule still results in UNKNOWN.
- This is not a reachability analysis: routing, NACLs, host firewalls, other ports,
  other broad ranges, and combinations of narrower CIDRs are outside the check.

## CloudWatch Logs

Docker bootstrap uses the journald driver for host logging. Optional CloudWatch delivery copies existing JSON
`configuration_load`, `posture_observation`, and sanitized `application_error`
events to `/cloud-security-posture-explorer/app`, stream = hosting instance ID.
`scan_id` is unchanged across configuration, observations, and scan errors.

Set the optional JSON boolean `cloudwatch_logs_enabled` in the existing SSM
document (default true). CloudWatch uses its `region`; changes take effect on the
next scan without a restart. No logging environment variables are needed. EC2
uses IMDSv2 to discover the initial SSM region, so a local env file is optional.
Before the first valid SSM load, configuration errors can be sent in the hosting
region. Later failures retain the process's last successful logging settings.

The background sender uses only refreshable EC2-role credentials and IMDSv2;
no local credential/profile provider is registered for logging. Delivery is best
effort with a bounded queue; failure never changes scan results or liveness.
Raw errors and HTTP access logs are not uploaded. See [setup, permissions,
limitations, and verification](../docs/CLOUDWATCH_LOGS.md).

## Tests (no AWS credentials or external network access required)

```sh
.venv/bin/python -m unittest discover -s app/tests -v
.venv/bin/python -m pip check
```

Windows equivalent:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s app/tests -v
```

Provider tests inject clients, block network connects, and use botocore Stubber
for real SDK operation/parameter validation. Credential lookup is mocked for
unsigned test clients; no test keys are created. Cases include API success,
AccessDenied, credential/timeout/connection failures, malformed/partial data,
empty results, scope isolation, protocol aliases, encrypted/unencrypted EBS volumes,
incomplete volume attachments, mixed good/bad rules, and audit
redaction. A real loopback HTTP test verifies health during a blocked collection. Bootstrap
workflow tests require Bash (Git Bash on Windows); without it they are skipped.
CI uses an Ubuntu runner with Bash and Python 3.12, runs the full suite and shell
syntax check, and gates image publication with `needs: test`.
Synthetic fixtures remain only as test inputs; the running server never imports
or falls back to them. The dependency lock records the tested versions; update it
deliberately alongside test execution rather than silently updating deployments.

## Files and operations

- `config.py`: shared environment and JSON scope validation.
- `config_provider.py`: primary SSM load, environment fallback, configuration audit.
- `ec2_metadata.py`: IMDSv2-only bootstrap region and hosting instance identity.
- `audit.py`: request-local scan correlation and safe application error JSON.
- `cloudwatch_logs.py`: bounded asynchronous logging copy, IMDSv2, EC2 role-only client.
- `aws_support.py`: shared bounded SDK settings and safe audit metadata.
- `aws_provider.py`: boto3 reads, response normalization, observation audit events.
- `checks.py`: pure evidence validation and evaluation.
- `server.py`: HTML, routes, threaded wsgiref listener and scan lock.
- `tests/`: offline unit/SDK-stub tests and loopback HTTP integration test.
- `deploy/`: SSM JSON, fallback environment and posture-only policy examples;
  the systemd unit is a legacy source-deployment reference, not used by bootstrap.
- `../bootstrap.sh`: Docker/ECR EC2 provisioning and liveness validation.
- `../.github/workflows/build-and-push.yml`: test, image publication, then SSM deployment.

Observation logs contain scan ID, zero-based target index (buckets first, then
groups, then instance Name targets, in allowlist order), kind, timestamp, status, safe
reason/diagnostic, and AWS request ID when available. `request_id` identifies the
last attempted API call; `api_requests` preserves operation names and request IDs
for every attempted call, including both EBS collection stages. A failed call
without an AWS response has a null request ID. They omit resource names, response payloads, and
exception messages. Retain the deployed configuration/version privately to map
indexes during an investigation. Logs are operational evidence, not a tamper-proof
audit trail. Optional CloudWatch delivery adds a central copy; journal retention
and CloudTrail remain separate operator responsibilities.

Live inventory appears on the HTML page. The application has no built-in login:
use the loopback binding with authenticated SSH access or an already protected
local reverse proxy. Do not expose an unauthenticated proxy to this service.
See [architecture](../docs/ARCHITECTURE.md) and
[IAM policy review](../docs/IAM_POLICY_REVIEW.md).
