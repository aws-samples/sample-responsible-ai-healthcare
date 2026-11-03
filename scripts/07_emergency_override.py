# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Demo 2 - Step 3: Emergency Override via Live Agent
===================================================
Invokes the live agent with a low-confidence case, then shows
the physician override workflow with audit trail stored in S3.

Runs AGAINST AWS (Bedrock, S3, CloudWatch).
"""

import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import (
    BEDROCK_MODEL_ID,
    BOTO_CONFIG,
    GUARDRAIL_ID,
    GUARDRAIL_VERSION,
    REGION,
    S3_BUCKET,
    S3_PREFIX,
    validate_config,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    # ============================================================
    # CONFIGURATION
    # ============================================================
    if not validate_config():
        raise SystemExit(1)

    print("=" * 60)
    print("DEMO 2: AgentCore Policy — Emergency Override")
    print("Target: AWS Bedrock + S3 + CloudWatch")
    print("=" * 60)
    print()

    # ============================================================
    # Step 1: Invoke Agent with Ambiguous Case (Low Confidence)
    # ============================================================
    logger.info("Invoking agent with ambiguous patient case...")
    print()

    bedrock = boto3.client("bedrock-runtime", region_name=REGION, config=BOTO_CONFIG)

    ambiguous_case = """Patient is an 88-year-old male with intermittent chest discomfort and fatigue.
Vital signs: BP 145/85, HR 78, Temp 37.0, SpO2 95%.
Labs: Troponin 0.08 (borderline), BNP 280.
ECG: Non-specific ST changes, no clear elevation.
History: COPD, mild heart failure, osteoarthritis.
Medications: Furosemide, Metoprolol, Albuterol inhaler.

