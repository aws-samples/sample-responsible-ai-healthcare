# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
###############################################################################
# S3 Cross-Region Replication — data lake and audit logs
#
# Replicas are written to var.replica_region, encrypted with a customer managed
# KMS key in that Region. Deletes are replicated so the replica mirrors the
# source; the source buckets keep versioning, so prior versions stay recoverable.
#
# Replicas contain PHI. Keep replica_region in the same jurisdiction as
# aws_region and include it in your data-residency and BAA scope.
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-config-for-kms-objects.html
###############################################################################

locals {
  replicated_buckets = [aws_s3_bucket.data_lake, aws_s3_bucket.access_logs]
  replica_buckets    = [aws_s3_bucket.data_lake_replica, aws_s3_bucket.access_logs_replica]

  # All replication pairs, including the server access log buckets
  replication_sources      = concat(local.replicated_buckets, [aws_s3_bucket.server_access_logs])
  replication_destinations = concat(local.replica_buckets, [aws_s3_bucket.server_access_logs_replica])
}

# ─────────────────────────────────────────────────────────────────────────────
# Replica KMS key
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_kms_key" "replica" {
  provider                = aws.replica
  description             = "KMS key for S3 cross-Region replicas (${var.project_name})"
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
        # Replication with S3 Bucket Keys calls kms:Decrypt to verify the bucket
        # key, so the key policy must allow it for the replication role.
        Sid    = "AllowS3ReplicationRole"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.s3_replication.arn
        }
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "s3.${var.replica_region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

resource "aws_kms_alias" "replica" {
  provider      = aws.replica
  name          = "alias/${var.project_name}-s3-replica"
  target_key_id = aws_kms_key.replica.key_id
}

# ─────────────────────────────────────────────────────────────────────────────
# Replica buckets (same controls as the source buckets)
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_s3_bucket" "data_lake_replica" {
  provider = aws.replica

  # bucket_prefix (max 37 chars) keeps names within S3's 63-character limit
  bucket_prefix = "${substr(var.project_name, 0, 20)}-data-rep-"
  force_destroy = var.environment == "demo" ? true : false

  lifecycle {
    precondition {
      condition     = var.replica_region != var.aws_region
      error_message = "replica_region must differ from aws_region."
    }
  }
}

resource "aws_s3_bucket_versioning" "data_lake_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.data_lake_replica.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data_lake_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.data_lake_replica.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.replica.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "data_lake_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.data_lake_replica.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "data_lake_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.data_lake_replica.id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_notification" "data_lake_replica" {
  provider    = aws.replica
  bucket      = aws_s3_bucket.data_lake_replica.id
  eventbridge = true
}

# Access logs go to the replica Region's log destination bucket (server_access_logs.tf)
resource "aws_s3_bucket_logging" "data_lake_replica" {
  provider      = aws.replica
  bucket        = aws_s3_bucket.data_lake_replica.id
  target_bucket = aws_s3_bucket.server_access_logs_replica.id
  target_prefix = "replica-buckets/data-lake/"
}

resource "aws_s3_bucket" "access_logs_replica" {
  provider = aws.replica

  # bucket_prefix (max 37 chars) keeps names within S3's 63-character limit
  bucket_prefix = "${substr(var.project_name, 0, 20)}-logs-rep-"
  force_destroy = var.environment == "demo" ? true : false

  lifecycle {
    precondition {
      condition     = var.replica_region != var.aws_region
      error_message = "replica_region must differ from aws_region."
    }
  }
}

resource "aws_s3_bucket_versioning" "access_logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.access_logs_replica.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.access_logs_replica.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.replica.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "access_logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.access_logs_replica.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "access_logs_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.access_logs_replica.id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_notification" "access_logs_replica" {
  provider    = aws.replica
  bucket      = aws_s3_bucket.access_logs_replica.id
  eventbridge = true
}

