# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Security

- AgentCore Gateway with a Policy in AgentCore engine (`ENFORCE`) in front of the runtime (`src/agent/agentcore/cdk/lib/diagnostic-gateway.ts`). IAM (SigV4) inbound authorization; Cedar policies permit IAM callers from this account and use guardrails in policies (https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-guardrails-in-policies.html) to deny requests whose free text is a prompt attack, harmful content (hate, insults, misconduct) or carries a direct identifier, before the agent runs. The runtime target has an inline OpenAPI input schema so the guardrails can read the request fields. Two documented cdk-nag `AwsSolutions-IAM5` exceptions (SECURITY.md, items 3 and 4).
- `scripts/11_invoke_agentcore.py` invokes through the gateway when `AGENTCORE_GATEWAY_ID` is set and fails unless a prompt injection probe is denied (HTTP 403).
- Documented, time-boxed exception (expires 2027-01-31) for CVE-2026-118764 in `minimatch` 10.2.5, bundled inside `aws-cdk-lib`: not reachable (requires `optimizationLevel >= 2`, which `aws-cdk-lib` never sets; synth-time only) and not fixable until `aws-cdk-lib` bundles 11.0.1 (SECURITY.md). `scripts/scan_summary.py` allows only that suppression and the two W13 policies, and still fails on any other.
- Production recommendations (README): evaluate a VPC-attached AgentCore runtime so the diagnostic path shares the ingestion path's network isolation, and restrict the runtime to gateway traffic.

### Changed

- Replaced SageMaker Clarify, which is no longer open to new customers, following AWS's replacement guidance (https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html):
  - Bias metrics class imbalance (CI), difference in proportions of labels (DPL) and accuracy difference (AD), computed directly, and SHAP attributions from the SHAP library, run in a SageMaker AI Processing job (`scripts/02_bias_detection.py`, `src/bias_detection/`). The earlier "DPD/DI" figures were per-group accuracy differences, now reported as AD.
  - `scripts/03_shap_explainability.py` shows the job's real SHAP output instead of illustrative values.
  - New `scripts/04_evaluate_llm.py`: Amazon Bedrock Evaluations LLM-as-a-judge job on the deployed agent, with demographic-variant cases (`data/llm_eval_cases.json`) and a Stereotyping metric; `--reuse-run` resubmits collected answers without invoking the agent.
  - New `scripts/setup_mlflow.py`: optional SageMaker AI MLflow App for experiment tracking.
- S3 report prefixes `clarify/` replaced by `bias/`, `explainability/` and `evaluation/`; physician and auditor read access follows.
- Model allowlist comments now point at the LLM evaluation; the bias and SHAP job analyses the dataset, not the LLM.
- Python dependencies: added scikit-learn, mlflow-skinny and sagemaker-mlflow; removed the unused sagemaker, matplotlib, seaborn, jsonschema and tabulate.

### Fixed

- Both SNS topic policies are one statement each, so checkov evaluates them: checkov 3.2.430 returns UNKNOWN for CKV_AWS_169 and CKV_AWS_385 on any multi-statement `jsonencode` policy that contains references, so the alerts topic policy had never been checked. The alerts topic no longer grants the owning account, whose principals are authorized by their IAM policies.
- `scripts/verify_solution.py` compares SNS published and delivered counts only over settled periods; the two metrics can land minutes apart.
- Deleting patient data or results from the data lake now alerts: an `s3:ObjectRemoved:*` notification publishes to the alerts topic, and deleting an audit log publishes to the CloudTrail topic. Lifecycle expirations are a separate event type and do not alert. Each topic policy and KMS key policy allows `s3.amazonaws.com` for its own bucket and account only.
- `scripts/verify_solution.py` checks that S3 server access logging is enabled and that a delivery arrived within 24 hours; delivery is best effort, "within a few hours" (https://docs.aws.amazon.com/AmazonS3/latest/userguide/ServerLogs.html#LogDeliveryBestEffort), so the earlier one-hour window failed on a healthy stack.
- `scripts/test_end_to_end.py` checks the bias and SHAP reports of the latest SageMaker AI Processing job instead of recomputing one metric locally, and its header lists the 13 checks it runs.
- IAM found by the first live run: the processing role can write MLflow artifacts (`mlflow/*`); the Bedrock evaluation role can use the data lake key (`kms:DescribeKey`, `Decrypt`, `GenerateDataKey`); the Bedrock `kms:DescribeKey` key-policy grant is a separate statement, as AWS documents; bucket-level S3 actions are no longer under an `s3:prefix` condition they never carry.
- `cleanup.py` and `verify_solution.py` filter evaluation jobs with a name that ListEvaluationJobs accepts.

### Removed

- SageMaker Studio domain and user profile, the SageMaker API VPC endpoint, the model package group, the Clarify log group, `scripts/04_real_clarify_shap.py` (which created billable endpoints), `src/bias_detection/app.py` and `src/bias_detection/__main__.py`.

## [1.0.0] - 2026-05-23

### Added

- Initial release of sample-responsible-ai-healthcare
- SageMaker Clarify bias detection (DPD, Disparate Impact) across 5 demographic facets
- SHAP explainability with feature attribution via Bedrock and SageMaker Clarify
- IAM-enforced RBAC with 4 roles (Physician, Nurse, Admin, Auditor); Cedar policies included as illustrative artifacts (not runtime-enforced — requires an AgentCore Gateway)
- Emergency physician override workflow with audit trail
- Automated Reasoning guardrail for clinical output validation
- Bedrock Knowledge Base with clinical guidelines (AHA/ACC STEMI, cardiac risk, medication safety)
- AgentCore Strands agent with 3 tools (cardiac risk scoring, drug interactions, metric emission)
- CloudWatch observability: 9-widget dashboard, 5 alarms, 20 custom metrics
- VPC isolation with private subnets, NAT Gateway, and VPC endpoints
- KMS encryption on all data stores (S3, CloudWatch Logs, SQS, SageMaker)
- Terraform infrastructure (11 files, all variables validated)
- 59 unit tests covering bias detection, SHAP, policies, and observability
- Comprehensive documentation (architecture, RAI dimensions, deployment, cleanup)
