# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Demo 1 - Step 4: Evaluate the deployed agent with Amazon Bedrock Evaluations
============================================================================
Runs the synthetic cases in data/llm_eval_cases.json through the deployed
AgentCore agent (so the guardrails and output validation are part of what is
evaluated), then submits the responses to an Amazon Bedrock Evaluations
LLM-as-a-judge job as bring-your-own inference responses:
https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation-judge.html

Each clinical vignette has variants that change one demographic attribute. The
judge scores every response for correctness, completeness, harmfulness,
stereotyping and refusal; a fair agent scores the same across a vignette's
variants. Amazon Bedrock Evaluations is AWS's managed replacement for
SageMaker Clarify's foundation model evaluation:
https://docs.aws.amazon.com/sagemaker/latest/dg/clarify-availability-change.html

Environment: AGENTCORE_RUNTIME_ID; MLFLOW_TRACKING_ARN (optional)
Usage: python scripts/04_evaluate_llm.py [--reuse-run RUN]
  --reuse-run  Resubmit evaluation/input/RUN/dataset.jsonl without invoking the agent again
Runs AGAINST AWS (Bedrock AgentCore, Bedrock Evaluations, S3, CloudWatch).
"""

import argparse
import importlib.util
import json
import logging
import os
import sys
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import AWS_ACCOUNT_ID, BOTO_CONFIG, REGION, S3_BUCKET, S3_PREFIX, validate_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CASES_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "llm_eval_cases.json")
MODEL_IDENTIFIER = "rai-healthcare-agent"
# Judge from a different model family than the generator (Claude), from the supported list:
# https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation-judge.html#evaluation-judge-supported
# Not configurable: the evaluation role (infra/sagemaker.tf) may invoke only this judge.
JUDGE_MODEL = "us.amazon.nova-pro-v1:0"
METRICS = [
    "Builtin.Correctness",
    "Builtin.Completeness",
    "Builtin.Harmfulness",
    "Builtin.Stereotyping",
    "Builtin.Refusal",
]
EVAL_ROLE = f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/rai-healthcare-demo-bedrock-evaluation-role"
MAX_WAIT_SECONDS = 3600
DEMOGRAPHIC_FIELDS = ("ethnicity", "language", "location_type")


def expand_cases(spec: dict) -> list[dict]:
    """Baseline and single-attribute variants of every vignette, plus safety cases (pure)."""
    cases = []
    for v in spec["vignettes"]:
        cases.append(
            {
                "id": f"{v['id']}:baseline",
                "vignette": v["id"],
                "category": "baseline",
                "patient": v["patient"],
                "reference": v["reference"],
            }
        )
        for attribute, change in v["variants"].items():
            cases.append(
                {
                    "id": f"{v['id']}:{attribute}",
                    "vignette": v["id"],
                    "category": attribute,
                    "patient": {**v["patient"], **change},
                    "reference": v["reference"],
                }
            )
    for s in spec["safety_cases"]:
        cases.append(
            {
                "id": s["id"],
                "vignette": s["id"],
                "category": s["category"],
                "patient": s.get("patient"),
                "prompt": s.get("prompt"),
                "reference": s["reference"],
            }
        )
    return cases


def agent_payload(case: dict) -> dict:
    """The agent request. Demographics the agent template omits are stated in the complaint text (pure)."""
    if case.get("patient") is None:
        return {"prompt": case["prompt"]}
    patient = dict(case["patient"])
    stated = [f"{f.replace('_', ' ')}: {patient.pop(f)}" for f in DEMOGRAPHIC_FIELDS if f in patient]
    if stated:
        patient["chief_complaint"] = f"[{'; '.join(stated)}] {patient['chief_complaint']}"
    return {"prompt": "Provide diagnostic recommendation.", "patient_data": patient}


def case_prompt(case: dict) -> str:
    """The prompt text the judge sees (pure)."""
    return json.dumps(agent_payload(case), sort_keys=True)


def dataset_line(case: dict, response: str) -> str:
    return json.dumps(
        {
            "prompt": case_prompt(case),
            "referenceResponse": case["reference"],
            "category": case["category"],
            "modelResponses": [{"response": response, "modelIdentifier": MODEL_IDENTIFIER}],
        }
    )


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4)


def summarize(records: list[dict], cases: list[dict]) -> dict:
    """Mean score per metric, per category, and the largest gap across each vignette's variants (pure)."""
    by_prompt = {case_prompt(c): c for c in cases}
    overall, per_category = defaultdict(list), defaultdict(lambda: defaultdict(list))
    per_vignette = defaultdict(lambda: defaultdict(dict))
    for r in records:
        case = by_prompt.get(r["inputRecord"]["prompt"])
        for s in r["automatedEvaluationResult"]["scores"]:
            if s.get("result") is None:
                continue
            name = s["metricName"].removeprefix("Builtin.")
            overall[name].append(s["result"])
            per_category[r["inputRecord"].get("category", "none")][name].append(s["result"])
            if case and case["category"] != "safety":
                per_vignette[case["vignette"]][name][case["category"]] = s["result"]
    gaps = {}
    for vignette, metrics in per_vignette.items():
        gaps[vignette] = {m: round(max(v.values()) - min(v.values()), 4) for m, v in metrics.items() if len(v) > 1}
    return {
        "overall": {m: _mean(v) for m, v in overall.items()},
        "per_category": {c: {m: _mean(v) for m, v in ms.items()} for c, ms in per_category.items()},
        "max_variant_gap": {m: max((g[m] for g in gaps.values() if m in g), default=0.0) for m in overall},
        "variant_gaps": gaps,
    }


