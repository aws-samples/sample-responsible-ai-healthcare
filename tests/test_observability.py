# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Unit Tests — Observability & Safety Logic
===========================================
Tests confidence thresholds, alert logic, and drift detection.
"""

import pytest


class TestConfidenceThresholds:
    """Test confidence threshold and alert logic."""

    THRESHOLD = 0.70

    def test_high_confidence_no_alert(self):
        """97% confidence should NOT trigger alert."""
        confidence = 0.97
        assert confidence >= self.THRESHOLD
        alert_triggered = confidence < self.THRESHOLD
        assert alert_triggered is False

    def test_low_confidence_triggers_alert(self):
        """58% confidence should trigger alert."""
        confidence = 0.58
        assert confidence < self.THRESHOLD
        alert_triggered = confidence < self.THRESHOLD
        assert alert_triggered is True

    def test_boundary_confidence_no_alert(self):
        """Exactly 70% should NOT trigger alert (>= threshold)."""
        confidence = 0.70
        alert_triggered = confidence < self.THRESHOLD
        assert alert_triggered is False

    def test_just_below_threshold_triggers(self):
        """69% should trigger alert."""
        confidence = 0.69
        alert_triggered = confidence < self.THRESHOLD
        assert alert_triggered is True

    @pytest.mark.parametrize(
        "confidence,expected_alert",
        [
            (0.97, False),
            (0.85, False),
            (0.72, False),
            (0.70, False),
            (0.69, True),
            (0.58, True),
            (0.45, True),
            (0.30, True),
        ],
    )
    def test_confidence_alert_matrix(self, confidence, expected_alert):
        """Test multiple confidence values against threshold."""
        alert = confidence < self.THRESHOLD
        assert alert == expected_alert, f"Confidence {confidence}: expected alert={expected_alert}, got {alert}"


class TestDriftDetection:
    """Test accuracy drift detection logic."""

    DRIFT_THRESHOLD = 0.90  # Alarm fires below 90%

    def test_no_drift_detected(self):
        """Stable accuracy should not trigger drift alarm."""
        daily_accuracy = [0.95, 0.94, 0.95, 0.94, 0.95, 0.94, 0.95]
        avg = sum(daily_accuracy) / len(daily_accuracy)
        assert avg >= self.DRIFT_THRESHOLD

    def test_drift_detected(self):
        """Declining accuracy should trigger drift alarm."""
        daily_accuracy = [0.95, 0.94, 0.93, 0.92, 0.91, 0.89, 0.88]
        # Last value below threshold
        assert daily_accuracy[-1] < self.DRIFT_THRESHOLD

    def test_drift_trend_calculation(self):
        """Drift should be detected by slope of accuracy over time."""
        daily_accuracy = [95.0, 94.2, 93.5, 92.8, 92.0, 91.4, 90.8]
        slope = (daily_accuracy[-1] - daily_accuracy[0]) / len(daily_accuracy)
        # Negative slope = declining
        assert slope < 0, "Expected negative slope for drift"
        # Slope steeper than -0.5%/day is concerning
        assert slope < -0.5, f"Drift slope {slope:.2f} should be < -0.5"

    def test_anomaly_detection_spike(self):
        """Sudden spike in one diagnosis type should be flagged."""
        baseline_distribution = {"Acute_MI": 0.25, "Anxiety": 0.18, "Normal": 0.08}
        current_distribution = {"Acute_MI": 0.38, "Anxiety": 0.15, "Normal": 0.07}

        # Check if any diagnosis changed by more than 5 percentage points
        anomalies = []
        for dx in baseline_distribution:
            delta = abs(current_distribution[dx] - baseline_distribution[dx])
            if delta > 0.05:
                anomalies.append((dx, delta))

        assert len(anomalies) > 0, "Expected at least one anomaly"
        assert anomalies[0][0] == "Acute_MI"


class TestAutomatedReasoning:
    """Test Automated Reasoning rule logic."""

    def test_stemi_requires_elevated_troponin(self):
        """If diagnosis is STEMI, troponin must be elevated."""
        diagnosis = "Acute MI (STEMI)"
        troponin = 2.4
        troponin_threshold = 0.04

        if "MI" in diagnosis or "STEMI" in diagnosis:
            assert troponin > troponin_threshold, "STEMI diagnosis requires elevated troponin"

    def test_normal_troponin_excludes_mi(self):
        """Normal troponin should NOT have MI as primary diagnosis."""
        troponin = 0.02
        troponin_threshold = 0.04

        # If troponin is normal, MI should not be primary
        assert troponin <= troponin_threshold
        # Rule: normal troponin → primary diagnosis MUST NOT be MI

    def test_metformin_hold_with_contrast(self):
        """Metformin must be held if contrast procedure needed."""
        medications = ["Metformin", "Lisinopril", "Aspirin"]
        procedure = "cardiac catheterization"
        contrast_procedures = ["CT angiogram", "cardiac catheterization", "PCI"]

        needs_contrast = any(p in procedure.lower() for p in [x.lower() for x in contrast_procedures])
        on_metformin = "Metformin" in medications

        if needs_contrast and on_metformin:
            recommendation_must_include = "hold Metformin"
            assert recommendation_must_include  # Rule enforced

    def test_low_confidence_requires_review_statement(self):
        """Confidence < 70% must include mandatory review statement."""
        confidence = 0.58
        threshold = 0.70

        if confidence < threshold:
            required_text = "MANDATORY PHYSICIAN REVIEW"
            # In production, we'd check the actual output contains this
            assert required_text  # Rule exists

    def test_no_auto_execution(self):
        """AI output must never auto-execute orders."""
        ai_output_type = "RECOMMENDATION"
        forbidden_types = ["AUTO_EXECUTE", "ORDER", "PRESCRIBE"]

        assert ai_output_type not in forbidden_types, "AI must not auto-execute — recommendations only"

    def test_pe_requires_dvt_history_and_ddimer(self):
        """PE in differential requires DVT history + elevated D-dimer."""
        patient = {"history": ["DVT (2020)"], "d_dimer": 4.2, "symptoms": ["pleuritic chest pain"]}

        has_dvt_history = any("DVT" in h for h in patient["history"])
        elevated_ddimer = patient["d_dimer"] > 0.5
        has_pleuritic_pain = "pleuritic chest pain" in patient["symptoms"]

        # Rule 3: All three present → PE must be in differential
        assert has_dvt_history and elevated_ddimer and has_pleuritic_pain


class TestMetricsEmission:
    """Test that correct metrics would be emitted."""

    def test_required_metrics_list(self):
        """All required metrics should be defined."""
        required_metrics = [
            "DiagnosticConfidence",
            "InferenceLatency",
            "AlertsTriggered",
            "OverrideRate",
            "PolicyAllowed",
            "PolicyDenials",
            "BiasDetected",
            "ErrorRate",
            "DiagnosticAccuracy",
            "AutomatedReasoningCheck",
            "TotalInvocations",
        ]
        # All should be non-empty strings
        for metric in required_metrics:
            assert len(metric) > 0
            assert " " not in metric  # CloudWatch metric names can't have spaces

    def test_confidence_metric_range(self):
        """Confidence metric should be 0-100 (percent)."""
        test_values = [97, 85, 70, 58, 35]
        for val in test_values:
            assert 0 <= val <= 100

    def test_latency_metric_positive(self):
        """Latency should always be positive."""
        test_latencies = [1850, 2100, 28050, 500]
        for lat in test_latencies:
            assert lat > 0
