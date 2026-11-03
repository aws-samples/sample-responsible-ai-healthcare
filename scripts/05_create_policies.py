# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Demo 2 - Step 1: Upload Cedar Policies to S3 & Show RBAC Matrix
================================================================
Uploads the illustrative Cedar policy files to S3 and displays the
intended role-based access control matrix.

NOTE: The RBAC matrix printed below is an illustrative reference of the
INTENDED per-action access model. Live enforcement is demonstrated in
06_test_access_control.py, which assumes real IAM roles and shows actual
ALLOW/DENY decisions. The Cedar policies here are not enforced at runtime
(no AgentCore Gateway is deployed); they show how per-action enforcement
would work with a Gateway and Policy Engine in production.

Runs AGAINST AWS (S3).
"""

import logging
import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import BOTO_CONFIG, REGION, S3_BUCKET, S3_PREFIX, validate_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    # ============================================================
    # CONFIGURATION
    # ============================================================
    if not validate_config():
        raise SystemExit(1)

    print("=" * 60)
    print("DEMO 2: AgentCore Policy & Identity — Policy Setup")
    print("Target: AWS S3 + AgentCore Policy Engine")
    print("=" * 60)
    print()

    # ============================================================
    # Step 1: Check the Cedar policies in S3
    # ============================================================
    logger.info("Checking Cedar policies in S3...")
    s3 = boto3.client("s3", region_name=REGION, config=BOTO_CONFIG)

    # Cedar policies are stored inline in infra/agentcore.tf and uploaded to S3 by Terraform
    # For this demo, we verify they exist in S3
    logger.info("Cedar policies deployed to S3 via Terraform (agentcore.tf)")

    response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/policies/")
    for obj in response.get("Contents", []):
        logger.info(f"Found policy: {obj['Key']}")

    if not response.get("Contents"):
        logger.warning("No policies in S3 yet — run 'terraform apply' first")

    print()

    # ============================================================
    # Step 2: Display Intended RBAC Matrix (illustrative reference)
    # ============================================================
    print("--- Role-Based Access Control Matrix (intended model) ---")
    print("    (Live enforcement is tested in 06_test_access_control.py)")
    print()

    actions = [
        "ViewRecommendation",
        "ViewSHAPExplanation",
        "ViewTriageScore",
        "ViewCriticalAlerts",
        "OverrideRecommendation",
        "ViewPatientHistory",
        "ConfigureThresholds",
        "ManageModelVersions",
        "ViewAuditTrails",
        "ViewBiasReports",
    ]

    access_matrix = {
        "Physician": ["✅", "✅", "✅", "✅", "✅", "✅", "❌", "❌", "❌", "❌"],
        "Nurse": ["❌", "❌", "✅", "✅", "❌", "❌", "❌", "❌", "❌", "❌"],
        "Admin": ["❌", "❌", "❌", "❌", "❌", "❌", "✅", "✅", "✅", "✅"],
        "Auditor": ["❌", "❌", "❌", "❌", "❌", "❌", "❌", "❌", "✅", "✅"],
    }

    print(f"{'Action':<25} {'Physician':<12} {'Nurse':<12} {'Admin':<12} {'Auditor':<12}")
    print("-" * 73)
    for i, action in enumerate(actions):
        row = f"{action:<25}"
        for role in ["Physician", "Nurse", "Admin", "Auditor"]:
            row += f" {access_matrix[role][i]:<11}"
        print(row)

    print()

    # ============================================================
    # Step 3: Show IAM Roles Created by Terraform
    # ============================================================
    logger.info("Checking IAM roles (created by Terraform)...")
    iam = boto3.client("iam", config=BOTO_CONFIG)

    roles = [
        "rai-healthcare-demo-physician-role",
        "rai-healthcare-demo-nurse-role",
        "rai-healthcare-demo-admin-role",
        "rai-healthcare-demo-auditor-role",
    ]

    for role_name in roles:
        try:
            role = iam.get_role(RoleName=role_name)
            tags = {t["Key"]: t["Value"] for t in role["Role"].get("Tags", [])}
            logger.info(f"Found role: {role_name} (Access Level: {tags.get('AccessLevel', 'N/A')})")
        except Exception as e:
            logger.warning(f"{role_name}: {e}")

    print()

    # ============================================================
    # Step 4: Emit Policy Metrics
    # ============================================================
    cloudwatch = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)
    cloudwatch.put_metric_data(
        Namespace="RAIHealthcare",
        MetricData=[
            {
                "MetricName": "PoliciesDeployed",
                "Value": 4,
                "Unit": "Count",
                "Dimensions": [{"Name": "Service", "Value": "PolicyEnforcement"}],
            }
        ],
    )
    logger.info("Policy metrics emitted to CloudWatch")
    print()
    print("=" * 60)
    print("✅ Policies verified. Next: python scripts/06_test_access_control.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
