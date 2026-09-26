# Project Plan

## Project statement

Create a small, read-only AWS security posture application and use it as a **simulated customer workload** for monitoring, incident coordination, recovery, and retrospective practice. Use Terraform for lab infrastructure and Git for change history. The objective is demonstrable engineering and incident-management judgment, not a production landing zone.

## Audience and outcomes

- **Lab user:** can inspect a limited set of security checks in the personal AWS account.
- **Lab operator:** can determine whether the application is healthy, see why an alarm fired, follow a runbook, record decisions and status updates, and verify recovery.
- **Portfolio reviewer:** can reproduce the design rationale and inspect Terraform, tests, a controlled exercise, and honest limitations without receiving credentials.

## Scope

### MVP in scope

1. Read-only inspection of selected S3 bucket-level public access block settings and selected security group ingress rules; clear check rationale and `UNKNOWN` on incomplete evidence.
2. Simple UI and application health endpoint; no production data.
3. One hosting option selected after a cost and access review. **Candidate:** a small EC2 instance for hands-on OS/network troubleshooting; a Lambda-based alternative may be explored separately.
4. One application-level signal and one infrastructure-level signal; CloudWatch alarm(s) and an explicitly tested notification path.
5. A workload definition, response runbook, controlled failure, recovery verification, and incident exercise record.
6. Terraform code, reviewed plans, tests, README, and teardown notes.

### Out of scope for MVP

- AWS official Incident Detection and Response enrollment or Support case integration.
- Multi-account landing zone, full compliance assessment, remediation, real customer incidents, public unauthenticated security inventory, and continuous 24x7 coverage.
- RDS, Bedrock, NAT gateway, load balancer, multi-AZ architecture, and CI/CD until justified by a specific learning outcome and cost review.

## Workload definition (draft)

- **Workload:** personal posture dashboard.
- **Customer outcome:** an authorized lab user can obtain a current, clearly scoped set of security check results.
- **Critical user journey:** open the application and retrieve check results without a server error.
- **Application-level signal:** successful end-to-end check retrieval or a representative health transaction; exact mechanism and threshold are to be validated in the implementation.
- **Infrastructure-level signal:** EC2 status checks if EC2 is selected. A healthy instance does not by itself prove a healthy application.
- **Impact statement for exercises:** the lab user cannot retrieve results, or results are stale/unavailable; do not imply impact to actual customers.

## Delivery milestones and acceptance criteria

### M0. Guardrails and baseline

- Confirm AWS Free/Paid plan, available credits, eligible services, region, and budget alert destination.
- Record the existing S3 exercise separately; verify `.gitignore` is spelled correctly and ignores state, credentials, local plans, and private variable files. Commit `.terraform.lock.hcl`.
- Review and reduce existing long-lived administrator access; never store credentials in code or screenshots.
- **Done when:** budget notification is tested/confirmed, baseline plan is documented, and no secrets are staged for Git.

### M1. Local application prototype

- Implement the checks against sample fixtures first; show PASS/REVIEW/UNKNOWN and a clear evidence timestamp.
- Add tests for overly broad ingress, expected S3 settings, access denied, and incomplete data.
- **Done when:** a local demo works with synthetic data and tests pass without AWS credentials.

### M2. Deploy a bounded AWS workload

- Select EC2 or Lambda based on access design and estimated charges; document the decision.
- Use Terraform for workload resources and an application IAM role limited to read-only APIs actually required.
- Protect access to the dashboard; do not publish an unauthenticated inventory endpoint.
- **Done when:** an authorized user can retrieve live lab results and unauthorized access is denied.

### M3. Observability and response

- Instrument an application-level check and infrastructure-level check; choose alarm thresholds, evaluation windows, and missing-data treatment explicitly.
- Test alarm delivery, write the runbook, and record a baseline of normal behavior.
- **Done when:** a controlled lab failure triggers the expected signal, the operator receives it, and recovery is verified from the user journey.

### M4. Exercise and portfolio

- Perform a reversible failure only on dedicated lab resources. Capture timestamps, evidence, decisions, customer-style updates, recovery, and follow-ups.
- Add architecture and teardown notes; document cost/credit consumption and limitations.
- **Done when:** a reviewer can follow the story from code change to detection to restoration and retrospective.

### M5. Optional extensions

- CI checks (`terraform fmt`, `terraform validate`, unit tests, reviewed plan); use short-lived CI credentials rather than stored AWS keys.
- Compare EC2 and Lambda deployment models. Consider RDS or Bedrock only for a concrete product requirement, not to pad the architecture.

## Learning map

- Terraform: provider, variables, outputs, references, state, drift, reviewed plans, and safe teardown.
- AWS: IAM, EC2 or Lambda, S3 and security group inspection, CloudWatch metrics/alarms, notification delivery, and cost management.
- Incident management: impact framing, triage, communication cadence, escalation criteria, recovery validation, and problem record.

## Immediate next action

Start M0. Do not deploy additional resources until account plan, budget, access method, and expected cleanup are documented.
