# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Request and metric building in scripts/02_bias_detection.py and the bar rendering in
scripts/03_shap_explainability.py (no AWS calls).
"""

import importlib.util
import os

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")


def _load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(SCRIPTS, name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def launcher():
    return _load("02_bias_detection.py", "bias_launcher_under_test")


@pytest.fixture(scope="module")
def explain_script():
    return _load("03_shap_explainability.py", "explain_script_under_test")


KMS = "arn:aws:kms:us-east-1:111122223333:key/example"


class TestProcessingRequest:
    def test_uses_the_aws_scikit_learn_container(self, launcher, monkeypatch):
        monkeypatch.delenv("BIAS_JOB_IMAGE_URI", raising=False)
        uri = launcher.job_request("rai-healthcare-demo-bias-x", "x", KMS, "")["AppSpecification"]["ImageUri"]
        assert uri.endswith(f"/sagemaker-scikit-learn:{launcher.SKLEARN_IMAGE_TAG}")
        assert uri.split(".")[0] in launcher.SKLEARN_REGISTRY.values()

    def test_volume_and_outputs_are_encrypted_with_the_given_key(self, launcher):
        req = launcher.job_request("rai-healthcare-demo-bias-x", "x", KMS, "")
        assert req["ProcessingResources"]["ClusterConfig"]["VolumeKmsKeyId"] == KMS
        assert req["ProcessingOutputConfig"]["KmsKeyId"] == KMS

    def test_job_name_stays_inside_the_iam_and_cleanup_scope(self, launcher):
        req = launcher.job_request("rai-healthcare-demo-bias-20260101-000000", "20260101-000000", KMS, "")
        assert req["ProcessingJobName"].startswith("rai-healthcare-demo-")

    def test_outputs_go_to_the_bias_and_explainability_prefixes(self, launcher):
        outputs = launcher.job_request("j", "run1", KMS, "")["ProcessingOutputConfig"]["Outputs"]
        assert sorted(o["S3Output"]["S3Uri"].split("/")[-3] for o in outputs) == ["bias", "explainability"]
        assert all(o["S3Output"]["S3Uri"].endswith("/run1/") for o in outputs)

    def test_mlflow_arn_is_passed_only_when_set(self, launcher):
        assert "MLFLOW_TRACKING_ARN" not in launcher.job_request("j", "r", KMS, "")["Environment"]
        arn = "arn:aws:sagemaker:us-east-1:111122223333:mlflow-app/app-x"
        env = launcher.job_request("j", "r", KMS, arn)["Environment"]
        assert env["MLFLOW_TRACKING_ARN"].endswith("mlflow-app/app-x")

    def test_an_unconfigured_region_fails_loudly(self, launcher, monkeypatch):
        monkeypatch.delenv("BIAS_JOB_IMAGE_URI", raising=False)
        with pytest.raises(SystemExit):
            launcher.image_uri("ap-southeast-7")


class TestMetricData:
    BIAS = {
        "facets": [
            {"facet": "gender", "AD": 0.0, "DPL": 0.7, "bias_detected": True},
            {"facet": "ethnicity", "AD": 0.01, "DPL": 0.02, "bias_detected": False},
        ],
        "facets_with_bias": ["gender"],
    }
    SHAP = {"global_importance": [{"feature": "a"}, {"feature": "b"}, {"feature": "c"}]}

    def test_counts_come_from_the_report(self, launcher):
        data = {m["MetricName"]: m for m in launcher.metric_data(self.BIAS, self.SHAP) if len(m["Dimensions"]) == 1}
        assert data["BiasDetected"]["Value"] == 1
        assert data["FacetsAnalyzed"]["Value"] == 2
        assert data["FeaturesAttributed"]["Value"] == 3

    def test_per_facet_metrics_carry_the_facet_dimension(self, launcher):
        per_facet = [m for m in launcher.metric_data(self.BIAS, self.SHAP) if m["MetricName"] == "AccuracyDifference"]
        facets = {d["Value"] for m in per_facet for d in m["Dimensions"] if d["Name"] == "Facet"}
        assert facets == {"gender", "ethnicity"}


def test_bars_scale_to_the_largest_value(explain_script):
    lines = explain_script.bars([{"feature": "x", "shap": 2.0}, {"feature": "y", "shap": -1.0}], "shap", 2.0)
    assert lines[0].count("█") == explain_script.BAR_WIDTH
    assert lines[1].count("█") == explain_script.BAR_WIDTH // 2
    assert lines[1].rstrip().endswith("-1.000")
