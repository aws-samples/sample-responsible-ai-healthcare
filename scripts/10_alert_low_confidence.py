# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Demo 3 - Step 3: Trigger Low Confidence Alert + Drift Detection
================================================================
Invokes agent with ambiguous case to trigger low-confidence alert,
then checks CloudWatch alarms and dashboard state.

Runs AGAINST AWS (Bedrock, CloudWatch, SNS).
"""

import json
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime, timedelta

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import BEDROCK_MODEL_ID, BOTO_CONFIG, GUARDRAIL_ID, GUARDRAIL_VERSION, REGION, validate_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    if not validate_config():
        sys.exit(1)

    # ============================================================
    # CONFIGURATION
    # ============================================================

    print("=" * 60)
    print("DEMO 3: AgentCore Observability — Alert & Drift Detection")
    print("Target: AWS Bedrock + CloudWatch Alarms")
    print("=" * 60)
    print()

    bedrock = boto3.client("bedrock-runtime", region_name=REGION, config=BOTO_CONFIG)
    cloudwatch = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)

    # ============================================================
    # Step 1: Trigger Low Confidence (Ambiguous Case)
    # ============================================================
    logger.info("Triggering low confidence alerts...")
    print()

    ambiguous_cases = [
        (
            "Patient is 55-year-old with vague abdominal discomfort, no fever, normal"
            " labs. Could be anything from IBS to early appendicitis. Provide diagnosis"
            " with confidence."
        ),
        (
            "Patient is 40-year-old with intermittent dizziness, normal BP, normal"
            " glucose, normal ECG. Unclear etiology. Provide diagnosis with confidence."
        ),
        (
            "Patient is 78-year-old with generalized weakness and mild confusion."
            " Labs unremarkable. Multiple possible causes. Provide diagnosis"
            " with confidence."
        ),
    ]

    low_confidence_count = 0

    for i, case in enumerate(ambiguous_cases, 1):
        print(f"  Case {i}: {case[:60]}...")

        start = time.time()
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            guardrailIdentifier=GUARDRAIL_ID,
            guardrailVersion=GUARDRAIL_VERSION,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 1000,
                    "temperature": 0.1,
                    "messages": [{"role": "user", "content": case}],
                    "system": (
                        "You are a clinical AI. For ambiguous cases with unclear diagnosis,"
                        " your confidence MUST be below 0.65. Always output JSON with"
                        " 'confidence' field (0.0-1.0)."
                    ),
                }
            ),
        )
        latency = (time.time() - start) * 1000

        output = json.loads(response["body"].read())["content"][0]["text"]

        # Extract confidence
        confidence = 50  # default low
        match = re.search(r'"confidence"\s*:\s*([\d.]+)', output)
        if match:
            confidence = float(match.group(1))
            if confidence <= 1:
                confidence = confidence * 100

        is_low = confidence < 70
        if is_low:
            low_confidence_count += 1

        status = "🚨 LOW" if is_low else "✅ OK"
        print(f"         Confidence: {confidence:.0f}% {status} | Latency: {latency:.0f}ms")

        # Emit metric
        cloudwatch.put_metric_data(
            Namespace="RAIHealthcare",
            MetricData=[
                {
                    "MetricName": "DiagnosticConfidence",
                    "Value": confidence,
                    "Unit": "Percent",
                    "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}],
                    "Timestamp": datetime.now(UTC),
                },
                {
                    "MetricName": "AlertsTriggered",
                    "Value": 1 if is_low else 0,
                    "Unit": "Count",
                    "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}],
                    "Timestamp": datetime.now(UTC),
                },
            ],
        )
        logger.info(f"Case {i} metrics emitted to CloudWatch")

    print()
    print(f"  Low confidence alerts triggered: {low_confidence_count}/{len(ambiguous_cases)}")
    print()

    # ============================================================
    # Step 2: Check CloudWatch Alarm State
    # ============================================================
    logger.info("Checking CloudWatch alarm states...")
    print()

    response = cloudwatch.describe_alarms(AlarmNamePrefix="rai-healthcare-demo")
    for alarm in response["MetricAlarms"]:
        state = alarm["StateValue"]
        icon = "🚨" if state == "ALARM" else "✅" if state == "OK" else "❓"
        print(f"  {icon} {alarm['AlarmName']}")
        print(f"     State: {state} | Threshold: {alarm.get('Threshold', 'N/A')}")
        if state == "ALARM":
            print(f"     Reason: {alarm.get('StateReason', 'N/A')[:80]}")

    print()

    # ============================================================
    # Step 3: Emit Drift Simulation Metrics
    # ============================================================
    logger.info("Simulating accuracy drift...")
    print()

    # Simulate 7 days of declining accuracy
    drift_metrics = []
    base_accuracy = 95.0

    for day in range(7):
        # Simulate gradual drift
        daily_accuracy = base_accuracy - (day * 0.8) + (0.5 if day == 6 else 0)
        drift_metrics.append(
            {
                "MetricName": "DiagnosticAccuracy",
                "Value": daily_accuracy,
                "Unit": "Percent",
                "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}],
                "Timestamp": datetime.now(UTC) - timedelta(days=6 - day),
            }
        )
        print(f"  Day {day + 1}: Accuracy {daily_accuracy:.1f}% {'⚠️' if daily_accuracy < 93 else '✅'}")

    cloudwatch.put_metric_data(Namespace="RAIHealthcare", MetricData=drift_metrics)
    print()
    logger.info("Drift metrics emitted (7-day trend)")
    print()

    # ============================================================
    # Step 4: Emit Error Rate Metrics
    # ============================================================
    logger.info("Emitting error rate metric...")

    cloudwatch.put_metric_data(
        Namespace="RAIHealthcare",
        MetricData=[
            {
                "MetricName": "ErrorRate",
                "Value": 0.3,
                "Unit": "Percent",
                "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}],
                "Timestamp": datetime.now(UTC),
            }
        ],
    )
    logger.info("Error rate: 0.3% (within threshold)")
    print()

    # ============================================================
    # Step 5: Summary Dashboard Link
    # ============================================================
    print("=" * 60)
    print("OBSERVABILITY DEMO SUMMARY")
    print("=" * 60)
    print()
    print(f"  Low confidence alerts: {low_confidence_count}")
    first, last = drift_metrics[0]["Value"], drift_metrics[-1]["Value"]
    print(f"  Accuracy drift (simulated): {first:.1f}% → {last:.1f}% over {len(drift_metrics)} days")
    print(f"  CloudWatch alarms: {len(response['MetricAlarms'])} configured")
    print()
    print("  📊 Dashboard:")
    print(
        f"  https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}#dashboards:name=rai-healthcare-demo-monitoring"
    )
    print()
    print("RAI Dimensions Demonstrated:")
    print("  • Safety: Low-confidence alerts triggered on ambiguous cases")
    print("  • Veracity: Accuracy drift detected over 7-day window")
    print("  • Transparency: Full metrics visible in CloudWatch dashboard")
    print()
    print("=" * 60)
    print("✅ ALL 3 DEMOS COMPLETE — END-TO-END ON AWS")
    print("=" * 60)
    print()
    print("Recap:")
    print("  Demo 1: Bias, SHAP + LLM evaluation (SageMaker AI Processing, Bedrock Evaluations)")
    print("  Demo 2: AgentCore Policy — RBAC + Override (IAM, S3, Bedrock)")
    print("  Demo 3: AgentCore Observability — Alerts + Drift (CloudWatch, Bedrock)")
    print()
    print("RAI Lifecycle: DETECT → ENFORCE → MONITOR")
    print("=" * 60)


if __name__ == "__main__":
    main()
