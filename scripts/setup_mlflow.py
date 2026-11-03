# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Create (or find) the Amazon SageMaker AI MLflow App for this sample
===================================================================
AWS's replacement guidance for SageMaker Clarify logs bias, SHAP and evaluation
results to a SageMaker AI MLflow App. MLflow Apps are the managed MLflow offering
AWS recommends over tracking servers:
https://docs.aws.amazon.com/sagemaker/latest/dg/mlflow-app-setup.html

The AWS Terraform provider has no MLflow App resource, so this script creates it,
like scripts/setup_guardrail.py does for the Automated Reasoning guardrail.
The app's artifact store is the data lake bucket's mlflow/ prefix; its service
role (rai-healthcare-demo-mlflow-app-role) comes from infra/sagemaker.tf.

Prints:  export MLFLOW_TRACKING_ARN="arn:aws:sagemaker:...:mlflow-app/..."
Runs AGAINST AWS (SageMaker AI).
"""

import logging
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import AWS_ACCOUNT_ID, BOTO_CONFIG, REGION, S3_BUCKET, S3_PREFIX, validate_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

APP_NAME = "rai-healthcare-demo-mlflow"
ROLE_NAME = "rai-healthcare-demo-mlflow-app-role"
ACTIVE = ("Created", "Updated")
WAIT_SECONDS = 1800


def find_app(apps: list[dict], name: str = APP_NAME) -> dict | None:
    """The app summary named exactly `name` that is not being deleted (pure)."""
    for app in apps:
        if app.get("Name") == name and app.get("Status") not in ("Deleting", "Deleted", "DeleteFailed"):
            return app
    return None


def create_request(kms_key: str) -> dict:
    """The CreateMlflowApp request (pure, unit-tested)."""
    return {
        "Name": APP_NAME,
        "ArtifactStoreUri": f"s3://{S3_BUCKET}/{S3_PREFIX}/mlflow/",
        "RoleArn": f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/{ROLE_NAME}",
        "KmsKeyId": kms_key,
        # A shared account must not have its default MLflow App changed by a sample
        "AccountDefaultStatus": "DISABLED",
        "ModelRegistrationMode": "AutoModelRegistrationDisabled",
        "Tags": [{"Key": "Project", "Value": "rai-healthcare-demo"}],
    }


def list_apps(sm) -> list[dict]:
    apps, token = [], None
    while True:
        page = sm.list_mlflow_apps(**({"NextToken": token} if token else {}))
        apps += page.get("Summaries", [])
        token = page.get("NextToken")
        if not token:
            return apps


def main():
    if not validate_config(require_guardrail_id=False):
        raise SystemExit(1)
    sm = boto3.client("sagemaker", region_name=REGION, config=BOTO_CONFIG)
    s3 = boto3.client("s3", region_name=REGION, config=BOTO_CONFIG)

    app = find_app(list_apps(sm))
    if app:
        arn = app["Arn"]
        logger.info(f"Found existing MLflow App {APP_NAME} ({app['Status']})")
    else:
        kms_key = s3.get_bucket_encryption(Bucket=S3_BUCKET)["ServerSideEncryptionConfiguration"]["Rules"][0][
            "ApplyServerSideEncryptionByDefault"
        ]["KMSMasterKeyID"]
        arn = sm.create_mlflow_app(**create_request(kms_key))["Arn"]
        logger.info(f"Creating MLflow App {APP_NAME} (about 25 minutes)")

    status, deadline = "", time.time() + WAIT_SECONDS
    while time.time() < deadline:
        status = sm.describe_mlflow_app(Arn=arn)["Status"]
        if status in ACTIVE or status.endswith("Failed"):
            break
        time.sleep(15)
    if status not in ACTIVE:
        print(f"❌ MLflow App {APP_NAME} is {status}")
        raise SystemExit(1)

    print()
    print("  MLflow App ready. Set this before scripts 02 and 04:")
    print(f'  export MLFLOW_TRACKING_ARN="{arn}"')
    print()
    print("  Open the MLflow UI (URL valid 5 minutes):")
    print(f"  aws sagemaker create-presigned-mlflow-app-url --arn {arn}")


if __name__ == "__main__":
    main()
