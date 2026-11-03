# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
###############################################################################
# Bedrock Knowledge Base — Clinical Guidelines
# Matches architecture: "Clinical Guidelines Knowledge Base" → Bedrock FM
###############################################################################

# S3 location for clinical guidelines documents
resource "aws_s3_object" "clinical_guidelines_folder" {
  bucket  = aws_s3_bucket.data_lake.id
  key     = "${var.project_name}/knowledge-base/guidelines/"
  content = ""
}

# Upload clinical guidelines documents
resource "aws_s3_object" "aha_guidelines" {
  bucket  = aws_s3_bucket.data_lake.id
  key     = "${var.project_name}/knowledge-base/guidelines/aha_acc_stemi_guidelines.txt"
  content = file("${path.module}/../knowledge_base/aha_acc_stemi_guidelines.txt")
}

resource "aws_s3_object" "cardiac_risk_guidelines" {
  bucket  = aws_s3_bucket.data_lake.id
  key     = "${var.project_name}/knowledge-base/guidelines/cardiac_risk_assessment.txt"
  content = file("${path.module}/../knowledge_base/cardiac_risk_assessment.txt")
}

resource "aws_s3_object" "medication_safety_guidelines" {
  bucket  = aws_s3_bucket.data_lake.id
  key     = "${var.project_name}/knowledge-base/guidelines/medication_safety.txt"
  content = file("${path.module}/../knowledge_base/medication_safety.txt")
}

# IAM role for Bedrock Knowledge Base
resource "aws_iam_role" "knowledge_base" {
  name = "${var.project_name}-kb-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "bedrock.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        # https://docs.aws.amazon.com/bedrock/latest/userguide/kb-permissions.html (Trust relationship)
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
          ArnLike = {
            "aws:SourceArn" = "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:knowledge-base/*"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "knowledge_base_permissions" {
  name = "${var.project_name}-kb-policy"
  role = aws_iam_role.knowledge_base.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3ReadGuidelines"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = [
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/knowledge-base/*"
        ]
      },
      {
        # Ingestion enumerates the data-source prefix, so the Knowledge Base role
        # needs s3:ListBucket. Constrained by s3:prefix to the guidelines prefix,
        # so it cannot list patient data or bias reports.
        # https://docs.aws.amazon.com/AmazonS3/latest/userguide/walkthrough1.html (s3:prefix condition)
        Sid    = "S3ListGuidelines"
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.data_lake.arn
        Condition = {
          StringLike = {
            "s3:prefix" = ["${var.project_name}/knowledge-base/*"]
          }
        }
      },
      {
        Sid    = "BedrockEmbeddings"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel"
        ]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"
      },
      {
        # Data-plane access to the OpenSearch Serverless vector store. Required in
        # addition to the collection's AOSS data access policy; without it Bedrock's
        # CreateKnowledgeBase validation call to the collection returns 403 Forbidden.
        # aoss:APIAccessAll operates on the Collection resource (Service Authorization
        # Reference: https://servicereference.us-east-1.amazonaws.com/v1/aoss/aoss.json).
        # collection/* because the console's quick-create vector store generates the
        # collection name and ID during Knowledge Base creation, so neither is known
        # here. Two controls narrow it: only Bedrock acting for a Knowledge Base in
        # this account can assume the role (trust policy above), and OpenSearch
        # Serverless also requires the collection's own data access policy to name
        # this role, which only the collection the Knowledge Base uses does.
        Sid    = "OpenSearchServerlessDataAccess"
        Effect = "Allow"
        Action = [
          "aoss:APIAccessAll"
        ]
        Resource = "arn:aws:aoss:${var.aws_region}:${data.aws_caller_identity.current.account_id}:collection/*"
      }
    ]
  })
}

# The Knowledge Base and its OpenSearch Serverless vector store are created in the
# console (docs/deployment.md Step 4): the vector index inside the collection is an
# OpenSearch API call, which the AWS provider cannot make. scripts/cleanup.py
# deletes both during teardown.
