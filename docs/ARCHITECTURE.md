# Architecture and Decision Record

**Status: proposed; not deployed.** This is a single-account learning workload, not a full AWS landing zone or official IDR workload.

## Logical view

```text
Authorized lab user
        |
        v
Protected dashboard / API  -- read-only AWS SDK calls -->  selected lab S3 and EC2 metadata
        |
        +--> application health transaction --> CloudWatch signal --> alarm --> notification
        |
Hosting resource (candidate: EC2) --> EC2 status-check signal --> separate alarm

Operator --> runbook --> investigation --> recovery check --> exercise record
Ubuntu development VM --> Git / Terraform --> personal AWS account
```

## Application behavior

- Inspect only resources in the personal lab account; initial scope is explicit, not an unrestricted organization-wide scan.
- For S3, bucket-level Block Public Access is only part of effective access evaluation; account/organization settings and policy context matter. Label the check narrowly, and use `UNKNOWN` when effective exposure cannot be established.
- For security groups, flag a selected rule for review when a sensitive management port is open to a broad source; do not assert compromise or full network reachability from one rule.
- A failed AWS API call is a visibility error, not evidence of compliance.
- Show scan time, account/region scope, and check rationale; avoid exposing account identifiers or internal inventory in a public portfolio screenshot.

## Hosting decision gate

**EC2 candidate:** useful for OS, network, process, and status-check troubleshooting; may incur compute, storage, public IPv4, and monitoring charges. Design an authorized access path before deployment. Do not open management ports to the world.

**Lambda alternative:** useful for a serverless comparison; requires its own authenticated access and workload-level observability design. A Lambda function URL created for an AWS credit activity is not automatically the same as a secure public dashboard.

Decision remains open until M0 cost and access review. Record the choice, alternatives, and reason in a Git commit/ADR.

## Terraform boundaries

- Keep the existing S3 learning exercise and its state separate from the new application stack. Never casually move a resource between states.
- Start with local state only for a single-user lab; protect and back it up securely. If collaboration/automation warrants a remote backend, design a dedicated state bucket with versioning and S3 lockfile support; do not assume the application bucket is the backend.
- Use references to express dependencies, e.g., a workload role referenced by the compute resource; use outputs only for useful, non-secret values.
- Review `terraform plan` before applying. A replacement or destroy is a decision point, not routine noise.

## Monitoring model

- **User journey:** representative check retrieval succeeds and is fresh enough for the lab's documented expectation.
- **Application signal:** a health transaction or error/freshness signal; exact implementation and costs must be validated.
- **Infrastructure signal:** EC2 status checks if EC2 is selected.
- **Alarm design:** document threshold, evaluation periods, missing-data behavior, notification destination, and test result. Avoid using CPU alone as a proxy for customer impact.
- **Recovery:** verify the user journey, not only a green infrastructure metric.

## Security boundaries

- Dedicated personal account; no employer resources or data.
- Application role: read-only permissions required for the two checks; no administrator permissions, mutation APIs, or embedded access keys.
- Restrict access to the UI/API; log errors without credentials or sensitive response payloads.
- Fault exercises only on tagged, disposable lab resources with a documented rollback.

## Architecture references

- [AWS IDR observability guidance](https://docs.aws.amazon.com/IDR/latest/userguide/observe-idr.html)
- [AWS IDR CloudWatch alarm guidance](https://docs.aws.amazon.com/IDR/latest/userguide/idr-gs-ingest-cw-alarms.html)
- [EC2 status checks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/monitoring-system-instance-status-check.html)
- [S3 public access controls](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html)
- [Terraform S3 backend](https://developer.hashicorp.com/terraform/language/backend/s3)
