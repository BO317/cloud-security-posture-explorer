# Workload IAM policy review

This is a review of the application's required permissions and an example policy,
not an inspection or modification of the currently deployed role. The operator
reports both actions are already granted. No additional action is required for
the implemented checks.

| SDK operation | IAM action | Scope |
| --- | --- | --- |
| `s3.get_public_access_block(Bucket=name)` | `s3:GetBucketPublicAccessBlock` | Exact configured bucket ARNs; no object `/*` suffix |
| `ec2.describe_security_groups(GroupIds=[id])` | `ec2:DescribeSecurityGroups` | `Resource: "*"`; restrict `ec2:Region` to the configured region |

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
CloudWatch API integration; local systemd journal logging adds no AWS permissions.

Operator SSH/SSM access and deployment tooling permissions are separate from
the application role and are outside this two-action policy. Do not broaden the
workload role merely to make operator tasks convenient.
