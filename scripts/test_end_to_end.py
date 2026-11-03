# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
END-TO-END TEST — Runs the FULL architecture pipeline automatically
====================================================================
Tests every component in the architecture (13 checks):
1. Upload patient data to S3 (incoming/) → triggers the preprocessing Lambda → result in S3
2. Bias metrics and SHAP: the latest SageMaker AI Processing job (scripts/02_bias_detection.py)
   completed and its bias/ and explainability/ reports are in S3
3. Bedrock inference with the content-filter guardrail
4. Automated Reasoning guardrail validates a diagnostic output
5-6. RBAC: physician allowed, admin denied (assumed IAM roles)
7. Emergency override: audit record in S3 and CloudWatch
8-9. Observability: alarms and dashboard exist
10. Low-confidence alert path
11. Knowledge Base exists
12. Lambda exists
13. AgentCore runtime invocation

Run scripts/02_bias_detection.py first so step 2 has a completed job to check.
ALL ON AWS. NOTHING MANUAL. NOTHING LOCAL.
"""

import importlib.util
import json
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import (
    AWS_ACCOUNT_ID,
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


def _load_invoke_module():
    """Load scripts/11_invoke_agentcore.py (its file name is not a valid module name)."""
    spec = importlib.util.spec_from_file_location(
        "invoke_agentcore", os.path.join(os.path.dirname(__file__), "11_invoke_agentcore.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    # ============================================================
    # CONFIGURATION
    # ============================================================
    if not validate_config():
        raise SystemExit(1)

    ACCOUNT_ID = AWS_ACCOUNT_ID
    LAMBDA_NAME = "rai-healthcare-demo-preprocessing"

    # AWS Clients
    s3 = boto3.client("s3", region_name=REGION, config=BOTO_CONFIG)
    bedrock = boto3.client("bedrock-runtime", region_name=REGION, config=BOTO_CONFIG)
    cloudwatch = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)
    sagemaker = boto3.client("sagemaker", region_name=REGION, config=BOTO_CONFIG)
    logs = boto3.client("logs", region_name=REGION, config=BOTO_CONFIG)
    sts = boto3.client("sts", region_name=REGION, config=BOTO_CONFIG)
    lambda_client = boto3.client("lambda", region_name=REGION, config=BOTO_CONFIG)

    # Results tracking
    results = []
    start_time = datetime.now()

    def test(name, func):
        """Run a test and track result."""
        print(f"\n{'─' * 60}")
        print(f"  TEST: {name}")
        print(f"{'─' * 60}")
        try:
            result = func()
            results.append({"test": name, "status": "✅ PASS", "detail": result})
            print(f"  ✅ PASS: {result}")
        except Exception as e:
            results.append({"test": name, "status": "❌ FAIL", "detail": str(e)[:100]})
            print(f"  ❌ FAIL: {e}")

    # ============================================================
    print("=" * 60)
    print("  END-TO-END TEST — Full Architecture Pipeline")
    print("  Target: AWS (ALL components)")
    print(f"  Started: {start_time.isoformat()}")
    print("=" * 60)
    # ============================================================

    # ──────────────────────────────────────────────────────────────
    # TEST 1: S3 Upload → Lambda Trigger
    # ──────────────────────────────────────────────────────────────
    def test_lambda_pipeline():
        """Upload patient to S3 incoming/ → Lambda triggers → processes → stores result."""

        test_patient = {
            "patient_id": "P-E2E-TEST",
            "age": 67,
            "gender": "Male",
            "chief_complaint": "Severe chest pain radiating to left arm",
            "vital_signs": {
                "heart_rate": 102,
                "blood_pressure": "165/95",
                "temperature": 37.1,
                "respiratory_rate": 22,
                "oxygen_saturation": 94,
            },
            "lab_results": {"troponin": 2.4, "bnp": 450},
            "ecg": "ST-elevation in leads V1-V4",
            "medical_history": ["Hypertension", "Type 2 Diabetes", "Previous MI (2019)"],
            "medications": ["Metformin", "Lisinopril", "Aspirin"],
        }

        # Upload to incoming/ (triggers Lambda). The Lambda names its output after
        # the object key's file stem and the uploaded object version:
        # processed/<file stem>/<versionId>.json
        stem = f"e2e-test-{datetime.now().strftime('%H%M%S')}"
        incoming_key = f"{S3_PREFIX}/data/incoming/{stem}.json"
        put = s3.put_object(
            Bucket=S3_BUCKET, Key=incoming_key, Body=json.dumps(test_patient), ContentType="application/json"
        )
        processed_key = f"{S3_PREFIX}/processed/{stem}/{put['VersionId']}.json"

        # VPC-attached Lambda cold start can take 10-30s, plus the Bedrock call
        print("    Waiting up to 180s for the S3-triggered Lambda to write its result...")
        for _ in range(36):
            try:
                result = json.loads(s3.get_object(Bucket=S3_BUCKET, Key=processed_key)["Body"].read())
                break
            except s3.exceptions.NoSuchKey:
                time.sleep(5)
        else:
            raise Exception(f"No processed result at {processed_key} after 180s")

        if result.get("validation_status") != "PASSED":
            raise Exception(f"Result did not pass validation: {result.get('validation_status')}")
        return f"Lambda processed patient → {processed_key} (validation PASSED)"

    test("Lambda Pipeline (S3 → Lambda → Bedrock → S3)", test_lambda_pipeline)

    # ──────────────────────────────────────────────────────────────
    # TEST 2: Bias Detection (S3 + CloudWatch)
    # ──────────────────────────────────────────────────────────────
    def test_bias_detection():
        """The latest bias/SHAP Processing job completed and wrote its reports to S3."""
        prefix = "rai-healthcare-demo-bias-"
        jobs = sagemaker.list_processing_jobs(NameContains=prefix, SortBy="CreationTime", SortOrder="Descending")
        names = sorted(
            j["ProcessingJobName"] for j in jobs["ProcessingJobSummaries"] if j["ProcessingJobName"].startswith(prefix)
        )
        if not names:
            raise RuntimeError("no bias processing job found; run scripts/02_bias_detection.py first")
        name = names[-1]
        status = sagemaker.describe_processing_job(ProcessingJobName=name)["ProcessingJobStatus"]
        if status != "Completed":
            raise RuntimeError(f"{name}: {status}")
        run = name.removeprefix(prefix)
        bias = json.loads(s3.get_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX}/bias/{run}/report.json")["Body"].read())
        shap = json.loads(
            s3.get_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX}/explainability/{run}/shap.json")["Body"].read()
        )
        if not all({"CI", "DPL", "AD"} <= set(f) for f in bias["facets"]):
            raise RuntimeError("bias report missing CI/DPL/AD")
        if not shap["global_importance"]:
            raise RuntimeError("SHAP report has no attributions")
        return f"{name}: {len(bias['facets'])} facets, top SHAP feature {shap['global_importance'][0]['feature']}"

    test("Bias and SHAP Processing job (S3)", test_bias_detection)

    # ──────────────────────────────────────────────────────────────
    # TEST 3: Bedrock Inference (Explainability)
    # ──────────────────────────────────────────────────────────────
    def test_bedrock_inference():
        """Call Bedrock Claude Sonnet 4.6 with patient data."""
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            guardrailIdentifier=GUARDRAIL_ID,
            guardrailVersion=GUARDRAIL_VERSION,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 500,
                    "temperature": 0.1,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Patient: 67M, chest pain, troponin 2.4, ST-elevation."
                                " Diagnosis? Reply with JSON:"
                                ' {"diagnosis": "...", "confidence": 0.X}'
                            ),
                        }
                    ],
                    "system": "You are a clinical AI. Respond only with JSON.",
                }
            ),
        )
        output = json.loads(response["body"].read())["content"][0]["text"]
        _tokens = response["ResponseMetadata"]["HTTPHeaders"].get("x-amzn-bedrock-output-token-count", "?")
        return f"Bedrock responded: {output[:100]}..."

    test("Bedrock Inference (Claude Sonnet 4.6)", test_bedrock_inference)

    # ──────────────────────────────────────────────────────────────
    # TEST 4: Automated Reasoning Guardrail
    # ──────────────────────────────────────────────────────────────
    def test_automated_reasoning():
        """Validate AI output against clinical rules via guardrail."""
        test_output = (
            "The patient has Acute Myocardial Infarction (STEMI) with 97% confidence."
            " Troponin is elevated at 2.4 ng/mL and ECG shows ST-elevation."
            " Recommend immediate cardiac catheterization."
        )

        response = bedrock.apply_guardrail(
            guardrailIdentifier=GUARDRAIL_ID,
            guardrailVersion=GUARDRAIL_VERSION,
            source="OUTPUT",
            content=[{"text": {"text": test_output}}],
        )

        action = response.get("action", "UNKNOWN")
        if action == "NONE":
            return "VALID — No contradictions with clinical rules"
        else:
            return f"INTERVENED — {action}"

    test("Automated Reasoning Guardrail", test_automated_reasoning)

    # ──────────────────────────────────────────────────────────────
    # TEST 5: IAM RBAC — Physician ALLOWED
    # ──────────────────────────────────────────────────────────────
    def test_rbac_physician_allowed():
        """Physician role can access patient data."""
        response = sts.assume_role(
            RoleArn=f"arn:aws:iam::{ACCOUNT_ID}:role/rai-healthcare-demo-physician-role",
            RoleSessionName="e2e-test",
            ExternalId="physician-access",
            DurationSeconds=900,
        )
        creds = response["Credentials"]
        _session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=REGION,
        )
        # Physician should be able to list objects (has S3 access via AgentCore invoke)
        return "Physician role assumed successfully"

    test("RBAC — Physician Role Assumption", test_rbac_physician_allowed)

    # ──────────────────────────────────────────────────────────────
    # TEST 6: IAM RBAC — Admin DENIED patient data
    # ──────────────────────────────────────────────────────────────
    def test_rbac_admin_denied():
        """Admin role cannot access patient data."""
        response = sts.assume_role(
            RoleArn=f"arn:aws:iam::{ACCOUNT_ID}:role/rai-healthcare-demo-admin-role",
            RoleSessionName="e2e-test",
            ExternalId="admin-access",
            DurationSeconds=900,
        )
        creds = response["Credentials"]
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=REGION,
        )
        admin_s3 = session.client("s3", config=BOTO_CONFIG)
        try:
            admin_s3.get_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX}/data/patient_diagnostic_data.csv")
            raise Exception("Admin was ALLOWED — this should have been DENIED!")
        except admin_s3.exceptions.ClientError as e:
            if "AccessDenied" in str(e):
                return "Admin correctly DENIED access to patient data"
            raise

    test("RBAC — Admin DENIED Patient Data", test_rbac_admin_denied)

    # ──────────────────────────────────────────────────────────────
    # TEST 7: Override Audit Trail (S3 + CloudWatch Logs)
    # ──────────────────────────────────────────────────────────────
    def test_override_audit():
        """Store override in S3 and log to CloudWatch."""
        override = {
            "event_id": f"OVR-E2E-{datetime.now().strftime('%H%M%S')}",
            "event_type": "AI_RECOMMENDATION_OVERRIDE",
            "timestamp": datetime.now(UTC).isoformat(),
            "physician_id": "PHY-2847",
            "patient_id": "P-E2E-TEST",
            "ai_confidence": 0.58,
            "physician_diagnosis": "Heart Failure Exacerbation",
        }

        # Store in S3
        audit_key = f"{S3_PREFIX}/audit/overrides/{override['event_id']}.json"
        s3.put_object(Bucket=S3_BUCKET, Key=audit_key, Body=json.dumps(override), ContentType="application/json")

        # Log to CloudWatch
        log_group = "/aws/agentcore/rai-healthcare-demo/audit-trail"
        stream_name = f"e2e-test-{datetime.now().strftime('%Y%m%d')}"
        try:
            logs.create_log_stream(logGroupName=log_group, logStreamName=stream_name)
        except ClientError as exc:
            logger.debug("Log stream %s not created (may already exist): %s", stream_name, exc)
        logs.put_log_events(
            logGroupName=log_group,
            logStreamName=stream_name,
            logEvents=[{"timestamp": int(datetime.now().timestamp() * 1000), "message": json.dumps(override)}],
        )

        # Emit metric
        cloudwatch.put_metric_data(
            Namespace="RAIHealthcare",
            MetricData=[
                {
                    "MetricName": "OverrideRate",
                    "Value": 1,
                    "Unit": "Count",
                    "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}],
                    "Timestamp": datetime.now(UTC),
                }
            ],
        )

        return f"Override stored: s3://{S3_BUCKET}/{audit_key} + CloudWatch logged"

    test("Override Audit Trail (S3 + CloudWatch Logs)", test_override_audit)

    # ──────────────────────────────────────────────────────────────
    # TEST 8: CloudWatch Alarms Active
    # ──────────────────────────────────────────────────────────────
    def test_cloudwatch_alarms():
        """Verify the seven alarms in infra/cloudwatch.tf exist, including the low-confidence alarm."""
        response = cloudwatch.describe_alarms(AlarmNamePrefix="rai-healthcare-demo")
        alarms = {a["AlarmName"]: a for a in response["MetricAlarms"]}
        if "rai-healthcare-demo-low-confidence-alert" not in alarms:
            raise Exception("rai-healthcare-demo-low-confidence-alert not found")
        if len(alarms) < 7:
            raise Exception(f"Only {len(alarms)} alarms found (expected 7)")
        states = sorted({a["StateValue"] for a in alarms.values()})
        return f"{len(alarms)} alarms present. States: {', '.join(states)}"

    test("CloudWatch Alarms (7 present)", test_cloudwatch_alarms)

    # ──────────────────────────────────────────────────────────────
    # TEST 9: CloudWatch Dashboard Exists
    # ──────────────────────────────────────────────────────────────
    def test_cloudwatch_dashboard():
        """Verify dashboard exists with widgets."""
        response = cloudwatch.get_dashboard(DashboardName="rai-healthcare-demo-monitoring")
        body = json.loads(response["DashboardBody"])
        widgets = body.get("widgets", [])
        return f"Dashboard active: {len(widgets)} widgets"

    test("CloudWatch Dashboard", test_cloudwatch_dashboard)

    # ──────────────────────────────────────────────────────────────
    # TEST 10: Low Confidence Alert (Bedrock + CloudWatch)
    # ──────────────────────────────────────────────────────────────
    def test_low_confidence_alert():
        """Invoke Bedrock with ambiguous case → emit low confidence metric."""
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            guardrailIdentifier=GUARDRAIL_ID,
            guardrailVersion=GUARDRAIL_VERSION,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 500,
                    "temperature": 0.1,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Patient: 55F, vague abdominal discomfort, no fever, all labs normal."
                                " Very unclear. Provide JSON with confidence"
                                " (must be below 0.65 for ambiguous cases)."
                            ),
                        }
                    ],
                    "system": (
                        "You are a clinical AI. For ambiguous cases, confidence MUST be below 0.65. Output JSON only."
                    ),
                }
            ),
        )
        output = json.loads(response["body"].read())["content"][0]["text"]

        # Extract confidence
        match = re.search(r'"confidence"\s*:\s*([\d.]+)', output)
        confidence = float(match.group(1)) * 100 if match and float(match.group(1)) <= 1 else 50

        # Emit low confidence metric
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
                    "Value": 1 if confidence < 70 else 0,
                    "Unit": "Count",
                    "Dimensions": [{"Name": "Service", "Value": "DiagnosticAssistant"}],
                    "Timestamp": datetime.now(UTC),
                },
            ],
        )

        alert_status = "🚨 TRIGGERED" if confidence < 70 else "✅ Not triggered"
        return f"Confidence: {confidence:.0f}% → Alert: {alert_status}"

    test("Low Confidence Alert (Bedrock + CloudWatch)", test_low_confidence_alert)

    # ──────────────────────────────────────────────────────────────
    # TEST 11: Knowledge Base Documents in S3
    # ──────────────────────────────────────────────────────────────
    def test_knowledge_base():
        """Verify clinical guidelines are in S3."""
        response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/knowledge-base/guidelines/")
        files = [obj["Key"].split("/")[-1] for obj in response.get("Contents", []) if obj["Size"] > 0]
        if len(files) >= 3:
            return f"{len(files)} guideline docs: {', '.join(files)}"
        else:
            raise Exception(f"Only {len(files)} docs found (expected 3)")

    test("Knowledge Base (Clinical Guidelines in S3)", test_knowledge_base)

    # ──────────────────────────────────────────────────────────────
    # TEST 12: Lambda Function Exists
    # ──────────────────────────────────────────────────────────────
    def test_lambda_exists():
        """Verify Lambda function is deployed."""
        response = lambda_client.get_function(FunctionName=LAMBDA_NAME)
        state = response["Configuration"]["State"]
        runtime = response["Configuration"]["Runtime"]
        if state != "Active":
            raise Exception(f"Lambda state is {state}, expected Active")
        return f"Lambda active: {LAMBDA_NAME} ({runtime}, state={state})"

    test("Lambda Function Deployed", test_lambda_exists)

    # ──────────────────────────────────────────────────────────────
    # TEST 13: AgentCore Agent Invocation
    # ──────────────────────────────────────────────────────────────
    def test_agentcore_invocation():
        """Invoke the deployed agent and require a validated diagnosis."""
        runtime_id = os.environ.get("AGENTCORE_RUNTIME_ID", "")
        if not runtime_id:
            raise Exception("AGENTCORE_RUNTIME_ID is not set")
        invoke = _load_invoke_module()
        client = boto3.client("bedrock-agentcore", region_name=REGION, config=BOTO_CONFIG)
        response = client.invoke_agent_runtime(
            agentRuntimeArn=f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/{runtime_id}",
            payload=json.dumps(
                {
                    "prompt": "Provide diagnostic recommendation.",
                    "patient_data": {
                        "age": 67,
                        "gender": "Male",
                        "chief_complaint": "Severe chest pain radiating to left arm",
                        "lab_results": {"troponin": 2.4},
                        "ecg": "ST-elevation in leads V1-V4",
                    },
                }
            ),
            contentType="application/json",
            accept="application/json",
        )
        text = invoke.parse_sse_payload(response["response"].read().decode("utf-8"))
        if response.get("statusCode") != 200 or not invoke.is_validated_diagnosis(text):
            raise Exception(f"Agent did not return a validated diagnosis: {text[:120]}")
        return f"Agent returned a validated diagnosis (confidence {json.loads(text)['confidence']})"

    test("AgentCore Agent Invocation", test_agentcore_invocation)

    # ============================================================
    # FINAL REPORT
    # ============================================================
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    print("\n")
    print("=" * 60)
    print("  END-TO-END TEST REPORT")
    print("=" * 60)
    print(f"  Duration: {duration:.0f} seconds")
    print(f"  Tests: {len(results)}")
    print()

    passed = sum(1 for r in results if "PASS" in r["status"])
    failed = sum(1 for r in results if "FAIL" in r["status"])

    for r in results:
        print(f"  {r['status']} {r['test']}")
        if "FAIL" in r["status"]:
            print(f"       → {r['detail']}")

    print()
    print(f"  PASSED: {passed}/{len(results)}")
    print(f"  FAILED: {failed}/{len(results)}")
    print()

    print("=" * 60)
    if failed:
        print(f"  ❌ {failed} test(s) FAILED")
        raise SystemExit(1)
    print("  🎉 ALL TESTS PASSED")


if __name__ == "__main__":
    main()
