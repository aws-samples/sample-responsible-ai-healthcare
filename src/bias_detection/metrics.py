# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Standardized bias metrics, computed directly.

Amazon SageMaker Clarify is no longer open to new customers. AWS's replacement
guidance is to compute Clarify's standardized bias metrics directly from label
counts and confusion-matrix values:
https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html

Each function implements the formula on the linked metric reference page, with
facet a as the privileged group and facet d as everyone else.
"""

import pandas as pd

# Facet column -> privileged value (facet a). Every other value is facet d.
FACETS = {
    "ethnicity": "Caucasian",
    "age_group": "non-elderly",
    "language": "English",
    "location_type": "Urban",
    "gender": "Male",
}

# Positive label for the pre-training metrics: an acute coronary syndrome diagnosis
ACS_DIAGNOSES = frozenset({"Acute_MI", "Unstable_Angina"})
LABEL_COLUMN = "acs_label"

# Recorded outcome for the post-training metric: 1 when the AI-assisted diagnosis was correct
CORRECT_COLUMN = "diagnosis_correct"

# This sample's tolerance for a metric to count as bias. Clarify defines the metrics, not a
# threshold; 0.05 matches the sample's stated goal of under 5% variance across groups.
BIAS_THRESHOLD = 0.05

ELDERLY_AGE = 65


def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add age_group and the binary ACS label used by the metrics."""
    out = df.copy()
    out["age_group"] = out["age"].map(lambda a: "elderly" if a >= ELDERLY_AGE else "non-elderly")
    out[LABEL_COLUMN] = out["primary_diagnosis"].isin(ACS_DIAGNOSES).astype(int)
    return out


def class_imbalance(n_a: int, n_d: int) -> float:
    """CI = (n_a - n_d) / (n_a + n_d).

    https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-bias-metric-class-imbalance.html
    """
    if n_a + n_d == 0:
        raise ValueError("class imbalance needs at least one member in either facet")
    return (n_a - n_d) / (n_a + n_d)


def difference_in_proportions_of_labels(q_a: float, q_d: float) -> float:
    """DPL = q_a - q_d, where q is the proportion of a facet with a positive label.

    https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-data-bias-metric-true-label-imbalance.html
    """
    return q_a - q_d


def accuracy_difference(acc_a: float, acc_d: float) -> float:
    """AD = ACC_a - ACC_d.

    https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-post-training-bias-metric-ad.html
    """
    return acc_a - acc_d


def facet_metrics(df: pd.DataFrame, facet: str, privileged: str) -> dict:
    """CI, DPL and AD for one facet, with the counts and proportions behind them."""
    a = df[df[facet] == privileged]
    d = df[df[facet] != privileged]
    if a.empty or d.empty:
        raise ValueError(f"facet '{facet}' needs members on both sides of '{privileged}'")
    q_a, q_d = a[LABEL_COLUMN].mean(), d[LABEL_COLUMN].mean()
    acc_a, acc_d = a[CORRECT_COLUMN].mean(), d[CORRECT_COLUMN].mean()
    ci = class_imbalance(len(a), len(d))
    dpl = difference_in_proportions_of_labels(q_a, q_d)
    ad = accuracy_difference(acc_a, acc_d)
    return {
        "facet": facet,
        "privileged_value": privileged,
        "n_a": len(a),
        "n_d": len(d),
        "CI": round(ci, 4),
        "q_a": round(q_a, 4),
        "q_d": round(q_d, 4),
        "DPL": round(dpl, 4),
        "accuracy_a": round(acc_a, 4),
        "accuracy_d": round(acc_d, 4),
        "AD": round(ad, 4),
        "bias_detected": bool(abs(dpl) > BIAS_THRESHOLD or abs(ad) > BIAS_THRESHOLD),
    }


def bias_report(df: pd.DataFrame) -> dict:
    """Bias metrics for every configured facet."""
    data = add_derived_columns(df)
    facets = [facet_metrics(data, f, p) for f, p in FACETS.items()]
    return {
        "report_type": "bias_metrics",
        "method": "Standardized Clarify bias metrics computed directly (CI, DPL, AD)",
        "label": f"{LABEL_COLUMN} (primary_diagnosis in {sorted(ACS_DIAGNOSES)})",
        "outcome_for_accuracy": CORRECT_COLUMN,
        "threshold": BIAS_THRESHOLD,
        "records": len(data),
        "facets": facets,
        "facets_with_bias": [f["facet"] for f in facets if f["bias_detected"]],
    }
