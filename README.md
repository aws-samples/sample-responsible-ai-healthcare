# Responsible AI for Healthcare Diagnostics on AWS

![License](https://img.shields.io/badge/License-MIT--0-blue.svg)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)
![Terraform](https://img.shields.io/badge/Terraform-%3E%3D1.5-purple.svg)
![AWS](https://img.shields.io/badge/AWS-Bedrock%20%7C%20SageMaker%20%7C%20AgentCore-orange.svg)

> **Important notice** — This sample is for **educational purposes only** — not a substitute for professional medical advice. All patient data is **synthetic**; do not input real PHI. See [docs/responsible-ai.md](docs/responsible-ai.md) for details.

---

## Overview

This repository provides a fully deployable reference architecture demonstrating how to build an **AI-Powered Healthcare Diagnostic Assistant** that embeds Responsible AI (RAI) principles at every layer of the stack. The assistant processes synthetic cardiac patient data and produces diagnostic recommendations while enforcing fairness, explainability, safety, and governance controls using native AWS services.

The demo is designed for Solution Architects, ML Engineers, and Healthcare IT teams who need a concrete, end-to-end example of operationalizing Responsible AI on AWS. It covers the complete lifecycle from bias detection in training data, through runtime guardrails and access control, to post-deployment monitoring and drift detection.

All patient data is **synthetic** (40 patients, cardiac diagnosis scenarios) and no protected health information (PHI) is used at any point. The infrastructure is deployed via Terraform and can be torn down completely with a single command.

---

## Architecture

### High-Level Design

The system has two independent paths operating on the same patient data:

![High-level design: synthetic patient data feeds two paths. The AI diagnostic agent, with guardrails, returns a recommendation and confidence score that a physician reviews, accepts or overrides. The bias path computes bias metrics and SHAP explainability in a SageMaker AI Processing job, and runs LLM evaluation with Amazon Bedrock Evaluations. Both paths send metrics and low-confidence alerts to monitoring.](docs/architecture-hld.png)

- **AI Diagnostic Agent** — Clinician calls the Amazon Bedrock AgentCore Gateway (IAM authorization → Policy in AgentCore with guardrails in policies) → AgentCore Runtime (Anthropic Claude Sonnet 4.6 → Amazon Bedrock Knowledge Bases → Amazon Bedrock Guardrails) → Physician reviews recommendation. The gateway's Cedar policies deny a request whose text is a prompt attack, harmful content or carries direct identifiers before it reaches the agent ([guardrails in policies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-guardrails-in-policies.html)). Per-role access control is enforced by AWS Identity and Access Management (IAM); the per-role Cedar RBAC policies are illustrative artifacts.
- **Bias, explainability and LLM evaluation** — `scripts/02_bias_detection.py` runs an Amazon SageMaker AI Processing job that computes the standardized bias metrics (class imbalance CI, difference in proportions of labels DPL, accuracy difference AD) and SHAP attributions. `scripts/04_evaluate_llm.py` scores the deployed agent with an Amazon Bedrock Evaluations LLM-as-a-judge job. Results go to Amazon S3, Amazon CloudWatch and, optionally, an Amazon SageMaker AI MLflow App.

> **Why not SageMaker Clarify?** Amazon SageMaker Clarify is no longer open to new customers. This sample follows AWS's replacement guidance: compute the standardized bias metrics directly, use the SHAP library, run both in a SageMaker AI Processing job, track results in a SageMaker AI MLflow App, and evaluate the LLM with Amazon Bedrock Evaluations. See [SageMaker Clarify availability change](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html).

### Low-Level Design

The infrastructure as deployed: what runs inside the VPC, the AWS-managed services outside it, and the observability, audit and security controls. Numbers give the read order: **1–6** the agent path, **7–11** the ingestion path, **B1–B4** the on-demand Responsible AI assessment:

![Low-level design as deployed in us-east-1: a clinician calls the AgentCore Gateway with SigV4; its policy engine (Cedar with guardrails in policies) screens the request text and denies prompt attacks, harmful content and direct identifiers with HTTP 403; a permitted request reaches the Bedrock AgentCore runtime, which calls Claude Sonnet 4.6 through an Automated Reasoning guardrail and retrieves guidelines from a Bedrock Knowledge Base backed by OpenSearch Serverless; these are AWS-managed and outside the VPC. Patient JSON uploaded to the S3 data lake triggers the preprocessing Lambda, the only workload in the VPC, which reaches Bedrock Runtime, CloudWatch, X-Ray and S3 through VPC endpoints; any other egress goes to an AWS Network Firewall that drops and logs it. The bias and SHAP SageMaker AI Processing job, the SageMaker AI MLflow App and Amazon Bedrock Evaluations run AWS-managed, outside the VPC. CloudWatch alarms, data lake deletions and the EventBridge audit-log and trail tamper rules notify SNS, CloudTrail writes to an audit bucket, nine customer managed KMS keys encrypt the data (six in us-east-1, three in us-west-2), and the buckets replicate to us-west-2.](docs/architecture-lld-v2.drawio.png)

- **Inside the VPC** — Only the preprocessing Lambda. It runs in private subnets A and B and reaches AWS only through VPC endpoints: Amazon S3 (gateway), Amazon Bedrock Runtime, Amazon CloudWatch (metrics) and AWS X-Ray (interface). See `infra/vpc.tf`.
- **Internet egress** — None. The VPC has no internet gateway and no NAT gateway. The Lambda security group allows outbound 443 only to the interface endpoints and the S3 prefix list. The private subnets' default route points to an AWS Network Firewall (`aws_networkfirewall_firewall.main`) whose default action drops and logs every flow, so any attempt to leave the VPC is blocked and recorded.
- **AWS-managed, outside the VPC** — The Amazon Bedrock AgentCore Gateway and its policy engine (`src/agent/agentcore/cdk/lib/diagnostic-gateway.ts`), the Amazon Bedrock AgentCore runtime (Strands agent, `networkMode: PUBLIC`; see [Production recommendations](#production-recommendations)), Claude Sonnet 4.6 on Amazon Bedrock, both Bedrock guardrails (Automated Reasoning on the agent path, content filters on the Lambda path), the Bedrock Knowledge Base with its OpenSearch Serverless index, the bias and SHAP SageMaker AI Processing job (only while `scripts/02_bias_detection.py` runs), the SageMaker AI MLflow App (created by `scripts/setup_mlflow.py`) and Amazon Bedrock Evaluations jobs (`scripts/04_evaluate_llm.py`).
- **Observability, audit and security** — CloudWatch metrics, alarms, dashboard, logs and Transaction Search; the SNS alerts topic, which also receives an alert when patient data is deleted from the data lake; CloudTrail with S3 data events; nine customer managed KMS keys (six in us-east-1, three for the us-west-2 replicas); IAM roles including the four demo RBAC roles; IAM Access Analyzer. The data lake and audit logs replicate to `us-west-2`.
- **Not deployed** — AgentCore Identity and the per-role Cedar RBAC policies: the policies in `infra/agentcore.tf` are illustrative and IAM enforces per-role access.

---

## AWS Services Used

| Service | Purpose |
|---------|---------|
| Amazon S3 | Patient data storage, bias, SHAP and evaluation reports, MLflow artifacts, audit trail |
| AWS Lambda | Data preprocessing, orchestration pipeline |
| Amazon SageMaker AI (Processing) | Bias metrics (CI, DPL, AD) and SHAP explainability in a Processing job on the AWS scikit-learn container (`scripts/02_bias_detection.py`) |
| Amazon SageMaker AI (MLflow App) | Experiment tracking for the bias, SHAP and evaluation runs (`scripts/setup_mlflow.py`, optional) |
| Amazon Bedrock Evaluations | LLM-as-a-judge evaluation of the deployed agent, including stereotyping across demographic variants (`scripts/04_evaluate_llm.py`) |
| Amazon Bedrock (Claude Sonnet) | Diagnostic reasoning and natural language output |
| Amazon Bedrock (Automated Reasoning) | Logical validation guardrails |
| Amazon Bedrock (Knowledge Base) | Clinical guideline grounding (RAG) |
| Amazon Bedrock AgentCore | Agent runtime with Strands SDK; AgentCore Gateway with a policy engine whose Cedar policies apply guardrails to every request (the per-role Cedar RBAC policies are illustrative — see note below) |
| Amazon CloudWatch | Metrics, alarms, dashboard, logs |
| Amazon SNS | Alert notifications: low-confidence diagnoses, audit-log tampering and patient data deletion |
| AWS IAM | Least-privilege roles, RBAC enforcement |
| AWS KMS | Encryption at rest for all data stores |
| Amazon VPC | Network isolation with private subnets, VPC Endpoints, AWS Network Firewall (default-deny egress) |

---

## Prerequisites

- **AWS Account** with permissions to create the services listed above
- **AWS CLI v2** configured with appropriate credentials
- **Terraform** >= 1.5.0
- **Python** >= 3.11
- **pip** (Python package manager)
- **Amazon Bedrock model access** enabled for `anthropic.claude-sonnet-4-6` in your target region
- **Node.js** >= 20 (`aws-cdk-lib` requires it; AgentCore CDK deployment only)
- **uv** (the AgentCore CLI uses it to package the agent): [install guide](https://docs.astral.sh/uv/getting-started/installation/)
- For `make test` / `make scan-all`: the development tools listed in [docs/deployment.md](docs/deployment.md#development-and-scanning-tools)
- **Git**

> **Note:** The AgentCore agent (`src/agent/`) has additional runtime dependencies (`strands-agents`, `bedrock-agentcore`). These are listed in `src/agent/requirements.txt` and are installed on the AgentCore runtime automatically during deployment — you do not need them locally unless developing the agent.

---

## Regional availability

This sample is built and tested in **us-east-1** and runs only in US Regions:

| Constraint | Why | Source |
|---|---|---|
| US cross-Region inference profile only (`us.anthropic.claude-sonnet-4-6`, guardrail profile `us.guardrail.v1:0`) | Prompts carry patient data, so they stay on US routing; the `global.` profile is rejected | [Inference profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html) |
| Automated Reasoning checks | Generally available in US East (N. Virginia), US East (Ohio), US West (Oregon), and four Regions outside the US | [Automated Reasoning availability](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-automated-reasoning-checks.html#availability-and-language-support) |
| Amazon Bedrock AgentCore Runtime | Available in all three of those US Regions | [AgentCore supported Regions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html) |
| Guardrails in policies (AgentCore Gateway policy engine) | Available in US East (N. Virginia), US East (Ohio) and US West (Oregon); not in every Region | [Guardrails regional availability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-guardrails-in-policies.html#guardrails-regional-availability) |
| SageMaker AI scikit-learn container for the bias job | The default image URI covers us-east-1, us-east-2 and us-west-2; elsewhere set `BIAS_JOB_IMAGE_URI` | [Docker registry paths](https://docs.aws.amazon.com/sagemaker/latest/dg-ecr-paths/sagemaker-algo-docker-registry-paths.html) |

So deploy in **us-east-1, us-east-2 or us-west-2**. Check model access for Claude Sonnet 4.6 and
Amazon Titan Text Embeddings V2 in the Region you choose.

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/aws-samples/sample-responsible-ai-healthcare.git
cd sample-responsible-ai-healthcare

# 2. Create a Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Edit .env with your AWS account ID and S3 bucket name, then source it:
source .env

# 5. Configure Terraform variables
cp infra/terraform.tfvars.example infra/terraform.tfvars
# Edit infra/terraform.tfvars with your values

# 5b. Configure the AgentCore deployment target (needed for step 8 / docs/deployment.md)
cp src/agent/agentcore/aws-targets.json.example src/agent/agentcore/aws-targets.json
# Edit it and replace 123456789012 with your own AWS account id

# 6. Deploy infrastructure
cd infra
terraform init
terraform plan
terraform apply
cd ..

# 7. Complete post-Terraform setup (see docs/deployment.md for full details)
python scripts/setup_guardrail.py        # Creates Automated Reasoning guardrail
export GUARDRAIL_ID=<id-from-output>     # Set the guardrail ID

# 8. Run the demos (from project root)
python scripts/01_prepare_data.py
python scripts/02_bias_detection.py
```

> **Note:** Steps 7-8 are a simplified path. For the full deployment including Knowledge Base and AgentCore agent, see [docs/deployment.md](docs/deployment.md).

---

## Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `AWS_ACCOUNT_ID` | Your 12-digit AWS account ID | -- | Yes |
| `S3_BUCKET` | S3 bucket name for demo data | -- | Yes |
| `GUARDRAIL_ID` | Bedrock guardrail identifier | -- | Yes |
| `AWS_REGION` | AWS region for deployment | `us-east-1` | No |
| `S3_PREFIX` | S3 key prefix for all objects | `rai-healthcare-demo` | No |
| `BEDROCK_MODEL_ID` | Bedrock model (requires `us.` prefix) | `us.anthropic.claude-sonnet-4-6` | No |
| `CONFIDENCE_THRESHOLD` | Mandatory-review threshold as a **fraction** in `[0.50, 1.0]` (not a percent) | `0.70` | No |
| `GUARDRAIL_VERSION` | Guardrail version | `DRAFT` | No |
| `MLFLOW_TRACKING_ARN` | MLflow App ARN printed by `scripts/setup_mlflow.py`; scripts 02 and 04 log to it when set | -- | No |
| `AGENTCORE_RUNTIME_ID` | Deployed agent runtime id, needed by `scripts/04_evaluate_llm.py` | -- | For script 04 |
| `BIAS_JOB_INSTANCE_TYPE` | Instance type of the bias and SHAP Processing job | `ml.t3.medium` | No |
| `BIAS_JOB_IMAGE_URI` | Override for the scikit-learn container image (Regions other than us-east-1, us-east-2, us-west-2) | -- | No |

### Terraform Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `aws_region` | AWS region for deployment | `us-east-1` |
| `project_name` | Project name used for resource naming | `rai-healthcare-demo` |
| `environment` | Environment name | `demo` |
| `bedrock_model_id` | Bedrock base model ID (allowlisted: the model `scripts/04_evaluate_llm.py` evaluates) | `anthropic.claude-sonnet-4-6` |
| `bedrock_inference_profile` | Cross-Region inference profile prefix (`us` or `global`) | `us` |
| `replica_region` | Region for S3 cross-Region replicas (data lake + audit logs; contains PHI) | `us-west-2` |
| `confidence_threshold` | Confidence threshold below which alerts fire, as a **percent** (50-100) | `70` |
| `alarm_email` | Email for CloudWatch alarm notifications | `""` |
| `judge_model_regions` | Regions the Bedrock Evaluations judge inference profile can route to | `["us-east-1", "us-east-2", "us-west-2"]` |

**The two thresholds use different units, on purpose.** The Terraform variable
`confidence_threshold` is a **percent** (`70`), because the CloudWatch alarm and dashboard work in percent
(`DiagnosticConfidence` is published with `Unit: Percent`). The application variable
`CONFIDENCE_THRESHOLD` is a **fraction** (`0.70`), because it is compared against the model's `confidence`
field, which is a 0.0-1.0 value. Terraform divides by 100 when it passes the value to the Lambda, so
`confidence_threshold = 70` becomes `CONFIDENCE_THRESHOLD=0.7`. The Lambda, the agent runtime and
`config.py` each **refuse to start** (`RuntimeError`) if `CONFIDENCE_THRESHOLD` is outside `[0.50, 1.0]`;
a percent value such as `70` fails fast instead of being silently clamped.

Create your configuration file:

```bash
cp infra/terraform.tfvars.example infra/terraform.tfvars
```

Edit `infra/terraform.tfvars` with your specific values:

```hcl
aws_region           = "us-east-1"
project_name         = "rai-healthcare-demo"
environment          = "demo"
bedrock_model_id     = "anthropic.claude-sonnet-4-6"
confidence_threshold = 70 # percent (50-100); the app receives 0.70
alarm_email          = "your-email@example.com"
```

---

## Deployment

### Deploy with Terraform

```bash
cd infra

# Initialize providers
terraform init

# Preview changes
terraform plan

# Apply infrastructure
terraform apply
```

> **Regional availability — S3 server access log delivery to CloudWatch Logs.** The data lake and
> audit buckets deliver their server access logs twice: to a KMS-encrypted destination bucket, and to a
> KMS-encrypted CloudWatch Logs log group. The CloudWatch Logs delivery path is newer than the
> S3-bucket path and is not available in every Region. If `terraform apply` fails on
> `aws_cloudwatch_log_delivery_source` with an unsupported log type, that Region does not yet offer
> it: remove the CloudWatch delivery resources from `infra/server_access_logs.tf` and the S3-bucket
> path alone continues to work, and it is KMS-encrypted too — see
> [docs/responsible-ai.md](docs/responsible-ai.md) under "Known scanner findings" for how that was
> established, since the AWS documentation reads as though it cannot be. Also see
> [Delivering server access logs to CloudWatch Logs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/sal-cw-enabling.html).

Terraform will provision:
- VPC with private subnets, VPC Endpoints and a default-deny AWS Network Firewall (no internet or NAT gateway)
- S3 buckets (data lake, audit logs, server access logs, cross-Region replicas) encrypted with customer managed KMS keys
- S3 server access logs on both delivery paths: a KMS-encrypted destination bucket and a
  KMS-encrypted CloudWatch Logs log group (per Region)
- CloudTrail (management and S3 data events), VPC flow logs and an IAM Access Analyzer
- IAM roles with least-privilege policies, including the four demo RBAC roles
- The preprocessing Lambda function, its DLQ and the S3 trigger
- IAM roles for the bias and SHAP Processing job, the SageMaker AI MLflow App and Bedrock Evaluations jobs
- The Bedrock content-filter guardrail used by the Lambda
- IAM role and S3 sources for the Bedrock Knowledge Base, and the illustrative Cedar policy documents
- CloudWatch dashboard, metrics and alarms, and the SNS alerts topic

These are **not** created by Terraform; [docs/deployment.md](docs/deployment.md) walks through each:
- The Automated Reasoning guardrail used by the agent: `python scripts/setup_guardrail.py`
- The Bedrock Knowledge Base itself (created in the console or CLI from the Terraform-provisioned sources)
- The AgentCore agent runtime: deployed with CDK (`src/agent/agentcore/cdk`)
- The SageMaker AI MLflow App: `python scripts/setup_mlflow.py` (the AWS provider has no MLflow App resource)

### Verify Deployment

After `terraform apply` completes, verify the outputs:

```bash
terraform output
```

---

## Running the Demos

### Demo 1: Bias, Explainability and LLM Evaluation

Measures bias in the diagnostic dataset across demographics (ethnicity, age, location, language, gender), explains a diagnostic model with SHAP, and evaluates the deployed agent's answers with Amazon Bedrock Evaluations. This follows AWS's replacement guidance for SageMaker Clarify, which is no longer open to new customers ([availability change](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html)).

```bash
# (Optional) Create the SageMaker AI MLflow App, then export the ARN it prints
python scripts/setup_mlflow.py
export MLFLOW_TRACKING_ARN=<arn-from-output>

# Upload the synthetic patient data
python scripts/01_prepare_data.py

# Bias metrics (CI, DPL, AD) and SHAP in a SageMaker AI Processing job
python scripts/02_bias_detection.py

# Show the SHAP attributions the job computed, with a Bedrock-written narrative
python scripts/03_shap_explainability.py

# Evaluate the deployed agent with an Amazon Bedrock Evaluations LLM-as-a-judge job
export AGENTCORE_RUNTIME_ID=<runtime-id>
python scripts/04_evaluate_llm.py
```

`scripts/02_bias_detection.py` starts the Processing job `rai-healthcare-demo-bias-<timestamp>` on the AWS scikit-learn container (`sagemaker-scikit-learn:1.9-0-cpu-py3`, `ml.t3.medium`). The job runs `src/bias_detection/job.py`:

- `metrics.py` computes the standardized bias metrics directly from their definitions: class imbalance (CI) and difference in proportions of labels (DPL) on the acute coronary syndrome label, and accuracy difference (AD) on `diagnosis_correct`. A facet is flagged when |DPL| or |AD| is above 0.05.
- `explain.py` trains a gradient-boosted classifier on 15 clinical features and attributes its predictions with `shap.TreeExplainer`.

The job writes `bias/<run>/report.json` and `explainability/<run>/shap.json` under the S3 prefix, logs to the MLflow App when `MLFLOW_TRACKING_ARN` is set, and the script emits CloudWatch metrics from the real output. It exits 1 if the job fails.

**Example output (bias metrics, from a local run of the job code on the 40-patient dataset):**

```
Facet            n_a/n_d      CI     DPL      AD  Bias?
ethnicity        12/28   -0.400 +0.286 +0.000  ⚠️  YES
age_group        22/18   +0.100 -0.818 +0.151  ⚠️  YES
language         25/15   +0.250 +0.240 -0.187  ⚠️  YES
location_type    23/17   +0.150 +0.036 +0.384  ⚠️  YES
gender           20/20   +0.000 +0.700 +0.000  ⚠️  YES

Facets with bias (|DPL| or |AD| > 0.05): ['ethnicity', 'age_group', 'language', 'location_type', 'gender']
```

The synthetic dataset is biased on purpose, so the demo has something to detect and act on. The largest accuracy gap is by location (urban 91% vs. rural 53% correct). The SHAP model reaches 97.5% cross-validated accuracy, and its top features are `chest_pain_score`, `troponin_level` and `heart_rate`.

`scripts/04_evaluate_llm.py` sends the 14 cases in `data/llm_eval_cases.json` through the deployed agent: 4 clinical vignettes, each with variants that change exactly one demographic attribute, plus 2 safety cases (prompt injection and an out-of-scope request). It submits the answers as bring-your-own inference responses to the Bedrock Evaluations job `rai-healthcare-demo-eval-<timestamp>`. The judge, `us.amazon.nova-pro-v1:0`, comes from a different model family than the agent. It scores Correctness, Completeness, Harmfulness, Stereotyping and Refusal. The script writes `evaluation/output/<run>/summary.json` with the mean of each metric and the largest gap across a vignette's demographic variants (a fair agent scores the same on all of them). It also publishes `Eval<Metric>` and `EvalVariantGap<Metric>` to CloudWatch and exits 1 if the job fails.

**Example output (LLM evaluation, from a real run):**

```
Metric                   Mean    Max gap across demographic variants
Correctness             1.000                                  0.000
Completeness            1.000                                  0.000
Harmfulness             0.000                                  0.000
Stereotyping            0.071                                  1.000
Refusal                 0.143                                  0.000
```

In this run the agent refused only the two safety cases, and the judge flagged one response for stereotyping: on the STEMI vignette's ethnicity variant, the agent added a population-level statement about cardiovascular risk by ethnicity that the baseline answer did not contain. This is the kind of finding the evaluation exists to surface; review it before relying on the agent.

To resubmit already-collected answers without invoking the agent again, pass the run id of an existing `evaluation/input/<run>/` folder: `python scripts/04_evaluate_llm.py --reuse-run <run>`.

### Demo 2: AgentCore Policy and Identity -- RBAC and Override Workflow

Demonstrates role-based access control and physician override for low-confidence diagnoses. Access control is enforced by IAM resource policies (which role can read patient data, reports, and metrics). The per-action Cedar policies in `infra/agentcore.tf` are illustrative and not enforced in this demo. The deployed AgentCore Gateway already has a policy engine (it enforces the guardrail policies); for production, add per-role policies to that engine and authenticate callers with JWTs that carry the role.

```bash
# List the illustrative Cedar policies Terraform uploaded to S3 and display the RBAC matrix
python scripts/05_create_policies.py

# Test RBAC enforcement live by assuming each IAM role (physician vs. nurse vs. admin vs. auditor)
python scripts/06_test_access_control.py

# Simulate emergency physician override workflow
python scripts/07_emergency_override.py
```

**Example output (access control — live IAM role assumption):**

```
  ✅ ALLOWED [Physician ] Physician reads patient data
  ✅ ALLOWED [Physician ] Physician reads bias reports
  🚫 DENIED [Nurse     ] Nurse reads patient data
  🚫 DENIED [Admin     ] Admin reads patient data
  ✅ ALLOWED [Admin     ] Admin reads CloudWatch alarms
  ✅ ALLOWED [Auditor   ] Auditor reads bias reports
  🚫 DENIED [Auditor   ] Auditor reads patient data
  🚫 DENIED [Auditor   ] Auditor writes CloudWatch metric

  Matching the expected contract: 8/8
```

The script checks each result against its expected decision and exits 1 on any mismatch.

### Demo 3: AgentCore Observability -- Monitoring and Alerts

Sets up CloudWatch metrics, alarms, and drift detection for the diagnostic pipeline.

```bash
# Configure tracing and metric emission
python scripts/08_setup_tracing.py

# Simulate diagnostic runs and emit metrics
python scripts/09_simulate_diagnostic.py

# Trigger low-confidence alert scenario
python scripts/10_alert_low_confidence.py
```

**Example output (observability, from a real run):**

```
  Case 1: Patient is 55-year-old with vague abdominal discomfort, no f...
         Confidence: 28% 🚨 LOW | Latency: 7282ms
  Case 2: Patient is 40-year-old with intermittent dizziness, normal B...
         Confidence: 21% 🚨 LOW | Latency: 7448ms
  Case 3: Patient is 78-year-old with generalized weakness and mild co...
         Confidence: 45% 🚨 LOW | Latency: 7302ms
  Low confidence alerts triggered: 3/3
```

The low-confidence alarm (`rai-healthcare-demo-low-confidence-alert`) evaluates one-minute periods, so it moves to `ALARM` about a minute later and notifies the SNS alerts topic. The 7-day accuracy-drift series the script prints is simulated.

### AgentCore -- Invoke the Deployed Healthcare Agent

Invokes the live Strands agent on Bedrock AgentCore. The agent uses tools (cardiac risk scoring, drug interaction checks, CloudWatch metrics) autonomously.

```bash
# Invoke the deployed agent through the AgentCore Gateway (CDK output GatewayId).
# The script also sends a prompt injection probe and fails unless the gateway denies it (HTTP 403).
export AGENTCORE_GATEWAY_ID=<GatewayId>
python scripts/11_invoke_agentcore.py

# Without AGENTCORE_GATEWAY_ID the script invokes the runtime directly
```

**Example output (trimmed):**

```
--- AgentCore Response (session: <session-id>) ---
  Status: 200
  Latency: 36521ms

{
  "primary_diagnosis": "ST-Elevation Myocardial Infarction (STEMI) — Anterior",
  "confidence": 0.96,
  "priority": "Priority 1 — CRITICAL / IMMEDIATE INTERVENTION REQUIRED",
  "differential_diagnoses": [ ... ],
  "recommended_actions": [ ... ],
  "mandatory_physician_review": false
}
```

The script exits 1 unless the agent returns HTTP 200 and a validated diagnosis. CloudWatch metrics are emitted by the agent code from the validated output, not by the model.

### End-to-End Test

Run the full pipeline integration test (13 steps; exits 1 if any fails):

```bash
python scripts/test_end_to_end.py
```

---

## Verification

Three re-runnable checks cover the solution end to end:

```bash
# 1. Live: checks against the deployed stack (ingestion, agent, guardrail binding, tracing,
#    RBAC, alarm -> SNS delivery, network, audit logs, and the latest bias job, Bedrock
#    evaluation and MLflow App). Fails on terraform drift first. Run Demo 1 first.
export AGENTCORE_RUNTIME_ID=<runtime-id>
export EXPECTED_AGENT_GUARDRAIL_ID=<automated-reasoning-guardrail-id>
make verify-live

# 2. Security: every scanner (ASH, checkov, bandit, semgrep, GitLab KICS, Trivy, cfn-nag)
#    in one report at .scan-output/summary.md; fails on any finding at Low or above
make scan-all

# 3. Offline: unit tests, including tests that fail if this README drifts from the code
make test
```

`make verify-live` also needs three account settings, and fails on whichever is missing:

- `alarm_email` set in `infra/terraform.tfvars`, with the SNS subscription confirmed, for the
  alarm-to-SNS delivery checks
- CloudWatch Transaction Search enabled in the account, for the X-Ray tracing check
  ([Transaction Search](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Transaction-Search.html))
- The SageMaker AI MLflow App from `scripts/setup_mlflow.py`, for the MLflow check

---

## Responsible AI Dimensions

| Dimension | How Addressed | AWS Service |
|-----------|---------------|-------------|
| Fairness | Bias metrics (CI, DPL, AD) across ethnicity, age, location, language, gender; LLM stereotyping and score gaps across demographic variants | SageMaker AI Processing, Bedrock Evaluations |
| Explainability | SHAP feature attribution, AI output explanations | SageMaker AI Processing (SHAP library), Bedrock |
| Privacy and Security | VPC isolation, KMS encryption, least-privilege IAM, RBAC, no PII in logs | VPC, KMS, IAM, AgentCore |
| Safety | Confidence thresholds, mandatory physician review below 70%, automated reasoning validation | Bedrock Automated Reasoning, CloudWatch |
| Controllability | Human-in-the-loop override, physician maintains final authority over diagnoses | IAM RBAC + emergency-override audit trail (S3 + CloudWatch) |
| Veracity and Robustness | Accuracy drift detection, automated reasoning guardrails, clinical guideline grounding | Bedrock Knowledge Base, CloudWatch |
| Governance | Complete audit trail in S3 and CloudWatch, model allowlist, bias, SHAP and evaluation reports, experiment tracking | S3, CloudWatch, SageMaker AI MLflow App |
| Transparency | CloudWatch dashboard with full metric visibility, SHAP attribution reports | CloudWatch, SageMaker AI Processing |

---

---

## Cost Considerations

This demo deploys real AWS resources that incur charges. Estimated costs for a single demo run:

| Resource | Estimated Cost | Notes |
|----------|---------------|-------|
| AWS Network Firewall | ~$288/month | $0.395 per endpoint-hour (us-east-1) + $0.065/GB processed |
| VPC Endpoints | ~$22/month | 3 interface endpoints at ~$0.01/hr each |
| SageMaker AI Processing (bias + SHAP) | A few cents per run | ml.t3.medium at $0.05/hour (us-east-1), billed only while the job runs |
| Amazon Bedrock Evaluations | Under $1 per run | Charged only for judge inference: Amazon Nova Pro at $0.0008/1K input and $0.0032/1K output tokens (14 cases × 5 metrics), plus the 14 agent invocations |
| SageMaker AI MLflow App | See [SageMaker AI pricing](https://aws.amazon.com/sagemaker/ai/pricing/) | No MLflow App price is published there or in the AWS Price List API (only tracking servers, from $0.60/hour). Delete the app with the cleanup when you are done |
| Amazon Bedrock (Claude Sonnet) | Per-token pricing | ~$3/1M input tokens, ~$15/1M output tokens |
| Bedrock Knowledge Base | No charge for the Knowledge Base itself | You pay for its embeddings (Amazon Titan Text Embeddings V2) and its vector store |
| Amazon OpenSearch Serverless (Knowledge Base vector store) | Billed per OpenSearch Compute Unit (OCU) hour for as long as the collection exists, idle or not | See [OpenSearch Service pricing](https://aws.amazon.com/opensearch-service/pricing/). Often the largest cost after the firewall; `make full-cleanup` deletes the collection |
| CloudWatch | ~$3/month | Metrics, dashboard, alarms |
| S3 | < $1/month | Minimal storage for demo data |
| Lambda | < $1/month | Low invocation count in demo |

**Total estimated cost for a single demo session**: $5-15 (excluding the Network Firewall monthly baseline).

**To minimize costs**:
- Run `terraform destroy` promptly after completing the demo
- The Network Firewall and the OpenSearch Serverless collection are the largest fixed costs; destroy infrastructure when not in use
- Processing and evaluation jobs only charge while they run; delete the MLflow App (`make full-cleanup` does) when you are done

---

## Cleanup

To remove all deployed resources and stop incurring charges, run the full teardown — it destroys the
agent runtime first, then the infrastructure, then the resources Terraform does not manage, and
finally reports anything left:

```bash
make full-cleanup
```

See [docs/cleanup.md](docs/cleanup.md) for the individual phases and for the known
`terraform destroy` blockers. The Network Firewall has delete protection on, and the
`pre-clean` phase of `make full-cleanup` turns it off. To do the infrastructure part on its
own, turn off delete protection first:

```bash
cd infra

# Turn off the firewall's delete protection (exact-name match only)
python ../scripts/cleanup.py --phase pre-clean

# Preview what will be destroyed
terraform plan -destroy

# Destroy all resources
terraform destroy
```

Confirm the destruction when prompted. This will remove:
- All S3 buckets and their contents (bias, SHAP and evaluation reports, MLflow artifacts, audit logs)
- VPC, subnets, VPC Endpoints and the Network Firewall
- Lambda function and its DLQ
- The IAM roles for the Processing job, the MLflow App and Bedrock Evaluations
- The Network Firewall, CloudTrail trail and IAM Access Analyzer
- CloudWatch dashboard, alarms, and log groups
- IAM roles and policies
- KMS keys (scheduled for deletion)
- SNS topics and subscriptions

> **Important**: Ensure you have downloaded any bias reports or audit logs you wish to retain before running `terraform destroy`.

---

## Testing

Run the test suite locally using pytest:

```bash
# Install test dependencies
pip install pytest pytest-cov

# Run all tests
pytest tests/ -v

# Run specific test modules
pytest tests/test_bias_detection.py -v
pytest tests/test_shap_explainability.py -v
pytest tests/test_policies.py -v
pytest tests/test_observability.py -v

# Run with coverage report
pytest tests/ --cov=. --cov-report=term-missing
```

### Test Modules

| Test File | Coverage |
|-----------|----------|
| `tests/test_agent.py` | Agent tools, output validation (fail-closed), confidence thresholds, input sanitization, drug interactions |
| `tests/test_safety_controls.py` | Guardrail fail-fast, prompt-boundary escaping, fail-closed output, threshold floor, model allowlist, PHI-free errors |
| `tests/test_lambda_handler.py` | Lambda preprocessing, data normalization, prompt construction |
| `tests/test_agentcore_invocation.py` | AgentCore server-sent-events parsing, the gateway endpoint and the 403 policy-denial check in `scripts/11_invoke_agentcore.py` |
| `src/agent/agentcore/cdk/test/gateway.test.ts` | Gateway and guardrail policies: IAM auth, ENFORCE mode, input schema, the four policies and their categories, least-privilege role, cdk-nag clean (`npm test`) |
| `tests/test_setup_guardrail.py` | Automated Reasoning guardrail setup: API contract, ARN parsing, guardrail selection |
| `tests/test_bias_detection.py` | CI, DPL and AD against known answers from their definitions, and the dataset bias report |
| `tests/test_shap_explainability.py` | Real SHAP attributions on the dataset (additivity, report shape) and the job entry point without MLflow |
| `tests/test_bias_job_launcher.py` | The Processing job request (image, KMS, names, S3 prefixes, MLflow env) and the CloudWatch metrics from its output |
| `tests/test_llm_evaluation.py` | Evaluation cases (one attribute per variant), the Bedrock Evaluations and MLflow App requests validated against the AWS API models, and the score summary |
| `tests/test_policies.py` | RBAC matrix logic and override workflow rules |
| `tests/test_observability.py` | Metric emission, alarm logic, and drift detection |
| `tests/test_cleanup_blast_radius.py` | Teardown only ever selects this sample's resources (exact names, tags, VPC scope), including the MLflow App and bias and evaluation jobs |
| `tests/test_live_script_contracts.py` | Live scripts report wrong access decisions and fail-closed agent output as failures |
| `tests/test_verify_solution.py` | Decision helpers of `make verify-live` (frames, PHI sentinels, percent units, routing, latest bias and evaluation job) |
| `tests/test_scan_summary.py` | `make scan-all` fails on any Low-or-above, suppressed or missing scanner result |
| `tests/test_docs_alignment.py` | This README and `docs/` name only paths, scripts, targets and alarms that exist, and the low-level diagram matches `infra/` (VPC endpoints, firewall, what runs inside the VPC, model, nothing undeployed drawn as deployed) |
| `tests/test_license_headers.py` | Every source file carries the MIT-0 SPDX license header |

---

## Troubleshooting

### Terraform apply fails with "Access Denied"

Ensure your AWS credentials have sufficient permissions. The deploying principal needs permissions to create IAM roles, VPC resources, S3 buckets, Lambda functions, and Bedrock resources. Using an administrator role for initial deployment is recommended.

### Bedrock model access not enabled

```
Error: AccessDeniedException: You don't have access to the model
```

Navigate to the Amazon Bedrock console, select "Model access" in the left navigation, and request access to `Anthropic Claude Sonnet`. Access is typically granted immediately.

### Bias Processing job fails

`scripts/02_bias_detection.py` prints the job's `FailureReason`; for details run `aws sagemaker describe-processing-job --processing-job-name <name>` and read the job's log stream in the `/aws/sagemaker/ProcessingJobs` log group. If the job cannot start for capacity, check the Service Quotas value "ml.t3.medium for processing job usage", or run on another instance type with `export BIAS_JOB_INSTANCE_TYPE=ml.m5.large`. In a Region other than us-east-1, us-east-2 or us-west-2, set `BIAS_JOB_IMAGE_URI` to the scikit-learn image for that Region from the [Docker registry paths](https://docs.aws.amazon.com/sagemaker/latest/dg-ecr-paths/sagemaker-algo-docker-registry-paths.html).

### Bedrock evaluation job fails

`scripts/04_evaluate_llm.py` prints the job's `failureMessages`; run `aws bedrock get-evaluation-job --job-identifier <job-arn>` for the full status. Make sure model access is enabled for the judge model (Amazon Nova Pro) in every Region its inference profile routes to (`judge_model_regions`). After fixing the cause, `--reuse-run <run>` resubmits the same answers without invoking the agent again.

### Lambda times out reaching an AWS service

The private subnets have no internet path, so the function can reach only services with a VPC endpoint (S3, Bedrock Runtime, CloudWatch metrics, X-Ray). If you add a call to another AWS service, add its interface endpoint in `infra/vpc.tf`. Blocked attempts appear in the `/aws/network-firewall/rai-healthcare-demo/alerts` log group.

### CloudWatch alarms stuck in INSUFFICIENT_DATA

Alarms remain in `INSUFFICIENT_DATA` until metrics are first emitted. Run Demo 3 scripts to populate metrics, or use the helper script:

```bash
python scripts/populate_dashboard.py
```

### Python import errors

Ensure you are running from the project root directory and your virtual environment is activated:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Project Structure

```
sample-responsible-ai-healthcare/
├── config.py                              # Shared configuration (env var driven)
├── pyproject.toml                         # Python project metadata and tooling
├── Makefile                               # Automation targets (test, validate, verify-live, scan-all, ...)
├── requirements.txt                       # Python dependencies (pinned)
├── .env.example                           # Environment variable template
├── .ash.yaml                              # ASH scan configuration
├── .pre-commit-config.yaml                # Pre-commit hooks (ruff, bandit, etc.)
├── src/
│   ├── lambda/
│   │   └── preprocessing/
│   │       └── index.py                   # Lambda function — patient data ingestion
│   ├── bias_detection/                    # Code of the bias and SHAP Processing job
│   │   ├── job.py                         # Job entry point (reports to S3, optional MLflow logging)
│   │   ├── metrics.py                     # Bias metrics CI, DPL, AD
│   │   ├── explain.py                     # SHAP attributions (shap.TreeExplainer)
│   │   └── requirements.txt               # Packages the job installs (shap, mlflow-skinny, sagemaker-mlflow)
│   └── agent/                             # AgentCore Strands agent
│       ├── app/
│       │   └── raihealthdiag/
│       │       ├── main.py                # Agent entry point
│       │       └── model/                 # Model loader (allowlist + guardrail)
│       └── agentcore/
│           ├── agentcore.json             # AgentCore runtime config
│           └── cdk/                       # CDK stack: runtime, plus the gateway and guardrail policies (lib/diagnostic-gateway.ts)
├── scripts/
│   ├── 01_prepare_data.py                 # Upload synthetic data to S3
│   ├── 02_bias_detection.py               # Bias and SHAP SageMaker AI Processing job
│   ├── 03_shap_explainability.py          # Show the job's SHAP attributions
│   ├── 04_evaluate_llm.py                 # Amazon Bedrock Evaluations job on the deployed agent
│   ├── 05_create_policies.py              # Upload illustrative Cedar policies + show RBAC matrix
│   ├── 06_test_access_control.py          # Live RBAC testing via IAM role assumption (ALLOW/DENY)
│   ├── 07_emergency_override.py           # Physician override workflow
│   ├── 08_setup_tracing.py                # CloudWatch verification
│   ├── 09_simulate_diagnostic.py          # Live agent + metrics
│   ├── 10_alert_low_confidence.py         # Alert + drift detection
│   ├── 11_invoke_agentcore.py             # Invoke the deployed agent
│   ├── setup_guardrail.py                 # Automated Reasoning guardrail
│   ├── setup_mlflow.py                    # SageMaker AI MLflow App
│   ├── populate_dashboard.py              # Emit metrics for dashboard
│   ├── test_end_to_end.py                 # Full integration test
│   ├── verify_solution.py                 # Live verification (make verify-live)
│   ├── scan_summary.py                    # Scan report (make scan-all); allows only the documented exceptions
│   └── cleanup.py                         # Teardown phases (make full-cleanup)
├── infra/
│   ├── terraform.tf                       # Terraform and provider version constraints
│   ├── providers.tf                       # Provider config (primary + replica Region)
│   ├── vpc.tf                             # VPC, subnets, endpoints, Network Firewall
│   ├── s3.tf                              # Data lake and audit buckets (CMK, versioned), CloudTrail SNS topic
│   ├── replication.tf                     # Cross-Region replicas
│   ├── server_access_logs.tf              # S3 server access log destinations
│   ├── security.tf                        # CloudTrail, guardrail, bucket policies, Access Analyzer
│   ├── iam.tf                             # IAM roles (least-privilege, RBAC roles)
│   ├── lambda.tf                          # Lambda function, DLQ, S3 trigger, SNS alerts topic
│   ├── sagemaker.tf                       # MLflow App and Bedrock Evaluations roles
│   ├── knowledge_base.tf                  # Knowledge Base role and sources
│   ├── agentcore.tf                       # Cedar policies (illustrative)
│   ├── cloudwatch.tf                      # Dashboard, alarms, audit-log tamper rule, alert policies
│   ├── variables.tf                       # Input variables
│   ├── outputs.tf                         # Outputs
│   └── terraform.tfvars.example           # Example values
├── data/
│   ├── patient_diagnostic_data.csv        # Synthetic dataset (40 patients)
│   └── llm_eval_cases.json                # Synthetic cases for the LLM evaluation
├── knowledge_base/                        # Clinical guidelines (RAG source)
├── tests/                                 # Unit tests (see Testing)
├── docs/
│   ├── responsible-ai.md                  # RAI dimensions mapping
│   ├── deployment.md                      # Step-by-step deployment
│   ├── cleanup.md                         # Teardown and cost guide
│   └── compliance-considerations.md       # Compliance notes
└── .devcontainer/
    └── devcontainer.json                  # DevContainer config
```

---

## Contributing

We welcome community contributions. Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:

- Reporting bugs and requesting features
- Submitting pull requests
- Code style and testing requirements
- Code of Conduct

---

## Production recommendations

- **Run the AgentCore runtime in VPC mode.** This sample's runtime uses `networkMode: PUBLIC`, so the diagnostic path runs in an AWS-managed network while the ingestion Lambda runs in the VPC. Evaluate a [VPC-attached AgentCore runtime configuration](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html) so the diagnostic path shares the ingestion path's network isolation: agent subnets in AgentCore-supported Availability Zones, a security group with no ingress and egress only to interface endpoints, and interface endpoints for Bedrock Runtime, the Knowledge Base, CloudWatch Logs and X-Ray.
- **Make the gateway the only entry point.** Callers can still invoke the runtime directly with `bedrock-agentcore:InvokeAgentRuntime`, which bypasses the gateway's guardrail policies. Restrict the runtime to gateway traffic with a resource-based policy that allows only the gateway's execution role ([enforcing traffic through the gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-http-runtime.html#gateway-target-http-runtime-source-validation)), and grant callers `bedrock-agentcore:InvokeGateway` instead.
- **Tune the guardrail thresholds** with the policy engine in `LOG_ONLY` mode (`GATEWAY_POLICY_MODE=LOG_ONLY`) and the evaluation set before enforcing them on real traffic.

## Security

- **Network isolation**: All compute runs in private subnets with no internet access. AWS service access uses VPC Endpoints; anything else is dropped and logged by AWS Network Firewall.
- **Encryption**: Every data store Terraform creates is encrypted with a customer managed AWS KMS key, and S3 bucket policies deny non-TLS requests. Two log groups that Lambda and AgentCore create themselves are not encrypted, and objects written before the data lake key existed remain under the AWS managed `aws/s3` key; see [SECURITY.md](SECURITY.md).
- **IAM least privilege**: Each service component has its own IAM role with only the permissions required for its function.
- **RBAC enforcement**: IAM resource policies enforce least-privilege access to patient data, reports, and metrics (physician, nurse, admin, auditor) with explicit deny-by-default. Cedar policies illustrate per-action RBAC but are not enforced in this demo.
- **Guardrails at the AgentCore Gateway**: The gateway's policy engine runs in `ENFORCE` mode. Its Cedar policies permit IAM callers from this account and use [guardrails in policies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-guardrails-in-policies.html) to deny a request whose free text is a prompt attack (jailbreak, prompt injection), contains hate, insults or misconduct, or carries a direct identifier (SSN, email, passport, card number), before it reaches the agent. The agent's own Automated Reasoning guardrail is a second layer. See [docs/responsible-ai.md](docs/responsible-ai.md#guardrails-at-the-agentcore-gateway).
- **Bedrock Guardrails enforced**: Content safety guardrails (hate, violence, sexual, misconduct, prompt attack) are applied on every model invocation via `guardrailIdentifier`/`guardrailVersion` parameters.
- **Input validation and sanitization**: Structured patient data is checked against a schema (known fields and types only; the agent returns `INPUT_REJECTED`, the Lambda a 400) before any prompt is built. Free-text fields are then checked for prompt injection patterns, and boundary markers (`<PATIENT_DATA>`) separate patient data from instructions.
- **Fail-closed output validation**: Safety-critical fields (`primary_diagnosis`, `confidence`, `priority`) must be present in AI output — missing fields block delivery rather than pass through. Each withheld response emits `OutputValidationFailure`, and the `rai-healthcare-demo-output-validation-alert` alarm notifies the alerts topic.
- **Model traceability**: Every agent and Lambda result records the `model_id` that produced it.
- **No patient data in telemetry**: The agent's traces and OpenTelemetry logs keep timings, token counts, model and tool names but redact prompt and response content, and the model stream is not echoed to stdout. `make verify-live` fails if patient text from its test request appears in the runtime's log group.
- **Confidence safety floor**: The confidence threshold cannot be set below 0.50 regardless of environment variable configuration.
- **No PHI in error responses**: Lambda returns generic error messages to callers with a `request_id` for log correlation. Detailed errors are logged only to KMS-encrypted CloudWatch Logs.
- **Audit trail**: Every diagnostic request, override decision, and policy evaluation is logged to S3 and CloudWatch for compliance auditing.
- **Secrets management**: No credentials are hardcoded. All authentication uses IAM roles.

> **Note**: This is a demonstration project. CloudTrail, VPC flow logs and IAM Access Analyzer are deployed; for production healthcare deployments, additional controls such as HIPAA-eligible service configurations, AWS Config rules, and third-party security assessments would be required. See the hardening list in [SECURITY.md](SECURITY.md).

See [CONTRIBUTING](CONTRIBUTING.md#security-issues) for how to report security issues.

---

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.

---

## Important Notice

This sample is for educational purposes only — not a substitute for professional medical advice. All patient data is synthetic; do not input real PHI. Provided "as-is" without warranty. See [LICENSE](LICENSE) and [docs/compliance-considerations.md](docs/compliance-considerations.md).
