# Security and Cost Guardrails

## Account and credits

- Confirm whether the personal account is on the Free plan or Paid plan in Billing before provisioning. Do not treat historical credit amounts or activity screenshots as the current balance; verify the console before an exercise.
- Free plan expiration and Paid plan billing differ. Do not assume a credit balance is a spending cap. Review the current account-plan consequences before enabling additional organizational services; they are not required by this application.
- Create a modest account-wide monthly cost budget with email alerts before new infrastructure. Budget data/notifications can lag; check actual usage and remaining credits manually during experiments.
- Tag all supported lab resources with project/environment/owner-purpose values that contain no private information. Record the resources, region, creation date, and deletion verification.

## Cost review before each apply

1. Identify every planned service and billable dimension: compute runtime, EBS, public IPv4, ECR image storage, CloudWatch logs/metrics/alarms, GitHub Actions usage, data transfer, and any optional services.
2. Check current AWS pricing and Free Tier/credit eligibility for the chosen region and account plan. Record assumptions; do not promise zero cost.
3. Review `terraform plan` for unexpected resources or replacement/destruction.
4. Define a stop/teardown procedure. A stopped instance may still leave chargeable resources; verify cleanup in the AWS console and billing views.
5. Re-check credit balance after experiments. Do not leave a recurring job, RDS database, NAT gateway, or test instance running without a deliberate reason.

## Credits activity tracker (optional, not product requirements)

- Budgets: set a cost budget and verify the activity status.
- EC2 is the selected host; when ending an exercise, follow the reviewed teardown plan and verify dependent resources.
- Lambda: AWS describes a web app using a Lambda function URL; verify the activity-specific steps, but do not expose the posture API without a suitable access design.
- Bedrock: playground activity can be completed separately using non-sensitive sample prompts; do not send employer data.
- RDS: create only for a short, separately budgeted database-learning exercise if needed; it is not part of the MVP.

Completion and credit award must be checked in the AWS console; use of a service alone does not establish award.

## Identity and secrets

- Secure root with MFA and avoid root for routine tasks.
- The lab previously used an IAM user with a long-lived access key and broad administrator permissions. Treat this as a temporary learning bootstrap, not the application's identity. Rotate/remove it when practical and move toward temporary credentials and least privilege.
- Use an IAM role for the running workload; no access keys in Terraform files, environment files committed to Git, screenshots, logs, or incident records.
- Keep Terraform state private; it can contain sensitive values. Ignore local state and `*.tfvars` as appropriate; inspect `git status` before each commit. Commit `.terraform.lock.hcl` for provider reproducibility.
- Protect the dashboard: bootstrap publishes port 8000 on all host interfaces.
  Use restricted network access and an authenticated tunnel/proxy; no built-in login exists.
- `ssm-user` has Docker-group access and passwordless sudo by explicit design.
  Both grant host administration; they are not least-privilege application accounts.
  The current container runs as root because the Dockerfile has no USER directive.
- The EC2 instance role and the GitHub image-push/deployment roles serve different
  purposes. Keep static keys out of all three paths. Confirm GitHub `lab` required
  reviewers/branch restrictions and the external SSM deployment document separately.
- Posture reads do not remediate resources. Optional CloudWatch delivery does write
  streams/events; deployment tooling intentionally changes the running container.
  Do not describe the entire delivery system as read-only.

## Safe exercise rules

- Use only disposable, clearly tagged lab resources and synthetic data.
- State the expected impact, rollback action, and stop condition before a fault exercise.
- Do not run disruption experiments against employer accounts, production systems, third-party services, or shared resources.
- Do not assert a five-minute SLA or official AWS IDR coverage; this lab is self-operated.

## References

- [AWS Free/Paid account plans](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)
- [AWS credit activities](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans-activities.html)
- [AWS Budgets behavior](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)
- [AWS IAM best practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [CloudWatch cost guidance](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_billing.html)
