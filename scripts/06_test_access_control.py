# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Demo 2 - Step 2: Test Access Control via IAM Role Assumption
=============================================================
Tests RBAC by assuming different IAM roles and attempting
to access resources. Shows ALLOW/DENY with real AWS calls.

Runs AGAINST AWS (IAM, S3).
"""

import json
import logging
import os
import sys
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import AWS_ACCOUNT_ID, BOTO_CONFIG, REGION, S3_BUCKET, S3_PREFIX, validate_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# Each scenario: (role, external id, description, action, expected decision).
# The expected column is the access-control contract the IAM policies enforce;
# any other outcome, including an error that prevented the check, is a failure.
SCENARIOS = [
    (
        "rai-healthcare-demo-physician-role",
        "physician-access",
        "Physician reads patient data",
        "read_patient_data",
        "ALLOW",
    ),
    (
        "rai-healthcare-demo-physician-role",
        "physician-access",
        "Physician reads bias reports",
        "read_bias_reports",
        "ALLOW",
    ),
    ("rai-healthcare-demo-nurse-role", "nurse-access", "Nurse reads patient data", "read_patient_data", "DENY"),
    ("rai-healthcare-demo-admin-role", "admin-access", "Admin reads patient data", "read_patient_data", "DENY"),
    ("rai-healthcare-demo-admin-role", "admin-access", "Admin reads CloudWatch alarms", "read_alarms", "ALLOW"),
    ("rai-healthcare-demo-auditor-role", "auditor-access", "Auditor reads bias reports", "read_bias_reports", "ALLOW"),
    ("rai-healthcare-demo-auditor-role", "auditor-access", "Auditor reads patient data", "read_patient_data", "DENY"),
    ("rai-healthcare-demo-auditor-role", "auditor-access", "Auditor writes CloudWatch metric", "write_metric", "DENY"),
]

DENIED_ERROR_CODES = ("AccessDenied", "AccessDeniedException", "Forbidden")


def classify_error(error_code: str) -> str:
    """Map an AWS error code to a decision: DENY for authorization refusals, ERROR otherwise."""
    return "DENY" if error_code in DENIED_ERROR_CODES else "ERROR"


def mismatches(results: list[dict]) -> list[dict]:
    """Return the results whose decision differs from the expected decision."""
    return [r for r in results if r["decision"] != r["expected"]]


def _read_patient_data(session):
    session.client("s3", config=BOTO_CONFIG).get_object(
        Bucket=S3_BUCKET, Key=f"{S3_PREFIX}/data/patient_diagnostic_data.csv"
    )
    return "Read patient data"


def _read_bias_reports(session):
    response = session.client("s3", config=BOTO_CONFIG).list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/bias/")
    return f"Listed {response.get('KeyCount', 0)} bias report files"


def _read_alarms(session):
    session.client("cloudwatch", config=BOTO_CONFIG).describe_alarms(AlarmNamePrefix="rai-healthcare-demo")
    return "Read CloudWatch alarms"


def _write_metric(session):
    session.client("cloudwatch", config=BOTO_CONFIG).put_metric_data(
        Namespace="RAIHealthcare", MetricData=[{"MetricName": "TestMetric", "Value": 1, "Unit": "Count"}]
    )
    return "Wrote CloudWatch metric"


ACTIONS = {
    "read_patient_data": _read_patient_data,
    "read_bias_reports": _read_bias_reports,
    "read_alarms": _read_alarms,
    "write_metric": _write_metric,
}


def run_scenario(role_name: str, external_id: str, action: str) -> tuple[str, str]:
    """Assume the role, run the action, and return (decision, detail)."""
    sts = boto3.client("sts", region_name=REGION, config=BOTO_CONFIG)
    try:
        credentials = sts.assume_role(
            RoleArn=f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/{role_name}",
            RoleSessionName=f"demo-test-{datetime.now().strftime('%H%M%S')}",
            ExternalId=external_id,
            DurationSeconds=900,
        )["Credentials"]
    except ClientError as e:
        return "ERROR", f"assume_role failed: {e.response['Error']['Code']}"
    session = boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=REGION,
    )
    try:
        return "ALLOW", ACTIONS[action](session)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        return classify_error(code), code


def run_all() -> list[dict]:
    """Run every scenario and return one result dict per scenario."""
    results = []
    for role_name, ext_id, description, action, expected in SCENARIOS:
        decision, detail = run_scenario(role_name, ext_id, action)
        results.append(
            {
                "timestamp": datetime.now().isoformat(),
                "role": role_name.replace("rai-healthcare-demo-", "").replace("-role", "").title(),
                "action": description,
                "expected": expected,
                "decision": decision,
                "detail": detail,
            }
        )
    return results


ICONS = {"ALLOW": "✅ ALLOWED", "DENY": "🚫 DENIED", "ERROR": "⚠️  ERROR"}


def main():
    if not validate_config():
        raise SystemExit(1)

    print("=" * 60)
    print("DEMO 2: AgentCore Policy & Identity — Access Control Test")
    print("Target: AWS IAM + S3")
    print("=" * 60)
    print()
    print("--- Access Control Test Scenarios ---")
    print()

    audit_log = run_all()
    for r in audit_log:
        flag = "" if r["decision"] == r["expected"] else f"   <-- MISMATCH (expected {r['expected']})"
        print(f"  {ICONS[r['decision']]} [{r['role']:<10}] {r['action']}{flag}")
        if r["decision"] != "ALLOW":
            print(f"       Reason: {r['detail']}")
    print()

    print("--- Audit Summary ---")
    allowed = sum(1 for a in audit_log if a["decision"] == "ALLOW")
    denied = sum(1 for a in audit_log if a["decision"] == "DENY")
    failed = mismatches(audit_log)
    print(f"  Total tests: {len(audit_log)}")
    print(f"  Allowed: {allowed}")
    print(f"  Denied: {denied}")
    print(f"  Matching the expected contract: {len(audit_log) - len(failed)}/{len(audit_log)}")
    print()

    # Upload audit log to S3
    s3 = boto3.client("s3", region_name=REGION, config=BOTO_CONFIG)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"{S3_PREFIX}/audit/access-test-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json",
        Body=json.dumps(audit_log, indent=2),
        ContentType="application/json",
    )
    logger.info(f"Audit log uploaded to S3: {S3_PREFIX}/audit/")

    # Emit metrics
    cloudwatch = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)
    cloudwatch.put_metric_data(
        Namespace="RAIHealthcare",
        MetricData=[
            {
                "MetricName": "PolicyAllowed",
                "Value": allowed,
                "Unit": "Count",
                "Dimensions": [{"Name": "Service", "Value": "PolicyEnforcement"}],
            },
            {
                "MetricName": "PolicyDenials",
                "Value": denied,
                "Unit": "Count",
                "Dimensions": [{"Name": "Service", "Value": "PolicyEnforcement"}],
            },
        ],
    )
    logger.info("Policy metrics emitted to CloudWatch")

    print()
    print("=" * 60)
    if failed:
        print(f"❌ Access control test FAILED: {len(failed)} scenario(s) did not match the expected decision")
        print("=" * 60)
        raise SystemExit(1)
    print("✅ Access control test passed: every scenario matched the expected decision")
    print("=" * 60)


if __name__ == "__main__":
    main()
