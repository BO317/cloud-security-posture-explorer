# Parameter Store configuration

Implemented locally and tested offline; not deployed or verified against AWS.
This replaces the earlier design: Parameter Store is primary, and the existing
systemd environment file is an automatic, whole-configuration fallback.

## Configuration contract

Create a **String** parameter named exactly:
`/cloud-security-posture-explorer/lab/config`.
Use the [JSON example](../app/deploy/parameter-store-config.example.json):

```json
{
  "region": "us-east-1",
  "allowed_buckets": ["replace-with-your-lab-bucket"],
  "allowed_security_groups": ["sg-0123456789abcdef0"],
  "allowed_instance_name_tags": ["cloud-security-posture-explorer"]
}
```

All four keys are required; unknown or duplicate keys are rejected. Each allowlist
must be an array of strings. Existing region/identifier syntax, whitespace
trimming, deduplication, and 100-distinct-identifiers-per-list limits apply.
Individual lists may be empty, but all three empty is invalid. Wildcards, ARNs,
comma-separated strings inside JSON arrays, nulls, and wrong types are invalid.
`region` selects the posture API region. No credentials belong in this document.
SecureString is not supported by this implementation; decryption is disabled.

Keep `/etc/cloud-security-posture.env` and the existing systemd `EnvironmentFile`
directive. systemd loads the file into the process; Python does not parse it.

| Environment variable | Purpose |
| --- | --- |
| `AWS_REGION` | Bootstrap SSM region and local fallback posture region |
| `AWS_DEFAULT_REGION` | Used only when AWS_REGION is absent |
| `SSM_REGION` | Optional override for SSM's region if different from the local region |
| `ALLOWED_BUCKETS` | Existing comma-separated fallback bucket list |
| `ALLOWED_SECURITY_GROUPS` | Existing comma-separated fallback group list |
| `ALLOWED_INSTANCE_NAME_TAGS` | Comma-separated literal EC2 Name values for fallback |

At least one valid bootstrap region must be supplied locally: the application
cannot read the remote region before it knows which SSM endpoint to contact.

The old `allowed_instances` JSON key is rejected, and a nonempty legacy
`ALLOWED_INSTANCES` invalidates the local fallback. Migrate both sources with
the code release; see [Name tag migration](DEPLOYMENT_EC2.md#name-tag-migration).
Name values follow the restricted literal syntax documented in [app/README.md](../app/README.md).

## Loading and failure behavior

Each accepted dashboard scan reads the latest parameter using boto3
`get_parameter(Name=..., WithDecryption=False)` and the default credential chain.
On EC2, retain the existing instance role and unit credential isolation; no keys
are added. The returned name, type, numeric version, JSON, and scope are validated
before any posture reads. One immutable Settings snapshot is used for that scan.

Missing parameter, AccessDenied, timeout, other SDK/API errors, malformed response,
invalid JSON, or invalid configuration trigger validation of the complete local
environment configuration. Sources are never merged. A valid fallback runs real
posture checks; it does not supply cached or synthetic PASS results. An invalid
fallback produces the existing HTTP 503 configuration UNKNOWN page with no posture
calls. Unexpected programming errors propagate to the existing safe 503 boundary.

The dashboard layout and check semantics are unchanged. `/healthz` remains HTTP
200 with `{"liveness":"ok"}` and never loads configuration or calls AWS. SSM uses
3-second connect and 5-second read timeouts with two total attempts; these are not
a whole-request deadline. There is no cache, version pin, or background refresh.
New SSM values take effect on the next scan; local environment edits require a
service restart. Keep fallback scope current: during an outage it may differ from
the primary scope. Review warning logs to distinguish that situation.

## IAM and networking

Add only `ssm:GetParameter` to the existing workload role, scoped to:

```text
arn:aws:ssm:<REGION>:<ACCOUNT_ID>:parameter/cloud-security-posture-explorer/lab/config
```

Substitute the SSM region, account, and partition as appropriate. Retain existing
S3/EC2 reads; changing the configured scope does not grant the role new access.
No PutParameter, path listing, history, or KMS permission is needed for this String
parameter. Publishing configuration belongs to a separate operator identity.
The application needs DNS/HTTPS access to the regional SSM endpoint and access to
IMDS for its role. Direct SDK reads do not require installing SSM Agent.
See [GetParameter](https://docs.aws.amazon.com/systems-manager/latest/APIReference/API_GetParameter.html)
and [SSM IAM resources](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ssm.html).

## Audit, rollout, and rollback

`configuration_load` logs the scan correlation ID, UTC load time, selected source,
returned version on success, safe fallback reason, outcome, and AWS request ID
when available. Successful fallback is WARNING; both sources invalid is ERROR.
Posture observation events carry the same scan ID and selected source/version.
Parameter values, resource identifiers, credentials, and raw errors are omitted.
No configuration-dump endpoint is added.

1. Record the current code revision and preserve the working unit/environment.
2. Run the offline suite below and deploy the reviewed code using the existing
   [EC2 procedure](DEPLOYMENT_EC2.md).
3. As the operator, publish the JSON String parameter and grant the exact read
   permission. This code change creates or modifies no AWS resources.
4. Retain a valid fallback environment, set the bootstrap region, and restart
   `cloud-security-posture.service` after deployment.
5. Open the dashboard through authenticated access. Check `journalctl -u
   cloud-security-posture.service` for `source: ssm`, a version, and matching scan
   IDs. Verify intended resources and fresh observations, not just health.
6. To roll back code, restore the known-good revision and restart with the retained
   environment. To undo a config edit, an authorized operator can republish the
   reviewed prior JSON as a new parameter version. There is no source-mode switch.

## Tests

```sh
.venv/bin/python -m unittest discover -s app/tests -v
```

On Windows use `.\.venv\Scripts\python.exe`. Tests cover valid SDK responses,
missing parameter, denied access, timeout, malformed JSON/responses, duplicate keys,
invalid scope, complete fallback, invalid fallback, redacted correlated logging,
and health isolation. They use mocks/Stubber with network connections blocked for
configuration/provider tests; no AWS credentials or live resources are required.
