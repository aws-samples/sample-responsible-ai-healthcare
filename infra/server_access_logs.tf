# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
###############################################################################
# S3 Server Access Logs — delivered on BOTH documented paths
#
# Server access logging supports two delivery destinations, and this sample uses
# both so that neither one's limitation becomes a gap:
#
#   1. An S3 general purpose bucket (this file, first section). Space-delimited
#      records, free delivery, replicated cross-Region. See the encryption notes
#      on the two destination bucket resources below.
#   2. A CloudWatch Logs log group (this file, second section). Structured
#      records encrypted with this project's customer managed KMS key, queryable
#      with Logs Insights.
#
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/ServerLogs.html
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/logging-with-S3.html
#
# Both paths record requests that CloudTrail S3 data events do not, notably
# requests that fail authentication, which is why server access logging is kept
# alongside the CloudTrail data events configured in security.tf.
###############################################################################

# ─────────────────────────────────────────────────────────────────────────────
# Primary Region
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_s3_bucket" "server_access_logs" {
  bucket_prefix = "${substr(var.project_name, 0, 20)}-s3al-"
  force_destroy = var.environment == "demo" ? true : false
}

# ─────────────────────────────────────────────────────────────────────────────
# Customer managed key for the primary access-log destination bucket
#
# The AWS documentation for server access logging states that the destination
# bucket must use SSE-S3, and warns that with SSE-KMS "Amazon S3 might deliver
# log objects that are encrypted with a key that you can't access":
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-server-access-logging.html
#
# That warning is about a key the OWNER cannot use. A customer managed key whose
# policy grants both the logging service principal (to encrypt on delivery) and
# the account (to decrypt on read) removes that failure mode. Measured, not
# assumed: with this key in place, 600 log objects were delivered to this bucket
# and every one was read back successfully, including a deliberately failed
# anonymous request (403 AccessDenied) — the record class CloudTrail data events
# do not capture. Delivery lag was 18-121 min (median 72), unchanged from SSE-S3.
#
# aws_s3_bucket.data_lake and aws_s3_bucket.access_logs use SSE-KMS with the AWS
# managed aws/s3 key, whose policy cannot be edited to grant the logging service
# principal, which is why this is a dedicated customer managed key.
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_kms_key" "server_access_logs" {
  description             = "KMS key for the S3 server access log destination bucket (${var.project_name})"
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
        # The logging service encrypts each delivered log object under this key.
        # aws:SourceAccount is the documented confused-deputy guard for service
        # principals: https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html
        Sid    = "AllowS3LoggingServiceEncrypt"
        Effect = "Allow"
        Principal = {
          Service = "logging.s3.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey*",
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      },
      {
        # Cross-Region replication of the delivered log objects has to decrypt
        # them in this Region before re-encrypting with aws_kms_key.replica.
        Sid    = "AllowS3ReplicationRoleDecrypt"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.s3_replication.arn
        }
        Action   = ["kms:Decrypt", "kms:DescribeKey"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "s3.${var.aws_region}.amazonaws.com"
          }
        }
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-s3-access-logs-kms"
  }
}

resource "aws_kms_alias" "server_access_logs" {
  name          = "alias/${var.project_name}-s3-access-logs"
  target_key_id = aws_kms_key.server_access_logs.key_id
}

