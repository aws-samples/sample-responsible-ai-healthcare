# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
###############################################################################
# Lambda — Preprocessing (Ingestion Layer)
# Matches architecture: Data Sources → S3 → Lambda Preprocessing → Bedrock
###############################################################################

# Lambda execution role
resource "aws_iam_role" "lambda_preprocessing" {
  name = "${var.project_name}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "lambda_permissions" {
  name = "${var.project_name}-lambda-policy"
  role = aws_iam_role.lambda_preprocessing.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3ReadPatientData"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = [
          "${aws_s3_bucket.data_lake.arn}/${var.project_name}/data/*"
        ]
      },
      {
        # Constrained by s3:prefix to the two prefixes the function touches, so it
        # cannot enumerate the rest of the bucket.
        # https://docs.aws.amazon.com/AmazonS3/latest/userguide/walkthrough1.html (s3:prefix condition)
        #
        # The processed/ prefix is listed because the idempotency check calls
        # HeadObject on a key that usually does not exist yet. HeadObject reports a
        # missing key as 404 only to a caller holding s3:ListBucket; without it the
        # answer is 403, which the handler cannot distinguish from a real denial.
        # "If you don't have the s3:ListBucket permission, Amazon S3 returns an HTTP
        # status code 403 Forbidden error."
        # https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html
        Sid    = "S3ListPatientData"
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.data_lake.arn
        Condition = {
          StringLike = {
            "s3:prefix" = [
              "${var.project_name}/data/*",
              "${var.project_name}/processed/*"
            ]
          }
        }
      },
      {
        Sid    = "S3WriteProcessed"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject"
        ]
        Resource = "${aws_s3_bucket.data_lake.arn}/${var.project_name}/processed/*"
      },
      {
        Sid    = "BedrockInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel"
        ]
        Resource = [
          "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_model_id}",
          "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/${var.bedrock_inference_profile}.${var.bedrock_model_id}",
          "arn:aws:bedrock:*::foundation-model/${var.bedrock_model_id}"
        ]
      },
      {
        # Required to invoke a model with guardrailIdentifier set
        # https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-permissions.html
        Sid      = "BedrockApplyGuardrail"
        Effect   = "Allow"
        Action   = ["bedrock:ApplyGuardrail"]
        Resource = aws_bedrock_guardrail.healthcare.guardrail_arn
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-*"
      },
      {
        Sid    = "DLQSend"
        Effect = "Allow"
        Action = [
          "sqs:SendMessage"
        ]
        Resource = aws_sqs_queue.lambda_dlq.arn
      },
      {
        # The DLQ is encrypted with a customer managed key, and a producer's
        # SendMessage triggers kms:GenerateDataKey plus a kms:Decrypt integrity
        # check once the data key reuse period expires. Without these the async
        # failure path silently drops the event instead of parking it on the DLQ.
        # "the producer must have the kms:Decrypt and kms:GenerateDataKey
        # permissions for the KMS key"
        # https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-key-management.html
        # Narrowed to calls SQS makes on the role's behalf; the role gets no
        # direct use of the key.
        Sid    = "DLQEncryption"
        Effect = "Allow"
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt"
        ]
        Resource = aws_kms_key.cloudwatch_logs.arn
        Condition = {
          StringEquals = {
            "kms:ViaService" = "sqs.${var.aws_region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

# Telemetry actions that cannot be resource-scoped, kept separate from the
# data-access policy above. PutMetricData is limited to the RAIHealthcare namespace.
resource "aws_iam_role_policy" "lambda_telemetry" {
  name = "${var.project_name}-lambda-telemetry"
  role = aws_iam_role.lambda_preprocessing.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "CloudWatchMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "RAIHealthcare"
          }
        }
      },
      {
        Sid    = "XRayTracing"
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords"
        ]
        Resource = "*"
      }
    ]
  })
}

# DLQ for Lambda
resource "aws_sqs_queue" "lambda_dlq" {
  name                      = "${var.project_name}-lambda-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = aws_kms_key.cloudwatch_logs.key_id
}