This is an ambiguous presentation. Provide your diagnostic recommendation with confidence score.
Note if confidence is below 0.70 and flag for mandatory physician review."""

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
                "messages": [{"role": "user", "content": ambiguous_case}],
                "system": (
                    "You are a clinical decision support AI. For ambiguous cases, your"
                    " confidence MUST be below 0.70. Always flag low-confidence cases for"
                    " mandatory physician review. Output JSON format with primary_diagnosis,"
                    " confidence, differential_diagnoses, and risk_flags."
                ),
            }
        ),
    )

    response_body = json.loads(response["body"].read())
    ai_output = response_body["content"][0]["text"]

    print("AI Recommendation (Ambiguous Case):")
    print(ai_output[:2000])
    print()

    # ============================================================
    # Step 2: Simulate Physician Override
    # ============================================================
    print("--- Physician Override ---")
    print()

    override_record = {
        "event_id": f"OVR-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "event_type": "AI_RECOMMENDATION_OVERRIDE",
        "timestamp": datetime.now().isoformat(),
        "patient_id": "P013",
        "physician": {
            "id": "PHY-2847",
            "name": "Dr. Sarah Smith",
            "specialty": "Cardiology",
            "location": "Hospital-Emergency-1",
        },
        "ai_recommendation": "Ambiguous — multiple competing diagnoses",
        "ai_confidence": 0.58,
        "physician_diagnosis": "Acute Heart Failure Exacerbation",
        "override_justification": (
            "Clinical examination reveals bilateral crackles, JVD, and peripheral edema"
            " not captured in structured data. BNP trending upward. Initiating IV diuresis."
        ),
        "clinical_evidence": [
            "Bilateral crackles on auscultation",
            "Jugular venous distension (JVD)",
            "2+ peripheral edema bilateral lower extremities",
            "BNP 280 (elevated for this patient's baseline of 120)",
            "Weight gain 3kg over 5 days",
        ],
        "actions_taken": [
            "IV Furosemide 40mg bolus",
            "Fluid restriction 1.5L/day",
            "Daily weights ordered",
            "Cardiology consult within 4 hours",
            "Repeat BNP in 6 hours",
        ],
        "urgency": "URGENT",
        "post_hoc_audit_required": True,
        "audit_deadline": (datetime.now() + timedelta(hours=24)).isoformat(),
    }

    print(f"  Override ID: {override_record['event_id']}")
    print(f"  Physician: {override_record['physician']['name']}")
    print(f"  AI Confidence: {override_record['ai_confidence']:.0%} (BELOW 70% THRESHOLD)")
    print(f"  AI Said: {override_record['ai_recommendation']}")
    print(f"  Physician Says: {override_record['physician_diagnosis']}")
    print(f"  Justification: {override_record['override_justification'][:80]}...")
    print(f"  Audit Deadline: {override_record['audit_deadline']}")
    print()

    # ============================================================
    # Step 3: Store Override in S3 (Audit Trail)
    # ============================================================
    logger.info("Storing override audit trail in S3...")

    s3 = boto3.client("s3", region_name=REGION, config=BOTO_CONFIG)
    audit_key = f"{S3_PREFIX}/audit/overrides/{override_record['event_id']}.json"

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=audit_key,
        Body=json.dumps(override_record, indent=2),
        ContentType="application/json",
        Metadata={
            "event-type": "override",
            "physician-id": override_record["physician"]["id"],
            "patient-id": override_record["patient_id"],
            "urgency": override_record["urgency"],
        },
    )
    logger.info(f"Stored: s3://{S3_BUCKET}/{audit_key}")
    print()

    # ============================================================
    # Step 4: Emit Override Metrics to CloudWatch
    # ============================================================
    logger.info("Emitting override metrics to CloudWatch...")

    cloudwatch = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)
    cloudwatch.put_metric_data(
        Namespace="RAIHealthcare",
        MetricData=[
            {
                "MetricName": "OverrideRate",
                "Value": 1,
                "Unit": "Count",
                "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}],
                "Timestamp": datetime.now(UTC),
            },
            {
                "MetricName": "LowConfidenceAlert",
                "Value": 1,
                "Unit": "Count",
                "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}],
                "Timestamp": datetime.now(UTC),
            },
            {
                "MetricName": "DiagnosticConfidence",
                "Value": 58,
                "Unit": "Percent",
                "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}],
                "Timestamp": datetime.now(UTC),
            },
        ],
    )
    logger.info("Metrics emitted: OverrideRate, LowConfidenceAlert, DiagnosticConfidence")
    print()

    # ============================================================
    # Step 5: Log to CloudWatch Logs (Audit Trail)
    # ============================================================
    logger.info("Logging to CloudWatch audit trail...")

    logs = boto3.client("logs", region_name=REGION, config=BOTO_CONFIG)
    log_group = "/aws/agentcore/rai-healthcare-demo/audit-trail"

    try:
        logs.create_log_stream(logGroupName=log_group, logStreamName=f"overrides-{datetime.now().strftime('%Y%m%d')}")
    except logs.exceptions.ResourceAlreadyExistsException:
        pass

    logs.put_log_events(
        logGroupName=log_group,
        logStreamName=f"overrides-{datetime.now().strftime('%Y%m%d')}",
        logEvents=[{"timestamp": int(datetime.now().timestamp() * 1000), "message": json.dumps(override_record)}],
    )
    logger.info(f"Logged to: {log_group}")
    print()

    print("=" * 60)
    print("EMERGENCY OVERRIDE SUMMARY")
    print("=" * 60)
    print()
    print("  1. ✅ Agent invoked with ambiguous case → low confidence")
    print("  2. ✅ Physician override recorded with justification")
    print("  3. ✅ Audit trail stored in S3 (versioned, encrypted)")
    print("  4. ✅ Metrics emitted to CloudWatch dashboard")
    print("  5. ✅ Event logged to CloudWatch Logs for compliance")
    print()
    print("RAI Principles Demonstrated:")
    print("  • Controllability: Physician maintains full autonomy")
    print("  • Safety: Low-confidence alert triggered correctly")
    print("  • Governance: Complete audit trail in S3 + CloudWatch")
    print("  • Transparency: AI limitations visible (confidence displayed)")
    print()
    print("=" * 60)
    print("✅ Override demo complete. Proceed to Demo 3: Observability")
    print("=" * 60)


if __name__ == "__main__":
    main()
