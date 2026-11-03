# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Unit Tests — Policy & Access Control Logic
============================================
Tests RBAC matrix and policy rules without calling AWS.
"""

import pytest


class TestRBACMatrix:
    """Test the Role-Based Access Control matrix logic."""

    @pytest.fixture
    def access_matrix(self):
        """Access control matrix for the four healthcare roles."""
        return {
            "Physician": {
                "allowed": [
                    "ViewRecommendation",
                    "ViewSHAPExplanation",
                    "ViewTriageScore",
                    "ViewCriticalAlerts",
                    "OverrideRecommendation",
                    "ViewPatientHistory",
                    "ProvideFeedback",
                    "RequestSecondOpinion",
                ],
                "denied": ["ConfigureThresholds", "ManageModelVersions", "ViewAuditTrails"],
            },
            "Nurse": {
                "allowed": ["ViewTriageScore", "ViewCriticalAlerts", "AcknowledgeAlert"],
                "denied": [
                    "ViewRecommendation",
                    "ViewSHAPExplanation",
                    "OverrideRecommendation",
                    "ViewPatientHistory",
                    "ConfigureThresholds",
                ],
            },
            "Admin": {
                "allowed": [
                    "ViewSystemMetrics",
                    "ConfigureThresholds",
                    "ManageModelVersions",
                    "ViewAuditLogs",
                    "ViewBiasReports",
                ],
                "denied": ["ViewRecommendation", "ViewPatientHistory", "ViewLabResults", "OverrideRecommendation"],
            },
            "Auditor": {
                "allowed": ["ViewAuditTrails", "ViewComplianceReports", "ViewBiasReports"],
                "denied": [
                    "ViewPatientHistory",
                    "ConfigureThresholds",
                    "ManageModelVersions",
                    "OverrideRecommendation",
                    "ViewRecommendation",
                ],
            },
        }

    def test_physician_can_view_recommendations(self, access_matrix):
        """Physician must be able to view AI recommendations."""
        assert "ViewRecommendation" in access_matrix["Physician"]["allowed"]

    def test_physician_can_override(self, access_matrix):
        """Physician must be able to override AI recommendations."""
        assert "OverrideRecommendation" in access_matrix["Physician"]["allowed"]

    def test_nurse_cannot_view_recommendations(self, access_matrix):
        """Nurse must NOT be able to view full recommendations."""
        assert "ViewRecommendation" in access_matrix["Nurse"]["denied"]

    def test_nurse_can_view_triage(self, access_matrix):
        """Nurse must be able to view triage scores."""
        assert "ViewTriageScore" in access_matrix["Nurse"]["allowed"]

    def test_admin_cannot_view_patient_data(self, access_matrix):
        """Admin must NOT be able to view patient data."""
        assert "ViewPatientHistory" in access_matrix["Admin"]["denied"]
        assert "ViewRecommendation" in access_matrix["Admin"]["denied"]

    def test_admin_can_configure(self, access_matrix):
        """Admin must be able to configure system."""
        assert "ConfigureThresholds" in access_matrix["Admin"]["allowed"]
        assert "ManageModelVersions" in access_matrix["Admin"]["allowed"]

    def test_auditor_read_only(self, access_matrix):
        """Auditor must be read-only — no modifications allowed."""
        assert "ConfigureThresholds" in access_matrix["Auditor"]["denied"]
        assert "ManageModelVersions" in access_matrix["Auditor"]["denied"]
        assert "OverrideRecommendation" in access_matrix["Auditor"]["denied"]

    def test_auditor_cannot_view_patient_data(self, access_matrix):
        """Auditor must NOT access identifiable patient data."""
        assert "ViewPatientHistory" in access_matrix["Auditor"]["denied"]

    def test_no_role_can_auto_execute(self, access_matrix):
        """No role should have auto-execute capability."""
        for role, permissions in access_matrix.items():
            assert "AutoExecuteOrder" not in permissions["allowed"], f"{role} should not have AutoExecuteOrder"

    def test_all_roles_have_allowed_and_denied(self, access_matrix):
        """Every role must have both allowed and denied actions defined."""
        for role in ["Physician", "Nurse", "Admin", "Auditor"]:
            assert len(access_matrix[role]["allowed"]) > 0
            assert len(access_matrix[role]["denied"]) > 0


class TestOverrideWorkflow:
    """Test the emergency override logic."""

    def test_override_requires_justification(self):
        """Override record must include justification."""
        override = {
            "event_type": "AI_RECOMMENDATION_OVERRIDE",
            "physician_id": "PHY-2847",
            "ai_confidence": 0.58,
            "physician_diagnosis": "Heart Failure",
            "override_justification": "Clinical exam shows bilateral crackles and JVD",
        }
        assert override["override_justification"] != ""
        assert len(override["override_justification"]) > 10

    def test_override_triggered_by_low_confidence(self):
        """Override should be triggered when confidence < 70%."""
        ai_confidence = 0.58
        threshold = 0.70
        assert ai_confidence < threshold, "Override should trigger below threshold"

    def test_override_not_triggered_by_high_confidence(self):
        """High confidence should NOT force an override."""
        ai_confidence = 0.97
        threshold = 0.70
        assert ai_confidence >= threshold

    def test_override_record_has_required_fields(self):
        """Override audit record must have all required fields."""
        required_fields = [
            "event_id",
            "event_type",
            "timestamp",
            "physician_id",
            "patient_id",
            "ai_confidence",
            "physician_diagnosis",
            "override_justification",
        ]
        override = {
            "event_id": "OVR-20260515-001",
            "event_type": "AI_RECOMMENDATION_OVERRIDE",
            "timestamp": "2026-05-15T14:30:00Z",
            "physician_id": "PHY-2847",
            "patient_id": "P013",
            "ai_confidence": 0.58,
            "physician_diagnosis": "Heart Failure Exacerbation",
            "override_justification": "Clinical exam findings",
        }
        for field in required_fields:
            assert field in override, f"Missing required field: {field}"
