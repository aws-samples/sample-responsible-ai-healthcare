# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import logging
import os

from strands.models.bedrock import BedrockModel

log = logging.getLogger(__name__)

# Model governance: scripts/04_evaluate_llm.py evaluates this agent on Claude
# Sonnet 4.6 with Amazon Bedrock Evaluations (base model ID
# anthropic.claude-sonnet-4-6, which has no dated snapshot ID). In-Region
# invocation is not offered for this model in US Regions, so only its US
# cross-Region inference profile is allowed. The global profile is not: it can
# route prompts, which carry patient data, outside the US, and the runtime role
# grants only the US profile. Changing models requires re-running that
# evaluation and updating this allowlist.
ALLOWED_MODEL_IDS = frozenset({"us.anthropic.claude-sonnet-4-6"})
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"


def load_model() -> BedrockModel:
    """Get Bedrock model client using IAM credentials.
    Using Claude Sonnet 4.6 — latest model for clinical accuracy.
    Low temperature (0.1) for deterministic medical recommendations.
    """
    model_id = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    guardrail_id = os.environ.get("GUARDRAIL_ID")
    guardrail_version = os.environ.get("GUARDRAIL_VERSION", "DRAFT")

    if model_id not in ALLOWED_MODEL_IDS:
        raise RuntimeError(
            f"BEDROCK_MODEL_ID '{model_id}' is not an assessed model. "
            f"Allowed: {sorted(ALLOWED_MODEL_IDS)}. Re-run scripts/04_evaluate_llm.py before changing models."
        )

    if not guardrail_id:
        raise RuntimeError("GUARDRAIL_ID is required. Agent refuses to start without safety guardrails.")

    if guardrail_version == "DRAFT":
        log.warning("GUARDRAIL_VERSION is DRAFT (mutable). Pin a published guardrail version outside demo use.")

    log.info(f"Loading model: {model_id} (guardrail: {guardrail_id} v{guardrail_version})")

    return BedrockModel(
        model_id=model_id,
        temperature=0.1,
        # The agent emits a full structured diagnostic JSON plus tool-use turns
        # (cardiac risk, drug-interaction checks); 2000 tokens truncates it and the
        # buffered output then fails JSON validation (MaxTokensReachedException).
        # 4096 lets the complete diagnosis serialize. Output is still buffered and
        # validated before anything is returned, so the safety contract is unchanged.
        max_tokens=4096,
        guardrail_id=guardrail_id,
        guardrail_version=guardrail_version,
        # The project guardrail attaches an Automated Reasoning policy, and Bedrock
        # rejects AR checks on the streaming ConverseStream API ("Automated
        # Reasoning checks are not supported with streaming APIs"). Use the
        # non-streaming Converse API instead. This is caller-invisible: main.py
        # already buffers the entire model output before validating and emitting a
        # single JSON (no token streaming is exposed), so the diagnostic contract
        # is unchanged — only the underlying Bedrock API differs.
        streaming=False,
    )
