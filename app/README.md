# Cloud Security Posture Explorer application

Read-only AWS collection with boto3, Python 3.10+, standard-library `wsgiref`,
server-rendered HTML, and `unittest`. No JavaScript framework or remediation.
The application remains a portfolio workload, not a production security product.

## Install, configure, and run

From the repository root, using Linux/EC2:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r app/requirements-lock.txt
export AWS_REGION=us-east-1
export ALLOWED_BUCKETS=your-personal-lab-bucket
export ALLOWED_SECURITY_GROUPS=sg-0123456789abcdef0
.venv/bin/python -m app.server --port 8000
```

Replace example resource identifiers. On Windows use `.venv\Scripts\python.exe`
and PowerShell environment assignments such as `$env:AWS_REGION='us-east-1'`.
Use the EC2 instance role for live collection. No static keys are needed or
accepted as application arguments. The SDK uses its default credential chain;
the deployment unit isolates the service from local profiles and static-key
environment variables so that EC2 role credentials are selected.

Open http://127.0.0.1:8000/ locally, or through authenticated SSH forwarding.
Stop with Ctrl+C. See [EC2 deployment](../docs/DEPLOYMENT_EC2.md) for systemd.

| Setting | Meaning |
| --- | --- |
| `AWS_REGION` | Explicit SDK region; `AWS_DEFAULT_REGION` is a fallback only when `AWS_REGION` is absent |
| `ALLOWED_BUCKETS` | Comma-separated bucket names; no wildcards, ARNs, or empty entries |
| `ALLOWED_SECURITY_GROUPS` | Comma-separated security group IDs; no discovery or filters from the browser |
| `--port` | HTTP port, default 8000; host remains fixed at `127.0.0.1` |

Allowlist entries are trimmed and deduplicated, with a maximum of 100 per list.
Either list may be empty to skip that service. Both empty, an absent region, or
invalid configuration makes `/` return 503 with UNKNOWN and performs no AWS reads.
The lists are read from the process environment for each scan; restart systemd
after editing its environment file. SDK timeouts are fixed at 3 seconds connect,
5 seconds read, and two total attempts in standard retry mode. Credential metadata
timeouts are separate (see deployment example). These are not a whole-scan deadline.

## Endpoints and collection lifecycle

| Method / path | Behavior |
| --- | --- |
| `GET /` | One live collection of configured resources and rendered results |
| `GET /healthz` | HTTP 200, `{"liveness": "ok"}`; no configuration lookup or AWS collection |
| `HEAD /`, `HEAD /healthz` | Same route processing, headers only; HEAD `/` also collects |
| Other paths | 404 |
| Other methods | 405; no write routes |

Resources are read sequentially, one explicit bucket or security group per API
request. One failed resource does not suppress the others. The scan has a UUID,
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

## Tests (no AWS credentials or network access required)

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
empty results, scope isolation, protocol aliases, mixed good/bad rules, and audit
redaction. A real loopback HTTP test verifies health during a blocked collection.
Synthetic fixtures remain only as test inputs; the running server never imports
or falls back to them. The dependency lock records the tested versions; update it
deliberately alongside test execution rather than silently updating deployments.

## Files and operations

- `config.py`: validated environment scope.
- `aws_provider.py`: boto3 reads, response normalization, observation audit events.
- `checks.py`: pure evidence validation and evaluation.
- `server.py`: HTML, routes, threaded wsgiref listener and scan lock.
- `tests/`: offline unit/SDK-stub tests and loopback HTTP integration test.
- `deploy/`: illustrative environment, systemd unit, and role permissions policy.

Observation logs contain scan ID, zero-based target index (buckets first, then
groups, in allowlist order), kind, timestamp, status, safe reason/diagnostic, and
AWS request ID when available. They omit resource names, response payloads, and
exception messages. Retain the deployed configuration/version privately to map
indexes during an investigation. Logs are local operational evidence, not a
tamper-proof audit trail; CloudTrail/central logging is not configured here.

Live inventory appears on the HTML page. The application has no built-in login:
use the loopback binding with authenticated SSH access or an already protected
local reverse proxy. Do not expose an unauthenticated proxy to this service.
See [architecture](../docs/ARCHITECTURE.md) and
[IAM policy review](../docs/IAM_POLICY_REVIEW.md).
