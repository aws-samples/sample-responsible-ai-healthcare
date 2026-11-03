# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Bias metrics in src/bias_detection/metrics.py.

Known-answer tests check each formula against its definition on the AWS
Clarify metric reference pages; dataset tests check the real report.
"""

import os

import pandas as pd
import pytest

from src.bias_detection import metrics

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "patient_diagnostic_data.csv")


@pytest.fixture(scope="module")
def df():
    return pd.read_csv(DATA)


class TestFormulas:
    @pytest.mark.parametrize(("n_a", "n_d", "expected"), [(10, 10, 0.0), (30, 10, 0.5), (0, 5, -1.0), (5, 0, 1.0)])
    def test_class_imbalance(self, n_a, n_d, expected):
        assert metrics.class_imbalance(n_a, n_d) == pytest.approx(expected)

    def test_class_imbalance_rejects_empty_facets(self):
        with pytest.raises(ValueError):
            metrics.class_imbalance(0, 0)

    @pytest.mark.parametrize(("q_a", "q_d", "expected"), [(0.7, 0.4, 0.3), (0.2, 0.6, -0.4), (0.5, 0.5, 0.0)])
    def test_difference_in_proportions_of_labels(self, q_a, q_d, expected):
        assert metrics.difference_in_proportions_of_labels(q_a, q_d) == pytest.approx(expected)

    def test_accuracy_difference(self):
        # Facet a: 80 of 100 correct; facet d: 60 of 100 correct
        assert metrics.accuracy_difference(0.8, 0.6) == pytest.approx(0.2)

    def test_facet_metrics_on_a_hand_built_frame(self):
        frame = pd.DataFrame(
            {
                "g": ["a", "a", "a", "a", "b", "b"],
                metrics.LABEL_COLUMN: [1, 1, 0, 0, 1, 1],
                metrics.CORRECT_COLUMN: [1, 1, 1, 0, 1, 0],
            }
        )
        m = metrics.facet_metrics(frame, "g", "a")
        assert (m["n_a"], m["n_d"]) == (4, 2)
        assert m["CI"] == pytest.approx((4 - 2) / 6, abs=1e-4)
        assert m["DPL"] == pytest.approx(0.5 - 1.0)
        assert m["AD"] == pytest.approx(0.75 - 0.5)
        assert m["bias_detected"] is True

    def test_a_facet_without_both_sides_is_rejected(self):
        frame = pd.DataFrame({"g": ["a", "a"], metrics.LABEL_COLUMN: [1, 0], metrics.CORRECT_COLUMN: [1, 1]})
        with pytest.raises(ValueError):
            metrics.facet_metrics(frame, "g", "a")


class TestDataset:
    def test_dataset_shape(self, df):
        assert len(df) == 40
        assert {"ethnicity", "gender", "language", "location_type", "age", "primary_diagnosis"} <= set(df.columns)

    def test_acs_label_is_derived_from_the_diagnosis(self, df):
        data = metrics.add_derived_columns(df)
        assert data[metrics.LABEL_COLUMN].sum() == df["primary_diagnosis"].isin(metrics.ACS_DIAGNOSES).sum()

    def test_report_covers_every_facet(self, df):
        report = metrics.bias_report(df)
        assert [f["facet"] for f in report["facets"]] == list(metrics.FACETS)
        for f in report["facets"]:
            assert -1 <= f["CI"] <= 1 and -1 <= f["DPL"] <= 1 and -1 <= f["AD"] <= 1
            assert f["n_a"] + f["n_d"] == 40

    def test_location_accuracy_gap_is_detected(self, df):
        # The synthetic data deliberately makes rural diagnoses less accurate
        location = {f["facet"]: f for f in metrics.bias_report(df)["facets"]}["location_type"]
        assert location["AD"] > metrics.BIAS_THRESHOLD
        assert "location_type" in metrics.bias_report(df)["facets_with_bias"]
