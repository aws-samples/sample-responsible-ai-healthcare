# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
###############################################################################
# Outputs
###############################################################################

output "s3_bucket_name" {
  description = "S3 bucket for patient data and model artifacts"
  value       = aws_s3_bucket.data_lake.id
}

output "s3_data_uri" {
  description = "S3 URI for patient diagnostic data"
  value       = "s3://${aws_s3_bucket.data_lake.id}/${var.project_name}/data/"
}

output "sagemaker_role_arn" {
  description = "SageMaker execution role ARN"
  value       = aws_iam_role.sagemaker_execution.arn
}

output "cloudwatch_dashboard_url" {
  description = "CloudWatch dashboard URL"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${var.project_name}-monitoring"
}

output "sns_topic_arn" {
  description = "SNS topic ARN for RAI alerts"
  value       = aws_sns_topic.rai_alerts.arn
}

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "physician_role_arn" {
  description = "Physician IAM role ARN (for demo)"
  value       = aws_iam_role.physician_role.arn
}

output "nurse_role_arn" {
  description = "Nurse IAM role ARN (for demo)"
  value       = aws_iam_role.nurse_role.arn
}

output "admin_role_arn" {
  description = "Admin IAM role ARN (for demo)"
  value       = aws_iam_role.admin_role.arn
}

output "auditor_role_arn" {
  description = "Auditor IAM role ARN (for demo)"
  value       = aws_iam_role.auditor_role.arn
}

output "lambda_function_arn" {
  description = "Lambda preprocessing function ARN"
  value       = aws_lambda_function.preprocessing.arn
}

output "knowledge_base_s3_path" {
  description = "S3 path for clinical guidelines (Knowledge Base source)"
  value       = "s3://${aws_s3_bucket.data_lake.id}/${var.project_name}/knowledge-base/guidelines/"
}

output "deployment_instructions" {
  description = "Next steps after terraform apply"
  value       = <<-EOT

    Infrastructure deployed! Next steps:

    1. Run demo scripts (from project root):
       python scripts/01_prepare_data.py
       python scripts/02_bias_detection.py
       python scripts/03_shap_explainability.py

    2. Deploy AgentCore agent:
       make deploy-agent   (from the repository root)

    3. View CloudWatch dashboard in AWS Console

    4. Run observability demo:
       python scripts/08_setup_tracing.py
       python scripts/09_simulate_diagnostic.py

    5. Invoke the deployed agent:
       python scripts/11_invoke_agentcore.py
  EOT
}

output "mlflow_app_role_arn" {
  description = "Service role for the SageMaker AI MLflow App (pass to scripts/setup_mlflow.py)"
  value       = aws_iam_role.mlflow_app.arn
}

output "bedrock_evaluation_role_arn" {
  description = "Service role for the Amazon Bedrock Evaluations job (scripts/04_evaluate_llm.py)"
  value       = aws_iam_role.bedrock_evaluation.arn
}
