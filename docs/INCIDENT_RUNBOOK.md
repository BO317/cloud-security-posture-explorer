# Lab Workload Incident Runbook

**Status:** operational draft aligned to Docker/ECR/SSM deployment, not a completed
exercise or an AWS Support/official IDR runbook. Alarm and notification fields
remain unverified; populate them only from actual deployment evidence.

## Workload profile

- Workload: Cloud Security Posture Explorer.
- Customer outcome: authorized lab user retrieves current, scoped posture results.
- Owner/on-call: lab operator (single-person exercise).
- Environment/region: `[record after deployment]`.
- Application liveness: `GET http://127.0.0.1:8000/healthz` on the host; require
  `{"liveness":"ok"}`. Separately request `/` through authenticated access and
  verify fresh, complete observations; REVIEW is not itself an outage.
- Alarm identifiers and links: `[record after deployment]`.
- Notification destination: `[record and test]`.
- Expected normal behavior and data freshness: `[measure and document]`.
- Rollback: [image deployment procedure](DEPLOYMENT_EC2.md#deploy-a-published-image);
  record the known-good image tag/digest. Infrastructure teardown remains in the
  owning infrastructure repository and requires its own reviewed procedure.

## Trigger and impact

Start this runbook when an application-level alarm, infrastructure alarm, or failed user journey indicates potential unavailability. Do not equate an alarm with confirmed user impact. Distinguish `ALARM`, `OK`, and `INSUFFICIENT_DATA`; missing data needs investigation rather than an automatic claim of failure.

## Response sequence

1. **Acknowledge and timestamp:** record alarm name, observed time, responder, environment, and whether this is a planned exercise.
2. **Verify the user journey:** attempt the authorized check retrieval. Record error/status, last successful result, and whether output is stale. Never paste credentials or sensitive inventory into the record.
3. **Bound the impact:** determine whether the issue is application, AWS API access, instance/service health, network path, or monitoring-only. Record evidence and uncertainty separately.
4. **Investigate:** inspect relevant application logs, CloudWatch alarm history/metrics, and EC2 status checks if applicable. Check recent deployments or IAM changes. Preserve timestamps and avoid speculative root-cause statements.
5. **Communicate:** provide a concise initial update, then updates at the cadence agreed for the exercise. State known impact, actions, next update, and unknowns. Use the templates below.
6. **Recover safely:** use the documented rollback or a reversible lab action. Avoid changing unrelated resources or granting broad permissions as a shortcut.
7. **Validate:** rerun the user journey, confirm data freshness and alarm recovery, and observe for recurrence. An EC2 status check alone is insufficient.
8. **Close and improve:** record actual cause (or `undetermined`), contributing factors, detection gaps, follow-up owner, and runbook changes.

## Docker and deployment diagnosis

Read-only host checks (run in an authorized Session Manager session):

```bash
docker ps -a --filter name=cloud-security-posture-explorer
docker inspect --format '{{.Config.Image}} {{.Image}} {{.State.Status}}' cloud-security-posture-explorer
curl --fail http://127.0.0.1:8000/healthz
docker logs --tail 100 cloud-security-posture-explorer
sudo journalctl CONTAINER_NAME=cloud-security-posture-explorer --since '15 minutes ago'
sudo tail -n 100 /var/log/bootstrap.log
```

| Symptom | Next evidence to inspect |
| --- | --- |
| CI test failure | Failed test/syntax step; publication and deployment should be skipped |
| ECR login/pull failure | Push vs host-pull role, image SHA existence, region and connectivity |
| Deployment waiting | `lab` environment protection/review state in GitHub |
| SSM deployment failure or unconfirmed timeout | Original CommandId, target ID, managed-instance status, document version and `deployImage` output; do not assume a timed-out workflow stopped the remote command |
| Health failure | Container state/logs, published port, Docker daemon and legacy-service conflict |
| Health OK but UNKNOWN/503 | `configuration_load` source/version/fallback reason, metadata access, IAM and API errors; do not infer PASS |
| Journal present, CloudWatch missing | SSM logging switch, group region/permissions, IMDSv2 and delivery error codes |

The workflow's deployment ID is fixed; host replacement may require updating it.
Posture Name tags do not resolve that deployment target. The custom SSM document
is not in this checkout; verify its actual steps instead of assuming it matches
bootstrap. Preserve workflow URL, commit/image SHA, CommandId, SSM parameter version,
scan ID and UTC timestamps privately; redact inventory before portfolio publication.

For image recovery, use a recorded known-good published tag and the deployment
procedure. Avoid concurrent manual recovery and an in-flight SSM deployment.
Recheck the running image, liveness, a fresh dashboard scan and log delivery after
recovery. Do not restart the legacy Python systemd unit on a Docker host.

## Escalation decision

In this single-person lab, `escalation` means documenting when a real team would engage application, networking, IAM, or AWS support specialists. Do not create a customer Support case or claim AWS team participation for a simulated exercise.

## Communication templates

### Initial update

> We are investigating an issue affecting the lab posture dashboard. The observed symptom is [verified symptom] beginning at [time and time zone]. Current user impact is [confirmed impact or not yet confirmed]. We are checking [next diagnostic step] and will provide the next update at [time]. This is a controlled lab exercise.

### Progress update

> The dashboard remains [available/degraded/unavailable]. We have confirmed [evidence] and ruled out [evidence]. The current recovery action is [action]. Root cause is [unconfirmed/confirmed with evidence]. Next update: [time].

### Resolution update

> The lab dashboard is responding normally as of [time], verified by [user-journey test]. We will monitor for recurrence and document the cause, timeline, and preventive actions. This was a controlled lab exercise.

## References

- [AWS IDR runbook and response-plan guidance](https://docs.aws.amazon.com/IDR/latest/userguide/idr-workloads-dev-runbook.html)
- [CloudWatch alarms](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Alarms.html)
- [EC2 status-check alarm guidance](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/creating_status_check_alarms.html)
