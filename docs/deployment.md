# Deployment Guide

This guide walks through deploying the RAI Healthcare Diagnostic Assistant from scratch.

## Quick-Reference Deployment Checklist

Complete these steps in order. Each step has a verification command.

| # | Step | Command | Verification |
|---|------|---------|--------------|
| 1 | Install Python deps | `pip install -r requirements.txt` | `python -c "import boto3, pandas, shap, sklearn, mlflow"` |
| 2 | Configure environment | `cp .env.example .env && source .env` | `echo $AWS_ACCOUNT_ID` shows your ID |
| 3 | Deploy infrastructure | `cd infra && terraform init && terraform apply` | `terraform output s3_bucket_name` |
| 4 | Create Knowledge Base | Console or CLI (see Step 4 below) | 3 documents indexed |
| 5 | Setup guardrail | `python scripts/setup_guardrail.py` | `echo $GUARDRAIL_ID $GUARDRAIL_VERSION` are set |
| 6 | Deploy AgentCore agent | `make deploy-agent` | Agent responds to invocation |
| 7 | Run demos | `python scripts/01_prepare_data.py` ... | Scripts complete without error |

> **Important:** Steps 4-6 require manual intervention and cannot be skipped. The environment variables `GUARDRAIL_ID` and `AGENTCORE_RUNTIME_ID` must be set after steps 5 and 6 before running scripts 04 and 11. The guardrail (step 5) must exist before the agent is deployed (step 6).

## Prerequisites

### AWS Account Requirements

- An AWS account with access to the following services:
  - Amazon S3
  - Amazon SageMaker AI (Processing jobs and an MLflow App)
  - Amazon Bedrock (Claude model access enabled) and Amazon Bedrock Evaluations
  - Amazon Bedrock AgentCore
  - Amazon CloudWatch
  - Amazon SNS
  - Amazon VPC
  - AWS IAM
  - AWS KMS
  - Amazon OpenSearch Serverless (for Knowledge Base)

- IAM permissions to create: VPCs, subnets, AWS Network Firewall firewalls and policies, IAM Access Analyzer analyzers, S3 buckets, IAM roles/policies, SageMaker AI Processing jobs and MLflow Apps, Bedrock evaluation jobs, CloudWatch dashboards/alarms, SNS topics, VPC endpoints, OpenSearch Serverless collections.

