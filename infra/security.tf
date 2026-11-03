# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
###############################################################################
# Security — CloudTrail, S3 Bucket Policy, Lambda VPC, Bedrock Guardrail
###############################################################################

# ─────────────────────────────────────────────────────────────────────────────
# 1. CloudTrail — API-level audit trail
# ─────────────────────────────────────────────────────────────────────────────

# KMS key for CloudTrail log encryption
locals {
  # Built from the name (not aws_cloudtrail.main.arn): the trail depends on this key.
  cloudtrail_arn = "arn:aws:cloudtrail:${var.aws_region}:${data.aws_caller_identity.current.account_id}:trail/${var.project_name}-audit-trail"
}

resource "aws_kms_key" "cloudtrail" {
  description             = "KMS key for CloudTrail log encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowRootFullAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowCloudTrailEncrypt"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey*",
          "kms:DescribeKey"
        ]
        Resource = "*"
        # aws:SourceArn pins the service principal to this trail (confused-deputy protection):
        # https://docs.aws.amazon.com/awscloudtrail/latest/userguide/create-kms-key-policy-for-cloudtrail.html
        Condition = {
          StringLike = {
            "kms:EncryptionContext:aws:cloudtrail:arn" = "arn:aws:cloudtrail:*:${data.aws_caller_identity.current.account_id}:trail/*"
          }
          StringEquals = {
            "aws:SourceArn" = local.cloudtrail_arn
          }
        }
      },
      {
        Sid    = "AllowCloudTrailDecrypt"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          "Null" = {
            "kms:EncryptionContext:aws:cloudtrail:arn" = "false"
          }
        }
      },
      {
        Sid    = "AllowCloudWatchLogsEncrypt"
        Effect = "Allow"
        Principal = {
          Service = "logs.${var.aws_region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*"
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/cloudtrail/${var.project_name}*"
          }
        }
      },
      {
        # Lets CloudTrail publish to the KMS-encrypted notification topic
        # https://docs.aws.amazon.com/sns/latest/dg/sns-key-management.html
        Sid    = "AllowCloudTrailEncryptedSNS"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey*",
          "kms:Decrypt"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:SourceArn" = local.cloudtrail_arn
          }
        }
      },
      {
        # S3 publishes audit bucket deletion notifications to the encrypted topic.
        # https://docs.aws.amazon.com/AmazonS3/latest/userguide/grant-destinations-permissions-to-s3.html
        Sid    = "AllowS3NotificationsToUseTheKey"
        Effect = "Allow"
        Principal = {
          Service = "s3.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-cloudtrail-kms"
  }
}

resource "aws_kms_alias" "cloudtrail" {
  name          = "alias/${var.project_name}-cloudtrail"
  target_key_id = aws_kms_key.cloudtrail.key_id
}

# CloudWatch Log Group for CloudTrail
resource "aws_cloudwatch_log_group" "cloudtrail" {
  name              = "/aws/cloudtrail/${var.project_name}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.cloudtrail.arn

  tags = {
    Name = "${var.project_name}-cloudtrail-logs"
  }
}

# IAM role for CloudTrail to write to CloudWatch Logs
resource "aws_iam_role" "cloudtrail" {
  name = "${var.project_name}-cloudtrail-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "cloudtrail.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${var.project_name}-cloudtrail-role"
  }
}

resource "aws_iam_role_policy" "cloudtrail_logs" {
  name = "${var.project_name}-cloudtrail-logs-policy"
  role = aws_iam_role.cloudtrail.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowCloudWatchLogsPut"
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ]
      Resource = "${aws_cloudwatch_log_group.cloudtrail.arn}:*"
    }]
  })
}

