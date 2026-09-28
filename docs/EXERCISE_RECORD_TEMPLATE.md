# Controlled Incident Exercise Record

> Template only. Populate with measured evidence from a future lab exercise; do not present sample fields as completed events. Use synthetic data and remove sensitive identifiers before publishing.

## Exercise metadata

- Exercise ID: `[LAB-YYYYMMDD-01]`
- Date/time zone: `[ ]`
- Workload commit and deployed image tag/digest: `[ ]`
- Workflow run URL, SSM CommandId and deployment-document version: `[ ]`
- SSM configuration version/source and representative scan ID: `[ ]`
- Region/account alias (no full account ID in public copy): `[ ]`
- Scenario and expected impact: `[ ]`
- Fault method and dedicated target: `[ ]`
- Rollback action and stop condition: `[ ]`
- Operator: `[ ]`

## Before starting

- [ ] Budget/credit balance checked.
- [ ] Target is owned, disposable, and clearly tagged.
- [ ] Baseline liveness and fresh posture observations verified separately.
- [ ] Known-good image tag/digest and recovery procedure recorded.
- [ ] For an alerting exercise, alarm and notification path tested; otherwise
      explicitly record manual detection and do not claim alarm validation.
- [ ] Rollback is ready; no employer or third-party resources are involved.

## Timeline (use actual timestamps)

| Time and zone | Observation/action | Evidence reference | Decision/owner |
| --- | --- | --- | --- |
| `[ ]` | Baseline verified | `[ ]` | `[ ]` |
| `[ ]` | Fault introduced | `[ ]` | `[ ]` |
| `[ ]` | User impact observed | `[ ]` | `[ ]` |
| `[ ]` | Alarm triggered/received | `[ ]` | `[ ]` |
| `[ ]` | Initial update sent (simulated) | `[ ]` | `[ ]` |
| `[ ]` | Recovery action | `[ ]` | `[ ]` |
| `[ ]` | User journey restored | `[ ]` | `[ ]` |

## Assessment

- Confirmed user impact and duration: `[ ]`
- Detection signal versus observed impact: `[ ]`
- Alarm delay (calculate from actual timestamps only): `[ ]`
- Diagnostic evidence: `[ ]`
- Root cause: `[confirmed with evidence / hypothesis / undetermined]`
- What worked: `[ ]`
- Gaps, false positives, or missing data: `[ ]`
- Recovery verification and recurrence check: `[ ]`
- Cost and cleanup verification: `[ ]`

## Problem record and follow-ups

| Action | Why it matters | Owner | Due date | Verification |
| --- | --- | --- | --- | --- |
| `[ ]` | `[ ]` | `[ ]` | `[ ]` | `[ ]` |

## Portfolio-safe summary

Write a short account of the simulated workload, verified impact, alerting behavior, decisions, recovery, and one improvement. Label all synthetic or simulated elements explicitly; never claim a real customer incident or official AWS IDR participation.
