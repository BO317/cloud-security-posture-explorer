# Cloud Security Posture Explorer + Incident Response Lab

A personal AWS learning and portfolio project that combines a small, read-only security posture application with workload monitoring and a controlled incident-response exercise. Infrastructure will be defined with Terraform. This is an educational lab, not a production security product or an AWS Incident Detection and Response onboarding.

## Why this project exists

Build an end-to-end story: define a workload, deploy it as code, explain its customer-facing outcome, monitor it, investigate a controlled disruption, communicate status, restore service, and document improvements.

## Current status

- Completed before this plan: a standalone Terraform S3 bucket exercise, AWS CLI authentication, and initial exploration of variables, outputs, and state.
- Implemented: boto3 read-only collection of allowlisted S3 buckets and security groups, PASS/REVIEW/UNKNOWN results, independent liveness, and offline automated tests. See [application instructions](app/README.md).
- Deployment baseline: the operator reports EC2 hosting with systemd and an instance role, without static AWS keys. This provider update still requires deployment and live verification; see [EC2 instructions](docs/DEPLOYMENT_EC2.md).
- Not implemented here: built-in authentication, alarms, notifications, runbook validation, and incident exercise. Live inventory must remain behind an authenticated access path.
- Existing lab bucket is **not** the Terraform state backend or an application data bucket. Do not repurpose or delete it without checking its state and contents.

## Proposed MVP

- A read-only web view of selected resources in a personal AWS lab account.
- Initial checks: S3 bucket-level Block Public Access configuration and security group inbound rules. Results must distinguish `PASS`, `REVIEW`, and `UNKNOWN`; an API error or missing visibility is never a `PASS`.
- A documented application health check and a separate infrastructure health signal.
- An alert route, a human-run response plan, and one controlled, reversible failure exercise.
- No real customer data, company information, public write API, or automatic remediation.

## Repository layout

```text
README.md
app/                      # AWS provider, checks, WSGI server, tests, deployment examples
docs/
  PROJECT_PLAN.md
  ARCHITECTURE.md
  DEPLOYMENT_EC2.md
  IAM_POLICY_REVIEW.md
  SECURITY_AND_COST.md
  INCIDENT_RUNBOOK.md
  EXERCISE_RECORD_TEMPLATE.md
```

The earlier Terraform exercise is separate and is not included in this checkout.

## Run and test

Requires Python 3.10 or newer and boto3. From the repository root on Linux/EC2:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r app/requirements-lock.txt
.venv/bin/python -m unittest discover -s app/tests -v
export AWS_REGION=us-east-1
export ALLOWED_BUCKETS=your-personal-lab-bucket
export ALLOWED_SECURITY_GROUPS=sg-0123456789abcdef0
.venv/bin/python -m app.server --port 8000
```

Replace sample identifiers with explicit personal-lab resources. Either resource
list can be empty, but not both. Tests require no AWS access or credentials; live
reads use the boto3 default credential chain and EC2 role. On Windows use
`.venv\Scripts\python.exe` and PowerShell `$env:NAME='value'` assignments.

Open http://127.0.0.1:8000/ locally or through authenticated SSH forwarding. The
server binds to loopback only. `/healthz` returns `{"liveness":"ok"}` independently
of AWS collection. Results contain actual collection/attempt timestamps. Synthetic
fixtures are test-only and never used as a fallback. Stop with Ctrl+C.

## Before any deployment

Review the plan and AWS account plan/credit balance, set a cost budget, and retain
authenticated access. This update creates no AWS resources and performs no writes.
Review the [architecture diagram](docs/ARCHITECTURE.md),
[EC2 rollout/rollback guide](docs/DEPLOYMENT_EC2.md), and
[IAM policy review](docs/IAM_POLICY_REVIEW.md) before enabling live collection.

## Portfolio integrity

The repository must label planned, simulated, and implemented components accurately. Do not claim AWS Enterprise Support, official Incident Detection and Response enrollment, a five-minute response guarantee, production readiness, or customer impact.

## References

- [AWS Incident Detection and Response overview](https://docs.aws.amazon.com/IDR/latest/userguide/what-is-idr.html)
- [AWS IDR monitoring and observability](https://docs.aws.amazon.com/IDR/latest/userguide/observe-idr.html)
- [AWS IDR runbook development](https://docs.aws.amazon.com/IDR/latest/userguide/idr-workloads-dev-runbook.html)
