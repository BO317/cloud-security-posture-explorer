# EC2 Docker/ECR deployment

`bootstrap.sh` is Ubuntu 24.04 cloud-init shell user data. The EC2 host installs
Docker and pulls an existing image; it does not clone the repository, build the
application, create a virtual environment, or install application dependencies.
The Python/WSGI application, SSM configuration, checks and JSON audit events are
unchanged. This deployment is tested offline, not live-validated on EC2.

## Prerequisites

- Use an Ubuntu 24.04 AMI with the Ubuntu main/universe package repositories
  enabled, systemd, and an operational SSM Agent for Session Manager access.
- Publish the image to
  `980468996827.dkr.ecr.us-east-1.amazonaws.com/cloud-security-posture-explorer`.
  It must match the host architecture and include support for `HOST=0.0.0.0`.
- Attach the existing EC2 instance role. No access keys or AWS credential files
  are required. Retain SSM configuration, posture read and CloudWatch permissions
  described in [IAM review](IAM_POLICY_REVIEW.md). Add ECR pull permissions below.
- Enable IMDS, require IMDSv2, and set the metadata response hop limit to **2** in
  the instance or launch template so bridge-networked containers can retrieve
  instance-role credentials. Bootstrap does not change AWS metadata options.
  See [AWS metadata options](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-options.html).
- Provide network access to Ubuntu repositories, `awscli.amazonaws.com` for initial
  AWS CLI installation, ECR API/registry and its S3 image
  layers, SSM, CloudWatch Logs, and the AWS APIs used by posture checks. IMDS must
  be reachable inside the container.
- Port 8000 is published on all host interfaces as requested. Keep the security
  group closed to public inventory access; use Session Manager port forwarding
  or an authenticated proxy. The application has no built-in authentication.

## Image selection and bootstrap

### CI publication gate

The `Build and Push to ECR` workflow runs on pushes to `feature/docker`.
Its `test` job uses Python 3.12 (matching the Dockerfile), installs
`app/requirements-lock.txt`, checks `bash -n bootstrap.sh`, and runs
`python -m unittest discover -s app/tests -v`, including the offline bootstrap tests.
The `build-and-push` job declares `needs: test`: a failed or skipped test job
prevents image building and publication. No failure bypass is configured.
The publication and deployment jobs each have `id-token: write` and assume
separate AWS roles. The test job has read-only repository access and requires
no AWS credentials.

Published image tags are the full commit SHA. After publication, `deploy-check`
requests deployment through SSM as described below. There is no PR trigger in
this workflow; GitHub branch protection settings are external to this repository
and have not been inspected by this documentation review.
After pushing the workflow, verify that `test` succeeds before `build-and-push`
starts in GitHub Actions. GitHub documents the dependency behavior in
[Using jobs in a workflow](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-jobs).

### SSM deployment job

The current job chain in [the workflow](../.github/workflows/build-and-push.yml) is:

```text
push to feature/docker -> test -> build-and-push -> deploy-check
                                                   (environment: lab)
```

`build-and-push` assumes `GitHubActionsECRPushRole`; `deploy-check` assumes
`GitHubActionsPostureDeployRole`. Both use OIDC, not stored AWS keys.
The deployment job targets the literal `INSTANCE_ID` in the workflow, in
`us-east-1`, and invokes the custom SSM document
`CloudSecurityPostureExplorerDeploy` with `ImageTag` set to the commit SHA.
It polls the `deployImage` plugin up to 200 times with five-second sleeps,
accepting only `Success` with response code zero. Job timeout is 25 minutes.
Deployment jobs share concurrency group `posture-lab-deployment` with
`cancel-in-progress: false`; this is not a guaranteed FIFO deployment queue.

The job references GitHub environment `lab`. Required reviewers and deployment
branch restrictions must be configured in GitHub Settings; `environment: lab`
alone does not prove that approval is enforced. Verify these external settings
before describing deployment as approval-gated. See
[GitHub deployment review](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments).

The SSM document body, IAM role policies/trust, environment protection settings,
and SSM Agent configuration are **not versioned in this repository**. Review the
actual document's default version and confirm that `ImageTag` validation, image
replacement, liveness check, environment forwarding, and journal configuration
match the intended behavior. The workflow does not pin `--document-version` or
independently curl the app: SSM plugin success alone does not prove the right
image is serving, that `/healthz` was checked, or that AWS posture scans succeed.
A polling/job timeout also does not establish that the remote command stopped;
inspect the original CommandId before retrying or rolling back.

