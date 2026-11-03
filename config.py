# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Shared configuration for RAI Healthcare Demo.
==============================================
All scripts import from here instead of hardcoding values.

Required environment variables (no defaults — must be set):
  AWS_ACCOUNT_ID   — Your AWS account ID
  S3_BUCKET        — S3 bucket name for the demo
  GUARDRAIL_ID     — Bedrock guardrail identifier (terraform output guardrail_id)

Optional environment variables (have sensible defaults):
  AWS_REGION            — AWS region (default: us-east-1)
  S3_PREFIX             — S3 key prefix (default: rai-healthcare-demo)
  GUARDRAIL_VERSION     — Bedrock guardrail version (default: DRAFT)
  CONFIDENCE_THRESHOLD  — Low-confidence threshold as a FRACTION in [0.50, 1.0]
                          (default: 0.70). Not a percent: a value outside that
                          range raises RuntimeError at import. Terraform's
                          confidence_threshold variable is the percent form (70)
                          and is divided by 100 before it reaches the app.
  BEDROCK_MODEL_ID      — Bedrock model to use (default: us.anthropic.claude-sonnet-4-6;
                          must be in ALLOWED_MODEL_IDS)
"""

import logging
import os

from botocore.config import Config as BotoConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Boto3 retry / timeout config — used by all scripts
# ---------------------------------------------------------------------------
BOTO_CONFIG = BotoConfig(
    retries={"max_attempts": 3, "mode": "adaptive"},
    connect_timeout=10,
    read_timeout=120,
)


# ---------------------------------------------------------------------------
# Required — no defaults
# ---------------------------------------------------------------------------
AWS_ACCOUNT_ID: str = os.environ.get("AWS_ACCOUNT_ID", "")
S3_BUCKET: str = os.environ.get("S3_BUCKET", "")

# ---------------------------------------------------------------------------
# Optional — sensible defaults
# ---------------------------------------------------------------------------
REGION: str = os.environ.get("AWS_REGION", "us-east-1")
S3_PREFIX: str = os.environ.get("S3_PREFIX", "rai-healthcare-demo")
GUARDRAIL_ID: str = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VERSION: str = os.environ.get("GUARDRAIL_VERSION", "DRAFT")
BEDROCK_MODEL_ID: str = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

# Models scripts/04_evaluate_llm.py evaluates with Amazon Bedrock Evaluations (Claude
# Sonnet 4.6 via its US cross-Region inference profile). The global profile is not
# allowed: it can route prompts, which carry patient data, outside the US. Keep in
# sync with src/agent/app/raihealthdiag/model/load.py.
ALLOWED_MODEL_IDS: frozenset[str] = frozenset({"us.anthropic.claude-sonnet-4-6"})

# Confidence threshold — a FRACTION in the closed interval [0.50, 1.0], the same
# scale as the model's confidence score. Terraform's confidence_threshold variable
# is a PERCENT and is divided by 100 before it reaches the application.
_MINIMUM_CONFIDENCE_FLOOR: float = 0.50
_MAXIMUM_CONFIDENCE: float = 1.0


def _load_confidence_threshold() -> float:
    """Read CONFIDENCE_THRESHOLD, rejecting anything outside [0.50, 1.0].

    Fails fast instead of clamping: clamping let a percent value like 70 pass
    silently and flagged every diagnosis for mandatory review. Keep in sync with
    src/lambda/preprocessing/index.py and src/agent/app/raihealthdiag/main.py.
    """
    raw = os.environ.get("CONFIDENCE_THRESHOLD", "0.70")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"CONFIDENCE_THRESHOLD must be a number, got '{raw}'") from exc
    if not _MINIMUM_CONFIDENCE_FLOOR <= value <= _MAXIMUM_CONFIDENCE:
        raise RuntimeError(
            f"CONFIDENCE_THRESHOLD must be a fraction in "
            f"[{_MINIMUM_CONFIDENCE_FLOOR}, {_MAXIMUM_CONFIDENCE}], got {value}. "
            f"Percent values such as 70 are not accepted."
        )
    return value


CONFIDENCE_THRESHOLD: float = _load_confidence_threshold()

# Derived values
SAGEMAKER_ROLE: str = f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/rai-healthcare-demo-sagemaker-role" if AWS_ACCOUNT_ID else ""


def validate_config(require_guardrail_id: bool = True) -> bool:
    """Check that required environment variables are set.

    Prints a helpful error message and returns False if any are missing.
    Returns True when configuration is valid.

    ``require_guardrail_id`` defaults to True, so every existing caller keeps the
    same behaviour. Pass False only from a script that *produces* GUARDRAIL_ID
    rather than consuming it — scripts/setup_guardrail.py creates the guardrail,
    so requiring GUARDRAIL_ID there made the script exit before it could run on a
    fresh deployment.
    """
    missing: list[str] = []

    if not AWS_ACCOUNT_ID:
        missing.append("AWS_ACCOUNT_ID")
    if not S3_BUCKET:
        missing.append("S3_BUCKET")
    if require_guardrail_id and not GUARDRAIL_ID:
        missing.append("GUARDRAIL_ID")

    if BEDROCK_MODEL_ID not in ALLOWED_MODEL_IDS:
        logger.error(
            f"BEDROCK_MODEL_ID '{BEDROCK_MODEL_ID}' is not an assessed model. Allowed: {sorted(ALLOWED_MODEL_IDS)}"
        )
        return False

    if missing:
        logger.error("=" * 60)
        logger.error("  Missing required environment variables")
        logger.error("=" * 60)
        for var in missing:
            logger.error(f"  - {var} is not set")
        logger.error("")
        logger.error("  Set them before running any demo script:")
        logger.error("    export AWS_ACCOUNT_ID='123456789012'")
        logger.error("    export S3_BUCKET='rai-healthcare-demo-123456789012-us-east-1'")
        logger.error("")
        logger.error("  Optional variables:")
        logger.error("    export AWS_REGION='us-east-1'")
        logger.error("    export S3_PREFIX='rai-healthcare-demo'")
        logger.error("    export GUARDRAIL_ID='your-guardrail-id'")
        logger.error("    export GUARDRAIL_VERSION='DRAFT'")
        logger.error("    export CONFIDENCE_THRESHOLD='0.70'  # fraction in [0.50, 1.0], not a percent")
        logger.error("    export BEDROCK_MODEL_ID='us.anthropic.claude-sonnet-4-6'")
        logger.error("=" * 60)
        return False

    return True
