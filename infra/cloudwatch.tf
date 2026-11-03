# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
###############################################################################
# CloudWatch — Observability, Dashboards, Alarms
# (AgentCore Observability uses CloudWatch under the hood)
###############################################################################

# ─────────────────────────────────────────────────────────────────────────────
# KMS Key for CloudWatch Logs encryption
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_kms_key" "cloudwatch_logs" {
  description             = "KMS key for CloudWatch Logs encryption"
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
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:*"
          }
        }
      }
    ]
  })
}

resource "aws_kms_alias" "cloudwatch_logs" {
  name          = "alias/${var.project_name}-cloudwatch-logs"
  target_key_id = aws_kms_key.cloudwatch_logs.key_id
}

# ─────────────────────────────────────────────────────────────────────────────
# Log Groups
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "diagnostic_agent" {
  name              = "/aws/agentcore/${var.project_name}/diagnostic-agent"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.cloudwatch_logs.arn
}

resource "aws_cloudwatch_log_group" "policy_enforcement" {
  name              = "/aws/agentcore/${var.project_name}/policy-enforcement"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.cloudwatch_logs.arn
}

resource "aws_cloudwatch_log_group" "audit_trail" {
  name              = "/aws/agentcore/${var.project_name}/audit-trail"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.cloudwatch_logs.arn
}

# ─────────────────────────────────────────────────────────────────────────────
# KMS key for the alerts SNS topic
# ─────────────────────────────────────────────────────────────────────────────
# A CUSTOMER MANAGED key is a functional requirement here, not hardening. A
# CloudWatch alarm action against a topic encrypted with the AWS managed key
# `alias/aws/sns` fails outright: that key's policy does not grant kms:Decrypt
# and kms:GenerateDataKey to CloudWatch, and an AWS managed key's policy cannot
# be edited. The alarm history records "CloudWatch Alarms does not have
# authorization to access the SNS topic encryption key".
# https://repost.aws/knowledge-center/cloudwatch-configure-alarm-sns
#
# The same holds for any AWS service event source publishing to an encrypted
# topic. The SNS developer guide lists the service principal per source and the
# two actions each needs on Resource "*" — kms:GenerateDataKey* and kms:Decrypt
# — naming `cloudwatch.amazonaws.com` for Amazon CloudWatch and
# `events.amazonaws.com` for EventBridge (listed under its former name, Amazon
# CloudWatch Events).
# https://docs.aws.amazon.com/sns/latest/dg/sns-key-management.html
#
# Scoping. Both service statements are narrowed with aws:SourceAccount, so the
# grant only covers requests originated by a resource in this account; the
# re:Post resolution above shows this same `aws:Source*` family as the way to
# restrict the statement. aws:SourceArn is deliberately NOT used: whether
# CloudWatch and EventBridge propagate it to the KMS authorization call for an
# encrypted-topic publish is NOT VERIFIED against any published AWS reference,
# and a condition key that is absent at evaluation time denies the request —
# which would reinstate the exact defect this key exists to fix. Per-publisher
# ARN pinning is enforced one layer out instead, on the topic's own resource
# policy below, where aws:SourceArn support IS documented.
resource "aws_kms_key" "sns_alerts" {
  description             = "KMS key for the alerts SNS topic (${var.project_name})"
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
        # The seven alarms below; without this, every alarm action is refused.
        Sid    = "AllowCloudWatchAlarmsToUseTheKey"
        Effect = "Allow"
        Principal = {
          Service = "cloudwatch.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey*",
          "kms:Decrypt"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      },
      {
        # S3 publishes data lake deletion notifications to the encrypted topic.
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
      },
      {
        # The SNS target of the audit-log and trail tamper rules.
        Sid    = "AllowEventBridgeRulesToUseTheKey"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey*",
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
    Name = "${var.project_name}-sns-alerts-kms"
  }
}

