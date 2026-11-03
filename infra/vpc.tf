# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
###############################################################################
# VPC — Network Isolation for Healthcare Data (HIPAA requirement)
###############################################################################

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "${var.project_name}-vpc"
  }
}

# Private subnet (SageMaker, AgentCore — no direct internet)
resource "aws_subnet" "private_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = "${var.aws_region}a"

  tags = {
    Name = "${var.project_name}-private-a"
  }
}

resource "aws_subnet" "private_b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = "${var.aws_region}b"

  tags = {
    Name = "${var.project_name}-private-b"
  }
}

# Firewall subnet: holds the AWS Network Firewall endpoint and nothing else.
# There is no internet gateway or NAT gateway, so the private subnets have no
# path to the internet; every AWS API they use is reached through the VPC
# endpoints below.
resource "aws_subnet" "firewall_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.10.0/24"
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.project_name}-firewall-a"
  }
}

# Same subnet, formerly the NAT gateway's public subnet
moved {
  from = aws_subnet.public_a
  to   = aws_subnet.firewall_a
}

# Private route table. The only non-local route sends traffic for anything
# outside the VPC to the firewall, whose default action drops and logs it, so an
# attempt to reach the internet is blocked and recorded rather than routed.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block      = "0.0.0.0/0"
    vpc_endpoint_id = local.network_firewall_endpoint_id
  }

  tags = {
    Name = "${var.project_name}-private-rt"
  }
}

resource "aws_route_table_association" "private_a" {
  subnet_id      = aws_subnet.private_a.id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "private_b" {
  subnet_id      = aws_subnet.private_b.id
  route_table_id = aws_route_table.private.id
}

# ─────────────────────────────────────────────────────────────────────────────
# VPC Endpoints (keep traffic within AWS network — HIPAA)
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_security_group" "vpc_endpoints" {
  name_prefix = "${var.project_name}-vpce-"
  description = "Security group for VPC endpoints - allows HTTPS from VPC CIDR"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Allow HTTPS from VPC CIDR for AWS service endpoints"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  tags = {
    Name = "${var.project_name}-vpce-sg"
  }
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = {
    Name = "${var.project_name}-s3-endpoint"
  }
}

resource "aws_vpc_endpoint" "bedrock" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private_a.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-bedrock-endpoint"
  }
}

# CloudWatch metrics (PutMetricData from the preprocessing Lambda)
resource "aws_vpc_endpoint" "monitoring" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.monitoring"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private_a.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-monitoring-endpoint"
  }
}

# AWS X-Ray (the preprocessing Lambda runs with active tracing)
resource "aws_vpc_endpoint" "xray" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.xray"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private_a.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-xray-endpoint"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Default Security Group — restrict all traffic (CIS benchmark)
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id
}

# ─────────────────────────────────────────────────────────────────────────────
# VPC Flow Logs
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_flow_log" "main" {
  vpc_id                   = aws_vpc.main.id
  traffic_type             = "ALL"
  iam_role_arn             = aws_iam_role.flow_log.arn
  log_destination          = aws_cloudwatch_log_group.vpc_flow_logs.arn
  max_aggregation_interval = 60
}

resource "aws_cloudwatch_log_group" "vpc_flow_logs" {
  name              = "/aws/vpc/${var.project_name}/flow-logs"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.cloudwatch_logs.arn
}

resource "aws_iam_role" "flow_log" {
  name = "${var.project_name}-flow-log-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
      # Confused-deputy protection, as documented for flow log roles:
      # https://docs.aws.amazon.com/vpc/latest/userguide/flow-logs-iam-role.html
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnLike = {
          "aws:SourceArn" = "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:vpc-flow-log/*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "flow_log" {
  name = "${var.project_name}-flow-log-policy"
  role = aws_iam_role.flow_log.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams"
      ]
      Resource = "${aws_cloudwatch_log_group.vpc_flow_logs.arn}:*"
    }]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# AWS Network Firewall — default-deny egress inspection
# ─────────────────────────────────────────────────────────────────────────────
# The private route table's 0.0.0.0/0 route targets this firewall. With strict
# rule ordering and no rules that pass traffic, the default actions drop every
# stateful flow and write an alert log, so any attempt to leave the VPC is
# blocked and recorded. Traffic to the VPC endpoints is VPC-local and never
# reaches the firewall.
resource "aws_kms_key" "network_firewall" {
  description             = "KMS key for the AWS Network Firewall and its policy (${var.project_name})"
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
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-network-firewall-kms"
  }
}

resource "aws_networkfirewall_firewall_policy" "egress_deny" {
  name = "${var.project_name}-egress-deny"

  encryption_configuration {
    type   = "CUSTOMER_KMS"
    key_id = aws_kms_key.network_firewall.arn
  }

  firewall_policy {
    stateless_default_actions          = ["aws:forward_to_sfe"]
    stateless_fragment_default_actions = ["aws:forward_to_sfe"]
    stateful_default_actions           = ["aws:drop_strict", "aws:alert_strict"]

    stateful_engine_options {
      rule_order = "STRICT_ORDER"
    }
  }
}

resource "aws_networkfirewall_firewall" "main" {
  name                = "${var.project_name}-egress"
  description         = "Default-deny egress firewall for the private subnets"
  firewall_policy_arn = aws_networkfirewall_firewall_policy.egress_deny.arn
  vpc_id              = aws_vpc.main.id

  delete_protection                 = true
  firewall_policy_change_protection = true
  subnet_change_protection          = true

  subnet_mapping {
    subnet_id = aws_subnet.firewall_a.id
  }

  encryption_configuration {
    type   = "CUSTOMER_KMS"
    key_id = aws_kms_key.network_firewall.arn
  }
}

locals {
  # The firewall exposes its endpoint id only through firewall_status
  network_firewall_endpoint_id = one(flatten([
    for s in aws_networkfirewall_firewall.main.firewall_status[0].sync_states : [for a in s.attachment : a.endpoint_id]
  ]))
}

resource "aws_cloudwatch_log_group" "network_firewall_alerts" {
  name              = "/aws/network-firewall/${var.project_name}/alerts"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.cloudwatch_logs.arn
}

resource "aws_networkfirewall_logging_configuration" "main" {
  firewall_arn = aws_networkfirewall_firewall.main.arn

  logging_configuration {
    log_destination_config {
      log_destination_type = "CloudWatchLogs"
      log_type             = "ALERT"
      log_destination = {
        logGroup = aws_cloudwatch_log_group.network_firewall_alerts.name
      }
    }
  }
}
