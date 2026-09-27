# Workload IAM policy review

Parameter Store configuration is primary. Local EnvironmentFile is optional;
EC2 can discover the bootstrap region through IMDSv2. Add `ssm:GetParameter` on
the exact configuration parameter ARN; see [configuration rollout and IAM details](PARAMETER_STORE_MIGRATION.md).
The existing workload policy example covers posture reads; add this configuration
read grant separately. No AWS permissions have been modified by this change.

This is a review of the application's required permissions and an example policy,
not an inspection or modification of the currently deployed role. The operator
reports the S3 and security-group actions are already granted. Enabling EBS
encryption checks additionally requires `ec2:DescribeInstances` and
`ec2:DescribeVolumes` on the existing instance role. This update does not apply
those permissions to AWS.

| SDK operation | IAM action | Scope |
| --- | --- | --- |
| `s3.get_public_access_block(Bucket=name)` | `s3:GetBucketPublicAccessBlock` | Exact configured bucket ARNs; no object `/*` suffix |
| `ec2.describe_security_groups(GroupIds=[id])` | `ec2:DescribeSecurityGroups` | `Resource: "*"`; restrict `ec2:Region` to the configured region |
| `ec2.describe_instances(Filters=[tag:Name, instance-state-name], MaxResults=5)` | `ec2:DescribeInstances` | `Resource: "*"`; same region restriction |
| `ec2.describe_volumes(VolumeIds=[...])` | `ec2:DescribeVolumes` | `Resource: "*"`; same region restriction |

Use [workload-policy.example.json](../app/deploy/workload-policy.example.json) as a
reviewable example. Replace its bucket and region placeholders; do not attach a
second policy blindly if the current role already has equivalent permissions.

AWS names the S3 SDK operation `GetPublicAccessBlock`, but the corresponding IAM
action is `s3:GetBucketPublicAccessBlock`. It returns only bucket-level settings;
it does not prove effective access across other policy layers.
[AWS S3 reference](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/get_public_access_block.html)

`DescribeSecurityGroups` does not support security-group resource-level
authorization. A policy with only specific security group ARNs for that action
will not provide the intended access. Its documented regional condition is
`ec2:Region`. The application always passes a single allowlisted GroupId, but the
role can still describe other groups in the permitted region: the allowlist is
application behavior, not an IAM isolation boundary.
[AWS EC2 authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ec2.html)

`DescribeInstances` and `DescribeVolumes` likewise require `Resource: "*"` and
support `ec2:Region`; they cannot be restricted to individual instance/volume ARNs
with these actions. The provider requests only explicit `ALLOWED_INSTANCE_NAME_TAGS` and
their mapped EBS volume IDs, but that application scope is not an IAM boundary.
The example policy includes all three EC2 reads. If EBS checks remain disabled,
the two new permissions can be omitted. No KMS permission, volume-content access,
or encryption/write action is required to inspect the `Encrypted` metadata.
See [DescribeInstances](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstances.html)
and [DescribeVolumes](https://docs.aws.amazon.com/boto3/latest/reference/services/ec2/client/describe_volumes.html).

Name-based resolution adds no new IAM action: tags arrive in DescribeInstances.
No DescribeTags or tag-write permission is needed. Keep configuration/Name-tag
write access restricted to the deployment operator, because a tag change can
change the application target. This is selection behavior, not resource-level
IAM enforcement.

## Identity and boundaries

- Attach the workload role to the existing EC2 instance profile. Its trust policy
  should allow the EC2 service (`ec2.amazonaws.com`) to assume it. Trust and
  permissions policies are separate; the app does not call `AssumeRole` itself.
- boto3 uses the default credential provider chain and temporary EC2 role
  credentials. No static credentials, explicit credential arguments, or profile
  selector are in the application. The service example removes credential/profile
  overrides to avoid accidentally selecting a different identity.
- Inspect all attached and inline policies, not just the example. Additional
  broad policies could grant writes even though this app only issues reads.
  A permissions boundary/SCP/explicit deny can also prevent these reads.
- The configured region bounds EC2 requests; an S3 bucket name is global and the
  SDK may redirect to its actual region. Bucket ARN scope remains essential. For
  stronger ownership enforcement, review a bucket-owner/account condition such
  as `s3:ResourceAccount` for your personal account before deployment. The app
  does not independently verify the account with STS and does not claim that it does.
- TLS verification remains enabled. Configured service endpoint URL overrides are
  ignored by the provider, reducing accidental routing to a custom endpoint.

The [SDK credential guide](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html)
documents default provider order and EC2 role use. Restrict the service account
and deployment configuration because earlier chain providers can otherwise
override the role.

## Permissions intentionally absent

No `s3:ListAllMyBuckets`, object reads/writes, bucket-policy/ACL modification,
`ec2:AuthorizeSecurityGroupIngress`, `ec2:RevokeSecurityGroupIngress`, or wildcard
`ec2:Describe*` is needed. No organization discovery or remediation exists.
Prefix-list and security-group reference resolution is not implemented; such
evidence becomes UNKNOWN rather than requiring new permissions. There is no
CloudWatch metric/alarm integration. Optional CloudWatch Logs delivery adds only
`logs:CreateLogStream` and `logs:PutLogEvents`, scoped to instance-ID streams in
`/cloud-security-posture-explorer/app`; see [logging IAM details](CLOUDWATCH_LOGS.md#aws-prerequisites-and-permissions).
The posture policy example intentionally does not include these telemetry writes
or the separate SSM grant. Local systemd journal logging adds no AWS permissions.

Operator SSH/SSM access and deployment tooling permissions are separate from
the application role and are outside this four-action policy. Do not broaden the
workload role merely to make operator tasks convenient.

## Docker host ECR pull permissions

The EC2 role also needs ECR authorization and repository-scoped image reads for
bootstrap. See the exact [actions and repository ARN](DEPLOYMENT_EC2.md#ecr-permissions-on-the-ec2-role).
No image push or repository administration permission is required. Application
posture, SSM and CloudWatch permissions remain unchanged.
