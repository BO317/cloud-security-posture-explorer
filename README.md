# Cloud Security Posture Explorer + Incident Response Lab

A personal AWS learning and portfolio project that combines a small, read-only security posture application with workload monitoring and a controlled incident-response exercise. Infrastructure will be defined with Terraform. This is an educational lab, not a production security product or an AWS Incident Detection and Response onboarding.

## Why this project exists

Build an end-to-end story: define a workload, deploy it as code, explain its customer-facing outcome, monitor it, investigate a controlled disruption, communicate status, restore service, and document improvements.

## Current status

- Completed before this plan: a standalone Terraform S3 bucket exercise, AWS CLI authentication, and initial exploration of variables, outputs, and state.
- Implemented: a local, read-only posture application using eight synthetic observations, PASS/REVIEW/UNKNOWN results, an independent liveness endpoint, and 12 automated tests. See [local app instructions](app/README.md).
- Not yet built: live AWS collection, workload hosting, authentication, alarms, notifications, runbook validation, and incident exercise.
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
app/                      # Local synthetic application, tests, and run instructions
docs/
  PROJECT_PLAN.md
  ARCHITECTURE.md
  SECURITY_AND_COST.md
  INCIDENT_RUNBOOK.md
  EXERCISE_RECORD_TEMPLATE.md
```

The earlier Terraform exercise is separate and is not included in this checkout.

## Run the local demo

Requires Python 3.10 or newer; no third-party packages or AWS credentials.
From the repository root:

```sh
python -m app.server --port 8000
python -m unittest discover -s app/tests -v
```

Open http://127.0.0.1:8000/. The server binds to loopback only. `/healthz`
reports application liveness, not scan success. All displayed resources and
observation timestamps are synthetic. Stop the server with Ctrl+C.

## Before any deployment

Review the plan and AWS account plan/credit balance, set a cost budget, and design authenticated access before deploying a new AWS resource. The local sample-data prototype is implemented; deployment remains future work. See `docs/PROJECT_PLAN.md` for acceptance criteria and sequence.

## Portfolio integrity

The repository must label planned, simulated, and implemented components accurately. Do not claim AWS Enterprise Support, official Incident Detection and Response enrollment, a five-minute response guarantee, production readiness, or customer impact.

## References

- [AWS Incident Detection and Response overview](https://docs.aws.amazon.com/IDR/latest/userguide/what-is-idr.html)
- [AWS IDR monitoring and observability](https://docs.aws.amazon.com/IDR/latest/userguide/observe-idr.html)
- [AWS IDR runbook development](https://docs.aws.amazon.com/IDR/latest/userguide/idr-workloads-dev-runbook.html)
