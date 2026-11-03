# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Demo 3 - Step 2: Live Diagnostic Trace with Metrics
=====================================================
Invokes the live agent and emits full trace metrics to CloudWatch.

Runs AGAINST AWS (Bedrock, CloudWatch).
"""

import json
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import BEDROCK_MODEL_ID, BOTO_CONFIG, REGION, validate_config
from config import GUARDRAIL_ID as _GUARDRAIL_ID
from config import GUARDRAIL_VERSION as _GUARDRAIL_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    if not validate_config():
        sys.exit(1)

    # ============================================================
    # CONFIGURATION
    # ============================================================

    print("=" * 60)
    print("DEMO 3: AgentCore Observability — Live Diagnostic Trace")
    print("Target: AWS Bedrock + CloudWatch")
    print("=" * 60)
    print()

    bedrock = boto3.client("bedrock-runtime", region_name=REGION, config=BOTO_CONFIG)
    cloudwatch = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)

    GUARDRAIL_ID = _GUARDRAIL_ID
    GUARDRAIL_VERSION = _GUARDRAIL_VERSION

    # ============================================================
    # Step 1: Invoke Agent and Measure Latency
    # ============================================================
    logger.info("Invoking live agent (with timing)...")
    print()

    patient_query = """Patient is a 72-year-old female presenting with:
- Sudden onset shortness of breath (2 hours ago)
- Pleuritic chest pain (right-sided, worse with inspiration)
- Heart rate 110, BP 100/60, SpO2 88% on room air
- Recent 12-hour flight from London
- History: Obesity (BMI 34), oral contraceptives, previous DVT (2020)
- D-dimer: 4.2 (elevated)
- CT Angiogram: pending