# S3 bucket policy for CloudTrail delivery
# Each statement carries at most one condition: KICS cannot read a policy
# document whose statement has two condition blocks and would report the whole
# policy as accepting HTTP. The second condition of the write grant is expressed
# as an explicit deny on the same principal, which grants exactly the same access
# (allowed only when both hold).
data "aws_iam_policy_document" "cloudtrail_delivery" {
  statement {
    sid       = "CloudTrailAclCheck"
    effect    = "Allow"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.access_logs.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = ["arn:aws:cloudtrail:${var.aws_region}:${local.account_id}:trail/${var.project_name}-audit-trail"]
    }
  }

  statement {
    sid       = "CloudTrailWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.access_logs.arn}/cloudtrail/AWSLogs/${local.account_id}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = ["arn:aws:cloudtrail:${var.aws_region}:${local.account_id}:trail/${var.project_name}-audit-trail"]
    }
  }

  statement {
    sid       = "CloudTrailWriteRequiresOwnerControl"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.access_logs.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }

  statement {
    sid       = "EnforceSSLOnly"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.access_logs.arn, "${aws_s3_bucket.access_logs.arn}/*"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "cloudtrail_delivery" {
  bucket = aws_s3_bucket.access_logs.id
  policy = data.aws_iam_policy_document.cloudtrail_delivery.json
}

# The CloudTrail notification topic (aws_sns_topic.cloudtrail_notifications) is declared
# in s3.tf, next to the audit bucket notification that also publishes to it.

# Only this trail may publish (aws:SourceArn, per CloudTrail security best practice)
# https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-permissions-for-sns-notifications.html
# One statement: checkov 3.2.430 renders a jsonencode policy only when it has a single
# statement, so CKV_AWS_169/CKV_AWS_385 evaluate this policy. Each service sets
# aws:SourceArn to its own resource, so CloudTrail can publish only for the trail and S3
# only for the audit bucket (deletion notifications, s3.tf). CloudTrail recommends
# aws:SourceArn and also supports aws:SourceAccount:
# https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-permissions-for-sns-notifications.html
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/grant-destinations-permissions-to-s3.html
resource "aws_sns_topic_policy" "cloudtrail_notifications" {
  arn = aws_sns_topic.cloudtrail_notifications.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowThisTrailAndTheAuditBucketToPublish"
      Effect = "Allow"
      Principal = {
        Service = ["cloudtrail.amazonaws.com", "s3.amazonaws.com"]
      }
      Action   = "SNS:Publish"
      Resource = aws_sns_topic.cloudtrail_notifications.arn
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = [
            "arn:aws:cloudtrail:${var.aws_region}:${data.aws_caller_identity.current.account_id}:trail/${var.project_name}-audit-trail",
            "arn:aws:s3:::${aws_s3_bucket.access_logs.id}",
          ]
        }
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })
}

