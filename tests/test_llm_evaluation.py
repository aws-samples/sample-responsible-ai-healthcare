# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Case expansion, dataset format, request shape and score summary in
scripts/04_evaluate_llm.py (no AWS calls).
"""

import importlib.util
import json
import os

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from botocore.stub import Stubber

ROOT = os.path.join(os.path.dirname(__file__), "..")


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location(
        "llm_eval_under_test", os.path.join(ROOT, "scripts", "04_evaluate_llm.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cases(ev):
    with open(os.path.join(ROOT, "data", "llm_eval_cases.json"), encoding="utf-8") as f:
        return ev.expand_cases(json.load(f))


def test_every_variant_changes_exactly_one_attribute(cases):
    baselines = {c["vignette"]: c for c in cases if c["category"] == "baseline"}
    for c in cases:
        if c["category"] in ("baseline", "safety"):
            continue
        base = baselines[c["vignette"]]["patient"]
        changed = [k for k in c["patient"] if c["patient"][k] != base.get(k)]
        assert len(changed) == 1, f"{c['id']} changes {changed}"


def test_case_ids_and_prompts_are_unique(ev, cases):
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({ev.case_prompt(c) for c in cases}) == len(cases)


def test_demographics_reach_the_agent_text(ev, cases):
    variant = next(c for c in cases if c["id"] == "stemi:ethnicity")
    complaint = ev.agent_payload(variant)["patient_data"]["chief_complaint"]
    assert "ethnicity: African_American" in complaint


def test_dataset_line_matches_the_byoi_format(ev, cases):
    line = json.loads(ev.dataset_line(cases[0], "response text"))
    assert set(line) == {"prompt", "referenceResponse", "category", "modelResponses"}
    assert line["modelResponses"] == [{"response": "response text", "modelIdentifier": ev.MODEL_IDENTIFIER}]


def test_request_validates_against_the_bedrock_api_model(ev):
    client = boto3.client("bedrock", region_name="us-east-1", config=Config(signature_version=UNSIGNED))
    request = ev.evaluation_request("rai-healthcare-demo-eval-x", "s3://b/in.jsonl", "s3://b/out/", "arn:aws:kms:k")
    with Stubber(client) as stub:
        stub.add_response(
            "create_evaluation_job", {"jobArn": "arn:aws:bedrock:us-east-1:111122223333:evaluation-job/x"}, request
        )
        client.create_evaluation_job(**request)


def test_judge_is_not_the_generator_family(ev):
    assert "anthropic" not in ev.JUDGE_MODEL


def test_summary_means_and_variant_gaps(ev, cases):
    def record(case, score):
        return {
            "inputRecord": {"prompt": ev.case_prompt(case), "category": case["category"]},
            "automatedEvaluationResult": {"scores": [{"metricName": "Builtin.Stereotyping", "result": score}]},
        }

    stemi = [c for c in cases if c["vignette"] == "stemi"]
    records = [record(c, 0.0) for c in stemi[:-1]] + [record(stemi[-1], 0.5)]
    summary = ev.summarize(records, cases)
    assert summary["overall"]["Stereotyping"] == pytest.approx(0.5 / len(stemi), abs=1e-4)
    assert summary["max_variant_gap"]["Stereotyping"] == pytest.approx(0.5)
    assert summary["variant_gaps"]["stemi"]["Stereotyping"] == pytest.approx(0.5)


def test_scores_without_a_result_are_ignored(ev, cases):
    rec = {
        "inputRecord": {"prompt": ev.case_prompt(cases[0]), "category": "baseline"},
        "automatedEvaluationResult": {"scores": [{"metricName": "Builtin.Correctness", "result": None}]},
    }
    assert ev.summarize([rec], cases)["overall"] == {}


@pytest.fixture(scope="module")
def mlflow_setup():
    spec = importlib.util.spec_from_file_location(
        "setup_mlflow_under_test", os.path.join(ROOT, "scripts", "setup_mlflow.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mlflow_app_is_found_by_exact_name_only(mlflow_setup):
    apps = [
        {"Name": "rai-healthcare-demo-mlflow-old", "Status": "Created", "Arn": "a"},
        {"Name": "other-team-mlflow", "Status": "Created", "Arn": "b"},
        {"Name": "rai-healthcare-demo-mlflow", "Status": "Deleting", "Arn": "c"},
    ]
    assert mlflow_setup.find_app(apps) is None
    apps.append({"Name": "rai-healthcare-demo-mlflow", "Status": "Created", "Arn": "d"})
    assert mlflow_setup.find_app(apps)["Arn"] == "d"


def test_mlflow_request_validates_and_never_changes_the_account_default(mlflow_setup):
    request = mlflow_setup.create_request("arn:aws:kms:us-east-1:111122223333:key/example")
    assert request["AccountDefaultStatus"] == "DISABLED"
    assert request["ArtifactStoreUri"].endswith("/mlflow/")
    client = boto3.client("sagemaker", region_name="us-east-1", config=Config(signature_version=UNSIGNED))
    with Stubber(client) as stub:
        stub.add_response(
            "create_mlflow_app", {"Arn": "arn:aws:sagemaker:us-east-1:111122223333:mlflow-app/app-x"}, request
        )
        client.create_mlflow_app(**request)
