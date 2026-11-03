# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

locals {
  prefix = var.project_name

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    UseCase     = "responsible-ai-healthcare-demo"
  }

  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # The in-Region buckets that hold audit material: CloudTrail logs and the
  # S3 server access log destination. Used to scope the audit-log tamper
  # EventBridge rule in cloudwatch.tf. Both have `eventbridge = true` set on
  # their notification configuration, which is what makes them observable.
  audit_log_bucket_names = [
    aws_s3_bucket.access_logs.id,
    aws_s3_bucket.server_access_logs.id,
  ]

  # Amazon Bedrock AgentCore runtime the clinical roles may invoke.
  # bedrock-agentcore:InvokeAgentRuntime is authorized on the "runtime" and
  # "runtime-endpoint" resource types; this sample invokes the runtime ARN
  # directly (scripts/11_invoke_agentcore.py passes no qualifier), so only the
  # runtime resource is granted. The ARN format for "runtime-endpoint" is not
  # verified against a published reference, so it is not guessed here.
  # Action and resource types verified against the AWS Service Reference:
  # https://servicereference.us-east-1.amazonaws.com/v1/bedrock-agentcore/bedrock-agentcore.json
  agentcore_runtime_arn = "arn:aws:bedrock-agentcore:${var.aws_region}:${data.aws_caller_identity.current.account_id}:runtime/${var.agentcore_runtime_id}"
}