resource "aws_kms_alias" "sns_alerts" {
  name          = "alias/${var.project_name}-sns-alerts"
  target_key_id = aws_kms_key.sns_alerts.key_id
}

# The alerts SNS topic (aws_sns_topic.rai_alerts) is declared in lambda.tf, next to
# the data lake notification that also publishes to it.

resource "aws_sns_topic_subscription" "email_alert" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.rai_alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ─────────────────────────────────────────────────────────────────────────────
# Audit-log tamper detection — EventBridge rule over the log buckets
# ─────────────────────────────────────────────────────────────────────────────
# The log buckets publish object events to EventBridge (the `eventbridge = true`
# notification configurations in s3.tf and server_access_logs.tf). This rule is
# what CONSUMES them: without a rule, enabling the publisher detects nothing.
#
# Scoped deliberately:
#   * Only the two in-Region log buckets. An EventBridge rule is regional, so a
#     rule here cannot see events from the replica-Region buckets; those are
#     covered by the replica Region's own trail, not by this rule.
#   * Only the event types that represent TAMPERING with a delivered log object.
#     "Object Created" is excluded because routine log delivery is a create and
#     would fire on every CloudTrail and server-access-log object.
#   * "Object Deleted" carries `reason = "Lifecycle Expiration"` when the
#     365-day expiry rule removes an object, which is scheduled behaviour, not
#     tampering — so that reason is excluded and only API-driven deletes match.
# Event types and the `reason` field:
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/EventBridge.html
resource "aws_cloudwatch_event_rule" "audit_log_tamper" {
  name        = "${var.project_name}-audit-log-tamper"
  description = "S3 object tampering (delete, ACL, tag or retention change) on this project's log buckets"

  event_pattern = jsonencode({
    source = ["aws.s3"]
    # $or is required rather than one flat pattern: the delete case needs a
    # `reason` filter and the other cases carry no `reason` field at all, so a
    # single `detail.reason` constraint would silently exclude them.
    "$or" = [
      {
        "detail-type" = ["Object Deleted"]
        detail = {
          bucket = { name = local.audit_log_bucket_names }
          reason = [{ "anything-but" = "Lifecycle Expiration" }]
        }
      },
      {
        "detail-type" = [
          "Object ACL Updated",
          "Object Tags Added",
          "Object Tags Deleted",
          "Object Retention Updated",
        ]
        detail = {
          bucket = { name = local.audit_log_bucket_names }
        }
      },
    ]
  })
}

resource "aws_cloudwatch_event_target" "audit_log_tamper_alerts" {
  rule      = aws_cloudwatch_event_rule.audit_log_tamper.name
  target_id = "${var.project_name}-rai-alerts"
  arn       = aws_sns_topic.rai_alerts.arn
}