The SSM managed instance must be online, the deployment role must be allowed to
send this document to that instance and read invocation results, and the instance
role must support SSM Agent operation plus ECR/app access. These are prerequisites,
not resources created by this repository.

**Instance replacement:** Name tags make EBS check targets resilient to changing
instance IDs, but CD still uses a fixed `INSTANCE_ID`. Update that workflow value
and any scoped deployment permissions after replacing the hosting instance.
Bucket names and security group IDs in SSM must also be kept current.

### Deploy a published image

Paste the complete root [bootstrap.sh](../bootstrap.sh) into EC2 shell user data,
including its `#!/bin/bash` line. Its only default image tag is:

```bash
IMAGE_TAG="${IMAGE_TAG:-1.1}"
```

For an existing host, copy the script to a local file and execute:

```bash
sudo bash bootstrap.sh
# Deploy a different published version, or roll back to a known-good version:
sudo env IMAGE_TAG='<published-full-commit-sha>' bash bootstrap.sh
```

Changing EC2 user data alone does not rerun bootstrap on an existing instance.
No Git pull on the EC2 host is needed. CI publishes SHA tags, not `1.1` or `1.2`.
The bootstrap default `1.1` only works if that tag already exists; use the exact
published SHA for a reviewed release. Repeating bootstrap without IMAGE_TAG can
replace a newer release with the default image. For new user data, set IMAGE_TAG
near the top to the desired published tag. For manual rollback, use the recorded
known-good tag with the same command above and independently verify the dashboard.

Bootstrap installs `docker.io`, `curl`, `jq`, `sudo`, `unzip`, and CA certificates
from the Ubuntu archive. It does not depend on the Ubuntu `awscli` package.
An existing `aws` command is retained. If missing, bootstrap downloads the official
AWS CLI v2 ZIP for x86_64 or ARM64 and runs its bundled installer with `--update`,
using `/usr/local/aws-cli` and `/usr/local/bin`. The temporary download is cleaned
up on exit; failed downloads/installations stop deployment. Every run logs
`aws --version`. Downloads use HTTPS; this follows the AWS quick-install method
without the optional separate PGP signature verification. See the
[official AWS installation instructions](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).
It enables/starts Docker, logs versions, ensures
`ssm-user` exists, appends Docker group membership, and validates a root-owned
mode-0440 sudoers file before installing the exact rule:

```text
ssm-user ALL=(ALL) NOPASSWD:ALL
```

Both Docker membership and passwordless sudo give host administration privileges.
Open a **new Session Manager session** after bootstrap for the group change.

The script logs into ECR using the instance role and a temporary private Docker
configuration, pulls the requested image, disables the two known legacy app
units (`cloud-security-posture-explorer.service`, `cloud-security-posture.service`)
if present, and stops/removes only the named application container. It starts the
replacement with `--restart unless-stopped`, `-p 8000:8000`, `HOST=0.0.0.0` and
journald logging. No application systemd unit is created; systemd manages Docker.
Old checkout, virtual environment, unit files and configuration are left intact.

Repeated runs replace the named container safely and repeat the permission setup.
Login/pull failures leave the existing application running. Replacement causes a
brief interruption; a start/health failure returns nonzero and leaves the new
container available for diagnosis. There is no automatic rollback. Concurrent
bootstrap executions are serialized with `flock`.

## Configuration

SSM remains primary: `/cloud-security-posture-explorer/lab/config`. Keep the
existing JSON with `region`, `allowed_buckets`, `allowed_security_groups`,
`allowed_instance_name_tags`, and optional `cloudwatch_logs_enabled`.

No local environment file is required. When present,
`/etc/cloud-security-posture.env` is preserved without modification. Bootstrap
passes only these fallback/bootstrap settings into the container:

- `SSM_REGION`, `AWS_REGION`, `AWS_DEFAULT_REGION`
- `ALLOWED_BUCKETS`, `ALLOWED_SECURITY_GROUPS`, `ALLOWED_INSTANCE_NAME_TAGS`
- `ALLOWED_INSTANCES` (legacy input; nonempty values still fail app validation)
- `AWS_METADATA_SERVICE_TIMEOUT`, `AWS_METADATA_SERVICE_NUM_ATTEMPTS`

