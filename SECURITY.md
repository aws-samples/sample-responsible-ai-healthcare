# Security Policy

## Disclaimer

This project is sample/educational code. It is **not intended for production use** without the
additional hardening described under [Production Hardening
Recommendations](#production-hardening-recommendations). It is also **not a medical device and not
medical advice** — every diagnostic output is a recommendation for a licensed physician to review,
and all patient data in the repository is synthetic. Do not input real protected health information
(PHI).

## Reporting Vulnerabilities

If you discover a potential security issue in this project, notify AWS/Amazon Security via the
[vulnerability reporting page](https://aws.amazon.com/security/vulnerability-reporting/) or email
aws-security@amazon.com. Do **not** open a public GitHub issue for a security vulnerability.

## AWS Services Used

| Service | What it does here |
|---------|-------------------|
| Amazon S3 | Patient data lake, bias reports, Cedar policy documents, CloudTrail and server access log destinations, cross-Region replicas |
| AWS Lambda | S3-triggered preprocessing and Amazon Bedrock inference (`src/lambda/preprocessing/`), in private subnets |
| Amazon SQS | Dead-letter queue for failed asynchronous Lambda invocations |
| Amazon Bedrock | Foundation model inference (Anthropic Claude Sonnet 4.6 via a cross-Region inference profile) |
| Amazon Bedrock Guardrails | Content filters (including PROMPT_ATTACK) on every inference, plus an Automated Reasoning policy on the agent path. No denied topics are configured |
| Amazon Bedrock Knowledge Bases | Clinical guideline grounding (role and source objects only; the Knowledge Base itself is created outside Terraform) |
| Amazon Bedrock AgentCore | Agent runtime (Strands SDK), and the AgentCore Gateway with a policy engine whose Cedar policies apply guardrails to every request; both deployed by the AgentCore CDK app under `src/agent/agentcore/` |
| Amazon SageMaker AI | A Processing job that computes bias metrics (CI, DPL, AD) and SHAP attributions, and an optional MLflow App for experiment tracking. SageMaker Clarify is no longer open to new customers; this replaces it per [AWS's guidance](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html) |
| Amazon Bedrock Evaluations | LLM-as-a-judge evaluation of the deployed agent (bring-your-own inference responses), encrypted with the data lake key |
| Amazon CloudWatch | Metrics, alarms, dashboard, and KMS-encrypted log groups |
| Amazon SNS | Alarm notifications, CloudTrail log-delivery notifications, and the audit-log tamper alert target. Fan-out is verified live: both the alarm publisher and the EventBridge publisher reach the confirmed subscriber — see the encryption note on the alerts topic below |
| Amazon EventBridge | Consumes S3 object events from the in-Region log buckets; one rule matches tampering with delivered audit logs. Detection and notification are both verified live |
| AWS CloudTrail | Management and S3 data-event audit trail with log file validation |
| AWS KMS | Customer managed keys for the patient data lake, CloudWatch Logs, CloudTrail (also the audit bucket default), the alerts SNS topic, the DLQ, the Processing job's volume and outputs, the MLflow App's artifacts, AWS Network Firewall and the replica Region |
| AWS Identity and Access Management (IAM) | Least-privilege service roles and the four demo RBAC roles (physician, nurse, admin, auditor) |
| Amazon VPC | Private subnets with no internet or NAT gateway, security groups, flow logs, and interface/gateway endpoints for Amazon S3, Bedrock Runtime, CloudWatch metrics and AWS X-Ray |
| AWS Network Firewall | Default-deny egress inspection on the private subnets' default route, with alert logs |
| IAM Access Analyzer | Account-level external access analyzer |
| AWS Signer | Code-signing profile for the Lambda deployment package |

## Prerequisites and Permissions

To deploy this solution you need:

- An AWS account with permissions to create S3 buckets and bucket policies, IAM roles and policies,
  KMS keys and key policies, Lambda functions, SQS queues, VPCs (subnets, route
  tables, security groups, VPC endpoints, flow logs), Network Firewall firewalls and policies, IAM Access Analyzer analyzers, CloudWatch log groups/alarms/dashboards, SNS
  topics, CloudTrail trails, SageMaker AI processing jobs and MLflow Apps, Bedrock guardrails and evaluation jobs, and AWS
  Signer signing profiles — in **two** Regions (`aws_region` and `replica_region`).
- Amazon Bedrock model access enabled for `anthropic.claude-sonnet-4-6` in the target Region.
- Terraform >= 1.5, Python >= 3.11, Node.js >= 20, uv, and the `agentcore` CLI 0.13.1 for the agent.
- Credentials that may assume the four demo RBAC roles (they trust the account root with an
  `sts:ExternalId`), which `scripts/06_test_access_control.py` exercises.

Deployment is intentionally privileged. Do not reuse the deploying principal as a runtime
principal.

## Known Security Considerations

These are accepted trade-offs in sample code. Each one is a deliberate decision, not an oversight.

| Item | Category | Rationale |
|------|----------|-----------|
| `kms:*` for the account root in all nine customer managed key policies (`infra/cloudwatch.tf`, `infra/replication.tf`, `infra/s3.tf`, `infra/security.tf`, `infra/server_access_logs.tf`, `infra/vpc.tf`) | Security debt | This is the AWS-documented default key policy statement that keeps a key manageable and prevents lockout. Production should replace it with a named key-administrator role. |
| Broad `except Exception` handlers (16 in tracked Python) | Security debt | The demo scripts and the Lambda favour continuing with a logged failure over aborting a walkthrough. The safety-critical paths are the exception: output validation and the confidence threshold **fail closed** and are tested. Production should narrow these to the specific client errors. |
| `Resource = "*"` on actions IAM can match only with `*` (`xray:Put*`/`xray:Get*Sampling*`, `logs:DescribeLogGroups`, `ec2:Describe*` have no resource types in the AWS Service Authorization Reference; `cloudwatch:PutMetricData`'s only resource type is a CloudWatch dataset, which this sample does not use) | Security debt | IAM cannot express a narrower resource for these calls. `PutMetricData` is constrained by a `cloudwatch:namespace` condition; the rest are read-only or telemetry-only. |
| KMS key deletion window of 7 days on all nine keys | Security debt | The minimum window, chosen so a demo teardown does not leave keys pending deletion for a month. Production should use 30 days. |
| The 12 Terraform-managed IAM roles carry no `description` | Security debt | Cosmetic for a sample; role intent is documented in the surrounding Terraform comments. Production should set a description on every role. |
| Two dedicated KMS keys exist only to encrypt the S3 server access log destination buckets (`infra/server_access_logs.tf`) | Deliberate design | The AWS managed `aws/s3` key cannot be granted to the logging service principal because its policy is not editable, and reusing the CloudTrail or replica keys would let a log producer use a key that protects a different audit trail or patient data. One key per Region per purpose is the narrower blast radius. |
| cdk-nag `AwsSolutions-IAM5` exceptions in `src/agent/agentcore/cdk/lib/runtime-execution-role.ts` and `src/agent/agentcore/cdk/lib/diagnostic-gateway.ts` | Documented exception | Together with the minimatch row below, the **only** scanner exceptions in the repository. See below. |
| CVE-2026-118764 / GHSA-g3hv-75wg-m249 (medium) in `minimatch` 10.2.5, bundled inside `aws-cdk-lib` (`src/agent/agentcore/cdk/package-lock.json`) | Documented exception, time-boxed (expires 2027-01-31) | Suppressed for ASH's grype (`.ash.yaml`) and Trivy (`.trivyignore`) only. **Not reachable:** the advisory needs an attacker-supplied glob pattern *and* `optimizationLevel >= 2`; `aws-cdk-lib` never sets `optimizationLevel` (minimatch defaults to 1), and the CDK app runs only at `cdk synth` on the deploying machine with this repository's own patterns, never in AWS. **Not fixable today:** the dependency is bundled (`inBundle`), so npm `overrides` cannot replace it, and the fix (11.0.1, released 2026-10-27) is in no `aws-cdk-lib` release yet (2.275.0 still requires `^10.2.6`). Both suppressions expire, so the scan fails again if it is still present. Remove them when `aws-cdk-lib` bundles minimatch >= 11.0.1. Approved by the repository owner on 2026-10-31. |
| `bandit` `B101` (`assert` used) in `tests/` | Expected false positive | `assert` is how pytest expresses an assertion. `tests/` is a path exclusion in `tool.bandit.exclude_dirs`, and ruff ignores `S101` there via `tool.ruff.lint.per-file-ignores` — no bandit test ID is skipped, so `B101` still reports everywhere else. `.ash.yaml` points ASH's bandit scanner at the same config, so `ash --source-dir .` and `make security-scan` scan the same files and both report zero. |
| The four demo RBAC roles trust the account root with a static `sts:ExternalId` | Security debt | Keeps the access-control demo runnable from one set of credentials. Production should trust specific federated principals and drop the shared external id. |
| The AWS Network Firewall has `delete_protection = true` (`infra/vpc.tf`) | Deliberate design | Required by checkov `CKV_AWS_344`. A `terraform destroy` fails on the firewall until delete protection is turned off; see [docs/cleanup.md](docs/cleanup.md). |
| Service-created CloudWatch log groups are unencrypted and never expire: `/aws/lambda/rai-healthcare-demo-preprocessing` and `/aws/bedrock-agentcore/runtimes/<runtimeId>-DEFAULT` both report `kmsKeyId: null` with no retention | Security debt | AWS Lambda and Bedrock AgentCore create these on first invocation, so Terraform never owns them — unlike the eight log groups it does declare, which are all CMK-encrypted with 365-day retention. Neither carries patient data: the Lambda logs a `request_id` and a SHA-256 object digest, never a key, and the agent runtime records no prompt or response content (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false`, Strands span redaction through `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_unredacted_attributes=`, and no stdout echo of the model stream; `make verify-live` fails if a request's patient text appears in the log group), but production should declare both as `aws_cloudwatch_log_group` with `kms_key_id` and `retention_in_days` so the service cannot pre-create them unencrypted. |
| SageMaker AI writes the bias Processing job's output to the service-created `/aws/sagemaker/ProcessingJobs` log group; earlier versions of this sample also left per-endpoint `/aws/sagemaker/Endpoints/*` log groups behind | Security debt | Created by SageMaker AI, not by this project, so Terraform does not set their encryption or retention. They hold job stdout for a synthetic dataset. Delete them during teardown or declare them with encryption and retention in production. This version creates no SageMaker endpoints. |
| The AgentCore CDK app reads a bare `GUARDRAIL_ID`, and this repository's `.env` sets that same variable to the Lambda's content-filter guardrail | Security debt | `source .env && cdk deploy` therefore points the agent at a guardrail with no Automated Reasoning policy, silently removing the agent's logical-consistency check. Deploys must export the agent's guardrail id explicitly. Production should read the value from a Terraform output or use a distinct variable name per component. |
| X-Ray tracing depends on account-level CloudWatch Transaction Search | Security debt | The account sends trace segments to CloudWatch Logs (`aws/spans`). That log group is reserved for AWS and is created only when Transaction Search is enabled; while it was missing, X-Ray rejected every segment ("The specified log group does not exist") and no traces were recorded. Re-enabling Transaction Search created it, and `make verify-live` now fails if Lambda traces stop arriving. The agent runtime's traces (`raihealthdiag_raihealthdiag.DEFAULT`) arrive the same way, through `xray:PutTraceSegments` on its role. |
| The AgentCore runtime uses `networkMode: PUBLIC` (`src/agent/agentcore/agentcore.json`) | Security debt | The AgentCore runtime runs in an AWS-managed network, not in this VPC, so the diagnostic path does not share the ingestion path's network isolation. Production should evaluate a [VPC-attached runtime configuration](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html); see [Production recommendations](README.md#production-recommendations). |
| The runtime can still be invoked directly, bypassing the AgentCore Gateway's guardrail policies | Security debt | Any principal allowed `bedrock-agentcore:InvokeAgentRuntime` on the runtime — the deploying principal and the demo physician and nurse roles (`infra/iam.tf`) — can call it without going through the gateway. Production should restrict the runtime to the gateway's execution role with a resource-based policy; see [Production recommendations](README.md#production-recommendations). |
| The alerts SNS topic **must** stay on its own customer managed key (`aws_kms_key.sns_alerts` in `infra/cloudwatch.tf`); the AWS managed key `alias/aws/sns` silently breaks every alert | Resolved defect, recorded because the constraint is non-obvious | The AWS managed key's policy grants `kms:GenerateDataKey*`/`kms:Decrypt` only to principals in this account calling via SNS (`kms:CallerAccount` + `kms:ViaService`), and it cannot be edited to add a service principal, so an alarm's or an EventBridge rule's publish is refused at the KMS step. This repository shipped that way and it was **measured, not inferred**: `AWS/SNS` `NumberOfMessagesPublished` had **zero datapoints** over a four-day window in which the low-confidence alarm entered ALARM repeatedly with one confirmed subscriber, the alarm's own action history recorded eight consecutive *"CloudWatch Alarms does not have authorization to access the SNS topic encryption key"* failures, and the audit-log tamper rule recorded `FailedInvocations` equal to `MatchedEvents` on every datapoint. **"0 failed" was not health** — a publish refused at the KMS step never enters SNS, so it is counted in neither `NumberOfMessagesPublished` nor `NumberOfNotificationsFailed`, which is why it went unnoticed. The customer managed key grants `cloudwatch.amazonaws.com`, `events.amazonaws.com` and `s3.amazonaws.com` (data lake deletion notifications) those two actions under an `aws:SourceAccount` condition, and delivery is now verified live from both publishers (see the Logging item under Production Hardening Recommendations). Do not revert the topic to `alias/aws/sns`: the scanners will stay green and the alerts will stop. |
| Lambda code signing is advisory and the deployed archive is unsigned (`infra/lambda.tf`) | Security debt | `aws_lambda_code_signing_config.preprocessing` sets `untrusted_artifact_on_deployment = "Warn"`, which logs rather than rejects, and the function deploys from `data.archive_file.lambda_zip` — an archive Terraform builds locally and never submits to AWS Signer. The `aws_signer_signing_profile.lambda` profile therefore exists but enforces nothing. `Warn` is chosen so a demo `terraform apply` is not blocked on an unsigned zip. Production should sign the artifact with the profile and set `untrusted_artifact_on_deployment = "Enforce"`. |
| The Knowledge Base and its OpenSearch Serverless collection are created outside Terraform | Security debt | Terraform owns only the Knowledge Base's IAM role and source objects (see Prerequisites), so `terraform destroy` cannot remove them. `scripts/cleanup.py` (run by `make full-cleanup`) deletes them in the safe order: data sources switched to `dataDeletionPolicy` RETAIN, the Knowledge Base, then its collection. Deleting the collection first leaves the Knowledge Base in `DELETE_UNSUCCESSFUL`; see [docs/cleanup.md](docs/cleanup.md). |
| The replica-Region (us-west-2) log buckets have no tamper alert | Security debt | EventBridge rules are regional, so `aws_cloudwatch_event_rule.audit_log_tamper` sees only the us-east-1 log buckets. The multi-Region trail still records changes to the replicas, but nothing alerts on them. Production should add the same rule, an SNS topic and a KMS key in the replica Region. |
| The `cloudtrail_notifications` topic has no subscriber | Security debt | CloudTrail publishes one message per delivered log file, and S3 publishes audit-bucket deletions to it. That volume suits an automated consumer (a queue or a function), not email, so none is subscribed. Audit-bucket deletions also reach the subscribed alerts topic through the `audit_log_tamper` rule, and stopping or changing the trail through `trail_tamper`. |
| An earlier log bucket can survive `terraform destroy` if it is not in Terraform state | Security debt | Buckets created by a previous deployment of this sample, or by hand, are not in state and so are never destroyed. They hold log objects only, no patient data, and cost storage. [docs/cleanup.md](docs/cleanup.md) gives the list-and-compare commands to find and remove them deliberately. |
| The Lambda path has no logical-consistency check: its guardrail carries content filters but no Automated Reasoning policy | Security debt | Two guardrails exist by design. The Automated Reasoning policy is attached to the guardrail bound to the **AgentCore runtime only**; the Lambda's `GUARDRAIL_ID` points at the content-filter guardrail. So the agent path gets a logical-consistency check on every inference and the Lambda path does not — both still get content filters. This is distinct from the bare-`GUARDRAIL_ID` row above, which is about the two ids sharing one variable name rather than about what each guardrail enforces. Production should attach a single guardrail carrying both the content filters and the Automated Reasoning policy to every inference path. |

### The scanner exceptions: cdk-nag `AwsSolutions-IAM5`

`AwsSolutions-IAM5` ("IAM entity contains wildcard permissions") is suppressed in exactly two
files, as cdk-nag metadata on managed policy resources: two in
`src/agent/agentcore/cdk/lib/runtime-execution-role.ts` (items 1 and 2 below) and two in
`src/agent/agentcore/cdk/lib/diagnostic-gateway.ts` (items 3 and 4). cfn-nag reports items 2 and 3
as W13, which `scripts/scan_summary.py` lists by policy name. Apart from these and the time-boxed
minimatch suppression above (one rule id, in `.ash.yaml` for grype and `.trivyignore` for Trivy),
nothing in this repository carries a scanner exception: no inline marker comments and no other
rule exclusions.

Two things do narrow what a given invocation reports, and both are stated here rather than implied:

- **A severity gate in CI only.** `.github/workflows/ci.yml` and the `pre-commit` hook run
  `bandit` at medium-and-above (`-ll`), so the 4 low-severity `B404`/`B603` findings that the
  teardown script used to produce would not have failed CI. `make security-scan` applies no
  threshold. `ash --source-dir .` reports every severity but fails only at medium and above
  (`severity_threshold: MEDIUM` in `.ash.yaml`). Both currently report nothing in tracked source.
- **Two path exclusions**, neither of which hides a finding:
  - **Generated and third-party trees** — `node_modules`, `.cache`, `.terraform`, `cdk.out` and
    `.venv` (plus the never-tracked `.agents/` notes in `.ash.yaml`). None is tracked by git.
  - **`tests/`**, excluded from `bandit` only, by the config in `pyproject.toml`
    (`tool.bandit.exclude_dirs`). Both `make security-scan` and ASH read that config
    (`.ash.yaml` sets `config_file: pyproject.toml`). Ruff ignores `S101` there
    (`tool.ruff.lint.per-file-ignores`). Both cover one finding class: `assert` used (`B101` /
    `S101`), which is how pytest expresses an assertion, so every hit in `tests/` is a false
    positive by construction. This is a *path* exclusion, not a rule skip: no bandit test id is
    disabled, so `B101` still reports anywhere outside `tests/`. The alternative — scanning
    `tests/` and disabling the `B101` test id globally — would be a rule suppression and is
    deliberately not used.

ASH's other scanners (semgrep, opengrep, detect-secrets, checkov and the rest) still scan `tests/`.
Only bandit skips it, and that skip removes nothing but the `B101` class. Before `.ash.yaml`
pointed bandit at this config, ASH ran bandit over `tests/` and reported 242 low-severity `B101`
hits, all in `tests/` and none anywhere else.

1. **The runtime log-stream wildcard** — `logs:CreateLogStream` and `logs:PutLogEvents` on
   `…:log-group:/aws/bedrock-agentcore/runtimes/<runtimeId>-DEFAULT:log-stream:*[runtime-logs*`.
   AgentCore generates the stdout/stderr stream name itself. The live name observed on this
   project's runtime is `2026/10/12/[runtime-logs-<uuid>]<uuid>` — a `YYYY/MM/DD/` date prefix plus
   two runtime-generated identifiers, one inside the bracket and one after it (CloudTrail eventID
   `fbd2526f-4c98-467f-b18a-8d937a7baa5e`). The date changes daily and the identifiers are not
   known before invocation, so no literal ARN can match and removing the wildcard stops the runtime
   from logging. The two other streams the runtime writes, `otel-rt-logs` and `spans`, are granted
   as exact ARNs. What bounds it: the resource is still prefixed with this runtime's exact
   log-group ARN, never `log-group:*`, which makes it narrower than the execution role [AWS
   documents for
   AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html).
2. **`Resource::*` on the telemetry policy**: one action, `xray:PutTraceSegments`. X-Ray actions
   have no resource types in the [AWS Service Authorization
   Reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_xray.html), so
   IAM can only match it with `*` (cfn-nag reports the same policy as W13). The policy was cut down
   from seven actions, each change measured live: the agent's metrics now go out as CloudWatch
   embedded metric format log lines, so `cloudwatch:PutMetricData` is gone, and IAM access advisor
   showed `logs:DescribeLogGroups` and the X-Ray sampling calls were never used. Removing X-Ray
   entirely was also tried: the agent's traces stopped, so `PutTraceSegments` stays.
3. **`Resource::*` on the gateway's guardrail checks policy**: one action,
   `bedrock:InvokeGuardrailChecks`, which Policy in AgentCore calls with the gateway execution
   role's forward access session to evaluate guardrails in policies. The action has no resource
   types in the [AWS Service Authorization
   Reference](https://servicereference.us-east-1.amazonaws.com/v1/bedrock/bedrock.json), so IAM can
   only match it with `*`. The checks evaluate inline content and use no guardrail resource.
4. **The gateway-name pattern on policy evaluation** — `bedrock-agentcore:AuthorizeAction` and
   `PartiallyAuthorizeActions` on `…:gateway/raihealthdiag-diagnostic-*`. Gateway creation
   authorizes these on the new gateway's own ARN, whose ID is generated during creation, so the
   exact ARN cannot be granted beforehand ([policy
   permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html)).
   The pattern is limited to this project's gateway name in this account and Region, and the same
   pattern scopes the role's trust policy.

Both suppressions are granular: each names the exact resource string it applies to, so a new
wildcard anywhere else in the stack still fails cdk-nag.

## Production Hardening Recommendations

Specific to this project's architecture:

- **IAM** — Replace the `kms:*` root statement in the nine key policies with a named
  key-administrator role. Give every role a `description`. Replace the account-root trust and
  static `sts:ExternalId` on the physician/nurse/admin/auditor roles with federated principals.
  Set `var.agentcore_runtime_id` to the exact runtime id after the first `make deploy-agent` so the
  clinical roles are scoped to one runtime instead of the project prefix.
- **Encryption** — Raise `deletion_window_in_days` from 7 to 30 on all nine KMS keys. Every SNS
  topic here already uses a customer managed key, which for the alerts topic is a functional
  requirement rather than hardening (see the row above). Every S3 bucket, including both server
  access log destination buckets, uses SSE-KMS; the two log destinations were migrated from SSE-S3
  after measuring that delivery and readback both survive it (see
  [docs/responsible-ai.md](docs/responsible-ai.md), "Known scanner findings").
- **Networking** — The private subnets have no internet path: no internet or NAT gateway, Lambda
  egress limited to the endpoint security group and the S3 prefix list, and a default-deny AWS
  Network Firewall on the default route. Deploy the firewall and the interface endpoints in a
  second Availability Zone for high availability.
- **Logging** — CloudTrail (management plus S3 data events), VPC flow logs, S3 server access
  logging and KMS-encrypted CloudWatch Logs with 365-day retention are already enabled, and one
  EventBridge rule alerts on tampering with delivered log objects (a second, `trail_tamper`, alerts
  when the trail is stopped, deleted or reconfigured). **Detection and fan-out are both
  proven live.** A scratch object was tagged and deleted in an audit bucket and then permanently
  removed; the rule matched all four tamper events (`AWS/Events` `MatchedEvents` = 4) while
  correctly ignoring the object's creation, and this time every invocation succeeded —
  `FailedInvocations` did not increment at all, against a pre-fix baseline where it equalled
  `MatchedEvents` on every datapoint. On the same pass the low-confidence alarm was driven into
  ALARM by three real ambiguous-case inferences (confidence 22%, 28%, 21%) and its action history
  flipped from eight consecutive encryption-key failures to `Succeeded`. `AWS/SNS` for the alerts
  topic shows **5 published, 5 delivered, 0 failed** across the two publishers, from a baseline of
  no published datapoints at all. Remaining work: extend the rule to the replica-Region buckets (an
  EventBridge rule is regional), add an organization trail, and ship findings to AWS Security Hub.
- **Change control for safety settings** — The confidence floor, the model allowlist and the
  guardrail requirement are enforced in code, but the values around them (`CONFIDENCE_THRESHOLD`,
  `GUARDRAIL_ID`, `BEDROCK_MODEL_ID`) live in the Lambda and AgentCore runtime configuration.
  Restrict `lambda:UpdateFunctionConfiguration`, `lambda:UpdateFunctionCode` and
  `bedrock-agentcore:UpdateAgentRuntime` on these resources to the CI/CD deployment role (an SCP or a
  permissions boundary), so a configuration change goes through the pipeline and its review. Limit
  `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` to the assessed model with an
  SCP as well; the project roles already grant only that model.
- **Credential handling** — The sample stores no application credential of any kind. The only
  sensitive configuration value is `alarm_email`, a Terraform variable marked `sensitive`, and
  `.env` is gitignored. Move any configuration you add to AWS Secrets Manager or AWS Systems
  Manager Parameter Store rather than to environment variables.
- **Deletion protection** — Enable S3 Object Lock on the audit bucket and the access log buckets,
  turn off `force_destroy` (it is on only because `var.environment == "demo"`), and enable
  termination protection on the CDK stack.
- **Data** — Replace the synthetic dataset with a real data path that is classified, access
  reviewed and covered by a HIPAA Business Associate Addendum before any PHI is involved.

## Resource Cleanup

`make full-cleanup` runs the whole teardown in the correct order — application runtime before
infrastructure. It is equivalent to:

1. `python scripts/cleanup.py --phase pre-clean` — orphaned SageMaker endpoints, configs and
   models, running bias and evaluation jobs (stopped), the MLflow App, and every object version in
   the demo buckets.
2. `make destroy-agent` — `cdk destroy` for the AgentCore runtime stack.
3. `make destroy` — `terraform destroy`. If a deployment from an earlier version stalls on SageMaker
   Studio EFS mount targets, run `python scripts/cleanup.py --phase unblock --force` and destroy again.
4. `python scripts/cleanup.py --phase post-clean` — Bedrock guardrails, the Knowledge Base and its
   data sources, finished Bedrock evaluation jobs, and the demo CloudWatch log groups.
5. `python scripts/cleanup.py --phase verify` — read-only report of anything still standing.

Two things do not disappear immediately: KMS keys enter a 7-day pending-deletion window, and the
replica-Region buckets are only emptied when `var.environment == "demo"` sets `force_destroy`.
Otherwise empty each bucket, including all object versions, before deleting it.

`scripts/cleanup.py` only ever deletes resources it can positively attribute to this project — by
exact name or name prefix from the project configuration, or by the SageMaker domain tag — and never shells out to
another program. Run `--phase verify` first if you share the account. See
[docs/cleanup.md](docs/cleanup.md).

## Dependencies

Every dependency carries both a lower and an upper bound so a new major release cannot be pulled in
unreviewed.

| Dependency | Version constraint | Notes |
|------------|--------------------|-------|
| boto3 | `>=1.36.0,<2.0` | AWS SDK for Python |
| pandas / numpy | `>=2.0.0,<3.0` / `>=1.24.0,<3.0` | Dataset handling for the bias analysis |
| scikit-learn | `>=1.5.0,<2.0` | Diagnostic model for SHAP (the Processing job uses the container's scikit-learn 1.9) |
| shap | `>=0.46.0,<0.55.0` | SHAP explainability (`src/bias_detection/requirements.txt`, also installed by the Processing job) |
| mlflow-skinny | `>=3.10.1,<3.11.0` | MLflow client, matched to the MLflow App's MLflow 3.10 server |
| sagemaker-mlflow | `>=0.5.0,<0.6.0` | AWS MLflow plugin (SigV4 auth, MLflow App ARN as tracking URI) |
| starlette | `>=1.3.1,<2.0` | Transitive (mlflow-skinny → fastapi); floor past CVE-2025-54121 and later advisories |
| bedrock-agentcore | `>=1.18.1,<2.0.0` | AgentCore runtime SDK (agent only) |
| strands-agents | `>=1.13.0,<2.0.0` | Strands Agents SDK (agent only) |
| botocore[crt] | `>=1.35.0,<2.0.0` | Agent runtime AWS calls |
| mcp | `>=1.30.0,<2.0.0` | Model Context Protocol client (agent only) |
| aws-opentelemetry-distro | `<1.0.0` | ADOT auto-instrumentation for the runtime |
| aws-cdk-lib | `2.273.0` (pinned) | Pinned past GHSA-vcrf-j523-4mrf |
| cdk-nag | `2.38.2` (pinned) | Policy checks at synth time |
| @aws/agentcore-cdk | `0.1.0-alpha.33` (pinned) | AgentCore L3 constructs; alpha, so pinned exactly |
| aws-cdk | `2.1145.0` (pinned) | CDK CLI |

Dependency scanning runs as part of `make validate` (grype and syft via the Automated Security
Helper, plus `npm audit` for the CDK app).
