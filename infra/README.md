# Infrastructure

Terraform configuration for the AWS Responsible AI Healthcare Demo.

## Prerequisites

- Terraform >= 1.5.0
- AWS CLI configured with appropriate credentials
- Amazon Bedrock model access enabled

## Usage

```bash
# Initialize
terraform init

# Preview
terraform plan

# Deploy
terraform apply

# Destroy
terraform destroy
```

## Files

| File | Purpose |
|------|---------|
| `terraform.tf` | Required providers and version constraints |
| `providers.tf` | AWS provider configuration with default tags |
| `backend.tf` | Remote state configuration (commented out) |
| `variables.tf` | Input variables with validation |
| `locals.tf` | Naming prefix and computed values |
| `main.tf` | Data sources |
| `vpc.tf` | VPC, private subnets, VPC endpoints, AWS Network Firewall (no internet or NAT gateway) |
| `s3.tf` | Data lake and audit buckets, KMS keys and lifecycle |
| `replication.tf` | Cross-Region replicas in `replica_region` |
| `server_access_logs.tf` | S3 server access log destinations |
| `security.tf` | CloudTrail, the content-filter guardrail, bucket policies, IAM Access Analyzer |
| `iam.tf` | IAM roles and least-privilege policies |
| `lambda.tf` | Lambda function and S3 trigger |
| `sagemaker.tf` | IAM roles of the SageMaker AI MLflow App and Amazon Bedrock Evaluations jobs (SageMaker Clarify is no longer open to new customers; these replace it per [AWS's guidance](https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html)) |
| `cloudwatch.tf` | Dashboard, alarms, log groups, SNS |
| `knowledge_base.tf` | Bedrock Knowledge Base resources |
| `agentcore.tf` | Cedar policies for AgentCore |
| `outputs.tf` | Output values |

## Inputs

| Name | Description | Default |
|------|-------------|---------|
| `aws_region` | AWS region | `us-east-1` |
| `project_name` | Resource naming prefix | `rai-healthcare-demo` |
| `environment` | Environment name | `demo` |
| `bedrock_model_id` | Bedrock base model (allowlisted) | `anthropic.claude-sonnet-4-6` |
| `bedrock_inference_profile` | Inference profile prefix (`us`/`global`) | `us` |
| `replica_region` | S3 cross-Region replica Region (must differ from `aws_region`) | `us-west-2` |
| `confidence_threshold` | Alert threshold as a **percent** (50-100). Divided by 100 for the Lambda's `CONFIDENCE_THRESHOLD`, which is a fraction in `[0.50, 1.0]` and fails startup outside it | `70` |
| `alarm_email` | SNS email (optional) | `""` |
| `judge_model_regions` | Regions the Bedrock Evaluations judge inference profile routes to | `["us-east-1", "us-east-2", "us-west-2"]` |

The SageMaker AI MLflow App is created by `scripts/setup_mlflow.py`, not Terraform: the AWS provider
has no MLflow App resource. The bias and SHAP Processing job's permissions are
`aws_iam_role_policy.sagemaker_processing` in `iam.tf`.
