# Security and Cost Guardrails

## Account and credits

- Confirm whether the personal account is on the Free plan or Paid plan in Billing before provisioning. The user reported an initial USD 100 credit and a dashboard showing five additional USD 20 activities not yet completed; verify the live credit balance and activity status in the console.
- Free plan expiration and Paid plan billing differ. Do not assume a credit balance is a spending cap. Do not enable AWS Organizations or Control Tower merely to label this a landing zone; AWS documents a Free-plan-to-Paid-plan consequence for those actions.
- Create a modest account-wide monthly cost budget with email alerts before new infrastructure. Budget data/notifications can lag; check actual usage and remaining credits manually during experiments.
- Tag all supported lab resources with project/environment/owner-purpose values that contain no private information. Record the resources, region, creation date, and deletion verification.

## Cost review before each apply

1. Identify every planned service and billable dimension: compute runtime, EBS, public IPv4, CloudWatch metrics/alarms/logs, storage, data transfer, and any optional services.
2. Check current AWS pricing and Free Tier/credit eligibility for the chosen region and account plan. Record assumptions; do not promise zero cost.
3. Review `terraform plan` for unexpected resources or replacement/destruction.
4. Define a stop/teardown procedure. A stopped instance may still leave chargeable resources; verify cleanup in the AWS console and billing views.
5. Re-check credit balance after experiments. Do not leave a recurring job, RDS database, NAT gateway, or test instance running without a deliberate reason.

## Credits activity tracker (optional, not product requirements)

- Budgets: set a cost budget and verify the activity status.
- EC2: only when selected for the workload or a bounded comparison; stop/terminate and verify dependent resources.
- Lambda: AWS describes a web app using a Lambda function URL; verify the activity-specific steps, but do not expose the posture API without a suitable access design.
- Bedrock: playground activity can be completed separately using non-sensitive sample prompts; do not send employer data.
- RDS: create only for a short, separately budgeted database-learning exercise if needed; it is not part of the MVP.

Completion and credit award must be checked in the AWS console; use of a service alone does not establish award.

## Identity and secrets

- Secure root with MFA and avoid root for routine tasks.
- The lab previously used an IAM user with a long-lived access key and broad administrator permissions. Treat this as a temporary learning bootstrap, not the application's identity. Rotate/remove it when practical and move toward temporary credentials and least privilege.
- Use an IAM role for the running workload; no access keys in Terraform files, environment files committed to Git, screenshots, logs, or incident records.
- Keep Terraform state private; it can contain sensitive values. Ignore local state and `*.tfvars` as appropriate; inspect `git status` before each commit. Commit `.terraform.lock.hcl` for provider reproducibility.
- Protect the dashboard: do not publish an unauthenticated account inventory or a write-capable API.

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
