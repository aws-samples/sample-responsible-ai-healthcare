# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Emit all metrics to CloudWatch to populate the dashboard immediately.
Run this before showing the dashboard during the demo.
"""

import logging
import os
import sys
from datetime import UTC, datetime

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import BOTO_CONFIG, REGION, validate_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    if not validate_config():
        raise SystemExit(1)

    cw = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)
    now = datetime.now(UTC)

    diag_dims = [{"Name": "Service", "Value": "DiagnosticAssistant"}]
    policy_dims = [{"Name": "Service", "Value": "PolicyEnforcement"}]

    metrics = [
        # DiagnosticAssistant dimension
        {"MetricName": "DiagnosticConfidence", "Value": 97, "Unit": "Percent", "Dimensions": diag_dims},
        {"MetricName": "DiagnosticConfidence", "Value": 58, "Unit": "Percent", "Dimensions": diag_dims},
        {"MetricName": "DiagnosticConfidence", "Value": 35, "Unit": "Percent", "Dimensions": diag_dims},
        {"MetricName": "InferenceLatency", "Value": 2100, "Unit": "Milliseconds", "Dimensions": diag_dims},
        {"MetricName": "InferenceLatency", "Value": 16900, "Unit": "Milliseconds", "Dimensions": diag_dims},
        {"MetricName": "AlertsTriggered", "Value": 3, "Unit": "Count", "Dimensions": diag_dims},
        {"MetricName": "DiagnosticAccuracy", "Value": 91.4, "Unit": "Percent", "Dimensions": diag_dims},
        {"MetricName": "OverrideRate", "Value": 1, "Unit": "Count", "Dimensions": diag_dims},
        {"MetricName": "ErrorRate", "Value": 0.3, "Unit": "Percent", "Dimensions": diag_dims},
        {"MetricName": "TotalInvocations", "Value": 5, "Unit": "Count", "Dimensions": diag_dims},
        # PolicyEnforcement dimension
        {"MetricName": "PolicyAllowed", "Value": 3, "Unit": "Count", "Dimensions": policy_dims},
        {"MetricName": "PolicyDenials", "Value": 5, "Unit": "Count", "Dimensions": policy_dims},
        # Automated Reasoning
        {"MetricName": "AutomatedReasoningCheck", "Value": 1, "Unit": "Count", "Dimensions": diag_dims},
    ]

    # Add timestamp to all
    for m in metrics:
        m["Timestamp"] = now

    cw.put_metric_data(Namespace="RAIHealthcare", MetricData=metrics)
    logger.info(f"Emitted {len(metrics)} metrics at {now.isoformat()}")
    logger.info("Refresh dashboard in 1 minute — all widgets will show data.")


if __name__ == "__main__":
    main()
