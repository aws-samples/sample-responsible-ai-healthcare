# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Bias and explainability job — runs as an Amazon SageMaker AI Processing job.

Follows AWS's replacement guidance for SageMaker Clarify: compute the
standardized bias metrics and SHAP attributions directly, inside a processing
step, and log the results to an Amazon SageMaker AI MLflow App.
https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html

Inputs (processing container paths, overridable for local runs):
  --input   directory holding patient_diagnostic_data.csv
  --output  directory that receives bias/report.json and explainability/shap.json
Environment:
  MLFLOW_TRACKING_ARN  ARN of the MLflow App; when unset, MLflow logging is skipped
"""

import argparse
import json
import logging
import os

import pandas as pd

try:
    from .explain import shap_report
    from .metrics import bias_report
except ImportError:  # run as a flat script inside the processing container
    from explain import shap_report
    from metrics import bias_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bias_job")

DATA_FILE = "patient_diagnostic_data.csv"
EXPERIMENT = "rai-healthcare-demo-bias-explainability"


def write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def log_to_mlflow(tracking_arn: str, bias: dict, shap: dict, files: list[str]) -> str:
    """Log the metrics and report files to the MLflow App; returns the run id."""
    import mlflow

    mlflow.set_tracking_uri(tracking_arn)
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name="bias-and-shap") as run:
        mlflow.log_params({"records": bias["records"], "threshold": bias["threshold"], "target": shap["target"]})
        for f in bias["facets"]:
            for key in ("CI", "DPL", "AD"):
                mlflow.log_metric(f"{f['facet']}_{key}", f[key])
        mlflow.log_metric("facets_with_bias", len(bias["facets_with_bias"]))
        mlflow.log_metric("shap_cv_accuracy", shap["cv_accuracy"])
        for path in files:
            mlflow.log_artifact(path)
        return run.info.run_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/opt/ml/processing/input/data")
    parser.add_argument("--output", default="/opt/ml/processing/output")
    args = parser.parse_args(argv)

    df = pd.read_csv(os.path.join(args.input, DATA_FILE))
    bias = bias_report(df)
    shap = shap_report(df)

    bias_path = os.path.join(args.output, "bias", "report.json")
    shap_path = os.path.join(args.output, "explainability", "shap.json")
    write_json(bias_path, bias)
    write_json(shap_path, shap)

    tracking_arn = os.environ.get("MLFLOW_TRACKING_ARN", "")
    if tracking_arn:
        run_id = log_to_mlflow(tracking_arn, bias, shap, [bias_path, shap_path])
        write_json(os.path.join(args.output, "bias", "mlflow_run.json"), {"mlflow_run_id": run_id})
        log.info(f"Logged to MLflow run {run_id}")
    else:
        log.info("MLFLOW_TRACKING_ARN not set; skipped MLflow logging")

    log.info(f"Facets with bias: {bias['facets_with_bias']}")
    log.info(f"Top SHAP feature: {shap['global_importance'][0]['feature']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
