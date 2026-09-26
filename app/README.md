# Local Cloud Security Posture Explorer

Implemented: a read-only, synthetic local demonstration of milestone M1.
Not implemented: live AWS collection, deployment, authentication, monitoring,
remediation, or incident exercises. This is not production-ready software.
No AWS credentials, company data, network API calls, or third-party packages
are used. Terraform and AWS resources are outside this implementation.

## Run and test

Requires Python 3.10 or newer. From the repository root:

```sh
python -m app.server
```

Open http://127.0.0.1:8000 and stop with Ctrl+C. An alternate port can be selected
with `python -m app.server --port 8001`. On Windows, `py` may be used instead of
`python` if that is the installed Python launcher. No virtual environment or
package installation is required.

```sh
python -m unittest discover -s app/tests -v
```

The development server binds only to `127.0.0.1`. Do not publish it through a
proxy or tunnel. Only GET and HEAD are supported; there is no live inventory
API or mutation route.

## Structure

```text
app/
  __init__.py
  checks.py          Pure validation and PASS/REVIEW/UNKNOWN evaluation
  sample_data.py     Eight invented observations, including simulated failures
  server.py          WSGI HTML dashboard and independent /healthz endpoint
  tests/            Check-logic and HTTP-handler tests
  README.md
```

## Evidence contract and interpretation

Each fixture has a resource name, check kind, and observation with `observed_at`,
`data`, and `error`. Timestamps must be ISO 8601 with a timezone. Fixture times
are fixed synthetic observation/attempt times, not proof of freshness. The page
also shows the time of local evaluation. A missing timestamp is displayed as
unavailable and makes the result UNKNOWN; it is never invented.

- S3 data contains all four bucket-level Block Public Access flags as booleans.
  All true produces PASS; any false produces REVIEW. Missing/invalid flags
  produce UNKNOWN. Effective public exposure remains UNKNOWN because bucket
  policies, ACLs, and account/organization settings are outside this check.
- Security group data is a complete normalized inbound-rule list. Each rule
  has `protocol` (`tcp`, `udp`, `icmp`, `icmpv6`, or `all`) and a nonempty `cidrs`
  list. TCP/UDP also require integer `from_port` and `to_port`, inclusive,
  between 0 and 65535. Worldwide IPv4 (`0.0.0.0/0`) or IPv6 (`::/0`) rules
  including port 22 or 3389 produce REVIEW. All-protocol worldwide rules also
  produce REVIEW. An explicitly empty complete list passes this narrow check.
  Other broad CIDRs, other ports, effective network paths, prefix lists, and
  source security groups are not evaluated. Unsupported/malformed rule evidence
  produces UNKNOWN. A future adapter must preserve unsupported sources as
  unknown evidence, never silently drop them or default failed retrieval to `[]`.
- Any non-null collection error overrides the data and produces UNKNOWN.
  Missing/invalid evidence takes precedence even if another rule needs REVIEW.
  Error payloads are not rendered. PASS never means comprehensive compliance.

The dashboard's COMPLETE/INCOMPLETE label describes evidence evaluation, not
security approval: REVIEW can coexist with complete evidence. Empty fixtures or
any UNKNOWN make evaluation INCOMPLETE. No successful live scan is claimed.

## Health is separate from posture

`GET /healthz` returns HTTP 200 with:

```json
{"liveness": "ok", "mode": "synthetic", "posture_scan": "not_evaluated"}
```

This route does not load observations or run checks. UNKNOWN results do not
turn liveness red, and HTTP 200 does not establish scan success or freshness.
An unexpected dashboard evaluation failure returns HTTP 503 with an UNKNOWN
message while liveness remains independently available. A future scan-health
signal must track collection completeness, last successful observation, and
an explicitly chosen freshness threshold separately.

## Future workload identity (design only)

After a separate access and cost review, an AWS SDK adapter could receive
temporary credentials through an EC2 instance-profile role or a Lambda execution
role, using the SDK credential provider chain. Do not embed keys or introduce
an IAM user's static credentials. AWS describes this in its
[SDK authentication guide](https://docs.aws.amazon.com/sdkref/latest/guide/access.html).

Limit the role to required read operations for explicitly selected lab resources;
scope resource permissions where supported and bound the account and region.
Preserve denied, failed, partial, and unsupported responses as UNKNOWN. Protect
the dashboard and any inventory API with authenticated, authorized access before
adding live collection. Hosting, IAM policies, credential acquisition, and
authentication are intentionally future work, not features of this local MVP.
