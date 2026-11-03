# Responsible AI Documentation

## Problem Statement

Healthcare AI systems that assist with clinical diagnosis carry significant risk. Biased models can produce disparate outcomes across demographic groups, opaque predictions prevent clinicians from understanding the reasoning behind recommendations, and unchecked AI outputs could lead to patient harm.

This demo addresses these risks by implementing a comprehensive Responsible AI framework that demonstrates how AWS services can be composed to build healthcare AI systems that are fair, explainable, safe, and auditable.

## Responsible AI Dimensions

The following table maps each RAI dimension to the specific implementation in this project:

| Dimension | How Addressed | AWS Service | Implementation Detail |
|-----------|--------------|-------------|----------------------|
| **Fairness** | Bias detection across protected attributes (age, gender, ethnicity, language, location) in the data and in the LLM's answers | SageMaker AI Processing, Amazon Bedrock Evaluations | A Processing job computes the standardized bias metrics class imbalance (CI), difference in proportions of labels (DPL) and accuracy difference (AD) per facet; a Bedrock Evaluations job scores the deployed agent for stereotyping and compares scores across single-attribute demographic variants of each case |
| **Explainability** | SHAP feature attribution for a diagnostic model | SageMaker AI Processing (SHAP library) | `shap.TreeExplainer` attributes a gradient-boosted classifier's predictions to the clinical features, globally and per patient; a Bedrock narrative explains the computed values in plain language |
| **Privacy & Security** | Data encryption, network isolation, least-privilege access | VPC, KMS, IAM, S3 | Private subnets with no internet path (no internet or NAT gateway; a default-deny AWS Network Firewall on the default route), VPC endpoints for Amazon S3, Bedrock Runtime, CloudWatch and X-Ray, role-based access, explicit deny policies |
| **Safety** | Automated reasoning guardrails validate clinical outputs | Bedrock Automated Reasoning | Formal logic rules check contraindications, dosage bounds, and clinical safety constraints before output delivery |
| **Controllability** | Human-in-the-loop override workflow | CloudWatch Alarms, SNS, IAM RBAC | Low-confidence outputs trigger mandatory physician review; physicians can override AI with audit trail (Cedar policies are illustrative only — not runtime-enforced) |
| **Veracity & Robustness** | Confidence scoring with drift detection | CloudWatch Metrics, Alarms | Continuous accuracy monitoring with alarms at 90% threshold; 7-day rolling accuracy tracked |
| **Governance** | Role-based access enforced by IAM (resource-level); Cedar policies illustrate per-action RBAC but are not enforced in this demo; comprehensive audit logging | IAM, Cedar (illustrative), CloudWatch Logs | Four distinct roles (Physician, Nurse, Admin, Auditor) with separation of duties; 365-day audit retention |
| **Transparency** | Full observability stack with audit trails | CloudWatch Dashboard, Log Groups, SageMaker AI MLflow App | 9-widget dashboard, policy decision logs, override records, model allowlist; bias, SHAP and evaluation runs tracked in an MLflow App |

## Human-in-the-Loop Flow

The system enforces mandatory human oversight for AI-generated clinical recommendations:

```
Patient Data Input
        |
        v
+------------------+
| AI Diagnostic    |
| (Bedrock Claude) |
+--------+---------+
         |
         v
+------------------+
| Automated        |
| Reasoning Check  |---> INVALID: Block output, alert physician
+--------+---------+
         |
         | VALID
         v
+------------------+
| Confidence Score |
| Assessment       |
+--------+---------+
         |
         +-----------> >= 70%: Present to physician with explanation
         |
         +-----------> < 70%: ALERT triggered
                              |
                              v
                    +------------------+
                    | SNS Notification |
                    | to Physician     |
                    +--------+---------+
                             |
                             v
                    +------------------+
                    | Physician Review |
                    | (MANDATORY)      |
                    +--------+---------+
                             |
                             +---> Accept AI recommendation
                             |     (logged to audit trail)
                             |
                             +---> Override AI recommendation
                             |     (logged with justification)
                             |
                             +---> Request second opinion
                                   (escalation logged)
```

### Key Safeguards

