# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
SHAP feature attribution, computed with the SHAP library directly.

AWS's replacement guidance for SageMaker Clarify explainability: "The SHAP
library is the same engine that powers SageMaker Clarify's explainability. Using
it directly gives you more flexibility."
https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html

A small gradient-boosted model predicts an acute coronary syndrome diagnosis from
the clinical features; TreeExplainer attributes each prediction to the features.
"""

import numpy as np
import pandas as pd
import shap
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import cross_val_score

try:
    from .metrics import LABEL_COLUMN, add_derived_columns
except ImportError:  # run as a flat script inside the processing container
    from metrics import LABEL_COLUMN, add_derived_columns

FEATURES = [
    "age",
    "chest_pain_score",
    "troponin_level",
    "ecg_abnormal",
    "blood_pressure_systolic",
    "heart_rate",
    "temperature",
    "respiratory_rate",
    "oxygen_saturation",
    "bmi",
    "smoking_history",
    "diabetes",
    "hypertension",
    "family_cardiac_history",
    "previous_mi",
]

RANDOM_STATE = 0


def train_model(df: pd.DataFrame) -> tuple[GradientBoostingClassifier, float]:
    """Fit the classifier and return it with its 5-fold cross-validated accuracy."""
    data = add_derived_columns(df)
    x, y = data[FEATURES], data[LABEL_COLUMN]
    model = GradientBoostingClassifier(n_estimators=100, max_depth=2, random_state=RANDOM_STATE)
    cv_accuracy = float(np.mean(cross_val_score(model, x, y, cv=5, scoring="accuracy")))
    model.fit(x, y)
    return model, cv_accuracy


def shap_report(df: pd.DataFrame, explain_patient: str = "P001") -> dict:
    """Global mean |SHAP| importance plus one patient's local attribution."""
    model, cv_accuracy = train_model(df)
    data = add_derived_columns(df)
    x = data[FEATURES]
    explainer = shap.TreeExplainer(model)
    values = np.asarray(explainer.shap_values(x))
    base_value = float(np.ravel(explainer.expected_value)[0])

    mean_abs = np.abs(values).mean(axis=0)
    global_importance = sorted(
        ({"feature": f, "mean_abs_shap": round(float(v), 4)} for f, v in zip(FEATURES, mean_abs, strict=True)),
        key=lambda r: r["mean_abs_shap"],
        reverse=True,
    )
    row = int(np.flatnonzero(data["patient_id"].to_numpy() == explain_patient)[0])
    local = sorted(
        (
            {"feature": f, "value": float(x.iloc[row][f]), "shap": round(float(v), 4)}
            for f, v in zip(FEATURES, values[row], strict=True)
        ),
        key=lambda r: abs(r["shap"]),
        reverse=True,
    )
    return {
        "report_type": "shap_explainability",
        "method": "shap.TreeExplainer on a GradientBoostingClassifier (log-odds units)",
        "shap_version": shap.__version__,
        "target": LABEL_COLUMN,
        "cv_accuracy": round(cv_accuracy, 4),
        "base_value": round(base_value, 4),
        "global_importance": global_importance,
        "local_explanation": {
            "patient_id": explain_patient,
            "predicted_probability": round(float(model.predict_proba(x.iloc[[row]])[0][1]), 4),
            "attributions": local,
        },
    }
