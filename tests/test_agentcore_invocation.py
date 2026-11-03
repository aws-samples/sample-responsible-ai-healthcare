# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Unit Tests — AgentCore response parsing in scripts/11_invoke_agentcore.py
=========================================================================
Exercises the real parse_sse_payload() from the demo script, not a replica.

Regression under test: the script used to read the server-sent-events body with
`re.findall(r'data: "(.*?)"', raw)`. Non-greedy matching ends at the first
escaped quote inside the payload, so a real diagnosis printed as `{\\` and the
first script a reader runs showed no clinical output at all.
"""

import importlib.util
import json
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPT_PATH = os.path.join(ROOT, "scripts", "11_invoke_agentcore.py")


@pytest.fixture(scope="module")
def invoke_script():
    """Import the demo script by path (its name starts with a digit).

    The module exits at import time unless AGENTCORE_RUNTIME_ID is set, so give it
    a dummy value. Nothing in this module contacts AWS.
    """
    previous = os.environ.get("AGENTCORE_RUNTIME_ID")
    os.environ["AGENTCORE_RUNTIME_ID"] = "runtime-under-test"
    try:
        spec = importlib.util.spec_from_file_location("invoke_agentcore_under_test", SCRIPT_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        if previous is None:
            os.environ.pop("AGENTCORE_RUNTIME_ID", None)
        else:
            os.environ["AGENTCORE_RUNTIME_ID"] = previous


# A frame shaped exactly like the live runtime's: one SSE line whose payload is a
# JSON *string* containing the diagnosis, so every inner quote is escaped.
DIAGNOSIS = {
    "primary_diagnosis": 'ST-Elevation Myocardial Infarction (STEMI) — Anterior ("LAD" territory)',
    "confidence": 0.97,
    "priority": "Priority 1",
    "mandatory_physician_review": False,
}


def _sse(payload) -> str:
    return f"data: {json.dumps(payload)}\n\n"


class TestParseSsePayload:
    def test_diagnosis_with_escaped_quotes_is_recovered_in_full(self, invoke_script):
        """The regression: output must not stop at the first escaped quote."""
        raw = _sse(json.dumps(DIAGNOSIS))

        text = invoke_script.parse_sse_payload(raw)

        assert text.strip() not in ("{", "{\\", "")
        assert json.loads(text) == DIAGNOSIS

    def test_confidence_and_review_flag_survive_parsing(self, invoke_script):
        raw = _sse(json.dumps(DIAGNOSIS))

        parsed = json.loads(invoke_script.parse_sse_payload(raw))

        assert parsed["confidence"] == 0.97
        assert parsed["mandatory_physician_review"] is False

    def test_validation_failed_envelope_is_readable(self, invoke_script):
        envelope = {"status": "VALIDATION_FAILED", "message": "AI output did not meet requirements."}
        raw = _sse(json.dumps(envelope))

        assert json.loads(invoke_script.parse_sse_payload(raw)) == envelope

    def test_multiple_frames_are_concatenated_in_order(self, invoke_script):
        raw = _sse('{"a": 1,') + _sse(' "b": 2}')

        assert json.loads(invoke_script.parse_sse_payload(raw)) == {"a": 1, "b": 2}

    def test_non_data_lines_and_done_sentinel_are_ignored(self, invoke_script):
        raw = "event: message\n" + _sse(json.dumps(DIAGNOSIS)) + "data: [DONE]\n\n"

        assert json.loads(invoke_script.parse_sse_payload(raw)) == DIAGNOSIS

    def test_empty_body_yields_empty_string(self, invoke_script):
        assert invoke_script.parse_sse_payload("") == ""

    def test_non_json_frame_is_surfaced_not_dropped(self, invoke_script):
        """A malformed frame must stay visible — silently dropping it would hide errors."""
        raw = "data: upstream failure\n\n"

        assert invoke_script.parse_sse_payload(raw) == "upstream failure"

    def test_frame_carrying_an_object_rather_than_a_string(self, invoke_script):
        """Some runtimes emit the object directly instead of a JSON string."""
        raw = _sse(DIAGNOSIS)

        assert json.loads(invoke_script.parse_sse_payload(raw)) == DIAGNOSIS

    def test_old_regex_approach_would_have_truncated(self, invoke_script):
        """Pin the bug this replaced, so nobody reintroduces the regex."""
        import re

        raw = _sse(json.dumps(DIAGNOSIS))
        truncated = "".join(re.findall(r'data: "(.*?)"', raw))

        assert truncated == "{\\"
        assert invoke_script.parse_sse_payload(raw) != truncated


# --- AgentCore Gateway path (guardrails in policies) --------------------------------


def test_gateway_endpoint_targets_the_diagnostic_runtime_target(invoke_script):
    url = invoke_script.gateway_endpoint("raihealthdiag-diagnostic-abc123", "us-east-1")
    assert url == "https://raihealthdiag-diagnostic-abc123.gateway.bedrock-agentcore.us-east-1.amazonaws.com/diagnostic"


@pytest.mark.parametrize("bad", ["", "gw/../x", "gw id", "https://example.com"])
def test_gateway_endpoint_refuses_anything_but_a_gateway_id(invoke_script, bad):
    with pytest.raises(ValueError):
        invoke_script.gateway_endpoint(bad, "us-east-1")


def _client_error(status):
    from botocore.exceptions import ClientError

    response = {"Error": {"Code": "AccessDeniedException"}, "ResponseMetadata": {"HTTPStatusCode": status}}
    return ClientError(response, "InvokeAgentRuntime")


def test_only_http_403_counts_as_a_policy_denial(invoke_script):
    assert invoke_script.is_policy_denial(_client_error(403))
    assert not invoke_script.is_policy_denial(_client_error(500))
    assert not invoke_script.is_policy_denial(_client_error(429))


def test_injection_probe_carries_no_patient_data(invoke_script):
    assert set(invoke_script.INJECTION_PROBE) == {"prompt"}
