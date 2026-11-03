# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Demo 1 - Step 2: Bias and explainability as an Amazon SageMaker AI Processing job
================================================================================
Amazon SageMaker Clarify is no longer open to new customers. This script follows
AWS's replacement guidance: it runs the standardized bias metrics (CI, DPL, AD)
and SHAP attribution (src/bias_detection) in a SageMaker AI Processing job on
the AWS scikit-learn container, and logs the results to a SageMaker AI MLflow App.
https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html

The job writes bias/<run>/report.json and explainability/<run>/shap.json to S3.
This script then emits the metrics to CloudWatch and exits 1 if the job fails.

Environment: MLFLOW_TRACKING_ARN (optional, printed by scripts/setup_mlflow.py)
Runs AGAINST AWS (S3, SageMaker AI, CloudWatch, optionally MLflow).
"""

import json
import logging
import os
import sys
import time
from datetime import UTC, datetime

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import BOTO_CONFIG, REGION, S3_BUCKET, S3_PREFIX, SAGEMAKER_ROLE, validate_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = os.path.join(os.path.dirname(__file__), "..")
CODE_DIR = os.path.join(ROOT, "src", "bias_detection")
CODE_FILES = ("job.py", "metrics.py", "explain.py", "requirements.txt")
DATA_FILE = os.path.join(ROOT, "data", "patient_diagnostic_data.csv")

# AWS scikit-learn container for SageMaker AI Processing. Registry account per Region:
# https://docs.aws.amazon.com/sagemaker/latest/dg/pre-built-docker-containers-scikit-learn-spark.html
SKLEARN_REGISTRY = {"us-east-1": "683313688378", "us-east-2": "257758044811", "us-west-2": "246618743249"}
SKLEARN_IMAGE_TAG = "1.9-0-cpu-py3"
INSTANCE_TYPE = os.environ.get("BIAS_JOB_INSTANCE_TYPE", "ml.t3.medium")
MAX_RUNTIME_SECONDS = 1800
CONTAINER_CODE = "/opt/ml/processing/input/code"


def image_uri(region: str) -> str:
    override = os.environ.get("BIAS_JOB_IMAGE_URI", "")
    if override:
        return override
    if region not in SKLEARN_REGISTRY:
        raise SystemExit(
            f"No scikit-learn registry account configured for {region}; set BIAS_JOB_IMAGE_URI "
            "from https://docs.aws.amazon.com/sagemaker/latest/dg-ecr-paths/sagemaker-algo-docker-registry-paths.html"
        )
    return f"{SKLEARN_REGISTRY[region]}.dkr.ecr.{region}.amazonaws.com/sagemaker-scikit-learn:{SKLEARN_IMAGE_TAG}"


def job_request(job_name: str, run: str, kms_key: str, mlflow_arn: str) -> dict:
    """The CreateProcessingJob request (pure, unit-tested)."""
    base = f"s3://{S3_BUCKET}/{S3_PREFIX}"
    environment = {"PYTHONUNBUFFERED": "1"}
    if mlflow_arn:
        environment["MLFLOW_TRACKING_ARN"] = mlflow_arn
    return {
        "ProcessingJobName": job_name,
        "RoleArn": SAGEMAKER_ROLE,
        "AppSpecification": {
            "ImageUri": image_uri(REGION),
            "ContainerEntrypoint": [
                "/bin/bash",
                "-c",
                f"pip install --quiet --no-cache-dir -r {CONTAINER_CODE}/requirements.txt && "
                f"python3 {CONTAINER_CODE}/job.py",
            ],
        },
        "ProcessingInputs": [
            {
                "InputName": "code",
                "S3Input": {
                    "S3Uri": f"{base}/bias/code/{run}/",
                    "LocalPath": CONTAINER_CODE,
                    "S3DataType": "S3Prefix",
                    "S3InputMode": "File",
                },
            },
            {
                "InputName": "data",
                "S3Input": {
                    "S3Uri": f"{base}/bias/input/",
                    "LocalPath": "/opt/ml/processing/input/data",
                    "S3DataType": "S3Prefix",
                    "S3InputMode": "File",
                },
            },
        ],
        "ProcessingOutputConfig": {
            "KmsKeyId": kms_key,
            "Outputs": [
                {
                    "OutputName": name,
                    "S3Output": {
                        "S3Uri": f"{base}/{name}/{run}/",
                        "LocalPath": f"/opt/ml/processing/output/{name}",
                        "S3UploadMode": "EndOfJob",
                    },
                }
                for name in ("bias", "explainability")
            ],
        },
        "ProcessingResources": {
            "ClusterConfig": {
                "InstanceCount": 1,
                "InstanceType": INSTANCE_TYPE,
                "VolumeSizeInGB": 10,
                "VolumeKmsKeyId": kms_key,
            }
        },
        "StoppingCondition": {"MaxRuntimeInSeconds": MAX_RUNTIME_SECONDS},
        "Environment": environment,
        "Tags": [{"Key": "Project", "Value": "rai-healthcare-demo"}],
    }


def metric_data(bias: dict, shap: dict) -> list[dict]:
    """CloudWatch metrics derived from the job's real output (pure, unit-tested)."""
    dims = [{"Name": "Service", "Value": "BiasDetection"}]
    data = [
        {"MetricName": "BiasDetected", "Value": len(bias["facets_with_bias"]), "Unit": "Count", "Dimensions": dims},
        {"MetricName": "FacetsAnalyzed", "Value": len(bias["facets"]), "Unit": "Count", "Dimensions": dims},
        {"MetricName": "BiasJobCompleted", "Value": 1, "Unit": "Count", "Dimensions": dims},
        {
            "MetricName": "FeaturesAttributed",
            "Value": len(shap["global_importance"]),
            "Unit": "Count",
            "Dimensions": dims,
        },
    ]
    for f in bias["facets"]:
        facet_dims = dims + [{"Name": "Facet", "Value": f["facet"]}]
        data += [
            {"MetricName": "AccuracyDifference", "Value": f["AD"], "Unit": "None", "Dimensions": facet_dims},
            {"MetricName": "LabelProportionDifference", "Value": f["DPL"], "Unit": "None", "Dimensions": facet_dims},
        ]
    return data


