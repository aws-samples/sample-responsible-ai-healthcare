# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Decision helpers of scripts/verify_solution.py.

The live checks call AWS, so these tests pin the pure logic the checks depend
on: each must reject the failure it exists to catch.
"""

import importlib.util
import json
import os

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_solution.py")


@pytest.fixture(scope="module")
def verify():
    spec = importlib.util.spec_from_file_location("verify_solution_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sse(*payloads):
    # The agent yields its result as a JSON string, so each frame is JSON-encoded twice
    return "\n".join(f"data: {json.dumps(json.dumps(p))}" for p in payloads)


class TestSseFrames:
    def test_single_validated_frame(self, verify):
        frames = verify.sse_frames(_sse({"confidence": 0.9, "mandatory_physician_review": False}))
        assert len(frames) == 1 and verify.is_diagnosis(frames[0])

    def test_multiple_frames_are_all_counted(self, verify):
        # A streamed, unvalidated answer arrives as several frames; the check requires exactly one
        assert len(verify.sse_frames(_sse("partial", "text", "chunks"))) == 3

    def test_done_and_non_data_lines_are_ignored(self, verify):
        raw = ": keep-alive\n" + _sse({"status": "VALIDATION_FAILED"}) + "\ndata: [DONE]\n"
        assert verify.sse_frames(raw) == [{"status": "VALIDATION_FAILED"}]


class TestFailClosed:
    @pytest.mark.parametrize(
        "frame",
        [{"status": "VALIDATION_FAILED", "message": "x"}, {"confidence": 0.5, "mandatory_physician_review": True}],
    )
    def test_validated_or_blocked_output_is_fail_closed(self, verify, frame):
        assert verify.is_fail_closed(frame)

    @pytest.mark.parametrize("frame", ["Here is a poem about the ocean", {"answer": "free text"}, None])
    def test_free_text_is_not_fail_closed(self, verify, frame):
        assert not verify.is_fail_closed(frame)

    def test_diagnosis_needs_both_safety_fields(self, verify):
        assert not verify.is_diagnosis({"confidence": 0.9})
        assert not verify.is_diagnosis({"mandatory_physician_review": False})


class TestPhiSentinels:
    def test_clean_response_has_no_leak(self, verify):
        body = json.dumps({"error": "Invalid patient data", "request_id": "abc"})
        assert verify.leaked_sentinels(body) == []

    def test_echoed_sentinel_is_reported(self, verify):
        body = json.dumps({"error": f"bad record for {verify.PHI_SENTINELS[1]}"})
        assert verify.leaked_sentinels(body) == [verify.PHI_SENTINELS[1]]


class TestPercentConfidence:
    @pytest.mark.parametrize("value", [18.0, 70.0, 97.0, 100.0])
    def test_percent_values_pass(self, verify, value):
        assert verify.is_percent_confidence(value)

    @pytest.mark.parametrize("value", [0.18, 0.97, 1.0, 0.0, 101.0])
    def test_fraction_regression_or_out_of_range_fails(self, verify, value):
        assert not verify.is_percent_confidence(value)


class TestNetwork:
    def test_default_route_target_is_found(self, verify):
        routes = [
            {"DestinationCidrBlock": "10.0.0.0/16", "GatewayId": "local"},
            {"DestinationCidrBlock": "0.0.0.0/0", "VpcEndpointId": "vpce-fw"},
        ]
        assert verify.default_route_target(routes) == "vpce-fw"

    def test_nat_default_route_is_reported_as_nat(self, verify):
        routes = [{"DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": "nat-1"}]
        assert verify.default_route_target(routes) == "nat-1"

    def test_no_default_route(self, verify):
        assert verify.default_route_target([{"DestinationCidrBlock": "10.0.0.0/16", "GatewayId": "local"}]) is None

    def test_firewall_endpoint_ids(self, verify):
        status = {"SyncStates": {"us-east-1a": {"Attachment": {"EndpointId": "vpce-fw", "Status": "READY"}}}}
        assert verify.firewall_endpoint_ids(status) == {"vpce-fw"}
        assert verify.firewall_endpoint_ids({}) == set()


class TestReport:
    def test_unexpected_exception_is_a_failure(self, verify):
        report = verify.Report()

        def broken():
            raise RuntimeError("credentials expired")

        report.guard("network", broken)
        assert [r["result"] for r in report.rows] == ["FAIL"]
        assert report.failed


class TestResponsibleAiChecks:
    def test_latest_job_is_the_newest_with_the_exact_prefix(self, verify):
        names = [
            "rai-healthcare-demo-bias-20261019-090000",
            "rai-healthcare-demo-bias-20261020-080000",
            "zz-rai-healthcare-demo-bias-20991231-000000",
        ]
        assert verify.latest_job(names, verify.BIAS_JOB_PREFIX) == "rai-healthcare-demo-bias-20261020-080000"

    def test_no_job_is_none(self, verify):
        assert verify.latest_job(["other-team-eval-1"], verify.EVAL_JOB_PREFIX) is None

    def test_missing_eval_metrics(self, verify):
        full = {"overall": {m: 0.5 for m in verify.EVAL_METRICS}}
        assert verify.missing_eval_metrics(full) == []
        assert verify.missing_eval_metrics({"overall": {"Correctness": 1.0}}) == [
            "Completeness",
            "Harmfulness",
            "Stereotyping",
            "Refusal",
        ]

    def test_names_match_the_scripts_that_create_them(self, verify):
        launcher = open(os.path.join(os.path.dirname(SCRIPT), "02_bias_detection.py")).read()
        evaluator = open(os.path.join(os.path.dirname(SCRIPT), "04_evaluate_llm.py")).read()
        mlflow = open(os.path.join(os.path.dirname(SCRIPT), "setup_mlflow.py")).read()
        assert 'f"rai-healthcare-demo-bias-{run}"' in launcher
        assert 'f"rai-healthcare-demo-eval-{run}"' in evaluator
        assert f'APP_NAME = "{verify.MLFLOW_APP_NAME}"' in mlflow
        assert all(f'"Builtin.{m}"' in evaluator for m in verify.EVAL_METRICS)

    def test_evaluation_name_filter_matches_the_api_pattern(self, verify):
        import re

        import boto3

        shape = boto3.client("bedrock", region_name="us-east-1").meta.service_model
        pattern = shape.operation_model("ListEvaluationJobs").input_shape.members["nameContains"].metadata["pattern"]
        assert re.fullmatch(pattern, verify.EVAL_NAME_FILTER)
        assert not re.fullmatch(pattern, verify.EVAL_JOB_PREFIX)


class TestCitesKnowledgeBase:
    DOCS = ["aha_acc_stemi_guidelines.txt", "cardiac_risk_assessment.txt", "medication_safety.txt"]

    def test_matches_file_names_and_stems(self, verify):
        frame = {"guidelines_referenced": ["cardiac_risk_assessment.txt", "AHA_ACC_STEMI_Guidelines (2013)"]}
        assert verify.cites_knowledge_base(frame, self.DOCS) == [
            "aha_acc_stemi_guidelines.txt",
            "cardiac_risk_assessment.txt",
        ]

    def test_generic_guideline_names_are_not_grounding(self, verify):
        frame = {"guidelines_referenced": ["AHA/ACC 2021 Chest Pain Guideline", "ESC 2023"]}
        assert verify.cites_knowledge_base(frame, self.DOCS) == []

    def test_missing_field_is_not_grounding(self, verify):
        assert verify.cites_knowledge_base({}, self.DOCS) == []