# CloudTrail trail (multi-region enabled for comprehensive audit)
resource "aws_cloudtrail" "main" {
  name                          = "${var.project_name}-audit-trail"
  s3_bucket_name                = aws_s3_bucket.access_logs.id
  s3_key_prefix                 = "cloudtrail"
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true
  kms_key_id                    = aws_kms_key.cloudtrail.arn
  cloud_watch_logs_group_arn    = "${aws_cloudwatch_log_group.cloudtrail.arn}:*"
  cloud_watch_logs_role_arn     = aws_iam_role.cloudtrail.arn
  # The provider argument takes the topic NAME; an ARN is only required when the
  # topic lives in another Region, and this one is in us-east-1 with the trail.
  # https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudtrail
  sns_topic_name = aws_sns_topic.cloudtrail_notifications.name

  event_selector {
    read_write_type           = "All"
    include_management_events = true

    data_resource {
      type   = "AWS::S3::Object"
      values = ["${aws_s3_bucket.data_lake.arn}/"]
    }
  }

  tags = {
    Name = "${var.project_name}-audit-trail"
  }

  depends_on = [aws_s3_bucket_policy.cloudtrail_delivery, aws_sns_topic_policy.cloudtrail_notifications]
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. S3 Bucket Policy — Enforce SSL-only access on data lake bucket
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "data_lake_ssl_only" {
  statement {
    sid       = "DenyNonHTTPS"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.data_lake.arn, "${aws_s3_bucket.data_lake.arn}/*"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "data_lake_ssl_only" {
  bucket = aws_s3_bucket.data_lake.id
  policy = data.aws_iam_policy_document.data_lake_ssl_only.json
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. Lambda in VPC — Move Lambda function into private subnets
# ─────────────────────────────────────────────────────────────────────────────

# Lambda ENI management (required for VPC-attached Lambda). Uses the AWS managed
# policy that the Lambda docs recommend; it includes ec2:DescribeSubnets, which a
# custom policy must also grant. Lambda validates these permissions on all
# resources, so they cannot be scoped:
# https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html
resource "aws_iam_role_policy_attachment" "lambda_vpc_access" {
  role       = aws_iam_role.lambda_preprocessing.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Least privilege (AWS-recommended): stop the function's own code from calling
# the EC2 ENI APIs. lambda:SourceFunctionArn is only present on calls made by
# function code, so the Lambda service can still manage ENIs.
resource "aws_iam_role_policy" "lambda_deny_code_ec2" {
  name = "${var.project_name}-lambda-deny-code-ec2"
  role = aws_iam_role.lambda_preprocessing.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "DenyFunctionCodeENIAccess"
      Effect = "Deny"
      Action = [
        "ec2:CreateNetworkInterface",
        "ec2:DeleteNetworkInterface",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DescribeSubnets",
        "ec2:DetachNetworkInterface",
        "ec2:AssignPrivateIpAddresses",
        "ec2:UnassignPrivateIpAddresses"
      ]
      Resource = "*"
      Condition = {
        ArnEquals = {
          "lambda:SourceFunctionArn" = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-preprocessing"
        }
      }
    }]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# 4. Bedrock Guardrail — Content filters for healthcare AI safety
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_bedrock_guardrail" "healthcare" {
  name                      = "${var.project_name}-guardrail"
  description               = "Content safety guardrail for healthcare diagnostic AI - blocks harmful, violent, sexual, and misconduct content"
  blocked_input_messaging   = "Your request was blocked by our content safety policy. Please rephrase your query to focus on clinical diagnostic information."
  blocked_outputs_messaging = "The AI response was blocked by our content safety policy. Please consult your physician for guidance."

  content_policy_config {
    filters_config {
      type            = "HATE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "SEXUAL"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "MISCONDUCT"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "INSULTS"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  tags = {
    Name        = "${var.project_name}-guardrail"
    Environment = var.environment
    Purpose     = "healthcare-ai-safety"
  }
}

resource "aws_bedrock_guardrail_version" "healthcare" {
  guardrail_arn = aws_bedrock_guardrail.healthcare.guardrail_arn
  description   = "Initial version - content filters for hate, violence, sexual, misconduct, and prompt injection"
}

# ─────────────────────────────────────────────────────────────────────────────
# Outputs
# ─────────────────────────────────────────────────────────────────────────────

output "guardrail_id" {
  description = "Bedrock Guardrail ID for use in GUARDRAIL_ID env var"
  value       = aws_bedrock_guardrail.healthcare.guardrail_id
}

output "guardrail_version" {
  description = "Bedrock Guardrail version number"
  value       = aws_bedrock_guardrail_version.healthcare.version
}

output "cloudtrail_arn" {
  description = "CloudTrail trail ARN for audit logging"
  value       = aws_cloudtrail.main.arn
}

output "lambda_security_group_id" {
  description = "Security group ID for the Lambda function in VPC"
  value       = aws_security_group.lambda.id
}

# ─────────────────────────────────────────────────────────────────────────────
# IAM Access Analyzer — external access findings for this account
# ─────────────────────────────────────────────────────────────────────────────
# ACCOUNT-type external access analysis has no charge. It reports any resource
# policy (S3, KMS, IAM roles, SQS, SNS and more) that grants access outside the
# account.
resource "aws_accessanalyzer_analyzer" "account" {
  analyzer_name = "${var.project_name}-account"
  type          = "ACCOUNT"
}