def evaluation_request(job_name: str, dataset_uri: str, output_uri: str, kms_key: str) -> dict:
    """The CreateEvaluationJob request (pure, unit-tested)."""
    return {
        "jobName": job_name,
        "roleArn": EVAL_ROLE,
        "applicationType": "ModelEvaluation",
        "customerEncryptionKeyId": kms_key,
        "evaluationConfig": {
            "automated": {
                "datasetMetricConfigs": [
                    {
                        "taskType": "General",
                        "dataset": {"name": "rai_clinical_cases", "datasetLocation": {"s3Uri": dataset_uri}},
                        "metricNames": METRICS,
                    }
                ],
                "evaluatorModelConfig": {"bedrockEvaluatorModels": [{"modelIdentifier": JUDGE_MODEL}]},
            }
        },
        "inferenceConfig": {
            "models": [{"precomputedInferenceSource": {"inferenceSourceIdentifier": MODEL_IDENTIFIER}}]
        },
        "outputDataConfig": {"s3Uri": output_uri},
        "jobTags": [{"key": "Project", "value": "rai-healthcare-demo"}],
    }


def _load_invoke_module():
    spec = importlib.util.spec_from_file_location(
        "invoke_agentcore", os.path.join(os.path.dirname(__file__), "11_invoke_agentcore.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect_responses(s3, cases: list[dict], run: str) -> str:
    """Run every case through the deployed agent and upload the dataset. Returns its key."""
    runtime_id = os.environ.get("AGENTCORE_RUNTIME_ID", "")
    if not runtime_id:
        print("AGENTCORE_RUNTIME_ID must be set.")
        raise SystemExit(1)
    invoke = _load_invoke_module()
    agent = boto3.client("bedrock-agentcore", region_name=REGION, config=BOTO_CONFIG)
    arn = f"arn:aws:bedrock-agentcore:{REGION}:{AWS_ACCOUNT_ID}:runtime/{runtime_id}"
    lines = []
    for case in cases:
        response = agent.invoke_agent_runtime(
            agentRuntimeArn=arn,
            payload=json.dumps(agent_payload(case)),
            runtimeSessionId=f"eval-{uuid.uuid4()}-{uuid.uuid4().hex[:4]}",
            contentType="application/json",
            accept="application/json",
        )
        text = invoke.parse_sse_payload(response["response"].read().decode("utf-8"))
        lines.append(dataset_line(case, text))
        logger.info(f"{case['id']}: {len(text)} chars")
    dataset_key = f"{S3_PREFIX}/evaluation/input/{run}/dataset.jsonl"
    s3.put_object(Bucket=S3_BUCKET, Key=dataset_key, Body="\n".join(lines).encode("utf-8"))
    return dataset_key


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reuse-run", help="resubmit evaluation/input/RUN/dataset.jsonl without invoking the agent")
    args = parser.parse_args()
    if not validate_config():
        raise SystemExit(1)
    s3 = boto3.client("s3", region_name=REGION, config=BOTO_CONFIG)
    bedrock = boto3.client("bedrock", region_name=REGION, config=BOTO_CONFIG)
    cw = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)

    with open(CASES_FILE, encoding="utf-8") as f:
        cases = expand_cases(json.load(f))
    run = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    print("=" * 60)
    print(f"DEMO 1: LLM evaluation — {len(cases)} cases through the deployed agent")
    print("=" * 60)
    if args.reuse_run:
        dataset_key = f"{S3_PREFIX}/evaluation/input/{args.reuse_run}/dataset.jsonl"
        s3.head_object(Bucket=S3_BUCKET, Key=dataset_key)
        logger.info(f"Reusing the agent responses in {dataset_key}")
    else:
        dataset_key = collect_responses(s3, cases, run)
    kms_key = s3.get_bucket_encryption(Bucket=S3_BUCKET)["ServerSideEncryptionConfiguration"]["Rules"][0][
        "ApplyServerSideEncryptionByDefault"
    ]["KMSMasterKeyID"]
    output_prefix = f"{S3_PREFIX}/evaluation/output/{run}/"
    job_name = f"rai-healthcare-demo-eval-{run}"
    job_arn = bedrock.create_evaluation_job(
        **evaluation_request(job_name, f"s3://{S3_BUCKET}/{dataset_key}", f"s3://{S3_BUCKET}/{output_prefix}", kms_key)
    )["jobArn"]
    logger.info(f"Started evaluation job {job_name} (judge {JUDGE_MODEL})")

    status, deadline = "InProgress", time.time() + MAX_WAIT_SECONDS
    while status in ("InProgress", "Stopping") and time.time() < deadline:
        time.sleep(60)
        job = bedrock.get_evaluation_job(jobIdentifier=job_arn)
        status = job["status"]
    if status != "Completed":
        print(f"❌ Evaluation job {job_name} ended {status}: {job.get('failureMessages', [])}")
        raise SystemExit(1)

    records = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=S3_BUCKET, Prefix=output_prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".jsonl"):
                body = s3.get_object(Bucket=S3_BUCKET, Key=obj["Key"])["Body"].read().decode("utf-8")
                records += [json.loads(line) for line in body.splitlines() if "automatedEvaluationResult" in line]
    summary = summarize(records, cases)
    s3.put_object(
        Bucket=S3_BUCKET, Key=f"{output_prefix}summary.json", Body=json.dumps(summary, indent=2).encode("utf-8")
    )

    print(f"\n{'Metric':22} {'Mean':>6} {'Max gap across demographic variants':>38}")
    for metric, value in summary["overall"].items():
        print(f"{metric:22} {value:6.3f} {summary['max_variant_gap'].get(metric, 0.0):38.3f}")
    print(f"\nSummary: s3://{S3_BUCKET}/{output_prefix}summary.json")

    dims = [{"Name": "Service", "Value": "LLMEvaluation"}]
    cw.put_metric_data(
        Namespace="RAIHealthcare",
        MetricData=[
            {"MetricName": f"Eval{m}", "Value": v, "Unit": "None", "Dimensions": dims}
            for m, v in summary["overall"].items()
        ]
        + [
            {"MetricName": f"EvalVariantGap{m}", "Value": v, "Unit": "None", "Dimensions": dims}
            for m, v in summary["max_variant_gap"].items()
        ],
    )

    mlflow_arn = os.environ.get("MLFLOW_TRACKING_ARN", "")
    if mlflow_arn:
        import mlflow

        mlflow.set_tracking_uri(mlflow_arn)
        mlflow.set_experiment("rai-healthcare-demo-llm-evaluation")
        with mlflow.start_run(run_name=job_name):
            mlflow.log_params({"judge": JUDGE_MODEL, "cases": len(cases), "job_arn": job_arn})
            for m, v in summary["overall"].items():
                mlflow.log_metric(m, v)
                mlflow.log_metric(f"variant_gap_{m}", summary["max_variant_gap"].get(m, 0.0))
        logger.info("Logged the evaluation summary to MLflow")
    print("\n✅ LLM evaluation complete.")


if __name__ == "__main__":
    main()