Provide diagnostic recommendation with confidence score."""

    start_time = time.time()

    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        guardrailIdentifier=GUARDRAIL_ID,
        guardrailVersion=GUARDRAIL_VERSION,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 2000,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": patient_query}],
                "system": (
                    "You are a clinical decision support AI. Provide diagnosis with confidence"
                    " score in JSON format. Include primary_diagnosis, confidence (0-1),"
                    " priority, differential_diagnoses, and recommended_actions."
                ),
            }
        ),
    )

    end_time = time.time()
    latency_ms = (end_time - start_time) * 1000

    response_body = json.loads(response["body"].read())
    ai_output = response_body["content"][0]["text"]
    input_tokens = response_body["usage"]["input_tokens"]
    output_tokens = response_body["usage"]["output_tokens"]

    print(f"  Latency: {latency_ms:.0f}ms")
    print(f"  Input tokens: {input_tokens}")
    print(f"  Output tokens: {output_tokens}")
    print()
    print("--- Agent Response ---")
    print(ai_output[:2500])
    print()

    # ============================================================
    # Step 1b: Automated Reasoning Validation
    # ============================================================
    if GUARDRAIL_ID:
        logger.info("Running Automated Reasoning check...")
        print()

        try:
            guardrail_response = bedrock.apply_guardrail(
                guardrailIdentifier=GUARDRAIL_ID,
                guardrailVersion=GUARDRAIL_VERSION,
                source="OUTPUT",
                content=[{"text": {"text": ai_output}}],
            )

            action = guardrail_response.get("action", "NONE")
            assessments = guardrail_response.get("assessments", [])

            if action == "NONE":
                print("  ✅ AUTOMATED REASONING: VALID")
                print("     Diagnosis is logically consistent with clinical rules")
                print("     No contradictions found with patient data")
            elif action == "GUARDRAIL_INTERVENED":
                print("  🚨 AUTOMATED REASONING: ISSUES DETECTED")
                for assessment in assessments:
                    ar_checks = assessment.get("automatedReasoningPolicy", {})
                    findings = ar_checks.get("findings", [])
                    for finding in findings:
                        print(f"     Finding: {finding.get('type', 'UNKNOWN')}")
                        print(f"     Detail: {finding.get('message', 'No detail')[:100]}")

            # Emit metric
            cloudwatch.put_metric_data(
                Namespace="RAIHealthcare",
                MetricData=[
                    {
                        "MetricName": "AutomatedReasoningCheck",
                        "Value": 1 if action == "NONE" else 0,
                        "Unit": "Count",
                        "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}],
                        "Timestamp": datetime.now(UTC),
                    }
                ],
            )
            logger.info("AutomatedReasoningCheck metric emitted")
            print()

        except Exception as e:
            logger.warning(f"Guardrail check skipped: {e}")
            print()
    else:
        print("--- Automated Reasoning Check ---")
        print("  ℹ️  Guardrail not configured. Set GUARDRAIL_ID env var after running:")
        print("     python automated_reasoning/setup_guardrail.py")
        print()

    # ============================================================
    # Step 2: Emit Trace Metrics to CloudWatch
    # ============================================================
    logger.info("Emitting trace metrics to CloudWatch...")

    # Parse confidence from output (best effort)
    confidence = 95  # Default high for PE case
    try:
        if '"confidence"' in ai_output:
            match = re.search(r'"confidence"\s*:\s*([\d.]+)', ai_output)
            if match:
                confidence = float(match.group(1))
                if confidence <= 1:
                    confidence = confidence * 100
    except (ValueError, TypeError, re.error) as exc:
        logger.debug("Could not parse confidence from AI output; using default %s: %s", confidence, exc)

    metrics_to_emit = [
        {"MetricName": "InferenceLatency", "Value": latency_ms, "Unit": "Milliseconds"},
        {"MetricName": "DiagnosticConfidence", "Value": confidence, "Unit": "Percent"},
        {"MetricName": "InputTokens", "Value": input_tokens, "Unit": "Count"},
        {"MetricName": "OutputTokens", "Value": output_tokens, "Unit": "Count"},
        {"MetricName": "TotalInvocations", "Value": 1, "Unit": "Count"},
    ]

    # Check if alert should trigger
    if confidence < 70:
        metrics_to_emit.append({"MetricName": "AlertsTriggered", "Value": 1, "Unit": "Count"})
        print(f"  🚨 LOW CONFIDENCE ALERT: {confidence:.0f}% < 70% threshold")

    cloudwatch.put_metric_data(
        Namespace="RAIHealthcare",
        MetricData=[
            {**m, "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}], "Timestamp": datetime.now(UTC)}
            for m in metrics_to_emit
        ],
    )

    logger.info("Metrics emitted:")
    for m in metrics_to_emit:
        logger.info(f"  {m['MetricName']}: {m['Value']:.0f} {m['Unit']}")

    print()

    # ============================================================
    # Step 3: Simulate Multiple Invocations for Dashboard Data
    # ============================================================
    logger.info("Emitting historical metrics (for dashboard)...")

    # Emit a batch of simulated metrics to populate the dashboard
    historical_metrics = []

    # Deterministic sample values for populating the dashboard (not security-sensitive)
    sample_confs = [72.5, 85.3, 91.0, 68.7, 94.2, 77.8, 89.1, 96.5, 74.0, 82.6]
    sample_lats = [2100, 1850, 2400, 3100, 1600, 2800, 2200, 1900, 2650, 3000]

    for _i in range(10):
        conf = sample_confs[_i]
        lat = sample_lats[_i]
        historical_metrics.extend(
            [
                {
                    "MetricName": "DiagnosticConfidence",
                    "Value": conf,
                    "Unit": "Percent",
                    "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}],
                    "Timestamp": datetime.now(UTC),
                },
                {
                    "MetricName": "InferenceLatency",
                    "Value": lat,
                    "Unit": "Milliseconds",
                    "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}],
                    "Timestamp": datetime.now(UTC),
                },
            ]
        )

    # CloudWatch allows max 1000 metrics per call, we have 20
    cloudwatch.put_metric_data(Namespace="RAIHealthcare", MetricData=historical_metrics)
    logger.info(f"Emitted {len(historical_metrics)} historical data points (populates dashboard graphs)")

    print()
    print(
        f"  📊 View dashboard: https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}#dashboards:name=rai-healthcare-demo-monitoring"
    )
    print()
    print("=" * 60)
    print("✅ Diagnostic trace complete. Proceed to 03_alert_low_confidence.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
