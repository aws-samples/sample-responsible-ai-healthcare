# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
###############################################################################
# Responsible AI assessment roles — SageMaker AI MLflow App and Amazon Bedrock
# Evaluations
#
# Amazon SageMaker Clarify is no longer open to new customers. The replacement
# AWS documents — bias metrics and SHAP in a processing job, results in a
# SageMaker AI MLflow App, LLM evaluation with Amazon Bedrock Evaluations — needs
# the two service roles below (the processing role is in iam.tf).
# https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html
###############################################################################

# ─────────────────────────────────────────────────────────────────────────────
# MLflow App service role — the app uses it to read and write its artifact
# store. The app itself is created by scripts/setup_mlflow.py (the AWS provider
# has no MLflow App resource). Model registration stays disabled, so no Model
# Registry permissions are granted.
# https://docs.aws.amazon.com/sagemaker/latest/dg/mlflow-app-setup-prerequisites-iam.html
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_iam_role" "mlflow_app" {
  name = "${var.project_name}-mlflow-app-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "sagemaker.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "mlflow_app" {
  name = "${var.project_name}-mlflow-artifacts"
  role = aws_iam_role.mlflow_app.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ArtifactObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.data_lake.arn}/${var.project_name}/mlflow/*"
      },
      {
        Sid      = "ArtifactListing"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.data_lake.arn
        Condition = {
          StringLike = { "s3:prefix" = ["${var.project_name}/mlflow/*", "${var.project_name}/mlflow"] }
        }
      },
      {
        # GetBucketLocation carries no s3:prefix, so it cannot share the listing condition
        Sid      = "ArtifactBucketLocation"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation"]
        Resource = aws_s3_bucket.data_lake.arn
      }
    ]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# Amazon Bedrock Evaluations service role (LLM-as-a-judge job)
# https://docs.aws.amazon.com/bedrock/latest/userguide/judge-service-roles.html
# The generator responses are supplied by scripts/04_evaluate_llm.py (bring your
# own inference responses), so the role invokes only the judge model.
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_iam_role" "bedrock_evaluation" {
  name = "${var.project_name}-bedrock-evaluation-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "bedrock.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
          ArnLike = {
            "aws:SourceArn" = "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:evaluation-job/*"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "bedrock_evaluation" {
  name = "${var.project_name}-bedrock-evaluation"
  role = aws_iam_role.bedrock_evaluation.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Judge model: Amazon Nova Pro through its US cross-Region inference profile,
        # which routes to the foundation model in these Regions.
        Sid    = "InvokeJudgeModel"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel"]
        Resource = concat(
          ["arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/us.amazon.nova-pro-v1:0"],
          [for r in var.judge_model_regions : "arn:aws:bedrock:${r}::foundation-model/amazon.nova-pro-v1:0"]
        )
      },
      {
        Sid      = "ReadPromptDataset"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.data_lake.arn}/${var.project_name}/evaluation/input/*"
      },
      {
        Sid      = "WriteResults"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = "${aws_s3_bucket.data_lake.arn}/${var.project_name}/evaluation/output/*"
      },
      {
        Sid      = "ListEvaluationPrefixes"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.data_lake.arn
        Condition = {
          StringLike = { "s3:prefix" = ["${var.project_name}/evaluation/*", "${var.project_name}/evaluation"] }
        }
      },
      {
        # Bucket-level calls that carry no s3:prefix, so they cannot share the listing condition
        Sid      = "EvaluationBucketMetadata"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
        Resource = aws_s3_bucket.data_lake.arn
      },
      {
        # The job runs as this role and encrypts its data with the data lake key
        # (customerEncryptionKeyId), so the role itself needs the key, not only through S3.
        # https://docs.aws.amazon.com/bedrock/latest/userguide/model-evaluation-security-kms.html
        Sid      = "UseDataLakeKey"
        Effect   = "Allow"
        Action   = ["kms:DescribeKey", "kms:Decrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.data_lake.arn
      }
    ]
  })
}