Use one `KEY=value` per line; blank lines, comments and simple paired quotes are
supported. Shell expansion, `export`, multiline values and inline comments are
not supported. Unknown settings are ignored without logging their values.
The file is never executed as shell code. Credentials, profiles and custom AWS
endpoints are not forwarded; host credential files are not mounted. Ensure the
published image also contains no AWS credentials or credential environment values.
With no region override, the app discovers the SSM region through IMDSv2.
SSM edits apply on the next scan; local fallback edits require a bootstrap rerun.

## ECR permissions on the EC2 role

In addition to existing permissions, allow:

| Action | Resource |
| --- | --- |
| `ecr:GetAuthorizationToken` | `*` |
| `ecr:BatchCheckLayerAvailability` | Repository ARN below |
| `ecr:GetDownloadUrlForLayer` | Repository ARN below |
| `ecr:BatchGetImage` | Repository ARN below |

Repository ARN:
`arn:aws:ecr:us-east-1:980468996827:repository/cloud-security-posture-explorer`.
No ECR push/create/delete permissions are needed. Cross-account roles also require
a compatible repository policy. Bootstrap changes no IAM policies or AWS resources.

## Verify and troubleshoot

After opening a new Session Manager session:

```bash
whoami
docker ps
sudo -n cat /var/log/bootstrap.log
docker logs --tail 100 cloud-security-posture-explorer
sudo journalctl CONTAINER_NAME=cloud-security-posture-explorer --since '10 minutes ago'
curl --fail http://127.0.0.1:8000/healthz
```

Bootstrap retries liveness up to 30 times, with bounded curl timeouts and a
2-second delay. It requires a running container and the JSON object
`{"liveness":"ok"}`; whitespace is accepted, extra fields/objects are rejected.
Failures return nonzero. **Liveness is not a successful posture scan**: independently
open the protected dashboard and verify SSM loading, observation timestamps,
UNKNOWN reasons, and CloudWatch delivery. IAM/API failures remain UNKNOWN.

Application JSON and scan IDs continue to reach the host journal via Docker's
[journald driver](https://docs.docker.com/engine/logging/drivers/journald/), and the
application's existing CloudWatch handler still sends to
`/cloud-security-posture-explorer/app`, stream = hosting instance ID.

## Offline tests

From a development checkout (not the EC2 container host):

```bash
python -m unittest app.tests.test_bootstrap -v
python -m unittest discover -s app/tests -v
bash -n bootstrap.sh
```

Bootstrap tests require Bash (Git Bash on Windows) and fake privileged/network
commands. They check first/repeated runs, running/stopped container replacement,
legacy service migration, preserved fallback configuration, role-only login,
sudoers validation, configurable tags, login/pull/start failure and health failure.
They also cover retaining an existing AWS CLI, installing the official x86_64 or
ARM64 package when absent, skipping installation on rerun, and failing safely on
download, extraction or installer errors.
They do not establish actual Linux permissions, image compatibility, live ECR/IAM
access or EC2 networking; verify these on the deployed lab instance.

## Name tag migration

For deployments that still use instance-ID configuration:

1. Give each intended EBS-check target a unique literal EC2 Name value in the
   configured region among pending/running/stopping/stopped instances.
2. Replace `allowed_instances` in the SSM JSON with `allowed_instance_name_tags`;
   remove the old key. Preserve all other required fields.
3. If a fallback file exists, replace nonempty `ALLOWED_INSTANCES` with
   `ALLOWED_INSTANCE_NAME_TAGS`. Recreate the container through bootstrap to load it.
4. Deploy a compatible image and verify Name resolution and volume observations.
   No match, multiple matches or incomplete data must produce UNKNOWN.

This migration affects posture targeting only; it does not change the CD target
`INSTANCE_ID` discussed above. No new tag-read action is required beyond the
existing DescribeInstances call.

## Legacy deployment examples

`app/deploy/cloud-security-posture.service` is a historical source/venv example
with `User=posture`. Current bootstrap does not install it and runs no application
systemd unit. The host account `ssm-user` is for Session Manager administration;
it is not the container's Linux user. The current Dockerfile has no USER directive
and therefore runs as container root. Docker restart policy manages the app,
while systemd manages the Docker daemon. Do not re-enable the old app service
alongside the container on port 8000.
