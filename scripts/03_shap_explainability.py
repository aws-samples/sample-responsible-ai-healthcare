# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Demo 1 - Step 3: Explain a diagnosis with real SHAP values
===========================================================
Reads the SHAP report written by the bias and explainability job
(scripts/02_bias_detection.py, computed with the SHAP library directly, as AWS
recommends now that SageMaker Clarify is closed to new customers), prints the global and per-patient
attributions, and asks Claude on Amazon Bedrock (with the guardrail) to explain
those attributions to a clinician. The narrative is the model's wording of the
computed values; it does not change them.

Runs AGAINST AWS (S3, Bedrock, CloudWatch).
"""

import json
import logging
import os
import sys

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

BAR_WIDTH = 40


def latest_shap_report(s3) -> tuple[str, dict]:
    """Key and content of the newest explainability/<run>/shap.json."""
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/explainability/")
    reports = [o for page in pages for o in page.get("Contents", []) if o["Key"].endswith("/shap.json")]
    if not reports:
        raise SystemExit("No SHAP report found. Run scripts/02_bias_detection.py first.")
    key = max(reports, key=lambda o: o["LastModified"])["Key"]
    return key, json.loads(s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read())


def bars(rows: list[dict], value_key: str, scale: float) -> list[str]:
    """Text bar chart lines for a list of attributions (pure, unit-tested)."""
    lines = []
    for r in rows:
        v = r[value_key]
        length = int(round(abs(v) / scale * BAR_WIDTH)) if scale else 0
        lines.append(f"  {r['feature']:26} {'█' * length:<{BAR_WIDTH}} {v:+.3f}")
    return lines


def main():
    if not validate_config():
        sys.exit(1)
    s3 = boto3.client("s3", region_name=REGION, config=BOTO_CONFIG)
    bedrock = boto3.client("bedrock-runtime", region_name=REGION, config=BOTO_CONFIG)
    cloudwatch = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)

    print("=" * 60)
    print("DEMO 1: Explainability — real SHAP attributions")
    print("=" * 60)

    key, report = latest_shap_report(s3)
    print(f"Report: s3://{S3_BUCKET}/{key}")
    print(f"Method: {report['method']} (SHAP {report['shap_version']})")
    print(f"Model 5-fold accuracy: {report['cv_accuracy']:.1%}   Base value (log-odds): {report['base_value']:+.3f}")

    print("\n--- Global importance: mean |SHAP| across all 40 patients ---")
    top = report["global_importance"]
    print("\n".join(bars(top, "mean_abs_shap", top[0]["mean_abs_shap"])))

    local = report["local_explanation"]
    attributions = local["attributions"][:8]
    print(f"\n--- Patient {local['patient_id']}: predicted ACS probability {local['predicted_probability']:.0%} ---")
    print("\n".join(bars(attributions, "shap", max(abs(a["shap"]) for a in attributions))))

    prompt = (
        "These SHAP attributions (log-odds units) explain a model's prediction of acute coronary syndrome "
        f"for patient {local['patient_id']} (predicted probability {local['predicted_probability']:.0%}):\n"
        f"{json.dumps(attributions, indent=2)}\n\n"
        "Explain to a physician, in five short bullet points, which findings drove the prediction and what "
        "would most change it. Use only the values given; do not invent new numbers."
    )
    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        guardrailIdentifier=GUARDRAIL_ID,
        guardrailVersion=GUARDRAIL_VERSION,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 800,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": prompt}],
            }
        ),
    )
    narrative = json.loads(response["body"].read())["content"][0]["text"]
    print("\n--- Clinician narrative (model wording of the SHAP values above) ---")
    print(narrative)

    cloudwatch.put_metric_data(
        Namespace="RAIHealthcare",
        MetricData=[
            {
                "MetricName": "ExplainabilityProvided",
                "Value": 1,
                "Unit": "Count",
                "Dimensions": [{"Name": "Service", "Value": "BiasDetection"}],
            }
        ],
    )
    print("\n✅ Explainability complete. Next: python scripts/04_evaluate_llm.py")


if __name__ == "__main__":
    main()