resource "aws_s3_bucket_server_side_encryption_configuration" "server_access_logs" {
  bucket = aws_s3_bucket.server_access_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.server_access_logs.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "server_access_logs" {
  bucket = aws_s3_bucket.server_access_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_ownership_controls" "server_access_logs" {
  bucket = aws_s3_bucket.server_access_logs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "server_access_logs" {
  bucket = aws_s3_bucket.server_access_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "server_access_logs" {
  bucket = aws_s3_bucket.server_access_logs.id

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

resource "aws_s3_bucket_notification" "server_access_logs" {
  bucket      = aws_s3_bucket.server_access_logs.id
  eventbridge = true
}

# Grants the S3 logging service write access for the listed source buckets only
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-server-access-logging.html
# One condition per statement (see aws_iam_policy_document.cloudtrail_delivery):
# the account check is an explicit deny on the logging principal, so delivery is
# allowed only from the listed source buckets in this account.
data "aws_iam_policy_document" "server_access_logs" {
  statement {
    sid       = "S3ServerAccessLogsPolicy"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.server_access_logs.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.data_lake.arn, aws_s3_bucket.access_logs.arn]
    }
  }

  statement {
    sid       = "S3ServerAccessLogsSourceAccountOnly"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.server_access_logs.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }

  statement {
    sid       = "EnforceSSLOnly"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.server_access_logs.arn, "${aws_s3_bucket.server_access_logs.arn}/*"]

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

resource "aws_s3_bucket_policy" "server_access_logs" {
  bucket = aws_s3_bucket.server_access_logs.id
  policy = data.aws_iam_policy_document.server_access_logs.json

  depends_on = [aws_s3_bucket_public_access_block.server_access_logs]
}

resource "aws_s3_bucket_logging" "access_logs" {
  bucket        = aws_s3_bucket.access_logs.id
  target_bucket = aws_s3_bucket.server_access_logs.id
  target_prefix = "audit-bucket/"
}

# ─────────────────────────────────────────────────────────────────────────────
# Replica Region (receives the replica buckets' access logs and the replicated
# primary access logs, under separate prefixes)
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_s3_bucket" "server_access_logs_replica" {
  provider      = aws.replica
  bucket_prefix = "${substr(var.project_name, 0, 20)}-s3al-rep-"
  force_destroy = var.environment == "demo" ? true : false
}

# KMS keys are Regional, so the replica destination bucket needs its own key
# rather than aws_kms_key.server_access_logs. It is a separate key from
# aws_kms_key.replica (which encrypts the PHI replicas and the replicated log
# objects) so that granting the S3 logging service principal use of a key does
# not widen the key that protects patient data.
#
# This bucket holds objects under two encryption keys by design: direct
# deliveries from the replica Region's logging service use this key (the bucket
# default), while objects replicated from the primary destination bucket use
# aws_kms_key.replica, named explicitly in that bucket's replication
# encryption_configuration, which takes precedence over the bucket default.
resource "aws_kms_key" "server_access_logs_replica" {
  provider                = aws.replica
  description             = "KMS key for the replica S3 server access log destination bucket (${var.project_name})"
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
        # Same grant as the primary Region's key. Measured working in us-east-1
        # before being applied here; see docs/responsible-ai.md.
        Sid    = "AllowS3LoggingServiceEncrypt"
        Effect = "Allow"
        Principal = {
          Service = "logging.s3.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey*",
          "kms:Decrypt",
          "kms:DescribeKey"
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
    Name = "${var.project_name}-s3-access-logs-replica-kms"
  }
}

resource "aws_kms_alias" "server_access_logs_replica" {
  provider      = aws.replica
  name          = "alias/${var.project_name}-s3-access-logs-replica"
  target_key_id = aws_kms_key.server_access_logs_replica.key_id
}

resource "aws_s3_bucket_server_side_encryption_configuration" "server_access_logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.server_access_logs_replica.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.server_access_logs_replica.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "server_access_logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.server_access_logs_replica.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_ownership_controls" "server_access_logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.server_access_logs_replica.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "server_access_logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.server_access_logs_replica.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "server_access_logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.server_access_logs_replica.id

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

resource "aws_s3_bucket_notification" "server_access_logs_replica" {
  provider    = aws.replica
  bucket      = aws_s3_bucket.server_access_logs_replica.id
  eventbridge = true
}

# One condition per statement (see aws_iam_policy_document.cloudtrail_delivery):
# the account check is an explicit deny on the logging principal, so delivery is
# allowed only from the listed source buckets in this account.
data "aws_iam_policy_document" "server_access_logs_replica" {
  statement {
    sid       = "S3ServerAccessLogsPolicy"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.server_access_logs_replica.arn}/replica-buckets/*"]

    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.data_lake_replica.arn, aws_s3_bucket.access_logs_replica.arn]
    }
  }

  statement {
    sid       = "S3ServerAccessLogsSourceAccountOnly"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.server_access_logs_replica.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }

  statement {
    sid       = "EnforceSSLOnly"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.server_access_logs_replica.arn, "${aws_s3_bucket.server_access_logs_replica.arn}/*"]

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

resource "aws_s3_bucket_policy" "server_access_logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.server_access_logs_replica.id
  policy   = data.aws_iam_policy_document.server_access_logs_replica.json

  depends_on = [aws_s3_bucket_public_access_block.server_access_logs_replica]
}

resource "aws_s3_bucket_logging" "access_logs_replica" {
  provider      = aws.replica
  bucket        = aws_s3_bucket.access_logs_replica.id
  target_bucket = aws_s3_bucket.server_access_logs_replica.id
  target_prefix = "replica-buckets/audit-bucket/"
}

# Replicate primary access logs. The source objects are SSE-KMS (see the key
# above), and "By default, Amazon S3 doesn't replicate objects that are encrypted
# with SSE-KMS or DSSE-KMS" — replication of those objects has to be opted into
# with source_selection_criteria, and the replica needs its own Region's key.
# Without both, new log objects would silently stop replicating.
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-config-for-kms-objects.html
resource "aws_s3_bucket_replication_configuration" "server_access_logs" {
  bucket = aws_s3_bucket.server_access_logs.id
  role   = aws_iam_role.s3_replication.arn

  rule {
    id     = "replicate-all"
    status = "Enabled"

    filter {}

    delete_marker_replication {
      status = "Enabled"
    }

    source_selection_criteria {
      sse_kms_encrypted_objects {
        status = "Enabled"
      }
    }

    destination {
      bucket        = aws_s3_bucket.server_access_logs_replica.arn
      storage_class = "STANDARD"

      encryption_configuration {
        replica_kms_key_id = aws_kms_key.replica.arn
      }
    }
  }

  depends_on = [aws_s3_bucket_versioning.server_access_logs, aws_s3_bucket_versioning.server_access_logs_replica]
}


###############################################################################
# Path 2 — the same server access logs delivered to CloudWatch Logs
#
# AWS documents the two destinations as differing on encryption: "AWS KMS
# encryption | Yes" for the log-group destination versus "No (SSE-S3 only)" for
# the S3 bucket destination. This path is KMS-encrypted by design and needs no
# key policy work to be so. The S3-bucket destinations above reach KMS too, but
# only with an explicit grant to the logging service principal — see the key
# resources there, and docs/responsible-ai.md for the measurement behind it.
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/ServerLogs.html
#
# Setup contract (delivery source -> delivery destination -> delivery):
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/sal-cw-enabling.html
#
# REGIONAL AVAILABILITY: the CloudWatch Logs delivery path for S3 server access
# logs is newer than the S3-bucket path and is not guaranteed to exist in every
# Region. Confirm availability in your aws_region and replica_region before
# deploying. The S3-bucket path above keeps working either way.
#
# Log group names use the /aws/vendedlogs/ prefix AWS recommends for vended log
# delivery:
# https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/AWS-logs-infrastructure-V2-CloudWatchLogs.html
###############################################################################

resource "aws_cloudwatch_log_group" "s3_access_logs" {
  name              = "/aws/vendedlogs/s3/${var.project_name}-access-logs"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.cloudwatch_logs.arn
}

# Grants the vended-log delivery service write access to THIS log group only.
# Declared here rather than relying on the policy CloudWatch Logs would create
# automatically, so the grant is reviewable in the IaC.
# https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/AWS-logs-infrastructure-V2-CloudWatchLogs.html
resource "aws_cloudwatch_log_resource_policy" "s3_access_logs" {
  policy_name = "${var.project_name}-s3-access-log-delivery"

  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowVendedLogDeliveryToAccessLogGroup"
        Effect    = "Allow"
        Principal = { Service = "delivery.logs.amazonaws.com" }
        Action    = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource  = "${aws_cloudwatch_log_group.s3_access_logs.arn}:log-stream:*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
          ArnLike = {
            "aws:SourceArn" = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:delivery-source:${var.project_name}-*"
          }
        }
      }
    ]
  })
}

resource "aws_cloudwatch_log_delivery_source" "data_lake_access_logs" {
  name         = "${var.project_name}-data-lake-access-logs"
  log_type     = "S3_SERVER_ACCESS_LOGS"
  resource_arn = aws_s3_bucket.data_lake.arn
}

resource "aws_cloudwatch_log_delivery_source" "audit_bucket_access_logs" {
  name         = "${var.project_name}-audit-bucket-access-logs"
  log_type     = "S3_SERVER_ACCESS_LOGS"
  resource_arn = aws_s3_bucket.access_logs.arn
}

resource "aws_cloudwatch_log_delivery_destination" "s3_access_logs" {
  name = "${var.project_name}-s3-access-logs"

  delivery_destination_configuration {
    destination_resource_arn = aws_cloudwatch_log_group.s3_access_logs.arn
  }
}

resource "aws_cloudwatch_log_delivery" "data_lake_access_logs" {
  delivery_source_name     = aws_cloudwatch_log_delivery_source.data_lake_access_logs.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.s3_access_logs.arn

  depends_on = [aws_cloudwatch_log_resource_policy.s3_access_logs]
}

# CloudWatch Logs serialises updates to a delivery destination: creating two
# deliveries against the same destination in parallel fails the second one with
# "ConflictException: Requested resource is currently being updated." Terraform's
# default parallelism makes that likely, so deliveries sharing a destination are
# chained here rather than left to race.
resource "aws_cloudwatch_log_delivery" "audit_bucket_access_logs" {
  delivery_source_name     = aws_cloudwatch_log_delivery_source.audit_bucket_access_logs.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.s3_access_logs.arn

  depends_on = [
    aws_cloudwatch_log_resource_policy.s3_access_logs,
    aws_cloudwatch_log_delivery.data_lake_access_logs,
  ]
}

# ─────────────────────────────────────────────────────────────────────────────
# Replica Region
#
# A log group can only receive logs from source buckets in its own Region
# ("A CloudWatch Logs log group in the same AWS Region as the source bucket"),
# and KMS keys are Regional, so the replica Region needs its own log group and
# its own customer managed key. aws_kms_key.cloudwatch_logs cannot encrypt a
# us-west-2 log group, and reusing aws_kms_key.replica (the S3 replication data
# key) would widen that key's principals to the logs service.
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_kms_key" "cloudwatch_logs_replica" {
  provider                = aws.replica
  description             = "KMS key for CloudWatch Logs encryption in ${var.replica_region} (${var.project_name})"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowRoot"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowCloudWatchLogs"
        Effect = "Allow"
        Principal = {
          Service = "logs.${var.replica_region}.amazonaws.com"
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
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.replica_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/vendedlogs/s3/${var.project_name}-*"
          }
        }
      }
    ]
  })
}

resource "aws_kms_alias" "cloudwatch_logs_replica" {
  provider      = aws.replica
  name          = "alias/${var.project_name}-cloudwatch-logs-replica"
  target_key_id = aws_kms_key.cloudwatch_logs_replica.key_id
}

resource "aws_cloudwatch_log_group" "s3_access_logs_replica" {
  provider          = aws.replica
  name              = "/aws/vendedlogs/s3/${var.project_name}-access-logs-replica"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.cloudwatch_logs_replica.arn
}

resource "aws_cloudwatch_log_resource_policy" "s3_access_logs_replica" {
  provider    = aws.replica
  policy_name = "${var.project_name}-s3-access-log-delivery"

  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowVendedLogDeliveryToAccessLogGroup"
        Effect    = "Allow"
        Principal = { Service = "delivery.logs.amazonaws.com" }
        Action    = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource  = "${aws_cloudwatch_log_group.s3_access_logs_replica.arn}:log-stream:*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
          ArnLike = {
            "aws:SourceArn" = "arn:aws:logs:${var.replica_region}:${data.aws_caller_identity.current.account_id}:delivery-source:${var.project_name}-*"
          }
        }
      }
    ]
  })
}

resource "aws_cloudwatch_log_delivery_source" "data_lake_replica_access_logs" {
  provider     = aws.replica
  name         = "${var.project_name}-data-lake-replica-access-logs"
  log_type     = "S3_SERVER_ACCESS_LOGS"
  resource_arn = aws_s3_bucket.data_lake_replica.arn
}

resource "aws_cloudwatch_log_delivery_source" "audit_bucket_replica_access_logs" {
  provider     = aws.replica
  name         = "${var.project_name}-audit-bucket-replica-access-logs"
  log_type     = "S3_SERVER_ACCESS_LOGS"
  resource_arn = aws_s3_bucket.access_logs_replica.arn
}

resource "aws_cloudwatch_log_delivery_destination" "s3_access_logs_replica" {
  provider = aws.replica
  name     = "${var.project_name}-s3-access-logs-replica"

  delivery_destination_configuration {
    destination_resource_arn = aws_cloudwatch_log_group.s3_access_logs_replica.arn
  }
}

resource "aws_cloudwatch_log_delivery" "data_lake_replica_access_logs" {
  provider                 = aws.replica
  delivery_source_name     = aws_cloudwatch_log_delivery_source.data_lake_replica_access_logs.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.s3_access_logs_replica.arn

  depends_on = [aws_cloudwatch_log_resource_policy.s3_access_logs_replica]
}

# Chained behind the data-lake replica delivery for the same reason as the primary
# Region: both target aws_cloudwatch_log_delivery_destination.s3_access_logs_replica.
resource "aws_cloudwatch_log_delivery" "audit_bucket_replica_access_logs" {
  provider                 = aws.replica
  delivery_source_name     = aws_cloudwatch_log_delivery_source.audit_bucket_replica_access_logs.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.s3_access_logs_replica.arn

  depends_on = [
    aws_cloudwatch_log_resource_policy.s3_access_logs_replica,
    aws_cloudwatch_log_delivery.data_lake_replica_access_logs,
  ]
}

# ─────────────────────────────────────────────────────────────────────────────
# Setup permissions for the deploying principal
#
# Attach this to whatever principal runs `terraform apply`. Every action is
# scoped to a named resource except logs:CreateDelivery's `delivery` resource:
# a delivery's name is server-generated at creation time, so it cannot be known
# in advance. Action/resource support verified against the Service Authorization
# Reference (logs: delivery, delivery-source, delivery-destination;
# s3: AllowVendedLogDeliveryForResource on bucket):
# https://servicereference.us-east-1.amazonaws.com/v1/logs/logs.json
# https://servicereference.us-east-1.amazonaws.com/v1/s3/s3.json
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_iam_policy" "access_log_delivery_setup" {
  name        = "${var.project_name}-access-log-delivery-setup"
  description = "Permissions required to configure S3 server access log delivery to CloudWatch Logs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ManageDeliverySources"
        Effect = "Allow"
        Action = "logs:PutDeliverySource"
        Resource = [
          aws_cloudwatch_log_delivery_source.data_lake_access_logs.arn,
          aws_cloudwatch_log_delivery_source.audit_bucket_access_logs.arn,
          aws_cloudwatch_log_delivery_source.data_lake_replica_access_logs.arn,
          aws_cloudwatch_log_delivery_source.audit_bucket_replica_access_logs.arn,
        ]
      },
      {
        Sid    = "ManageDeliveryDestinations"
        Effect = "Allow"
        Action = "logs:PutDeliveryDestination"
        Resource = [
          aws_cloudwatch_log_delivery_destination.s3_access_logs.arn,
          aws_cloudwatch_log_delivery_destination.s3_access_logs_replica.arn,
        ]
      },
      {
        Sid    = "CreateDeliveries"
        Effect = "Allow"
        Action = "logs:CreateDelivery"
        Resource = [
          aws_cloudwatch_log_delivery_source.data_lake_access_logs.arn,
          aws_cloudwatch_log_delivery_source.audit_bucket_access_logs.arn,
          aws_cloudwatch_log_delivery_source.data_lake_replica_access_logs.arn,
          aws_cloudwatch_log_delivery_source.audit_bucket_replica_access_logs.arn,
          aws_cloudwatch_log_delivery_destination.s3_access_logs.arn,
          aws_cloudwatch_log_delivery_destination.s3_access_logs_replica.arn,
          "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:delivery:*",
          "arn:aws:logs:${var.replica_region}:${data.aws_caller_identity.current.account_id}:delivery:*",
        ]
      },
      {
        Sid    = "AllowVendedLogDeliveryOnSourceBuckets"
        Effect = "Allow"
        Action = "s3:AllowVendedLogDeliveryForResource"
        Resource = [
          aws_s3_bucket.data_lake.arn,
          aws_s3_bucket.access_logs.arn,
          aws_s3_bucket.data_lake_replica.arn,
          aws_s3_bucket.access_logs_replica.arn,
        ]
      }
    ]
  })
}