# Failed invocation payloads contain patient data, so the queue denies plaintext
# access the same way the data lake bucket does.
# https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-security-best-practices.html
resource "aws_sqs_queue_policy" "lambda_dlq_ssl_only" {
  queue_url = aws_sqs_queue.lambda_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyNonHTTPS"
        Effect = "Deny"
        Principal = {
          AWS = "*"
        }
        Action   = "sqs:*"
        Resource = aws_sqs_queue.lambda_dlq.arn
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

# Lambda Code Signing — ensures only trusted code runs
resource "aws_signer_signing_profile" "lambda" {
  platform_id = "AWSLambda-SHA384-ECDSA"
  # Signer profileName only allows [a-zA-Z0-9_] (max 64; provider caps name_prefix
  # at 38 to leave room for the generated suffix), so hyphens in project_name must
  # become underscores.
  # https://docs.aws.amazon.com/signer/latest/api/API_PutSigningProfile.html
  name_prefix = "${replace(var.project_name, "-", "_")}_"

  signature_validity_period {
    value = 5
    type  = "YEARS"
  }
}

resource "aws_lambda_code_signing_config" "preprocessing" {
  allowed_publishers {
    signing_profile_version_arns = [aws_signer_signing_profile.lambda.version_arn]
  }

  policies {
    untrusted_artifact_on_deployment = "Warn"
  }
}

# Lambda function — preprocessing (VPC-attached for network isolation)
# Security group for Lambda function
resource "aws_security_group" "lambda" {
  name_prefix = "${var.project_name}-lambda-"
  description = "Security group for Lambda preprocessing function in VPC"
  vpc_id      = aws_vpc.main.id

  # HTTPS to the interface endpoints (Bedrock Runtime, CloudWatch, X-Ray) and to
  # Amazon S3 through its gateway endpoint. Nothing else is reachable.
  egress {
    description     = "HTTPS to the VPC interface endpoints"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.vpc_endpoints.id]
  }

  egress {
    description     = "HTTPS to Amazon S3 through the gateway endpoint"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    prefix_list_ids = [aws_vpc_endpoint.s3.prefix_list_id]
  }

  tags = {
    Name = "${var.project_name}-lambda-sg"
  }
}

resource "aws_lambda_function" "preprocessing" {
  function_name           = "${var.project_name}-preprocessing"
  role                    = aws_iam_role.lambda_preprocessing.arn
  handler                 = "index.handler"
  runtime                 = "python3.12"
  timeout                 = 60
  memory_size             = 256
  code_signing_config_arn = aws_lambda_code_signing_config.preprocessing.arn

  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  reserved_concurrent_executions = 10

  tracing_config {
    mode = "Active"
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.lambda_dlq.arn
  }

  kms_key_arn = aws_kms_key.cloudwatch_logs.arn

  vpc_config {
    subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    security_group_ids = [aws_security_group.lambda.id]
  }

  # var.confidence_threshold is a PERCENT (50-100). The application takes a
  # FRACTION in [0.50, 1.0] and refuses to start outside it, so divide by 100
  # here (src/lambda/preprocessing/index.py).
  environment {
    variables = {
      S3_BUCKET            = aws_s3_bucket.data_lake.id
      S3_PREFIX            = var.project_name
      BEDROCK_MODEL_ID     = "${var.bedrock_inference_profile}.${var.bedrock_model_id}"
      CONFIDENCE_THRESHOLD = tostring(var.confidence_threshold / 100)
      GUARDRAIL_ID         = aws_bedrock_guardrail.healthcare.guardrail_id
      GUARDRAIL_VERSION    = aws_bedrock_guardrail_version.healthcare.version
    }
  }

  depends_on = [aws_iam_role_policy_attachment.lambda_vpc_access]
}

# Package Lambda code
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../src/lambda/preprocessing/index.py"
  output_path = "${path.module}/../src/lambda/preprocessing/lambda.zip"
}

# S3 trigger — when new patient data arrives
resource "aws_lambda_permission" "s3_trigger" {
  statement_id   = "AllowS3Invoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.preprocessing.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.data_lake.arn
  source_account = data.aws_caller_identity.current.account_id
}

# Alerts topic: CloudWatch alarms, the audit-log tamper rule and data lake deletions
# (below) publish here. Declared next to the bucket notification that targets it.
resource "aws_sns_topic" "rai_alerts" {
  name              = "${var.project_name}-rai-alerts"
  kms_master_key_id = aws_kms_key.sns_alerts.id
}

resource "aws_s3_bucket_notification" "patient_data_trigger" {
  bucket = aws_s3_bucket.data_lake.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.preprocessing.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "${var.project_name}/data/incoming/"
    filter_suffix       = ".json"
  }

  # Any API deletion of patient data or results (PHI) alerts the on-call address.
  # Lifecycle expirations are a separate event type (s3:LifecycleExpiration:*), so
  # retention clean-up does not alert.
  # https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-how-to-event-types-and-destinations.html
  topic {
    topic_arn     = aws_sns_topic.rai_alerts.arn
    events        = ["s3:ObjectRemoved:*"]
    filter_prefix = "${var.project_name}/"
  }

  depends_on = [aws_lambda_permission.s3_trigger, aws_sns_topic_policy.rai_alerts]
}
