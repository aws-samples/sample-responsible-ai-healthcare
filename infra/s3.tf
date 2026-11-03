# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
###############################################################################
# S3 — Patient Data Lake & Model Artifacts
###############################################################################

# force_destroy enabled ONLY for demo environment to allow easy cleanup.
# For non-demo deployments (dev/staging/prod), this is false to prevent
# accidental deletion of patient data or audit logs.
resource "aws_s3_bucket" "data_lake" {
  bucket        = "${var.project_name}-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  force_destroy = var.environment == "demo" ? true : false
}

resource "aws_s3_bucket_versioning" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Customer managed key for the patient data lake. Only the roles that read or
# write the bucket can use it, and only through Amazon S3. Objects written
# before this key existed stay under the AWS managed aws/s3 key and remain
# readable; new objects use this key.
resource "aws_kms_key" "data_lake" {
  description             = "KMS key for the patient data lake bucket (${var.project_name})"
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
        Sid    = "AllowBucketRolesThroughS3"
        Effect = "Allow"
        Principal = {
          AWS = [
            aws_iam_role.lambda_preprocessing.arn,
            aws_iam_role.knowledge_base.arn,
            aws_iam_role.sagemaker_execution.arn,
            aws_iam_role.mlflow_app.arn,
            aws_iam_role.bedrock_evaluation.arn,
            aws_iam_role.physician_role.arn,
            aws_iam_role.auditor_role.arn,
            aws_iam_role.s3_replication.arn,
          ]
        }
        Action = [
          "kms:Decrypt",
          "kms:Encrypt",
          "kms:GenerateDataKey*",
          "kms:ReEncrypt*",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "s3.${var.aws_region}.amazonaws.com"
          }
        }
      },
      {
        # Amazon Bedrock Evaluations encrypts its job data with this key
        # (customerEncryptionKeyId in scripts/04_evaluate_llm.py), limited to this
        # account's evaluation jobs.
        # https://docs.aws.amazon.com/bedrock/latest/userguide/model-evaluation-security-kms.html
        Sid       = "AllowBedrockEvaluationJobs"
        Effect    = "Allow"
        Principal = { Service = "bedrock.amazonaws.com" }
        Action    = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource  = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:evaluationJobArn" = "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:evaluation-job/*"
            "aws:SourceArn"                          = "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:evaluation-job/*"
          }
        }
      },
      {
        # DescribeKey carries no encryption context, so it needs its own statement
        Sid       = "AllowBedrockEvaluationDescribeKey"
        Effect    = "Allow"
        Principal = { Service = "bedrock.amazonaws.com" }
        Action    = "kms:DescribeKey"
        Resource  = "*"
        Condition = {
          ArnLike = {
            "aws:SourceArn" = "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:evaluation-job/*"
          }
        }
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-data-lake-kms"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data_lake.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  rule {
    id     = "archive-old-bias-reports"
    status = "Enabled"

    filter {
      # Bias reports are written under <project_name>/bias/ (see
      # scripts/02_bias_detection.py), so the rule has to carry the project
      # prefix. Without it the filter would match nothing.
      prefix = "${var.project_name}/bias/"
    }

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Access Logging Bucket
# ─────────────────────────────────────────────────────────────────────────────
# Audit bucket: CloudTrail logs (KMS-encrypted). S3 server access logs go to the
# dedicated destination buckets in server_access_logs.tf.
resource "aws_s3_bucket" "access_logs" {
  bucket_prefix = "${var.project_name}-logs-"
  force_destroy = var.environment == "demo" ? true : false
}

# Publish every object event on this bucket to EventBridge. On its own this only
# makes the events observable — the consumer is
# aws_cloudwatch_event_rule.audit_log_tamper in cloudwatch.tf, which matches
# API-driven deletes and ACL/tag/retention changes on this bucket and targets the
# alerts topic. See that resource for what the rule does and does not cover.
# SNS notifications for CloudTrail log file delivery and audit-log deletions
# (encrypted with the CloudTrail CMK). Declared next to the notification below.
resource "aws_sns_topic" "cloudtrail_notifications" {
  name              = "${var.project_name}-cloudtrail-notifications"
  kms_master_key_id = aws_kms_key.cloudtrail.id
}

resource "aws_s3_bucket_notification" "access_logs" {
  bucket      = aws_s3_bucket.access_logs.id
  eventbridge = true

  # Any API deletion of an audit log publishes to the CloudTrail topic.
  # Lifecycle expirations are a separate event type and do not.
  topic {
    topic_arn = aws_sns_topic.cloudtrail_notifications.arn
    events    = ["s3:ObjectRemoved:*"]
  }

  depends_on = [aws_sns_topic_policy.cloudtrail_notifications]
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Only CloudTrail writes here, and it already encrypts both log and digest files
# with the CloudTrail key, so the bucket default uses that same key.
resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.cloudtrail.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    id     = "expire-old-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = 365
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_logging" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  # Access-log destination bucket (see server_access_logs.tf)
  target_bucket = aws_s3_bucket.server_access_logs.id
  target_prefix = "access-logs/"
}

# Upload patient data
resource "aws_s3_object" "patient_data" {
  bucket = aws_s3_bucket.data_lake.id
  key    = "${var.project_name}/data/patient_diagnostic_data.csv"
  source = "${path.module}/../data/patient_diagnostic_data.csv"
  # The bucket default encryption is SSE-KMS (with BucketKeyEnabled), so the ETag
  # S3 returns is NOT an MD5 of the object content and can never equal filemd5() —
  # using etag here is a permanent diff by construction. source_hash is the
  # documented remedy and is stored only in Terraform state.
  # https://docs.aws.amazon.com/AmazonS3/latest/API/API_Object.html
  # https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_object
  source_hash = filemd5("${path.module}/../data/patient_diagnostic_data.csv")
}