def main():
    if not validate_config(require_guardrail_id=False):
        raise SystemExit(1)
    s3 = boto3.client("s3", region_name=REGION, config=BOTO_CONFIG)
    sm = boto3.client("sagemaker", region_name=REGION, config=BOTO_CONFIG)
    cw = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)

    run = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    job_name = f"rai-healthcare-demo-bias-{run}"
    kms_key = s3.get_bucket_encryption(Bucket=S3_BUCKET)["ServerSideEncryptionConfiguration"]["Rules"][0][
        "ApplyServerSideEncryptionByDefault"
    ]["KMSMasterKeyID"]

    print("=" * 60)
    print("DEMO 1: Bias and explainability — SageMaker AI Processing job")
    print("=" * 60)

    s3.upload_file(DATA_FILE, S3_BUCKET, f"{S3_PREFIX}/bias/input/patient_diagnostic_data.csv")
    for name in CODE_FILES:
        s3.upload_file(os.path.join(CODE_DIR, name), S3_BUCKET, f"{S3_PREFIX}/bias/code/{run}/{name}")

    sm.create_processing_job(**job_request(job_name, run, kms_key, os.environ.get("MLFLOW_TRACKING_ARN", "")))
    logger.info(f"Started processing job {job_name} on {INSTANCE_TYPE}")

    status, desc = "InProgress", {}
    deadline = time.time() + MAX_RUNTIME_SECONDS + 300
    while status in ("InProgress", "Stopping") and time.time() < deadline:
        time.sleep(20)
        desc = sm.describe_processing_job(ProcessingJobName=job_name)
        status = desc["ProcessingJobStatus"]
    if status != "Completed":
        print(f"❌ Processing job {job_name} ended {status}: {desc.get('FailureReason', 'no reason given')}")
        raise SystemExit(1)

    def read(prefix: str, name: str) -> dict:
        body = s3.get_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX}/{prefix}/{run}/{name}")["Body"].read()
        return json.loads(body)

    bias, shap = read("bias", "report.json"), read("explainability", "shap.json")

    print(f"\n{'Facet':15} {'n_a/n_d':>8} {'CI':>7} {'DPL':>7} {'AD':>7}  Bias?")
    for f in bias["facets"]:
        flag = "⚠️  YES" if f["bias_detected"] else "✅ NO"
        print(f"{f['facet']:15} {f['n_a']:>3}/{f['n_d']:<4} {f['CI']:+.3f} {f['DPL']:+.3f} {f['AD']:+.3f}  {flag}")
    print(f"\nFacets with bias (|DPL| or |AD| > {bias['threshold']}): {bias['facets_with_bias']}")
    print("Top SHAP features: " + ", ".join(g["feature"] for g in shap["global_importance"][:5]))
    print(f"Reports: s3://{S3_BUCKET}/{S3_PREFIX}/bias/{run}/ and .../explainability/{run}/")

    cw.put_metric_data(Namespace="RAIHealthcare", MetricData=metric_data(bias, shap))
    logger.info("Bias and explainability metrics emitted to CloudWatch")
    print("\n✅ Bias and explainability job complete. Next: python scripts/03_shap_explainability.py")


if __name__ == "__main__":
    main()
