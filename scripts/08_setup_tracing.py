# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Demo 3 - Step 1: Verify Observability Setup
=============================================
Checks that CloudWatch log groups, alarms, and dashboard are active.

Runs AGAINST AWS (CloudWatch).
"""

import json
import logging
import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import BOTO_CONFIG, REGION, validate_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    if not validate_config():
        sys.exit(1)

    # ============================================================
    # CONFIGURATION
    # ============================================================

    print("=" * 60)
    print("DEMO 3: AgentCore Observability — Setup Verification")
    print("Target: AWS CloudWatch")
    print("=" * 60)
    print()

    cloudwatch = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)
    logs = boto3.client("logs", region_name=REGION, config=BOTO_CONFIG)

    # ============================================================
    # Step 1: Verify Log Groups
    # ============================================================
    logger.info("Verifying CloudWatch log groups...")

    log_groups = [
        "/aws/agentcore/rai-healthcare-demo/diagnostic-agent",
        "/aws/agentcore/rai-healthcare-demo/policy-enforcement",
        "/aws/agentcore/rai-healthcare-demo/audit-trail",
    ]

    for lg in log_groups:
        try:
            response = logs.describe_log_groups(logGroupNamePrefix=lg)
            if response["logGroups"]:
                retention = response["logGroups"][0].get("retentionInDays", "Never expire")
                print(f"  ✅ {lg} (retention: {retention} days)")
            else:
                print(f"  ⚠️  {lg} — not found")
        except Exception as e:
            print(f"  ⚠️  {lg} — {e}")

    print()

    # ============================================================
    # Step 2: Verify CloudWatch Alarms
    # ============================================================
    logger.info("Verifying CloudWatch alarms...")

    response = cloudwatch.describe_alarms(AlarmNamePrefix="rai-healthcare-demo")
    alarms = response["MetricAlarms"]

    if alarms:
        print(f"  Total alarms: {len(alarms)}")
        print()
        print(f"  {'Alarm Name':<45} {'State':<12} {'Threshold'}")
        print(f"  {'-' * 75}")
        for alarm in alarms:
            state = alarm["StateValue"]
            state_icon = "✅" if state == "OK" else "🚨" if state == "ALARM" else "❓"
            threshold = alarm.get("Threshold", "N/A")
            print(f"  {state_icon} {alarm['AlarmName']:<43} {state:<12} {threshold}")
    else:
        print("  ⚠️  No alarms found")

    print()

    # ============================================================
    # Step 3: Verify Dashboard
    # ============================================================
    logger.info("Verifying CloudWatch dashboard...")

    try:
        response = cloudwatch.get_dashboard(DashboardName="rai-healthcare-demo-monitoring")
        body = json.loads(response["DashboardBody"])
        widgets = body.get("widgets", [])
        print("  ✅ Dashboard: rai-healthcare-demo-monitoring")
        print(f"     Widgets: {len(widgets)}")
        for w in widgets:
            title = w.get("properties", {}).get("title", "Untitled")
            print(f"       • {title}")
    except Exception as e:
        logger.warning(f"Dashboard not found: {e}")

    print()

    # ============================================================
    # Step 4: Check Recent Metrics
    # ============================================================
    logger.info("Checking recent metrics (RAIHealthcare namespace)...")

    response = cloudwatch.list_metrics(Namespace="RAIHealthcare")
    metrics = response.get("Metrics", [])

    if metrics:
        metric_names = set(m["MetricName"] for m in metrics)
        print(f"  Active metrics: {len(metric_names)}")
        for name in sorted(metric_names):
            print(f"    • {name}")
    else:
        logger.warning("No metrics found yet (run demo scripts to emit metrics)")

    print()

    # ============================================================
    # Step 5: Show AgentCore Traces (if available)
    # ============================================================
    logger.info("Checking AgentCore traces...")

    try:
        # Check for AgentCore-generated log groups
        response = logs.describe_log_groups(logGroupNamePrefix="/aws/bedrock-agentcore")
        agent_logs = response.get("logGroups", [])

        if agent_logs:
            for lg in agent_logs:
                print(f"  ✅ {lg['logGroupName']}")
        else:
            # Also check for the raihealthdiag specific logs
            response = logs.describe_log_groups(logGroupNamePrefix="/aws/")
            for lg in response.get("logGroups", []):
                if "raihealthdiag" in lg["logGroupName"].lower() or "agentcore" in lg["logGroupName"].lower():
                    print(f"  ✅ {lg['logGroupName']}")
    except Exception as e:
        logger.warning(f"AgentCore traces: {e}")

    print()
    print(
        f"  Dashboard URL: https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}#dashboards:name=rai-healthcare-demo-monitoring"
    )
    print()
    print("=" * 60)
    print("✅ Observability verified. Proceed to 02_simulate_diagnostic.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
