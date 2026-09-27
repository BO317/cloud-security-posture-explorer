# Deploy the read-only provider on EC2

Parameter Store configuration is primary. The updated unit makes EnvironmentFile
optional: EC2 discovers the bootstrap region through IMDSv2 and requires no local
app settings. An existing environment file can remain as fallback. Add `ssm:GetParameter` on
the exact configuration parameter ARN; see [configuration rollout and IAM details](PARAMETER_STORE_MIGRATION.md).
The existing workload policy example covers posture reads; add this configuration
read grant separately. No AWS permissions have been modified by this change.

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

### Bootstrap checkout ownership

The root [bootstrap.sh](../bootstrap.sh) is a corrected copy of the operator's
cloud-init script from `secure-enterprise-platform-lab/posture-workload`. Replace
that project's copy when preparing user_data; this repository change does not
modify the separate infrastructure checkout or an existing EC2 host.

Cloud-init still runs as root, but the script recursively assigns the application
directory, `.git`, `.venv`, and application files to `ssm-user:ssm-user` and grants
owner read/write access (directory traversal and existing execute bits retained).
It ensures the group exists, repairs old ownership before checking an existing
checkout, and runs Git checks, venv creation, and pip as `ssm-user`. It preserves
existing revisions and configuration rather than resetting or pulling on reruns.
Ownership is reapplied after dependency installation. Symlinked checkout roots
are rejected; recursive chown does not dereference venv interpreter symlinks.

`User=ssm-user` and `ProtectSystem=strict` remain unchanged. The service's filesystem
view stays read-only; an interactive `ssm-user` shell can update the checkout.
Bootstrap does not change the identity of an already-open shell. On EC2, verify
from an `ssm-user` session after bootstrap:

```sh
whoami
cd /opt/cloud-security-posture-explorer
stat -c '%U:%G %n' . .git .venv
test -w .git && test -w .venv
git pull --ff-only
```

Network access and a clean/compatible Git history are still needed for pull.
The offline bootstrap tests run Bash with host mutations replaced by fakes and
cover fresh/repeat runs, preservation of existing config, and non-repository
directory refusal. They do not establish actual Linux UID permissions or a
successful EC2 deployment:

```sh
python3 -m unittest app.tests.test_bootstrap -v
```

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

For an optional legacy/local fallback only, copy `app/deploy/posture.env.example` to `/etc/cloud-security-posture.env`, replace
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
ALLOWED_INSTANCE_NAME_TAGS=cloud-security-posture-explorer
AWS_METADATA_SERVICE_TIMEOUT=2
AWS_METADATA_SERVICE_NUM_ATTEMPTS=1
```

Do not copy over an existing environment file without retaining its current
values. Comma-separated allowlists are explicit names/IDs, not patterns. Any
list can be empty; all three empty in both sources produce UNKNOWN with no posture calls. Up to 100 unique
entries per list are supported. Leave `ALLOWED_INSTANCE_NAME_TAGS` empty or unset to keep
EBS checks disabled. Setting it enables one aggregate EBS encryption result per
configured Name, based on the uniquely resolved instance and its attached volumes. Missing permissions or incomplete volume
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

## Name tag migration

This is a one-time configuration schema change. The new code does not accept
`allowed_instances`; a nonempty legacy `ALLOWED_INSTANCES` invalidates fallback
rather than silently dropping EBS checks. No Terraform or AWS resources are
modified by this application change.

1. Preserve the deployed revision, Parameter Store JSON/version, and local env.
2. Confirm the instance has the stable, case-sensitive `Name` tag value
   `cloud-security-posture-explorer`. Future replacements must receive the same
   tag from the existing deployment definition. Keep it unique in this account
   and region among pending/running/stopping/stopped instances.
3. Stop the existing service for the coordinated code/config update. If using the
   bootstrap unit, its name is `cloud-security-posture-explorer.service`; the older
   example below uses `cloud-security-posture.service`. Use the actual installed
   name for all systemctl/journalctl commands; do not create a second service.
4. Deploy/test the new code. As the configuration operator, replace the SSM JSON
   `allowed_instances` key with `allowed_instance_name_tags`, using literal Name
   values instead of IDs. Keep region, bucket, and security-group settings.
5. Replace `ALLOWED_INSTANCES` in `/etc/cloud-security-posture.env` with
   `ALLOWED_INSTANCE_NAME_TAGS=cloud-security-posture-explorer`. Update any saved
   cloud-init/bootstrap environment template as well. Keep both sources aligned.
6. Restart the existing service. Verify `configuration_load` selects `ssm`, then
   check the dashboard's EBS row (now labeled by Name), evidence time, status, and
   correlated audit events. Health alone does not establish a successful scan.
7. Roll back by restoring the matching previous code, SSM document, and local
   environment together, then restart. Old/new schema mixing is not supported.

The resolver filters by `tag:Name` and non-terminated states and requires exactly
one complete match. Zero matches (including propagation delay after creation),
duplicate Names, missing tags/identity/state, API failures, or any continuation
token yield UNKNOWN; no volume query occurs until identity is resolved. Stopped
instances remain in scope. Terminated/shutting-down instances are excluded by the
request. A bounded `MaxResults=5` response prevents unbounded lookup; a partial page
is rejected rather than assuming it proves uniqueness. Retry after replacement
or tag propagation settles. The app only observes; it performs no recovery action.

EC2 documents eventual consistency and recently terminated results in
[DescribeInstances](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstances.html).
The existing DescribeInstances/DescribeVolumes permissions suffice.

After this one-time migration, instance replacement with the same unique Name
requires no Parameter Store EC2 target edit. **This does not make every Terraform
destroy/apply independent of configuration:** security groups are still selected
by ID, and their IDs may change on recreation; bucket names, region, parameter,
role, and bootstrap settings must also remain valid. Security-group tag targeting
is outside this change.

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

## Optional CloudWatch Logs

Follow [CloudWatch Logs deployment](CLOUDWATCH_LOGS.md) to pre-create the fixed log
group, grant only stream creation/event writes, and use the SSM
`cloudwatch_logs_enabled` setting (default true).
Journal stays enabled. The stream name is the hosting instance ID from IMDSv2,
independent of the Name-tag scan targets. SSM logging changes apply on the next
scan without restarting. Local logging variables are not used. Verify cloud delivery separately from liveness/posture.

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
