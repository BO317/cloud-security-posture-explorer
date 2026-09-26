# Deploy the read-only provider on EC2

The operator reports the previous MVP already runs on EC2 under systemd with an
instance role. This guide updates that installation; no EC2/IAM changes have been
applied by this code change. Paths, service names, region, and resource identifiers
below are examples. Preserve the existing working unit/configuration for rollback.

## Prerequisites and access

- Python 3.10+ with venv, Git, systemd, and an existing EC2 instance profile.
- The role grants the reads for enabled checks in [IAM_POLICY_REVIEW.md](IAM_POLICY_REVIEW.md).
  EBS encryption adds `ec2:DescribeInstances` and `ec2:DescribeVolumes` to the
  existing role; no static credentials or write permissions are required.
- Instance Metadata Service is accessible to the process. Require IMDSv2 in the
  instance configuration; current boto3 supports role credentials through IMDS.
- Outbound DNS/HTTPS reaches the required AWS service endpoints, directly or
  through existing network infrastructure. Do not disable certificate verification.
- Keep port 8000 off public security-group ingress. The app binds only to loopback.
  Access it through an authenticated SSH tunnel or an existing authenticated and
  authorized reverse proxy. There is no application login or public inventory API.

## Prepare the release

First identify your current unit, checkout path, service account, and revision.
For the example unit name:

```sh
sudo systemctl cat cloud-security-posture.service
git -C /opt/cloud-security-posture-explorer rev-parse HEAD
git -C /opt/cloud-security-posture-explorer status --short
```

Record the old revision and retain the old environment/unit privately. If local
changes exist, preserve them before updating; do not reset or overwrite them.
Publish the reviewed code to your repository, then fetch the chosen release on
EC2 using your existing deployment account. Stop the service during an in-place
update so it cannot read a mixture of revisions:

```sh
sudo systemctl stop cloud-security-posture.service
cd /opt/cloud-security-posture-explorer
git pull --ff-only
python3 -m venv .venv
.venv/bin/python -m pip install -r app/requirements-lock.txt
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s app/tests -v
```

Use the owner of the checkout for Git and dependency installation. The runtime
account only needs read/execute access to the checkout and virtual environment.
If you deploy immutable release directories, prepare/test the new directory
before stopping the old service and update the unit paths when switching.

## Configure the bounded resource scope

Copy `app/deploy/posture.env.example` to `/etc/cloud-security-posture.env`, replace
the invented identifiers, and restrict permissions (root-owned, mode 0600 is
sufficient because the system service manager reads EnvironmentFile):

```sh
sudo install -m 0600 app/deploy/posture.env.example /etc/cloud-security-posture.env
sudoedit /etc/cloud-security-posture.env
```

Example contents:

```ini
AWS_REGION=us-east-1
ALLOWED_BUCKETS=your-personal-lab-bucket,another-personal-lab-bucket
ALLOWED_SECURITY_GROUPS=sg-0123456789abcdef0
ALLOWED_INSTANCES=i-0123456789abcdef0
AWS_METADATA_SERVICE_TIMEOUT=2
AWS_METADATA_SERVICE_NUM_ATTEMPTS=1
```

Do not copy over an existing environment file without retaining its current
values. Comma-separated allowlists are explicit names/IDs, not patterns. Any
list can be empty; all three empty produce UNKNOWN with no AWS calls. Up to 100 unique
entries per list are supported. Leave `ALLOWED_INSTANCES` empty or unset to keep
EBS checks disabled. Setting it enables one aggregate EBS encryption result per
instance, based on its attached volumes. Missing permissions or incomplete volume
results produce UNKNOWN; no attached-volume evidence also produces UNKNOWN.
Use a small scope: scans are sequential and
total latency increases with each target. S3 bucket names are global; configure
the intended region and restrict ownership through IAM as discussed in the review.

No `aws configure`, static keys, secret files, or credential arguments are needed.
The boto3 default chain obtains temporary credentials from the instance role.
The example unit deliberately clears static-key/profile overrides, disables shared
credential/config files for this service, and keeps EC2 metadata enabled. If
adapting an existing unit, retain those protections. Never put credentials in the
environment file. Do not enable botocore debug logging around live inventory.

## Adapt systemd

