# Cloud Security Posture Explorer + Incident Response Lab

A personal AWS learning and portfolio project that combines a small, read-only security posture application with workload monitoring and a controlled incident-response exercise. Infrastructure will be defined with Terraform. This is an educational lab, not a production security product or an AWS Incident Detection and Response onboarding.

## Why this project exists

Build an end-to-end story: define a workload, deploy it as code, explain its customer-facing outcome, monitor it, investigate a controlled disruption, communicate status, restore service, and document improvements.

## Current status

- Completed before this plan: a standalone Terraform S3 bucket exercise, AWS CLI authentication, and initial exploration of variables, outputs, and state.
- Implemented: boto3 read-only checks for allowlisted S3 buckets, security groups, and EBS encryption on allowlisted EC2 instances; PASS/REVIEW/UNKNOWN results, independent liveness, and offline automated tests. See [application instructions](app/README.md).
- Deployment: Ubuntu EC2 bootstrap now pulls a published Docker image from ECR using the instance role, without static AWS keys or a host application build. Live deployment verification remains required; see [EC2 instructions](docs/DEPLOYMENT_EC2.md).
- Implemented: optional CloudWatch JSON log delivery alongside journal, using EC2 role authentication and an instance-ID stream. See [logging deployment](docs/CLOUDWATCH_LOGS.md).
- Not implemented here: built-in authentication, alarms, notifications, runbook validation, and incident exercise. Live inventory must remain behind an authenticated access path.
- Existing lab bucket is **not** the Terraform state backend or an application data bucket. Do not repurpose or delete it without checking its state and contents.

## Current application and remaining lab work

- A read-only web view of selected resources in a personal AWS lab account.
- Implemented checks: S3 bucket-level Block Public Access, inbound TCP 22/3389 rules, and attached EBS encryption for EC2 Name targets. Results must distinguish `PASS`, `REVIEW`, and `UNKNOWN`; an API error or missing visibility is never a `PASS`.
- A documented application health check and a separate infrastructure health signal.
- Planned: an alert route and a measured incident exercise. A draft runbook and exercise template are available; they do not establish tested alerting.
- No real customer data, company information, public write API, or automatic remediation.

## Repository layout

```text
README.md
app/                      # AWS provider, checks, WSGI server, tests, deployment examples
docs/
  PROJECT_PLAN.md
  ARCHITECTURE.md
  PARAMETER_STORE_MIGRATION.md
  CLOUDWATCH_LOGS.md
  DEPLOYMENT_EC2.md
  IAM_POLICY_REVIEW.md
  SECURITY_AND_COST.md
  INCIDENT_RUNBOOK.md
  EXERCISE_RECORD_TEMPLATE.md
```

Dockerfile and bootstrap.sh define image packaging and EC2 container bootstrap.
`.github/workflows/build-and-push.yml` defines tests, ECR publication and SSM
deployment. The Terraform infrastructure and custom SSM deployment document are
not included in this checkout.

## Run and test

Local source development requires Python 3.10+ and boto3; CI and the Docker image
use Python 3.12. These venv commands are for a development checkout, not the EC2
container host. From the repository root on Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r app/requirements-lock.txt
.venv/bin/python -m unittest discover -s app/tests -v
export AWS_REGION=us-east-1
export ALLOWED_BUCKETS=your-personal-lab-bucket
export ALLOWED_SECURITY_GROUPS=sg-0123456789abcdef0
export ALLOWED_INSTANCE_NAME_TAGS=cloud-security-posture-explorer
.venv/bin/python -m app.server --port 8000
```

Replace sample identifiers with explicit personal-lab resources. Any resource
list can be empty, but at least one must be set. `ALLOWED_INSTANCE_NAME_TAGS` enables EBS
encryption checks and requires `ec2:DescribeInstances` and `ec2:DescribeVolumes`
on the existing role. Tests require no AWS access or credentials; live
reads use the boto3 default credential chain and EC2 role. On Windows use
`.venv\Scripts\python.exe` and PowerShell `$env:NAME='value'` assignments.

Open http://127.0.0.1:8000/ locally or through authenticated SSH forwarding. The
server defaults to loopback; set `HOST=0.0.0.0` inside a container to listen on
its network interfaces. `/healthz` returns `{"liveness":"ok"}` independently
of AWS collection. Results contain actual collection/attempt timestamps. Synthetic
fixtures are test-only and never used as a fallback. Stop with Ctrl+C.

Configuration now loads from Parameter Store first, with the existing local
environment as fallback. See the [configuration guide](docs/PARAMETER_STORE_MIGRATION.md)
for the JSON schema and exact `ssm:GetParameter` permission. EC2 automatically
discovers the SSM region through IMDSv2 when no override is configured; the local
environment file is optional. CloudWatch settings also live in SSM and refresh
on the next scan without a restart.

## Delivery pipeline

```text
push feature/docker -> test -> build-and-push -> deploy-check (lab environment)
```

CI runs the offline suite and Bash syntax check before publishing an image tagged
with the commit SHA. The SSM deployment job uses a separate OIDC role, passes that
SHA to `CloudSecurityPostureExplorerDeploy`, and polls its result. Required approval
depends on GitHub environment settings, not merely the `lab` name in YAML.
The workflow currently uses a fixed deployment instance ID. See the
[deployment contract and external prerequisites](docs/DEPLOYMENT_EC2.md#ssm-deployment-job).

Bootstrap defaults to image tag `1.1`; CI does not publish that tag. Select the
published SHA explicitly for a new release or rollback. EBS Name-based targeting
does not automatically update the CD target after EC2 replacement.

## Documentation map

- [Application contract](app/README.md): endpoints, checks, UNKNOWN and local tests.
- [Deployment guide](docs/DEPLOYMENT_EC2.md): CI/CD, bootstrap, image selection and recovery.
- [Configuration](docs/PARAMETER_STORE_MIGRATION.md): SSM schema and fallback.
- [CloudWatch Logs](docs/CLOUDWATCH_LOGS.md): destination, permissions and diagnosis.
- [Architecture](docs/ARCHITECTURE.md) and [IAM boundaries](docs/IAM_POLICY_REVIEW.md).
- [Project status](docs/PROJECT_PLAN.md), [runbook](docs/INCIDENT_RUNBOOK.md),
  [exercise record](docs/EXERCISE_RECORD_TEMPLATE.md), and [guardrails](docs/SECURITY_AND_COST.md).

## Before any deployment

Review the plan and AWS account plan/credit balance, set a cost budget, and retain
authenticated access. This code update provisions no AWS resources. Posture reads remain read-only;
when enabled, CloudWatch logging creates streams and writes log events to an
operator-provisioned group.
Review the [architecture diagram](docs/ARCHITECTURE.md),
[EC2 rollout/rollback guide](docs/DEPLOYMENT_EC2.md), and
[IAM policy review](docs/IAM_POLICY_REVIEW.md) before enabling live collection.

## Portfolio integrity

The repository must label planned, simulated, and implemented components accurately. Do not claim AWS Enterprise Support, official Incident Detection and Response enrollment, a five-minute response guarantee, production readiness, or customer impact.

## References

- [AWS Incident Detection and Response overview](https://docs.aws.amazon.com/IDR/latest/userguide/what-is-idr.html)
- [AWS IDR monitoring and observability](https://docs.aws.amazon.com/IDR/latest/userguide/observe-idr.html)
- [AWS IDR runbook development](https://docs.aws.amazon.com/IDR/latest/userguide/idr-workloads-dev-runbook.html)