# ─────────────────────────────────────────────────────────────────────────────
# Audit trail tamper detection — someone stops, deletes or reconfigures the trail
# ─────────────────────────────────────────────────────────────────────────────
# CloudTrail management events reach EventBridge as "AWS API Call via CloudTrail"
# while a trail is logging, which includes the StopLogging call itself.
# https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-service-event-cloudtrail.html
# requestParameters.name holds the trail name or ARN, whichever the caller passed.
resource "aws_cloudwatch_event_rule" "trail_tamper" {
  name        = "${var.project_name}-trail-tamper"
  description = "The project CloudTrail trail is stopped, deleted or reconfigured"

  event_pattern = jsonencode({
    source        = ["aws.cloudtrail"]
    "detail-type" = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = ["cloudtrail.amazonaws.com"]
      eventName   = ["StopLogging", "DeleteTrail", "UpdateTrail", "PutEventSelectors", "PutInsightSelectors"]
      requestParameters = {
        name = ["${var.project_name}-audit-trail", local.cloudtrail_arn]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "trail_tamper_alerts" {
  rule      = aws_cloudwatch_event_rule.trail_tamper.name
  target_id = "${var.project_name}-rai-alerts"
  arn       = aws_sns_topic.rai_alerts.arn
}

# EventBridge publishes to an SNS topic through the topic's RESOURCE policy, so
# the topic needs an explicit statement naming this rule.
# https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-use-resource-based.html
#
# Setting a topic policy REPLACES the SNS default policy, so the two publishers
# that already rely on the default are restated here explicitly and no more
# broadly than they need:
#   * the owning account, for Terraform and operator management of the topic;
#   * cloudwatch.amazonaws.com, for the seven alarms below, pinned by SourceArn to
#     this project's alarm name prefix.
#
# A topic policy alone is NOT sufficient for a service publisher when the topic
# is encrypted: authorization is evaluated twice, once by SNS against this policy
# and once by KMS against the encryption key's policy. While the topic used the
# AWS managed key `alias/aws/sns`, the second check refused every service
# publish, measurably — MatchedEvents = 2 with FailedInvocations = 2 for this
# rule, and NumberOfMessagesPublished = 0 on the topic across a four-day window
# in which alarms did fire (a publish refused at the KMS step never enters SNS,
# so it appears in neither NumberOfMessagesPublished nor
# NumberOfNotificationsFailed). The topic now uses aws_kms_key.sns_alerts above,
# whose policy grants both of these principals kms:GenerateDataKey* and
# kms:Decrypt. Both halves are required; neither is redundant.
#
# Verified live after that change, both publishers: this rule matched 4 tamper
# events and incremented FailedInvocations by 0, and the low-confidence alarm's
# action history went from "CloudWatch Alarms does not have authorization to
# access the SNS topic encryption key" to Succeeded. The topic recorded
# 5 published / 5 delivered / 0 failed.
# https://docs.aws.amazon.com/sns/latest/dg/sns-key-management.html
# One statement: checkov 3.2.430 renders a jsonencode policy only when it has a single
# statement, so CKV_AWS_169/CKV_AWS_385 evaluate this policy. Only the three AWS services
# that alert publish, each only for this project's own resource (aws:SourceArn) in this
# account. Principals in this account need no grant here: their IAM policies already apply.
# https://docs.aws.amazon.com/sns/latest/dg/sns-access-policy-use-cases.html
resource "aws_sns_topic_policy" "rai_alerts" {
  arn = aws_sns_topic.rai_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowThisProjectsAlarmsTamperRulesAndDataLakeToPublish"
      Effect = "Allow"
      Principal = {
        Service = ["cloudwatch.amazonaws.com", "events.amazonaws.com", "s3.amazonaws.com"]
      }
      Action   = "SNS:Publish"
      Resource = aws_sns_topic.rai_alerts.arn
      Condition = {
        ArnLike = {
          "aws:SourceArn" = [
            "arn:aws:cloudwatch:${var.aws_region}:${local.account_id}:alarm:${var.project_name}-*",
            "arn:aws:events:${var.aws_region}:${local.account_id}:rule/${var.project_name}-audit-log-tamper",
            "arn:aws:events:${var.aws_region}:${local.account_id}:rule/${var.project_name}-trail-tamper",
            "arn:aws:s3:::${aws_s3_bucket.data_lake.id}",
          ]
        }
        StringEquals = {
          "aws:SourceAccount" = local.account_id
        }
      }
    }]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# CloudWatch Alarms
# ─────────────────────────────────────────────────────────────────────────────

# Alarm: Low confidence output detected
resource "aws_cloudwatch_metric_alarm" "low_confidence" {
  alarm_name          = "${var.project_name}-low-confidence-alert"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DiagnosticConfidence"
  namespace           = "RAIHealthcare"
  period              = 60
  statistic           = "Minimum"
  threshold           = var.confidence_threshold
  alarm_description   = "AI diagnostic confidence dropped below ${var.confidence_threshold}% - mandatory physician review required"
  alarm_actions       = [aws_sns_topic.rai_alerts.arn]

  dimensions = {
    Service = "DiagnosticAssistant"
  }
}

# Alarm: AI output failed safety validation. The agent emits
# OutputValidationFailure each time it withholds a response that is unparseable
# or missing a safety-critical field (src/agent/app/raihealthdiag/main.py).
# Any single failure alarms: every one means a clinician got no recommendation.
resource "aws_cloudwatch_metric_alarm" "output_validation" {
  alarm_name          = "${var.project_name}-output-validation-alert"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "OutputValidationFailure"
  namespace           = "RAIHealthcare"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_description   = "AI output failed safety validation and was withheld - review the agent logs"
  alarm_actions       = [aws_sns_topic.rai_alerts.arn]

  dimensions = {
    Service = "DiagnosticAssistant"
  }
}

# Alarm: High error rate
resource "aws_cloudwatch_metric_alarm" "high_error_rate" {
  alarm_name          = "${var.project_name}-high-error-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ErrorRate"
  namespace           = "RAIHealthcare"
  period              = 300
  statistic           = "Average"
  threshold           = 5
  alarm_description   = "Diagnostic assistant error rate exceeded 5% - investigate immediately"
  alarm_actions       = [aws_sns_topic.rai_alerts.arn]

  dimensions = {
    Service = "DiagnosticAssistant"
  }
}

# Alarm: Accuracy drift
resource "aws_cloudwatch_metric_alarm" "accuracy_drift" {
  alarm_name          = "${var.project_name}-accuracy-drift"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 6 # 30 minutes of sustained low accuracy
  metric_name         = "DiagnosticAccuracy"
  namespace           = "RAIHealthcare"
  period              = 300
  statistic           = "Average"
  threshold           = 90
  alarm_description   = "7-day rolling accuracy dropped below 90% - model drift detected"
  alarm_actions       = [aws_sns_topic.rai_alerts.arn]

  dimensions = {
    Service = "DiagnosticAssistant"
  }
}

# Alarm: Latency spike
resource "aws_cloudwatch_metric_alarm" "high_latency" {
  alarm_name          = "${var.project_name}-high-latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "InferenceLatency"
  namespace           = "RAIHealthcare"
  period              = 60
  extended_statistic  = "p99"
  threshold           = 5000
  alarm_description   = "P99 latency exceeded 5 seconds - performance degradation"
  alarm_actions       = [aws_sns_topic.rai_alerts.arn]

  dimensions = {
    Service = "DiagnosticAssistant"
  }
}

# Alarm: Policy denial spike (potential security issue)
resource "aws_cloudwatch_metric_alarm" "policy_denial_spike" {
  alarm_name          = "${var.project_name}-policy-denial-spike"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "PolicyDenials"
  namespace           = "RAIHealthcare"
  period              = 300
  statistic           = "Sum"
  threshold           = 20
  alarm_description   = "Unusual number of policy denials - potential unauthorized access attempts"
  alarm_actions       = [aws_sns_topic.rai_alerts.arn]

  dimensions = {
    Service = "PolicyEnforcement"
  }
}

# Alarm: DLQ messages (failed Lambda invocations)
resource "aws_cloudwatch_metric_alarm" "dlq_messages" {
  alarm_name          = "${var.project_name}-dlq-messages"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Failed Lambda invocations detected in DLQ - investigate preprocessing errors"
  alarm_actions       = [aws_sns_topic.rai_alerts.arn]

  dimensions = {
    QueueName = aws_sqs_queue.lambda_dlq.name
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# CloudWatch Dashboard
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_cloudwatch_dashboard" "rai_healthcare" {
  dashboard_name = "${var.project_name}-monitoring"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 6
        height = 6
        properties = {
          title   = "Average Confidence Score"
          metrics = [["RAIHealthcare", "DiagnosticConfidence", "Service", "DiagnosticAssistant"]]
          period  = 60
          stat    = "Average"
          region  = var.aws_region
          yAxis   = { left = { min = 0, max = 100 } }
          annotations = {
            horizontal = [
              { value = var.confidence_threshold, label = "Alert Threshold", color = "#d13212" }
            ]
          }
        }
      },
      {
        type   = "metric"
        x      = 6
        y      = 0
        width  = 6
        height = 6
        properties = {
          title   = "Alerts Triggered"
          metrics = [["RAIHealthcare", "AlertsTriggered", "Service", "DiagnosticAssistant"]]
          period  = 60
          stat    = "Sum"
          region  = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 6
        height = 6
        properties = {
          title = "Inference Latency (ms)"
          metrics = [
            ["RAIHealthcare", "InferenceLatency", "Service", "DiagnosticAssistant", { stat = "p50", label = "P50" }],
            ["RAIHealthcare", "InferenceLatency", "Service", "DiagnosticAssistant", { stat = "p99", label = "P99" }]
          ]
          period = 60
          region = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 18
        y      = 0
        width  = 6
        height = 6
        properties = {
          title   = "Diagnostic Accuracy (7d)"
          metrics = [["RAIHealthcare", "DiagnosticAccuracy", "Service", "DiagnosticAssistant"]]
          period  = 60
          stat    = "Average"
          region  = var.aws_region
          yAxis   = { left = { min = 80, max = 100 } }
          annotations = {
            horizontal = [
              { value = 95, label = "Target", color = "#2ca02c" },
              { value = 90, label = "Alert", color = "#d13212" }
            ]
          }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 8
        height = 6
        properties = {
          # Tracks IAM-based access control decisions emitted by the demo scripts
          # (see 06_test_access_control.py). To extend this to live Cedar Policy
          # Engine decisions, ingest AgentCore Policy Engine logs into the
          # policy-enforcement log group and add a metric filter.
          title = "Policy Enforcement (Allow vs Deny)"
          metrics = [
            ["RAIHealthcare", "PolicyAllowed", "Service", "PolicyEnforcement", { color = "#2ca02c" }],
            ["RAIHealthcare", "PolicyDenials", "Service", "PolicyEnforcement", { color = "#d13212" }]
          ]
          period = 60
          stat   = "Sum"
          region = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 6
        width  = 8
        height = 6
        properties = {
          title   = "Override Rate (%)"
          metrics = [["RAIHealthcare", "OverrideRate", "Service", "DiagnosticAssistant"]]
          period  = 60
          stat    = "Sum"
          region  = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 6
        width  = 8
        height = 6
        properties = {
          title   = "Error Rate (%)"
          metrics = [["RAIHealthcare", "ErrorRate", "Service", "DiagnosticAssistant"]]
          period  = 60
          stat    = "Average"
          region  = var.aws_region
          annotations = {
            horizontal = [
              { value = 5, label = "Critical", color = "#d13212" },
              { value = 1, label = "Warning", color = "#ff7f0e" }
            ]
          }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 8
        height = 6
        properties = {
          title   = "Automated Reasoning (Pass/Fail)"
          metrics = [["RAIHealthcare", "AutomatedReasoningCheck", "Service", "DiagnosticAssistant"]]
          period  = 60
          stat    = "Sum"
          region  = var.aws_region
          annotations = {
            horizontal = [
              { value = 1, label = "VALID", color = "#2ca02c" }
            ]
          }
        }
      },
      {
        type   = "log"
        x      = 8
        y      = 12
        width  = 16
        height = 6
        properties = {
          title  = "Recent Audit Events (Overrides & Alerts)"
          query  = "SOURCE '/aws/agentcore/${var.project_name}/audit-trail' | fields @timestamp, @message | parse @message '\"event_type\":\"*\"' as event_type | parse @message '\"physician_id\":\"*\"' as physician | parse @message '\"patient_id\":\"*\"' as patient | parse @message '\"ai_confidence\":*,' as confidence | sort @timestamp desc | limit 20"
          region = var.aws_region
        }
      }
    ]
  })
}