- Bedrock model access granted for **both** of these models in your target region:
  - `anthropic.claude-sonnet-4-6` — clinical reasoning
  - `amazon.titan-embed-text-v2:0` — Knowledge Base embeddings

  Request access via the [Bedrock console](https://console.aws.amazon.com/bedrock/home#/modelaccess).

### Local Tools

| Tool | Minimum Version | Installation |
|------|----------------|--------------|
| AWS CLI | v2.x | [Install guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| Terraform | >= 1.5.0 | [Install guide](https://developer.hashicorp.com/terraform/install) |
| Python | >= 3.11 | [python.org](https://www.python.org/downloads/) |
| Node.js | >= 20 | Required for AgentCore CDK deployment (`aws-cdk-lib` requires Node.js 20 or later) |
| uv | any current release | The AgentCore CLI packages the agent with it: [install guide](https://docs.astral.sh/uv/getting-started/installation/) |
| AWS CDK | >= 2.x | `npm install -g aws-cdk` |
| AgentCore CLI | 0.13.1 | `npm install -g @aws/agentcore@0.13.1` (project pins `@aws/agentcore-cdk` 0.1.0-alpha.33) |

### Development and scanning tools

Needed only for `make test` and `make scan-all`, not to deploy.

| Tool | Used by | Installation |
|------|---------|--------------|
| pytest, ruff, bandit, pip-audit | `make test`, `make lint`, `make scan-all` | `pip install -e ".[dev]"` (pinned in `pyproject.toml`) |
| checkov 3.2.497 | `make scan-all` | Nothing to install: the Makefile runs it with `uvx`, isolated from the project environment |
| Docker | `make scan-all` (GitLab KICS image) | [Get Docker](https://docs.docker.com/get-started/get-docker/) |
| ASH (Automated Security Helper) | `make ash`, `make scan-all` | [ASH installation](https://awslabs.github.io/automated-security-helper/) |
| semgrep | `make scan-all` | `pip install semgrep` |
| Trivy | `make scan-all` | [Trivy installation](https://trivy.dev/latest/getting-started/installation/) |
| cfn-nag | `make scan-all` (CDK template) | `gem install cfn-nag` |

### AWS CLI Configuration

Ensure your AWS CLI is configured with credentials that have sufficient permissions:

```bash
aws configure
# or
export AWS_PROFILE=your-profile-name
```

Verify access:

```bash
aws sts get-caller-identity
```

## Step 1: Clone and Setup

```bash
git clone https://github.com/aws-samples/sample-responsible-ai-healthcare.git
cd sample-responsible-ai-healthcare

# Create and activate Python virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install Python dependencies
pip install -r requirements.txt
```

## Step 2: Configure Environment

```bash
# Copy the example environment file
cp .env.example .env
```

Edit `.env` with your values. **Important:** The `S3_BUCKET` value must match what Terraform will create. Terraform generates the bucket name as `<project_name>-<account_id>-<region>`. With default settings, this is:

```
S3_BUCKET=rai-healthcare-demo-<your-account-id>-us-east-1
```

Key variables to configure:

| Variable | Required | Description |
|----------|----------|-------------|
| `AWS_ACCOUNT_ID` | **Yes** | Your 12-digit AWS account ID |
| `S3_BUCKET` | **Yes** | Must be `rai-healthcare-demo-<account-id>-<region>` to match Terraform |
| `AWS_REGION` | No | AWS region for deployment (default: us-east-1) |
| `S3_PREFIX` | No | S3 key prefix (default: rai-healthcare-demo) |
| `BEDROCK_MODEL_ID` | No | Bedrock model to use (default: us.anthropic.claude-sonnet-4-6) |
| `CONFIDENCE_THRESHOLD` | No | Mandatory-review threshold as a **fraction** in `[0.50, 1.0]`, not a percent (default: 0.70). Outside that range the Lambda, the agent and `config.py` raise `RuntimeError` at startup. The Terraform variable `confidence_threshold` is the percent form (`70`) and is divided by 100 before it reaches the application |
| `KNOWLEDGE_BASE_ID` | For the agent deploy | Clinical guidelines Knowledge Base ID from Step 4. The CDK app refuses to synthesize runtimes without it |

Alarm email notifications are set with the Terraform variable `alarm_email` (Step 3), not an
environment variable.

Then source it:

```bash
source .env
```

## Step 3: Deploy Infrastructure (Terraform)

```bash
cd infra

# Copy and configure Terraform variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your preferred values

# Initialize Terraform
terraform init

# Preview the deployment plan
terraform plan

# Deploy infrastructure
terraform apply
```

Type `yes` when prompted to confirm.

> **Regional availability.** S3 server access log delivery to CloudWatch Logs is newer than delivery
> to an S3 bucket and is not available in every Region. If `apply` fails on
> `aws_cloudwatch_log_delivery_source` with an unsupported log type, your Region does not offer it
> yet — remove the CloudWatch delivery resources from `infra/server_access_logs.tf` and the
> S3-bucket path keeps working on its own. Reference:
> [Delivering server access logs to CloudWatch Logs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/sal-cw-enabling.html).
> Both paths are KMS-encrypted. The S3-bucket destinations need an explicit key policy grant to
> the logging service principal to get there; [responsible-ai.md](responsible-ai.md) under "Known
> scanner findings" records the measurement that confirmed it works.

Terraform will create:
- VPC with private subnets and VPC endpoints (S3, Bedrock Runtime, CloudWatch, X-Ray)
- S3 bucket with KMS encryption, versioning, and public access block
- IAM roles (bias and SHAP Processing job, MLflow App, Bedrock Evaluations, AgentCore, Lambda, Knowledge Base, Physician, Nurse, Admin, Auditor)
- Lambda function with DLQ, X-Ray tracing, and reserved concurrency
- CloudWatch dashboard (9 widgets), alarms (7), and log groups (8, KMS encrypted)
- S3 server access logging on both delivery paths: KMS-encrypted destination buckets (one per
  Region) and KMS-encrypted CloudWatch Logs log groups (one per Region)
- SNS topic for alerts
- SQS dead-letter queue (KMS encrypted)
- Cedar policies uploaded to S3
- Clinical guidelines uploaded to S3

Expected deployment time: 5-10 minutes.

Note the outputs after deployment completes:

```bash
terraform output
```

Save the `s3_bucket_name` and `sagemaker_role_arn` — you will need them later. `mlflow_app_role_arn` and `bedrock_evaluation_role_arn` are the roles `scripts/setup_mlflow.py` and `scripts/04_evaluate_llm.py` use.

## Step 4: Create Bedrock Knowledge Base

The Knowledge Base provides clinical guideline grounding (RAG) for the diagnostic agent. Terraform uploads the guideline documents to S3, but the Knowledge Base itself must be created via the console or CLI.

### Option A: AWS Console (Recommended for first-time setup)

1. Open the [Bedrock console](https://console.aws.amazon.com/bedrock/home#/knowledge-bases)
2. Open the **Create** menu (▾) and choose **Self-managed KB → Unstructured Vector Store KB**.
   Do **not** choose *Managed KB*, the console default: a managed knowledge base rejects the
   `vectorSearchConfiguration` the agent sends to `Retrieve` (`src/agent/app/raihealthdiag/main.py`).
3. Configure:
   - Name: `rai-healthcare-clinical-guidelines`
   - IAM role: Select the `rai-healthcare-demo-kb-role` created by Terraform
   - Data source: S3
   - S3 URI: `s3://<your-bucket>/rai-healthcare-demo/knowledge-base/guidelines/`
   - Embedding model: `Amazon Titan Embed Text v2`
   - Vector store: Create new OpenSearch Serverless collection (quick create)
4. Click **Create** and wait for status to become ACTIVE. If creation fails after the
   OpenSearch Serverless collection appears, open the collection's data access policy and check
   its principal: the console can add `role/service-role/rai-healthcare-demo-kb-role`, while
   Terraform creates the role without a path (`role/rai-healthcare-demo-kb-role`). Add the
   role's real ARN (`aws iam get-role --role-name rai-healthcare-demo-kb-role`) to the policy
   and create the knowledge base again.
5. Click **Sync** to index the 3 clinical guideline documents
6. Note the Knowledge Base ID and `export KNOWLEDGE_BASE_ID=<kb-id>`: the agent deployment (Step 6)
   requires it, and the agent calls `Retrieve` on this Knowledge Base before every diagnosis

The OpenSearch Serverless collection bills by the hour while it exists. `make full-cleanup` deletes
it: `scripts/cleanup.py` reads the collection ARN from the Knowledge Base before deleting the
Knowledge Base, so a console-generated collection name is found too.

### Option B: AWS CLI

The AWS CLI cannot create the vector index inside an OpenSearch Serverless collection (that is an
OpenSearch API call, not an AWS API), so there is no CLI-only path for a new vector store. Use
Option A to create the Knowledge Base with its vector store; afterwards the CLI can sync it:

```bash
aws bedrock-agent list-knowledge-bases \
  --query "knowledgeBaseSummaries[?name=='rai-healthcare-clinical-guidelines'].knowledgeBaseId" --output text
aws bedrock-agent list-data-sources --knowledge-base-id <kb-id>
aws bedrock-agent start-ingestion-job --knowledge-base-id <kb-id> --data-source-id <ds-id>
```

Then export the ID for the agent deployment (Step 6):

```bash
export KNOWLEDGE_BASE_ID=<kb-id>
```

Verify: 3 documents should be indexed (AHA/ACC STEMI guidelines, cardiac risk assessment, medication safety).

## Step 5: Setup Automated Reasoning Guardrail

```bash
# From project root
python scripts/setup_guardrail.py
```

This creates:
- An Automated Reasoning policy from `scripts/clinical_rules.txt`
- A Bedrock Guardrail that validates AI outputs against those rules and applies
  content filters (including the PROMPT_ATTACK filter)

After the script completes, note the Guardrail ID and update your environment:

```bash
export GUARDRAIL_ID=<guardrail-id-from-output>
export GUARDRAIL_VERSION=<guardrail-version-from-output>
```

`GUARDRAIL_VERSION=DRAFT` works for experimentation, but DRAFT is mutable. Publish a
version (`aws bedrock create-guardrail-version`) and use its number outside demo use.

If the script cannot create the policy via API (permissions vary by region), follow the manual steps it prints to create the policy in the Bedrock console.

## Step 6: Deploy AgentCore Agent

The diagnostic agent runs on Amazon Bedrock AgentCore using the Strands SDK.

### Bootstrap CDK (first time only)

```bash
npx cdk bootstrap aws://${AWS_ACCOUNT_ID}/${AWS_REGION}
```

### Deploy the agent

The agent refuses to start without a guardrail, and synthesis fails if
`GUARDRAIL_ID` and `GUARDRAIL_VERSION` are not set (Step 5). The CDK stack injects
them into the runtime and grants the runtime role `bedrock:ApplyGuardrail`.

Set `GUARDRAIL_ID`/`GUARDRAIL_VERSION` to the Automated-Reasoning guardrail created by
`scripts/setup_guardrail.py` in Step 5 (the agent's intended guardrail), and set
`AR_POLICY_ID`/`AR_POLICY_VERSION` from the same output; synthesis fails without them. Export
all four in the shell you deploy from, **after** any `source .env`: `.env` sets `GUARDRAIL_ID`
to the Lambda's content-filter guardrail, and deploying with that value silently removes the
Automated Reasoning check from the agent.

```bash
export GUARDRAIL_ID=<automated-reasoning-guardrail-id> GUARDRAIL_VERSION=<version>
export AR_POLICY_ID=<policy-id> AR_POLICY_VERSION=<policy-version>
export KNOWLEDGE_BASE_ID=<kb-id>   # Step 4; synthesis fails without it
```

After deploying, `make verify-live` (with `EXPECTED_AGENT_GUARDRAIL_ID` set to the same
guardrail id) fails if the runtime is bound to any other guardrail.

First create the deployment target file. It carries your 12-digit AWS account id, so git does not
track it — only the example is tracked:

```bash
cp src/agent/agentcore/aws-targets.json.example src/agent/agentcore/aws-targets.json
# Edit it and replace 123456789012 with your own account id (and the region, if it differs)
```

Then deploy the agent stack with the project's pinned CDK CLI, from the repository root:

```bash
make deploy-agent
```

That is `cd src/agent/agentcore/cdk && npm ci && npm run build && ./node_modules/.bin/cdk deploy`. CDK shows
the IAM changes and asks for confirmation.

> Use `make deploy-agent`, not `agentcore deploy`: the `agentcore` CLI 0.13.1 bundles a CDK
> toolkit that cannot read this project's cloud assembly (`aws-cdk-lib` 2.273.0 writes schema
> 54; the bundled toolkit supports up to 53), so `agentcore deploy` fails at synthesis. The
> project pins `@aws/agentcore-cdk` `0.1.0-alpha.33` and the CDK CLI `aws-cdk` 2.1145.0 in
> `src/agent/agentcore/cdk/package.json`.
> The CDK stack also deploys an AgentCore Gateway with a policy engine in front of the runtime
> (`src/agent/agentcore/cdk/lib/diagnostic-gateway.ts`); its Cedar policies apply guardrails to
> every request. It needs nothing extra at deploy time. Set `GATEWAY_POLICY_MODE=LOG_ONLY` only to
> tune thresholds (default `ENFORCE`). After deploying, export the stack output `GatewayId` as
> `AGENTCORE_GATEWAY_ID` to invoke the agent through the gateway.
>
> `agentcore.json` keeps `policyEngines` empty on purpose — the Cedar policies under
> `src/agent/agentcore/policies/` are illustrative RBAC artifacts; runtime Cedar enforcement
> requires an AgentCore Gateway (see `docs/responsible-ai.md`). Access control in this sample is
> enforced by IAM.

### After deployment

Note the **Runtime ID** from the deployment output (format: `raihealthdiag_raihealthdiag-XXXXXXXXXX`). Set it in your environment:

```bash
export AGENTCORE_RUNTIME_ID=<runtime-id-from-output>
```

Verify the agent is running:

```bash
aws bedrock-agentcore invoke-agent-runtime \
  --cli-binary-format raw-in-base64-out \
  --agent-runtime-arn "arn:aws:bedrock-agentcore:${AWS_REGION}:${AWS_ACCOUNT_ID}:runtime/${AGENTCORE_RUNTIME_ID}" \
  --payload '{"patient_data": {"age": 67, "gender": "M", "chief_complaint": "Chest pain radiating to left arm", "vital_signs": {"heart_rate": 102, "blood_pressure": "165/95"}}}' \
  --content-type "application/json" \
  --accept "application/json" \
  /dev/stdout
```

You should see a single JSON diagnostic recommendation that includes `confidence` and
`mandatory_physician_review`.

Output is validated before delivery, and the validator checks **shape, not topic**
(`validate_diagnostic_output` in `src/agent/app/raihealthdiag/main.py`). Model output that is not a
JSON object, that omits a safety-critical field, or whose `confidence` is non-numeric is discarded
and the caller receives `{"status": "VALIDATION_FAILED", ...}` instead.

A non-clinical free-form prompt normally does **not** take that path. The system prompt makes the
model refuse *inside* the required schema — the observed shape is a schema-valid frame with
`primary_diagnosis` set to a non-clinical placeholder and the refusal carried in
`recommended_actions` — so the refusal passes validation and is delivered as an ordinary validated
frame. Both outcomes satisfy the control that matters: nothing reaches the caller until it has been
validated, so unvalidated model text is never streamed.

## Step 7: Run Demo Scripts

Run the demos in order from the project root:

### Demo 1: Bias, Explainability and LLM Evaluation

SageMaker Clarify is no longer open to new customers, so this demo follows AWS's replacement guidance ([availability change](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html)): bias metrics and SHAP in a SageMaker AI Processing job, results tracked in a SageMaker AI MLflow App, and the LLM evaluated with Amazon Bedrock Evaluations.

```bash
# (Optional) Create the SageMaker AI MLflow App (creation takes about 25 minutes) and export the ARN it prints.
# The AWS Terraform provider has no MLflow App resource, so this script creates it.
python scripts/setup_mlflow.py
export MLFLOW_TRACKING_ARN=<arn-from-output>

# Prepare and upload synthetic patient dataset to S3
python scripts/01_prepare_data.py

# Bias metrics (CI, DPL, AD) and SHAP in a SageMaker AI Processing job (a few minutes)
python scripts/02_bias_detection.py

# Show the job's SHAP attributions and a Bedrock-written narrative of them
python scripts/03_shap_explainability.py

# Evaluate the deployed agent with Amazon Bedrock Evaluations (needs the runtime from Step 6;
# the script waits up to an hour for the judge job)
export AGENTCORE_RUNTIME_ID=<runtime-id>
python scripts/04_evaluate_llm.py
```

The MLflow App runs MLflow 3.10, so the client is `mlflow-skinny` 3.10 with the `sagemaker-mlflow` plugin, as [Integrate MLflow with your environment](https://docs.aws.amazon.com/sagemaker/latest/dg/mlflow-track-experiments.html) requires (client version matches the server). To open the MLflow UI:

```bash
aws sagemaker create-presigned-mlflow-app-url --arn "$MLFLOW_TRACKING_ARN"
```

### Demo 2: AgentCore Policy and Identity

```bash
# Verify Cedar policies in S3 and display RBAC matrix
python scripts/05_create_policies.py

# Test role-based access control (assumes IAM roles)
python scripts/06_test_access_control.py

# Demonstrate emergency physician override workflow
python scripts/07_emergency_override.py
```

### Demo 3: AgentCore Observability

```bash
# Verify CloudWatch log groups, alarms, and dashboard
python scripts/08_setup_tracing.py

# Simulate diagnostic queries (emits metrics)
python scripts/09_simulate_diagnostic.py

# Trigger low-confidence alerts and drift detection
python scripts/10_alert_low_confidence.py
```

### Invoke the Deployed Agent

```bash
# Invoke the live Strands agent on AgentCore Runtime
python scripts/11_invoke_agentcore.py
```

## Step 8: Verify Deployment

### Check CloudWatch Dashboard

```bash
aws cloudwatch get-dashboard --dashboard-name rai-healthcare-demo-monitoring
```

Or open the CloudWatch console and navigate to the `rai-healthcare-demo-monitoring` dashboard.

### Check Alarms

```bash
aws cloudwatch describe-alarms --alarm-name-prefix rai-healthcare-demo
```

### Check S3 Data

```bash
aws s3 ls s3://$S3_BUCKET/$S3_PREFIX/ --recursive
```

### Check Knowledge Base

```bash
aws bedrock-agent list-knowledge-bases
```

### Check AgentCore Runtime

```bash
python scripts/11_invoke_agentcore.py
```

### Check CloudWatch Logs

```bash
aws logs describe-log-groups --log-group-name-prefix /aws/agentcore/rai-healthcare-demo
```

## Troubleshooting

### Bedrock Model Access Denied

If you receive `AccessDeniedException` when invoking Bedrock:
1. Verify model access is granted in the Bedrock console for **both** `anthropic.claude-sonnet-4-6` and `amazon.titan-embed-text-v2:0`.
2. Ensure the IAM role has `bedrock:InvokeModel` permission for the specific model ARN.

### S3 Bucket Name Mismatch

If scripts fail with "NoSuchBucket" errors:
- Terraform creates the bucket as `rai-healthcare-demo-<account-id>-<region>`
- Your `S3_BUCKET` environment variable must match this exactly
- Run `terraform output s3_bucket_name` to see the actual bucket name

### AgentCore Runtime Not Found

If `scripts/11_invoke_agentcore.py` fails:
- Ensure `AGENTCORE_RUNTIME_ID` is set in your environment
- Verify the agent is deployed: check the AgentCore console or run `agentcore status`
- The agent may take 1-2 minutes to become available after initial deployment (cold start)

### Knowledge Base Returns Empty Results

If the Knowledge Base doesn't return clinical guidelines:
1. Verify the data source sync completed: `aws bedrock-agent list-ingestion-jobs --knowledge-base-id <kb-id> --data-source-id <ds-id>`
2. Check that 3 documents were indexed (0 failed)
3. Ensure the OpenSearch Serverless data access policy includes your role

### Terraform Apply Fails with "Access Denied"

Ensure your AWS credentials have sufficient permissions. The deploying principal needs permissions to create IAM roles, VPC resources, S3 buckets, Lambda functions, SageMaker AI Processing jobs, and KMS keys. Using an administrator role for initial deployment is recommended.

### Terraform State Issues

If Terraform state becomes inconsistent:
```bash
cd infra
terraform refresh
terraform plan
```

### SageMaker AI Processing Job Failures

`scripts/02_bias_detection.py` prints the job's `FailureReason`. For details:
```bash
aws sagemaker describe-processing-job --processing-job-name <job-name>
aws logs describe-log-streams --log-group-name /aws/sagemaker/ProcessingJobs \
  --log-stream-name-prefix <job-name>
```

If the job cannot start for capacity, check the Service Quotas value "ml.t3.medium for processing job usage", or set `BIAS_JOB_INSTANCE_TYPE=ml.m5.large`.

### Bedrock Evaluation Job Failures

`scripts/04_evaluate_llm.py` prints the job's `failureMessages`. For the full status:
```bash
aws bedrock get-evaluation-job --job-identifier <job-arn>
```

Enable model access for the judge (Amazon Nova Pro) in every Region its inference profile routes to (Terraform variable `judge_model_regions`).

### VPC Endpoint Connectivity

If services cannot communicate within the VPC:
1. Verify security group allows port 443 inbound from the VPC CIDR.
2. Check that the VPC endpoint has private DNS enabled.
3. Confirm route table associations for private subnets.

### CloudWatch Alarms Stuck in INSUFFICIENT_DATA

Alarms remain in `INSUFFICIENT_DATA` until metrics are first emitted. Run Demo 3 scripts to populate metrics, or use:

```bash
python scripts/populate_dashboard.py
```

## Estimated Cost

For a single demo session:

| Service | Estimated Cost |
|---------|---------------|
| AWS Network Firewall | ~$0.40 (for 1 hour) |
| VPC Endpoints (3 Interface) | ~$0.03 |
| OpenSearch Serverless (Knowledge Base) | Per OCU-hour while the collection exists; see [OpenSearch Service pricing](https://aws.amazon.com/opensearch-service/pricing/) |
| SageMaker AI Processing (bias + SHAP, ml.t3.medium at $0.05/hr) | A few cents |
| Amazon Bedrock Evaluations (judge Amazon Nova Pro: $0.0008/1K input, $0.0032/1K output tokens) | Under $1 |
| SageMaker AI MLflow App | Not published; see [SageMaker AI pricing](https://aws.amazon.com/sagemaker/ai/pricing/) |
| Bedrock inference (Claude Sonnet) | ~$0.50-2.00 |
| AgentCore Runtime | Per-invocation |
| CloudWatch | ~$0.10 |
| **Total per session** | **$5-15** |

**If left running idle (monthly):**

| Resource | Monthly Cost |
|----------|-------------|
| AWS Network Firewall | ~$288 |
| OpenSearch Serverless | Per OCU-hour for every hour the collection exists; see [OpenSearch Service pricing](https://aws.amazon.com/opensearch-service/pricing/) |
| VPC Endpoints | ~$22 |
| SageMaker AI MLflow App (if not deleted) | Not published |
| **Total idle** | **~$310/month** plus OpenSearch Serverless and the MLflow App |

**Important**: Run `terraform destroy` immediately after completing the demo to avoid ongoing charges. See the [Cleanup Guide](cleanup.md).