`app/deploy/cloud-security-posture.service` is a complete example using runtime
user/group `posture` and `/opt/cloud-security-posture-explorer`. Reuse your existing
unprivileged account or create a dedicated account through your normal host setup,
then adjust `User`, `Group`, `WorkingDirectory`, and `ExecStart` accordingly.
Do not install the example unchanged unless those paths and identities exist.

The unit uses the same `python -m app.server --port 8000` entry point. It adds a
read-only filesystem view, no privilege escalation, journal logging, and automatic
restart on process failure. `ProtectHome=true` requires code/venv outside `/home`.
Do not add `PrivateNetwork=true`: it would block AWS and metadata access.

After reviewing/adapting the unit (or merging the relevant settings into your
existing unit):

```sh
sudo systemd-analyze verify /etc/systemd/system/cloud-security-posture.service
sudo systemctl daemon-reload
sudo systemctl enable cloud-security-posture.service
sudo systemctl restart cloud-security-posture.service
sudo systemctl status cloud-security-posture.service --no-pager
```

If creating a new unit rather than editing an existing one, install your adapted
copy at `/etc/systemd/system/cloud-security-posture.service` with mode 0644 first.
Do not launch a second service on the existing service's port.

## Verify the release

On EC2:

```sh
curl --fail --max-time 3 http://127.0.0.1:8000/healthz
sudo journalctl -u cloud-security-posture.service -n 50 --no-pager
```

Health must return `{"liveness": "ok"}`. This verifies liveness only.
From your workstation, using your existing authorized SSH access:

```sh
ssh -N -L 127.0.0.1:8000:127.0.0.1:8000 your-ssh-user@your-ec2-host
```

Open http://127.0.0.1:8000/ and verify the configured resources, region, scan ID,
fresh observation/attempt timestamps, and reasons. Compare a selected result to
the AWS configuration using your authorized operator access. No resource should
appear outside the allowlists. Do not post live resource names in portfolio images.

The application has no automatic polling. Each GET/HEAD `/` collects again;
`/healthz` does not. API failures are rendered as per-resource UNKNOWN, commonly
with HTTP 200 so remaining evidence stays visible. COMPLETE is evidence coverage,
not a clean-security verdict. A 503 can indicate invalid configuration, another
scan in progress, or a whole-page failure. Never use health 200 alone as rollout
acceptance. AWS-side verification and systemd validation must be performed on EC2;
offline tests do not establish that deployment succeeded.

## Diagnose UNKNOWN without expanding permissions blindly

Use the page scan ID to find `posture_observation` JSON events in the journal.
Each event identifies a target by its zero-based index in the configured lists
(buckets first, then groups, then instances). It records status, safe diagnostic, and AWS request
ID when available; `api_requests` retains both DescribeInstances and DescribeVolumes
operation/request IDs for EBS checks. It intentionally omits inventory and raw AWS messages.

- `access_denied`: inspect the role, bucket resource scope, regional conditions,
  and any explicit denies/SCPs/endpoint policies.
- `sdk_error`: inspect the diagnostic class for credentials, timeout, or connection
  errors. Check instance-role attachment, IMDS access, DNS, and HTTPS connectivity.
- `api_error`: check target existence and request ID. Missing bucket BPA config
  is UNKNOWN by design.
- `invalid_response`: check incomplete responses or unsupported source references.
  Nonempty prefix-list/security-group sources are deliberately not resolved.
  For EBS, inspect the safe diagnostic for missing mappings, volumes, encryption
  flags, or attachment inconsistencies. Retry after any attachment operation settles.
- `unexpected_error`: inspect the deployed version and reproduce with offline
  tests. The application does not print raw exceptions containing inventory.

For an optional failure exercise, change only a copied app scope to a nonexistent
target and verify UNKNOWN plus working health, then restore configuration and
restart. No AWS resource or IAM mutation is necessary. This exercise is not marked
as completed by this guide.

## Rollback

Stop the service, switch to the previously recorded release using your normal
release process, restore its unit/environment and dependency environment, reload
systemd, and restart. Verify both liveness and the expected user journey. If rolling
back to the synthetic MVP, label it synthetic; do not treat its results as AWS
observations. No AWS resource rollback is needed for this application-only update.

## References

- [Boto3 credentials and EC2 role provider](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html)
- [Botocore timeout/retry configuration](https://docs.aws.amazon.com/botocore/latest/reference/config.html)