# ─────────────────────────────────────────────────────────────────────────────
# Transport encryption on the replica buckets
#
# The source buckets deny plaintext access (infra/security.tf,
# infra/server_access_logs.tf). The replicas hold the same patient data and the
# same access logs, so they carry the same deny. Replication itself is
# unaffected: S3 replicates over TLS.
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html
# ─────────────────────────────────────────────────────────────────────────────
data "aws_iam_policy_document" "data_lake_replica_ssl_only" {
  statement {
    sid       = "DenyNonHTTPS"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.data_lake_replica.arn, "${aws_s3_bucket.data_lake_replica.arn}/*"]

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

resource "aws_s3_bucket_policy" "data_lake_replica_ssl_only" {
  provider = aws.replica
  bucket   = aws_s3_bucket.data_lake_replica.id
  policy   = data.aws_iam_policy_document.data_lake_replica_ssl_only.json
}

data "aws_iam_policy_document" "access_logs_replica_ssl_only" {
  statement {
    sid       = "DenyNonHTTPS"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.access_logs_replica.arn, "${aws_s3_bucket.access_logs_replica.arn}/*"]

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

resource "aws_s3_bucket_policy" "access_logs_replica_ssl_only" {
  provider = aws.replica
  bucket   = aws_s3_bucket.access_logs_replica.id
  policy   = data.aws_iam_policy_document.access_logs_replica_ssl_only.json
}

# ─────────────────────────────────────────────────────────────────────────────
# Replication role
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_iam_role" "s3_replication" {
  name = "${var.project_name}-s3-replication-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "s3.amazonaws.com"
      }
      Action = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "s3_replication" {
  name = "${var.project_name}-s3-replication-policy"
  role = aws_iam_role.s3_replication.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadSourceReplicationConfig"
        Effect   = "Allow"
        Action   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
        Resource = [for b in local.replication_sources : b.arn]
      },
      {
        Sid    = "ReadSourceObjectVersions"
        Effect = "Allow"
        Action = [
          "s3:GetObjectVersionForReplication",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionTagging"
        ]
        Resource = [for b in local.replication_sources : "${b.arn}/*"]
      },
      {
        Sid      = "WriteReplicas"
        Effect   = "Allow"
        Action   = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"]
        Resource = [for b in local.replication_destinations : "${b.arn}/*"]
      },
      {
        # Source objects use the account's S3 key, the CloudTrail CMK (CloudTrail
        # logs), or the access-log CMK (aws_kms_key.server_access_logs). Scoped to
        # KMS keys in this account, used through S3, for these buckets only. With
        # S3 Bucket Keys the encryption context is the bucket ARN.
        Sid      = "DecryptSourceObjects"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = "arn:aws:kms:${var.aws_region}:${data.aws_caller_identity.current.account_id}:key/*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "s3.${var.aws_region}.amazonaws.com"
          }
          StringLike = {
            "kms:EncryptionContext:aws:s3:arn" = flatten([for b in local.replication_sources : [b.arn, "${b.arn}/*"]])
          }
        }
      },
      {
        Sid      = "EncryptReplicas"
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.replica.arn
        Condition = {
          StringEquals = {
            "kms:ViaService" = "s3.${var.replica_region}.amazonaws.com"
          }
          StringLike = {
            "kms:EncryptionContext:aws:s3:arn" = flatten([for b in local.replication_destinations : [b.arn, "${b.arn}/*"]])
          }
        }
      }
    ]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# Replication configuration (one per source bucket)
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_s3_bucket_replication_configuration" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id
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
      bucket        = aws_s3_bucket.data_lake_replica.arn
      storage_class = "STANDARD"

      encryption_configuration {
        replica_kms_key_id = aws_kms_key.replica.arn
      }
    }
  }

  depends_on = [aws_s3_bucket_versioning.data_lake, aws_s3_bucket_versioning.data_lake_replica]
}

resource "aws_s3_bucket_replication_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
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
      bucket        = aws_s3_bucket.access_logs_replica.arn
      storage_class = "STANDARD"

      encryption_configuration {
        replica_kms_key_id = aws_kms_key.replica.arn
      }
    }
  }

  depends_on = [aws_s3_bucket_versioning.access_logs, aws_s3_bucket_versioning.access_logs_replica]
}
