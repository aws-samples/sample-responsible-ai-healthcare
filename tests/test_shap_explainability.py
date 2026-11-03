# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
SHAP attribution in src/bias_detection/explain.py, computed for real on the dataset.
"""

import os

import numpy as np
import pandas as pd
import pytest

from src.bias_detection import explain, job

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@pytest.fixture(scope="module")
def report():
    return explain.shap_report(pd.read_csv(os.path.join(DATA_DIR, "patient_diagnostic_data.csv")))


def test_every_feature_is_attributed(report):
    assert {g["feature"] for g in report["global_importance"]} == set(explain.FEATURES)


def test_importance_is_sorted_and_non_negative(report):
    values = [g["mean_abs_shap"] for g in report["global_importance"]]
    assert values == sorted(values, reverse=True)
    assert all(v >= 0 for v in values)


def test_clinical_markers_lead_the_attribution(report):
    top = {g["feature"] for g in report["global_importance"][:4]}
    assert {"troponin_level", "chest_pain_score"} <= top


def test_local_attributions_add_up_to_the_prediction(report):
    # SHAP additivity: base value + sum of attributions = model output (log-odds)
    local = report["local_explanation"]
    log_odds = report["base_value"] + sum(a["shap"] for a in local["attributions"])
    probability = 1 / (1 + np.exp(-log_odds))
    assert probability == pytest.approx(local["predicted_probability"], abs=0.01)


def test_model_is_better_than_chance(report):
    assert report["cv_accuracy"] > 0.7


def test_job_writes_both_reports_without_mlflow(tmp_path, monkeypatch):
    monkeypatch.delenv("MLFLOW_TRACKING_ARN", raising=False)
    assert job.main(["--input", DATA_DIR, "--output", str(tmp_path)]) == 0
    assert (tmp_path / "bias" / "report.json").exists()
    assert (tmp_path / "explainability" / "shap.json").exists()
    assert not (tmp_path / "bias" / "mlflow_run.json").exists()
