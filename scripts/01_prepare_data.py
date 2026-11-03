# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Demo 1 - Step 1: Prepare & Upload Patient Data to S3
=====================================================
Uploads the synthetic patient dataset to S3 for the bias and explainability job (02).
Runs AGAINST AWS (S3).
"""

import logging
import os
import sys

import boto3
import pandas as pd

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

    DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "patient_diagnostic_data.csv")

    print("=" * 60)
    print("DEMO 1: Responsible AI assessment — Data Preparation")
    print("Target: AWS S3")
    print("=" * 60)
    print()

    # ============================================================
    # Step 1: Load and Explore the Dataset
    # ============================================================
    df = pd.read_csv(DATA_FILE)
    print(f"Dataset loaded: {df.shape[0]} patients, {df.shape[1]} features")
    print()

    print("--- Demographic Distribution ---")
    print("\nEthnicity breakdown:")
    print(df["ethnicity"].value_counts().to_string())
    print("\nGender breakdown:")
    print(df["gender"].value_counts().to_string())
    print("\nLanguage breakdown:")
    print(df["language"].value_counts().to_string())
    print("\nLocation type breakdown:")
    print(df["location_type"].value_counts().to_string())
    print("\nAge statistics:")
    print(f"  Mean: {df['age'].mean():.1f}, Min: {df['age'].min()}, Max: {df['age'].max()}")
    print()

    # ============================================================
    # Step 2: Upload to S3
    # ============================================================
    logger.info("Uploading data to S3...")
    s3 = boto3.client("s3", region_name=REGION, config=BOTO_CONFIG)

    # The bias and explainability job (scripts/02_bias_detection.py) reads this prefix
    key = f"{S3_PREFIX}/bias/input/patient_diagnostic_data.csv"
    s3.upload_file(Filename=DATA_FILE, Bucket=S3_BUCKET, Key=key)
    logger.info(f"Uploaded to: s3://{S3_BUCKET}/{key}")
    print()

    # ============================================================
    # Step 3: Verify Upload
    # ============================================================
    logger.info("Verifying S3 objects...")
    response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/bias/input/")
    for obj in response.get("Contents", []):
        logger.info(f"Verified: {obj['Key']} ({obj['Size']} bytes)")

    print()
    print("=" * 60)
    print("✅ Data uploaded to S3. Proceed to 02_bias_detection.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
