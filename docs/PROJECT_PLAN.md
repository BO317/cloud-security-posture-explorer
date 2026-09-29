# Project Plan and Delivery Status

This personal portfolio lab combines read-only AWS posture checks with deployment,
logging and incident-response practice. It is not a production security product,
a full landing zone, or an official AWS Incident Detection and Response workload.
Status below describes repository implementation, not proof of live AWS operation.

## Current implementation

| Area | Implemented in this repository | Remaining evidence/work |
| --- | --- | --- |
| Posture app | S3 bucket BPA, TCP 22/3389 ingress, attached EBS encryption; conservative UNKNOWN | Verify intended live targets and timestamps |
| Web interface | Server-rendered HTML, threaded wsgiref, independent `/healthz` | Validate protected access; no built-in authentication |
| Configuration | Primary SSM JSON, whole-environment fallback, Name tag targeting | Verify actual parameter and IAM grants |
| EC2 hosting | Ubuntu Docker/ECR bootstrap, role credentials, journal logs | Verify image, IMDSv2 hop limit, host/network permissions |
| Logging | Correlated JSON and optional CloudWatch copies | Verify group, retention, role grants and delivery |
| CI | Python 3.12 tests and Bash syntax gate before SHA-tagged ECR publication | Retain successful workflow evidence |
| CD | SSM deployment job using `lab` environment and a separate OIDC role | Verify required reviewers, custom SSM document and fixed instance target |
| Incident response | Runbook and exercise-record template | Run a controlled exercise and record measured evidence |
| Alarms/notifications | Not implemented in this repository | Design, configure and test before claiming automatic detection |
| Infrastructure | Terraform is outside this checkout | Review infrastructure and state in its owning repository |

The historical synthetic MVP is complete; fixtures are now test-only. The running
app uses AWS reads and never substitutes fixtures for failed collection. EC2 is
the selected host. Lambda remains an optional future comparison.

## Acceptance milestones

### 1. Guardrails and access

Confirm the current account budget, region, dedicated lab scope, protected access
path and cleanup plan. The host's Docker group and passwordless sudo intentionally
grant `ssm-user` administrative access; restrict who can open a Session Manager
session. Do not use employer resources, static AWS keys or public inventory access.

### 2. Reproducible deployment

Require the test job to succeed before publication. Record the image SHA/digest,
workflow run and SSM CommandId. Verify the `lab` environment's actual approval
rules and the external SSM document. After deployment, independently check the
container image, `/healthz`, a fresh dashboard scan and CloudWatch events.
Use [deployment instructions](DEPLOYMENT_EC2.md); health alone is insufficient.

### 3. Detection and response

Measure normal behavior before choosing alarm thresholds and notification paths.
Distinguish process liveness, scan completeness, security REVIEW findings and log
delivery. Validate infrastructure signals and the authorized user journey.
A CloudWatch Logs stream by itself is not an alarm or notification system.

### 4. Controlled exercise

Use the [runbook](INCIDENT_RUNBOOK.md) and [record template](EXERCISE_RECORD_TEMPLATE.md).
Record real timestamps, the chosen fault, known-good rollback image, observed
impact, recovery evidence and follow-up actions. Mark manual detection as manual;
do not invent alarm delivery, support-team participation or customer impact.

## Known follow-up work

- Export/version the custom SSM deployment document; review whether deployment
  should pin its version. Its implementation cannot be audited from this checkout.
- Review fixed CD instance-ID targeting separately from Name-based posture scope.
- Add PR checks and required branch rules if merge protection is desired; the
  current workflow triggers only on pushes to `feature/docker`.
- Record live approval/access/rollback evidence before calling the deployment
  path verified. Review ordering of concurrently queued releases.
- Consider additional image/security hardening as a separate change; current
  Dockerfile runs as root and the WSGI server remains a portfolio implementation.

Remediation, multi-account discovery, continuous monitoring, formal compliance,
RDS/Bedrock additions and production-readiness claims remain out of scope.