1. **No autonomous clinical decisions**: The AI never acts without physician review.
2. **Confidence threshold**: Outputs below 70% confidence require explicit physician acknowledgment.
3. **Emergency override**: Any physician can access any patient record in emergency situations, but all overrides are logged with full audit trail.
4. **Policy enforcement**: IAM policies prevent unauthorized roles from accessing patient data and reports. Cedar policies are illustrative only (see [Access Control](#access-control-cedar-rbac)).
5. **Automated reasoning validation**: Outputs are checked against formal clinical rules before presentation.

## Guardrails at the AgentCore Gateway

Diagnostic requests reach the agent through an AgentCore Gateway
(`src/agent/agentcore/cdk/lib/diagnostic-gateway.ts`) whose Policy in AgentCore engine runs in
`ENFORCE` mode. Its Cedar policies use [guardrails in policies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-guardrails-in-policies.html): the policy engine
calls Amazon Bedrock Guardrails on the request text and denies the request (HTTP 403) before the
agent runs.

| Policy | Effect | Screens | Threshold |
|---|---|---|---|
| `PermitAccountCallers` | `permit` | IAM callers from this account (the base permit the guardrails narrow) | — |
| `ForbidPromptAttacks` | `forbid` | `JAILBREAK`, `PROMPT_INJECTION` | score > 0.4 (documented default) |
| `ForbidHarmfulContent` | `forbid` | `HATE`, `INSULTS`, `MISCONDUCT` | score > 0.6 |
| `ForbidDirectIdentifiers` | `forbid` | `US_SOCIAL_SECURITY_NUMBER`, `EMAIL`, `US_PASSPORT_NUMBER`, `CREDIT_DEBIT_CARD_NUMBER` | any detection |

Every guardrail reads the request's free-text fields: `prompt`, `chief_complaint`, `ecg`,
`medical_history[]` and `medications[]` (numeric vitals and labs carry no text). If none of
them is present the request is denied rather than passed unscreened.

Choices made for clinical text, each one a trade-off to review per deployment:

- `VIOLENCE` and `SEXUAL` are not screened at the gateway, because injuries, assault and sexual
  health are routine clinical history. The agent's Bedrock guardrail still applies its content
  filters to the model call.
- `NAME`, `PHONE` and `AGE` are not screened: eponyms ("Beck triad"), "call 911" and the
  patient's age are clinical content. The identifiers that are screened are ones a diagnosis never
  needs (minimum necessary).
- Guardrail scores are non-deterministic, so a request near a threshold can be allowed once and
  denied the next time. Tune thresholds with `GATEWAY_POLICY_MODE=LOG_ONLY` and the evaluation
  set before enforcing them on real traffic.

The gateway screens requests only. Output screening (`suppressOutput`) is not configured: the
agent's answer is already validated fail-closed and checked by its Automated Reasoning guardrail.
`scripts/11_invoke_agentcore.py` (with `AGENTCORE_GATEWAY_ID` set) sends a prompt injection
probe and fails unless the gateway denies it.

## Access Control (Cedar RBAC)

The Cedar policies in `src/agent/agentcore/policies/*.cedar` define role-based
access patterns (Physician, Nurse, Admin, Auditor). In this demo, they are
**illustrative only** — they show the RBAC model but are **not** deployed as a
live AgentCore policy engine and are **not** evaluated at runtime. `agentcore.json`
intentionally keeps `policyEngines` empty: deploying these statements as
`AWS::BedrockAgentCore::Policy` resources fails `CreatePolicy` because AgentCore
Cedar policies are validated against a schema generated from an AgentCore Gateway's
tool input schemas and must constrain their resource to an `AgentCore::Gateway`,
which this sample does not define. **Access control in this sample is enforced by
IAM** (`infra/iam.tf`), demonstrated live by `scripts/06_test_access_control.py`.

**For production deployment**: Configure an AgentCore Gateway with a policy engine
that evaluates Cedar policies on each invocation, blocking unauthorized access at
the API layer.

## Model Governance

`scripts/04_evaluate_llm.py` evaluates the deployed agent on Claude Sonnet 4.6
(`anthropic.claude-sonnet-4-6`) with Amazon Bedrock Evaluations. The bias and SHAP job
analyzes the diagnostic dataset, not the LLM. This model has no dated snapshot ID and doesn't support
In-Region invocation in US Regions, so the code only allows its US cross-Region inference
profile (`us.anthropic.claude-sonnet-4-6`). The `global.` profile is rejected because it can
route prompts, which carry patient data, to Regions outside the US. The agent, the Lambda, and `config.py` refuse to start
with any other `BEDROCK_MODEL_ID`, and Terraform validates the same allowlist. Changing
models requires re-running that evaluation and updating `ALLOWED_MODEL_IDS`.

Every result records the model that produced it: the agent adds `model_id` to each validated
diagnosis, and the Lambda writes `model_id` into each result object in S3. `make verify-live`
checks the agent field.

A model change should follow this workflow (AWS Well-Architected Machine Learning Lens, model
versioning and change management):

1. Propose the new model ID in a pull request that changes `ALLOWED_MODEL_IDS` in the agent,
   the Lambda and `config.py`, the Terraform validation, and the IAM model ARNs together.
2. Re-run `scripts/02_bias_detection.py` and `scripts/04_evaluate_llm.py` against the candidate,
   and compare the metrics with the current model's runs in the MLflow App.
3. Have a clinical reviewer approve the comparison before merge.
4. Deploy through the pipeline only; see "Change control for safety settings" in
   [SECURITY.md](../SECURITY.md#production-hardening-recommendations).

## Drug interaction checks

`check_drug_interactions` (`src/agent/app/raihealthdiag/main.py`) is illustrative. It knows four
interaction pairs. For any pair it does not know, it returns `"interactions_found": "UNKNOWN"` with
**MANUAL VERIFICATION REQUIRED** and a `database_coverage` note, never a confident "no
interactions". A missing entry must never read as reassurance that a combination is safe.

Before clinical use:

- Replace the dictionary with an authoritative, maintained interaction source (for example a
  licensed clinical decision support database, or RxNorm-normalized drug identifiers checked
  against such a database), with its version recorded in every response.
- Normalize drug names to standard identifiers before lookup, so brand names, generics and
  misspellings resolve to the same entry.
- Keep the unknown-is-not-safe rule: a lookup that fails, times out or finds no entry must return
  UNKNOWN and require manual verification.
- Validate the tool against a reference set of known interacting and non-interacting pairs, and
  re-run that validation when the source is updated.

## Limitations and Assumptions

### What This Sample Demonstrates

- How to architect a responsible AI system on AWS
- Integration patterns for bias detection, explainability, guardrails, and observability
- Role-based access control for healthcare AI systems
- Human-in-the-loop workflow design
- Comprehensive monitoring and alerting for AI safety

### What This Sample Does NOT Guarantee

- **Not FDA approved**: This is a demonstration sample, not a certified medical device or clinical decision support system.
- **Not for production clinical use**: This code is provided for educational and demonstration purposes only. It must not be used to make actual clinical decisions or treat patients.
- **Synthetic data only**: All patient data used in this demo is synthetic and generated for demonstration purposes. No real patient data (PHI/PII) is included or should be used.
- **Not a complete RAI framework**: Production healthcare AI systems require additional validation, regulatory compliance (HIPAA, FDA, CE marking), clinical trials, and ongoing monitoring beyond what this sample demonstrates.
- **Model accuracy not validated**: The AI model's clinical accuracy has not been validated against clinical endpoints. The confidence scores are for demonstration purposes.
- **Not a substitute for clinical judgment**: AI recommendations shown in this demo are illustrative and should never be interpreted as actual medical advice.

### Assumptions

- Users have an AWS account with sufficient permissions to deploy the infrastructure.
- The demo is run in a non-production environment.
- Bedrock Claude model access has been granted in the target AWS region.
- All data processed through this system is synthetic.
- The clinical rules in the automated reasoning guardrail are simplified for demonstration and do not represent comprehensive medical knowledge.

## Known scanner findings

This sample runs `checkov`, `bandit`, `detect-secrets`, `grype`, `semgrep`, `cdk-nag` and the rest
of the Automated Security Helper suite. It carries **no inline exception comments, no config-level
rule exclusions and no severity thresholds that hide findings**, and it excludes no source path —
only generated and third-party trees (`node_modules`, `.cache`, `.terraform`, `cdk.out`, `.venv`)
are outside the scan set.

One cdk-nag rule (AwsSolutions-IAM5) is excepted, in one file, on two wildcards in the AgentCore
runtime role: the runtime log-stream name and the telemetry actions that have no resource types in
the AWS Service Authorization Reference; see SECURITY.md. That is the only scanner exception in the
repository, and it is described in full below.

### CKV_AWS_145 on the server access log destination buckets — resolved by measurement

This section used to explain why `CKV_AWS_145` ("Ensure that S3 buckets are encrypted with KMS by
default") was reported twice and accepted as permanent debt, on
`aws_s3_bucket.server_access_logs` and `aws_s3_bucket.server_access_logs_replica` in
`infra/server_access_logs.tf`. **It is kept here, corrected, because the reasoning was wrong and
the correction is the more useful record.**

**What the documentation says.** [Enabling Amazon S3 server access logging](https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-server-access-logging.html)
states that granting `s3:PutObject` to the logging service principal is not sufficient when the
destination bucket uses SSE-KMS default encryption, that the destination must use S3 managed keys,
and that with SSE-KMS Amazon S3 might deliver log objects encrypted under a key the owner cannot
access. (Content rephrased for compliance with licensing restrictions.) Read as a hard constraint,
that makes the finding unfixable, which is how it was carried.

**What measuring it in a live account showed.** The warning is about a key the *owner* cannot use.
A customer managed key whose policy grants the logging service principal
`kms:GenerateDataKey*`/`kms:Decrypt` (to encrypt on delivery) and the account root `kms:*` (to
decrypt on read) removes that failure mode. Both destination buckets now use SSE-KMS with such a
key — `aws_kms_key.server_access_logs` in the primary Region and
`aws_kms_key.server_access_logs_replica` in the replica Region — and `CKV_AWS_145` reports zero,
with no suppression.

The primary bucket was switched first while the replica was deliberately held on SSE-S3 as a
control, so the two modes could be compared side by side. Result over a one-and-a-half-hour window:

| Measurement | Result |
|---|---|
| Log objects delivered to the SSE-KMS bucket after the change | 600 |
| Of those, readable via `GetObject` | 600 (zero failures) |
| Log records recovered and parsed | 1,144 |
| Object encryption confirmed by `HeadObject` | `aws:kms` + the expected key ARN, `BucketKeyEnabled` |
| Deliberately failed anonymous request found in the delivered records | yes — `REST.GET.OBJECT 403 AccessDenied`, requester `-` |
| Record-to-delivery lag | 18 min min / 72 min median / 121 min max |
| Cross-Region replication of the SSE-KMS log objects | `ReplicationStatus: COMPLETED`, replica readable |
| Control bucket (SSE-S3) throughout | kept receiving readable logs, confirming the test bucket's behaviour was not a Region-wide artefact |

The failed anonymous request matters on its own: requests that fail authentication are precisely
what S3 server access logs capture and CloudTrail S3 data events do not, so recovering one from an
SSE-KMS destination shows the audit value of this path survives the encryption change.

**Two things this corrected.** First, SSE-KMS on a server-access-log destination is not a
functional dead end in this account — the documented warning describes a misconfiguration (an
unusable key), not a service limitation. Treat the AWS text as a caution to configure the key
policy correctly rather than a prohibition, and verify in your own account before relying on
either reading. Second, because a KMS-encrypted log bucket is a replication *source* here, the
encryption change alone would have silently stopped log replication: "By default, Amazon S3
doesn't replicate objects that are encrypted with SSE-KMS or DSSE-KMS"
([Replicating encrypted objects](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-config-for-kms-objects.html)).
`aws_s3_bucket_replication_configuration.server_access_logs` therefore carries an explicit
`source_selection_criteria` opt-in and a replica key, and the replication role's KMS conditions
cover the log buckets. Flipping bucket encryption without that plumbing is the kind of change that
passes a scanner and breaks a control.

**What else protects these buckets**: the same access logs are delivered a second time to a
CloudWatch Logs log group encrypted with this project's customer managed KMS key (see
[Logging requests with server access logging](https://docs.aws.amazon.com/AmazonS3/latest/userguide/ServerLogs.html)),
which is what made the experiment safe to run — no audit gap was possible while the S3 path was
under test. The buckets also carry versioning, a TLS-only bucket policy, public access blocks,
`BucketOwnerEnforced` object ownership, lifecycle expiry and cross-Region replication, and they
contain access log records only — never patient data.

**Note on scanner versions**: `CKV_AWS_145` is only evaluated by newer checkov builds. checkov
3.2.430 does not implement it for Terraform, so `checkov -d infra` never reported it; checkov
3.2.500 (the build bundled with the Automated Security Helper) does. The finding was found, and
fixed, against the newer build — measure against that one.

The measurement is summarized in the comment above `aws_kms_key.server_access_logs` in
`infra/server_access_logs.tf`.

### cdk-nag AwsSolutions-IAM5 in the AgentCore Gateway role

The gateway's execution role (`src/agent/agentcore/cdk/lib/diagnostic-gateway.ts`) carries two
more `AwsSolutions-IAM5` exceptions, each the narrowest grant IAM accepts: `Resource::*` on
`bedrock:InvokeGuardrailChecks`, which has no resource types, and the project's gateway-name
pattern on policy evaluation, because the gateway ARN does not exist until the gateway is created.
See [SECURITY.md](../SECURITY.md), items 3 and 4.

### cdk-nag AwsSolutions-IAM5 in the AgentCore runtime role

**What is excepted**: `AwsSolutions-IAM5` ("IAM entity contains wildcard permissions") on two of the
AgentCore runtime role's managed policies — the logs policy and the telemetry policy — declared in
`src/agent/agentcore/cdk/lib/runtime-execution-role.ts` as cdk-nag metadata on the policy resources.
It is an `addMetadata("cdk_nag", …)` call, not a comment, which is why it does not appear in a text
search for exception markers.

**Scope of the first exception**: a single resource pattern,
`arn:…:log-group:/aws/bedrock-agentcore/runtimes/<runtimeId>-DEFAULT:log-stream:*[runtime-logs*`.
The two other stream names the runtime writes, `otel-rt-logs` and `spans`, are granted as exact
ARNs with no wildcard.

**Why the wildcard cannot be removed**: AgentCore generates the stdout/stderr stream name itself,
and it contains both a date prefix and runtime-generated identifiers on either side of the literal
text — for example `2026/10/15/[runtime-logs-18d7a23a-…]a37adbda-…`. The date changes daily and the
identifiers are not known before invocation, so no literal ARN can match. Removing the wildcard
stops the runtime from logging.

**What bounds it**: the resource is still prefixed with this runtime's exact log-group ARN, never
`log-group:*`. That makes it narrower than the execution role AWS documents for AgentCore, which
grants `logs:CreateLogStream` and `logs:PutLogEvents` on
`log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*` — see
[AgentCore runtime permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html).
The reason recorded in the code carries the same evidence.

**The second wildcard**: the same rule is excepted on the runtime's telemetry policy, for
`Resource::*`, on one action: `xray:PutTraceSegments`. X-Ray actions have no resource types in the
[AWS Service Authorization Reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_xray.html),
so IAM can only match it with `*`; cfn-nag reports the same policy as W13. The agent publishes its
metrics as CloudWatch embedded metric format log lines, so it holds no `cloudwatch:PutMetricData`
grant, and the never-used `logs:DescribeLogGroups` and X-Ray sampling grants are removed. Removing
X-Ray entirely stopped the agent's traces in a live test, so this one action stays. Both
exceptions are granular — each names the exact resource string it applies to, so any new wildcard
elsewhere in the stack still fails cdk-nag. Synthesising the stack produces exactly two
`AwsSolutions-IAM5` entries in the template metadata and no others.

## References

- [AWS Responsible AI](https://aws.amazon.com/ai/responsible-ai/)
- [SageMaker Clarify availability change](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html) — Clarify is no longer open to new customers; this sample follows the replacement guidance on that page
- [Amazon SageMaker AI Processing](https://docs.aws.amazon.com/sagemaker/latest/dg/processing-job.html)
- [Managed MLflow on Amazon SageMaker AI](https://docs.aws.amazon.com/sagemaker/latest/dg/mlflow.html)
- [Amazon Bedrock Evaluations: LLM-as-a-judge](https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation-judge.html)
- [Amazon Bedrock Guardrails](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html)
- [Cedar Policy Language](https://www.cedarpolicy.com/)
- [AWS Well-Architected Framework - Machine Learning Lens](https://docs.aws.amazon.com/wellarchitected/latest/machine-learning-lens/machine-learning-lens.html)
