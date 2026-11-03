# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}-(central|east|west|north|south|northeast|southeast|northwest|southwest)-[0-9]$", var.aws_region))
    error_message = "Must be a valid AWS region identifier."
  }
}

variable "replica_region" {
  description = "Region for S3 cross-Region replicas of the data lake and audit logs. Must differ from aws_region; keep it in the same jurisdiction as aws_region (replicas contain PHI)."
  type        = string
  default     = "us-west-2"

  validation {
    condition     = can(regex("^[a-z]{2}-(central|east|west|north|south|northeast|southeast|northwest|southwest)-[0-9]$", var.replica_region))
    error_message = "Must be a valid AWS region identifier."
  }
}

variable "project_name" {
  description = "Project name used for resource naming (lowercase, hyphens allowed)"
  type        = string
  default     = "rai-healthcare-demo"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,29}$", var.project_name))
    error_message = "Must be 3-30 chars, lowercase alphanumeric with hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "demo"

  validation {
    condition     = contains(["dev", "staging", "prod", "demo"], var.environment)
    error_message = "Must be one of: dev, staging, prod, demo."
  }
}

variable "bedrock_model_id" {
  description = "Bedrock base model ID. Restricted to the model scripts/04_evaluate_llm.py evaluates with Amazon Bedrock Evaluations; changing it requires re-running that evaluation and updating ALLOWED_MODEL_IDS in the Python code."
  type        = string
  default     = "anthropic.claude-sonnet-4-6"

  validation {
    condition     = contains(["anthropic.claude-sonnet-4-6"], var.bedrock_model_id)
    error_message = "Only the assessed model (anthropic.claude-sonnet-4-6) is allowed. Re-run scripts/04_evaluate_llm.py before changing models."
  }
}

variable "bedrock_inference_profile" {
  description = "Cross-Region inference profile prefix for the model. Claude Sonnet 4.6 does not support In-Region invocation in US Regions. Only the US profile is allowed: the global profile can route prompts, which carry patient data, outside the US."
  type        = string
  default     = "us"

  validation {
    condition     = var.bedrock_inference_profile == "us"
    error_message = "Only the US cross-Region inference profile (us) is allowed; the global profile can route patient data outside the US."
  }
}

variable "confidence_threshold" {
  description = "Confidence threshold (%) below which alerts are triggered"
  type        = number
  default     = 70

  validation {
    condition     = var.confidence_threshold >= 50 && var.confidence_threshold <= 100
    error_message = "Must be between 50 and 100 (safety floor = 50%)."
  }
}

variable "alarm_email" {
  description = "Email address for CloudWatch alarm notifications (leave empty to skip)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "agentcore_runtime_id" {
  description = <<-DESC
    ID of the Amazon Bedrock AgentCore runtime the clinical roles may invoke, as
    printed by `agentcore deploy` (format `<project>_<runtime>-<suffix>`). The
    runtime is created by the AgentCore CDK app AFTER Terraform runs, so the
    default is the agent project's name followed by a wildcard, which scopes the
    physician and nurse roles to this account's and Region's AgentCore runtimes
    for this agent project. Set it to the exact id and re-apply to narrow the
    grant to that single runtime.
  DESC
  type        = string
  default     = "raihealthdiag_*"

  validation {
    condition     = can(regex("^[A-Za-z0-9_*-]+$", var.agentcore_runtime_id))
    error_message = "Must be an AgentCore runtime id (letters, digits, underscore, hyphen) optionally ending in *."
  }
}

variable "judge_model_regions" {
  description = "Regions the US cross-Region inference profile for the evaluation judge (Amazon Nova Pro) can route to"
  type        = list(string)
  default     = ["us-east-1", "us-east-2", "us-west-2"]
}
