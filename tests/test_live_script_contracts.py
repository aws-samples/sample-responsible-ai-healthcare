# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Pass/fail contracts of the live verification scripts.

The scripts call AWS, so only their decision logic is tested here: a wrong
access decision, an error that prevented a check, or a fail-closed agent
response must each be reported as a failure, never as a pass.
"""

import importlib.util
import json
import os

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")


def _load(file_name, module_name):
    spec = importlib.util.spec_from_file_location(module_name, os.path.join(SCRIPTS, file_name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def access_control():
    return _load("06_test_access_control.py", "access_control_under_test")


@pytest.fixture
def invoke_agentcore(monkeypatch):
    # The script refuses to import without a runtime id
    monkeypatch.setenv("AGENTCORE_RUNTIME_ID", "test-runtime")
    return _load("11_invoke_agentcore.py", "invoke_agentcore_under_test")


class TestAccessControlContract:
    def test_every_scenario_has_an_expected_decision(self, access_control):
        assert len(access_control.SCENARIOS) == 8
        assert {s[4] for s in access_control.SCENARIOS} == {"ALLOW", "DENY"}

    def test_every_scenario_action_exists(self, access_control):
        assert {s[3] for s in access_control.SCENARIOS} <= set(access_control.ACTIONS)

    @pytest.mark.parametrize("code", ["AccessDenied", "AccessDeniedException", "Forbidden"])
    def test_authorization_refusals_are_denials(self, access_control, code):
        assert access_control.classify_error(code) == "DENY"

    @pytest.mark.parametrize("code", ["ExpiredToken", "NoSuchKey", "ThrottlingException"])
    def test_other_errors_are_not_denials(self, access_control, code):
        # An error that prevented the check must not satisfy an expected DENY
        assert access_control.classify_error(code) == "ERROR"

    def test_matching_results_have_no_mismatches(self, access_control):
        results = [{"expected": "ALLOW", "decision": "ALLOW"}, {"expected": "DENY", "decision": "DENY"}]
        assert access_control.mismatches(results) == []

    @pytest.mark.parametrize(
        ("expected", "decision"), [("DENY", "ALLOW"), ("ALLOW", "DENY"), ("DENY", "ERROR"), ("ALLOW", "ERROR")]
    )
    def test_wrong_or_failed_decisions_are_mismatches(self, access_control, expected, decision):
        assert access_control.mismatches([{"expected": expected, "decision": decision}])


class TestInvokeAgentcoreContract:
    def test_validated_diagnosis_passes(self, invoke_agentcore):
        text = json.dumps({"primary_diagnosis": "STEMI", "confidence": 0.96, "mandatory_physician_review": False})
        assert invoke_agentcore.is_validated_diagnosis(text)

    @pytest.mark.parametrize(
        "text",
        [
            json.dumps({"status": "VALIDATION_FAILED", "message": "blocked"}),
            json.dumps({"primary_diagnosis": "STEMI", "mandatory_physician_review": False}),
            json.dumps({"primary_diagnosis": "STEMI", "confidence": 0.9}),
            json.dumps(["not", "an", "object"]),
            "free-form text the agent should never deliver",
            "",
        ],
    )
    def test_fail_closed_or_malformed_output_fails(self, invoke_agentcore, text):
        assert not invoke_agentcore.is_validated_diagnosis(text)
