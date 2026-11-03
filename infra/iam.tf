# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
###############################################################################
# IAM — Least Privilege Roles & Policies
# Following AWS Well-Architected Security Pillar: SEC03-BP02
#
# Statements whose actions cannot be resource-scoped (cloudwatch:PutMetricData)
# live in separate policies so every data-access policy contains only scoped ARNs.
#
# The SageMaker role has no ECR permissions: the scikit-learn processing image is
# a SageMaker pre-built image, pulled with service-owned credentials
# (docs.aws.amazon.com/sagemaker/latest/dg/docker-containers-prebuilt.html).
#
###############################################################################

# ─────────────────────────────────────────────────────────────────────────────
# SageMaker AI Processing role (bias metrics + SHAP job ONLY)
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_iam_role" "sagemaker_execution" {
  name = "${var.project_name}-sagemaker-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "sagemaker.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        # Only processing jobs assume this role (scripts/02_bias_detection.py).
        # https://docs.aws.amazon.com/sagemaker/latest/dg/security-confused-deputy-prevention.html
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
          ArnLike = {
            "aws:SourceArn" = "arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:processing-job/*"
          }
        }
      }
    ]
  })
}

# What the bias and explainability processing job itself does: read its code and
# input, write its two reports, write logs, and log runs to the MLflow App.
# The operator creates the job; the job role needs no SageMaker control-plane
# actions. https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-roles.html
resource "aws_iam_role_policy" "sagemaker_processing" {
  name = "${var.project_name}-sagemaker-processing"
  role = aws_iam_role.sagemaker_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadJobCodeAndInput"
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/bias/code/*",
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/bias/input/*"
        ]
      },
      {
        # The MLflow client uploads run artifacts straight to the app's artifact
        # store (mlflow/) with the caller's credentials, not through the app.
        Sid    = "WriteReports"
        Effect = "Allow"
        Action = ["s3:PutObject"]
        Resource = [
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/bias/*",
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/explainability/*",
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/mlflow/*"
        ]
      },
      {
        # Processing resolves S3Prefix inputs by listing them
        Sid      = "ListJobInputs"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.data_lake.arn
        Condition = {
          StringLike = {
            "s3:prefix" = ["${var.project_name}/bias/code/*", "${var.project_name}/bias/input/*"]
          }
        }
      },
      {
        Sid    = "ProcessingJobLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/sagemaker/ProcessingJobs:*"
      },
      {
        # MLflow App data plane. The app id is generated at creation
        # (scripts/setup_mlflow.py), so the ARN is scoped to this account's apps.
        # https://docs.aws.amazon.com/sagemaker/latest/dg/mlflow-app-setup-prerequisites-iam.html
        Sid      = "LogToMlflowApp"
        Effect   = "Allow"
        Action   = ["sagemaker:CallMlflowAppApi", "sagemaker:DescribeMlflowApp"]
        Resource = "arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:mlflow-app/*"
      }
    ]
  })
}

# The AgentCore runtime's execution role is created by the agent's CDK stack
# (src/agent/agentcore/cdk/lib/runtime-execution-role.ts), not here.
# ─────────────────────────────────────────────────────────────────────────────
# Demo User Roles (simulating hospital staff for Policy demo)
# Minimal permissions — only what each role needs
# ─────────────────────────────────────────────────────────────────────────────

# Physician: Can invoke the diagnostic agent (gateway ARN reserved for a production Gateway)
resource "aws_iam_role" "physician_role" {
  name = "${var.project_name}-physician-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = "physician-access"
          }
        }
      }
    ]
  })

  tags = {
    Role        = "Physician"
    AccessLevel = "Full"
  }
}

resource "aws_iam_role_policy" "physician_permissions" {
  name = "${var.project_name}-physician-policy"
  role = aws_iam_role.physician_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # InvokeAgentRuntime, NOT InvokeAgent: there is no
        # bedrock-agentcore:InvokeAgent action. Verified against the AWS Service
        # Reference, which lists InvokeAgentRuntime on the "runtime" resource
        # type and no InvokeAgent at all:
        # https://servicereference.us-east-1.amazonaws.com/v1/bedrock-agentcore/bedrock-agentcore.json
        Sid    = "InvokeAgentRuntime"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:InvokeAgentRuntime"
        ]
        Resource = local.agentcore_runtime_arn
      },
      {
        Sid    = "ReadPatientData"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = [
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/data/*",
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/bias/*",
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/explainability/*",
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/evaluation/output/*"
        ]
      },
      {
        # s3:GetObject cannot enumerate a prefix, so the physician journey
        # (browse patient records and the bias, explainability and LLM evaluation
        # reports) also needs s3:ListBucket. Granted on the bucket ARN but
        # constrained by s3:prefix to the prefixes above, so it is not an
        # unconditional bucket list.
        # https://docs.aws.amazon.com/AmazonS3/latest/userguide/walkthrough1.html (s3:prefix condition)
        Sid    = "ListPatientDataAndReports"
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.data_lake.arn
        Condition = {
          StringLike = {
            "s3:prefix" = [
              "${var.project_name}/data/*",
              "${var.project_name}/bias/*",
              "${var.project_name}/explainability/*",
              "${var.project_name}/evaluation/output/*"
            ]
          }
        }
      }
    ]
  })
}

# Nurse: Can only view triage-level data
resource "aws_iam_role" "nurse_role" {
  name = "${var.project_name}-nurse-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = "nurse-access"
          }
        }
      }
    ]
  })

  tags = {
    Role        = "Nurse"
    AccessLevel = "Limited"
  }
}

resource "aws_iam_role_policy" "nurse_permissions" {
  name = "${var.project_name}-nurse-policy"
  role = aws_iam_role.nurse_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # See the physician policy above: the real action is InvokeAgentRuntime.
        # https://servicereference.us-east-1.amazonaws.com/v1/bedrock-agentcore/bedrock-agentcore.json
        Sid    = "InvokeAgentRuntimeLimited"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:InvokeAgentRuntime"
        ]
        Resource = local.agentcore_runtime_arn
        Condition = {
          StringEquals = {
            "aws:PrincipalTag/Role" = "Nurse"
          }
        }
      }
    ]
  })
}

# Admin: System metrics and governance only, NO patient data
resource "aws_iam_role" "admin_role" {
  name = "${var.project_name}-admin-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = "admin-access"
          }
        }
      }
    ]
  })

  tags = {
    Role        = "Admin"
    AccessLevel = "Governance"
  }
}

resource "aws_iam_role_policy" "admin_permissions" {
  name = "${var.project_name}-admin-policy"
  role = aws_iam_role.admin_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "CloudWatchReadDashboard"
        Effect   = "Allow"
        Action   = ["cloudwatch:GetDashboard"]
        Resource = "arn:aws:cloudwatch::${data.aws_caller_identity.current.account_id}:dashboard/${var.project_name}-*"
      },
      {
        # cloudwatch:DescribeAlarms is a list operation: a DescribeAlarms call
        # (even with AlarmNamePrefix) is authorized against the account/region
        # alarm wildcard, not the queried prefix. Verified live: the previous
        # grant (alarm:${var.project_name}-*) was denied on
        # "arn:aws:cloudwatch:us-east-1:<acct>:alarm:*", so a name-prefixed ARN is
        # unsatisfiable. Scope to the account/region alarm wildcard (every alarm in
        # this account+region) — the narrowest resource the API will accept — not a
        # bare "*". https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/permissions-reference-cw.html
        Sid      = "CloudWatchReadAlarms"
        Effect   = "Allow"
        Action   = ["cloudwatch:DescribeAlarms"]
        Resource = "arn:aws:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:*"
      },
      {
        # GetMetricData and ListDashboards are not resource-scopable for
        # namespace metrics / dashboard listing (AWS Service Authorization Reference)
        Sid    = "CloudWatchReadMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricData",
          "cloudwatch:ListDashboards"
        ]
        Resource = "*"
      },
      {
        Sid    = "SageMakerReadReports"
        Effect = "Allow"
        Action = [
          "sagemaker:DescribeModelPackage",
          "sagemaker:DescribeModelPackageGroup",
          "sagemaker:ListModelPackages"
        ]
        Resource = "arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:model-package-group/${var.project_name}-*"
      },
      {
        Sid    = "DenyPatientData"
        Effect = "Deny"
        Action = [
          "s3:GetObject"
        ]
        Resource = "${aws_s3_bucket.data_lake.arn}/${var.project_name}/data/*"
      }
    ]
  })
}

# Auditor: Read-only audit trails, NO modifications
resource "aws_iam_role" "auditor_role" {
  name = "${var.project_name}-auditor-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = "auditor-access"
          }
        }
      }
    ]
  })

  tags = {
    Role        = "Auditor"
    AccessLevel = "ReadOnly"
  }
}

resource "aws_iam_role_policy" "auditor_permissions" {
  name = "${var.project_name}-auditor-policy"
  role = aws_iam_role.auditor_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogsRead"
        Effect = "Allow"
        Action = [
          "logs:GetLogEvents",
          "logs:FilterLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams"
        ]
        Resource = [
          "${aws_cloudwatch_log_group.audit_trail.arn}:*",
          "${aws_cloudwatch_log_group.policy_enforcement.arn}:*"
        ]
      },
      {
        Sid    = "S3ReadAuditReports"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = [
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/bias/*",
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/explainability/*",
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/evaluation/output/*"
        ]
      },
      {
        # s3:GetObject alone cannot list the report folders; the auditor journey
        # (enumerate the bias, explainability and evaluation reports) needs
        # s3:ListBucket. Granted on the bucket ARN but constrained by an s3:prefix
        # condition to the report prefixes only, so it is not an unconditional
        # bucket list.
        # https://docs.aws.amazon.com/AmazonS3/latest/userguide/walkthrough1.html (s3:prefix condition)
        Sid    = "S3ListAuditReports"
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.data_lake.arn
        Condition = {
          StringLike = {
            "s3:prefix" = [
              "${var.project_name}/bias/*",
              "${var.project_name}/explainability/*",
              "${var.project_name}/evaluation/output/*"
            ]
          }
        }
      },
      {
        Sid    = "DenyAllModifications"
        Effect = "Deny"
        Action = [
          "s3:PutObject",
          "s3:DeleteObject",
          "sagemaker:Create*",
          "sagemaker:Delete*",
          "sagemaker:Update*",
          "cloudwatch:PutMetricData",
          "cloudwatch:DeleteAlarms"
        ]
        Resource = "*"
      },
      {
        Sid    = "DenyPatientData"
        Effect = "Deny"
        Action = [
          "s3:GetObject"
        ]
        Resource = "${aws_s3_bucket.data_lake.arn}/${var.project_name}/data/*"
      }
    ]
  })
}
