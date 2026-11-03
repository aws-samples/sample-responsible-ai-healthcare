# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Setup Automated Reasoning Guardrail for Healthcare Diagnostic AI
=================================================================
This script:
1. Creates the Automated Reasoning policy ``healthcare-diagnostic-rules`` and builds
   its rules from scripts/clinical_rules.txt with the documented INGEST_CONTENT build
   workflow, then publishes an immutable policy version
2. Creates the Bedrock Guardrail ``rai-healthcare-diagnostic-guardrail`` with that
   policy version attached, and publishes a guardrail version
3. Prints the GUARDRAIL_* and AR_POLICY_* exports the agent deployment needs

Re-running is safe: an existing guardrail with that exact name is reported as it is
(with the policy version it actually has attached), and nothing new is created.
Any failure exits non-zero.

https://docs.aws.amazon.com/bedrock/latest/userguide/create-automated-reasoning-policy.html

Usage:
  python scripts/setup_guardrail.py
"""

import logging
import os
import re
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import BOTO_CONFIG, REGION, validate_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# The guardrail this script owns, matched EXACTLY wherever it is looked up. A
# substring test such as ``"healthcare" in name.lower()`` matches at least two
# guardrails in a deployed account — the Terraform content-filter guardrail
# (``rai-healthcare-demo-guardrail``, no Automated Reasoning) and this one — and
# list_guardrails does not guarantee ordering, so the substring test can return
# the content-filter guardrail and the agent then runs with no logical-consistency
# checking at all. Same anti-pattern as the exact-match fix in scripts/cleanup.py.
GUARDRAIL_NAME = "rai-healthcare-diagnostic-guardrail"

# The Automated Reasoning policy this script owns, also matched exactly. Reusing
# "the first policy in the account" attached whatever unrelated policy happened to
# be listed first.
AR_POLICY_NAME = "healthcare-diagnostic-rules"
RULES_PATH = os.path.join(os.path.dirname(__file__), "clinical_rules.txt")

# Guidance for rule extraction, passed as the document description
# (create-automated-reasoning-policy.html, "Write effective instructions").
RULES_INSTRUCTIONS = (
    "This policy validates a clinical decision-support assistant that returns a primary diagnosis, "
    "a confidence score, a priority and recommended actions for a patient with chest pain. "
    "Focus on medication safety, confidence thresholds for mandatory physician review, and "
    "diagnostic criteria."
)
BUILD_POLL_SECONDS = 15
BUILD_TIMEOUT_SECONDS = 1800

# Shape of a VERSIONED Automated Reasoning policy ARN. The numeric version suffix
# is mandatory: the base ARN resolves to the mutable DRAFT, and a guardrail cannot
# attach a DRAFT. Mirrors the pattern the service documents for policyArn:
# arn:aws(-[^:]+)?:bedrock:<region>:<account>:automated-reasoning-policy/<id>(:<version>)?
# with version ([1-9][0-9]{0,11}).
_VERSIONED_AR_POLICY_ARN = re.compile(
    r"^arn:aws[a-z0-9-]*:bedrock:[a-z0-9-]+:[0-9]{12}"
    r":automated-reasoning-policy/(?P<policy_id>[a-z0-9]+)"
    r":(?P<version>[1-9][0-9]*)$"
)


def _versioned_ar_policy_parts(policy_arn: str) -> tuple[str, str]:
    """Split a versioned AR policy ARN into ``(policy_id, numeric_version)``.

    Raises ValueError naming the offending value when the ARN is not of the form
    ``arn:…:automated-reasoning-policy/<id>:<version>``. The previous parse,
    ``policy_arn.split(":automated-reasoning-policy/", 1)[-1]`` followed by
    ``partition(":")``, produced wrong values in silence: str.split returns the
    whole string when the separator is absent, so partition then split on the ARN
    scheme colon and yielded ``("arn", "aws:bedrock:…")``. Those were printed as
    ``export AR_POLICY_ID`` / ``export AR_POLICY_VERSION`` with no error and ended
    up as a malformed IAM resource ARN in the agent CDK.
    """
    if not isinstance(policy_arn, str):
        raise ValueError(f"Automated Reasoning policy ARN must be a string, got {type(policy_arn).__name__}")

    match = _VERSIONED_AR_POLICY_ARN.match(policy_arn)
    if match is None:
        raise ValueError(
            f"Not a versioned Automated Reasoning policy ARN: '{policy_arn}'. Expected "
            "arn:aws:bedrock:<region>:<account>:automated-reasoning-policy/<id>:<version> "
            "with a numeric version. An unversioned base ARN resolves to the mutable "
            "DRAFT, which a guardrail cannot attach."
        )

    return match.group("policy_id"), match.group("version")


def _find_guardrail_by_name(guardrails, name):
    """Return the guardrail summary whose name equals ``name`` exactly, else None."""
    for guardrail in guardrails:
        if guardrail.get("name") == name:
            return guardrail
    return None


def _publish_policy_version(bedrock, base_arn: str) -> str:
    """Publish an immutable numeric version of ``base_arn`` and return its ARN.

    The single call site for create_automated_reasoning_policy_version, so the
    "reuse an existing policy" and "create a new policy" paths cannot drift apart
    again. The API takes ``policyArn`` plus ``lastUpdatedDefinitionHash`` (a
    REQUIRED concurrency token) and returns the versioned ARN under ``policyArn``.
    There is no ``automatedReasoningPolicyIdentifier`` parameter and no
    ``automatedReasoningPolicyArn`` response field; reading either raised a
    KeyError / ParamValidationError that the broad handler below used to swallow,
    leaving policy_arn = None and skipping guardrail creation entirely.

    https://docs.aws.amazon.com/bedrock/latest/APIReference/API_CreateAutomatedReasoningPolicyVersion.html
    https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock/client/create_automated_reasoning_policy_version.html
    """
    draft = bedrock.get_automated_reasoning_policy(policyArn=base_arn)
    version_response = bedrock.create_automated_reasoning_policy_version(
        policyArn=base_arn,
        lastUpdatedDefinitionHash=draft["definitionHash"],
    )
    return version_response["policyArn"]


def _print_exports(guardrail_id, guardrail_version, ar_policy_id, ar_policy_version, guardrail_arn):
    """Print the environment block the agent deployment needs.

    AR_POLICY_ID / AR_POLICY_VERSION come from the versioned AR policy ARN and let
    the agent CDK build an exact, wildcard-free InvokeAutomatedReasoningPolicy
    resource ARN (mirrors GUARDRAIL_ID / GUARDRAIL_VERSION).
    """
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print(f'  ║  export GUARDRAIL_ID="{guardrail_id}"')
    print(f'  ║  export GUARDRAIL_VERSION="{guardrail_version}"')
    print(f'  ║  export AR_POLICY_ID="{ar_policy_id}"')
    print(f'  ║  export AR_POLICY_VERSION="{ar_policy_version}"')
    print(f"  ║  Guardrail ARN: {guardrail_arn}")
    print("  ╚══════════════════════════════════════════════════════╝")
    print()
    print("  Set these in your shell (or .env) before `agentcore deploy`.")


def _paged(call, key, **kwargs):
    """Every item of a nextToken-paginated Bedrock list call."""
    items, token = [], None
    while True:
        response = call(**kwargs, **({"nextToken": token} if token else {}))
        items.extend(response.get(key, []))
        token = response.get("nextToken")
        if not token:
            return items


def _build_policy_from_rules(bedrock, base_arn: str) -> None:
    """Extract rules from clinical_rules.txt into the policy DRAFT.

    CreateAutomatedReasoningPolicy only creates the empty resource; the rules come
    from a StartAutomatedReasoningPolicyBuildWorkflow INGEST_CONTENT run on the
    source document. A completed build updates the DRAFT; if it ever has not, the
    extracted POLICY_DEFINITION asset is applied with UpdateAutomatedReasoningPolicy.
    """
    with open(RULES_PATH, encoding="utf-8") as f:
        rules_text = f.read()

    workflow_id = bedrock.start_automated_reasoning_policy_build_workflow(
        policyArn=base_arn,
        buildWorkflowType="INGEST_CONTENT",
        sourceContent={
            "policyDefinition": {"version": "1.0", "types": [], "rules": [], "variables": []},
            "workflowContent": {
                "documents": [
                    {
                        "document": rules_text.encode("utf-8"),
                        "documentContentType": "txt",
                        "documentName": "Clinical rules",
                        "documentDescription": RULES_INSTRUCTIONS,
                    }
                ]
            },
        },
    )["buildWorkflowId"]
    logger.info(f"Build workflow {workflow_id} started; extracting rules (this takes a few minutes)...")

    deadline = time.monotonic() + BUILD_TIMEOUT_SECONDS
    while True:
        status = bedrock.get_automated_reasoning_policy_build_workflow(policyArn=base_arn, buildWorkflowId=workflow_id)[
            "status"
        ]
        if status == "COMPLETED":
            break
        if status in ("FAILED", "CANCELLED", "CANCEL_REQUESTED"):
            raise RuntimeError(f"Automated Reasoning build workflow {workflow_id} ended {status}")
        if time.monotonic() > deadline:
            raise RuntimeError(f"Automated Reasoning build workflow {workflow_id} still {status} after timeout")
        time.sleep(BUILD_POLL_SECONDS)

    if bedrock.export_automated_reasoning_policy_version(policyArn=base_arn)["policyDefinition"].get("rules"):
        return
    definition = bedrock.get_automated_reasoning_policy_build_workflow_result_assets(
        policyArn=base_arn, buildWorkflowId=workflow_id, assetType="POLICY_DEFINITION"
    )["buildWorkflowAssets"]["policyDefinition"]
    if not definition.get("rules"):
        raise RuntimeError(f"Build workflow {workflow_id} extracted no rules from {RULES_PATH}")
    bedrock.update_automated_reasoning_policy(policyArn=base_arn, policyDefinition=definition)


def _policy_version_for_guardrail(bedrock) -> str:
    """Versioned ARN of the AR_POLICY_NAME policy, creating and building it if needed."""
    policies = _paged(bedrock.list_automated_reasoning_policies, "automatedReasoningPolicySummaries")
    existing = next((p for p in policies if p.get("name") == AR_POLICY_NAME), None)
    if existing:
        base_arn = existing["policyArn"]
        logger.info(f"Reusing Automated Reasoning policy '{AR_POLICY_NAME}': {base_arn}")
    else:
        base_arn = bedrock.create_automated_reasoning_policy(
            name=AR_POLICY_NAME,
            description=(
                "Clinical rules for AI-Powered Diagnostic Assistant -"
                " validates logical consistency of diagnostic outputs"
            ),
        )["policyArn"]
        logger.info(f"Created Automated Reasoning policy: {base_arn}")

    # A reused policy whose earlier build never finished has an empty DRAFT.
    if not bedrock.export_automated_reasoning_policy_version(policyArn=base_arn)["policyDefinition"].get("rules"):
        _build_policy_from_rules(bedrock, base_arn)

    policy_arn = _publish_policy_version(bedrock, base_arn)
    logger.info(f"Published policy version for attachment: {policy_arn}")
    return policy_arn


def _report_existing_guardrail(bedrock, summary) -> None:
    """Print the exports for an existing guardrail as it is actually configured.

    Uses its newest numbered version (DRAFT if none) and the Automated Reasoning
    policy version attached to that version, so the agent's IAM grant matches
    what the guardrail really evaluates.
    """
    versions = _paged(bedrock.list_guardrails, "guardrails", guardrailIdentifier=summary["id"])
    numbered = sorted((int(v["version"]) for v in versions if v.get("version", "").isdigit()), reverse=True)
    version = str(numbered[0]) if numbered else "DRAFT"
    guardrail = bedrock.get_guardrail(guardrailIdentifier=summary["id"], guardrailVersion=version)
    policies = guardrail.get("automatedReasoningPolicy", {}).get("policies", [])
    if len(policies) != 1:
        logger.error(
            f"Guardrail '{GUARDRAIL_NAME}' version {version} has {len(policies)} Automated Reasoning "
            "policies attached; expected exactly one. Fix it in the Bedrock console, then re-run."
        )
        raise SystemExit(1)
    ar_policy_id, ar_policy_version = _versioned_ar_policy_parts(policies[0])
    logger.info(f"Guardrail '{GUARDRAIL_NAME}' already exists (ID: {summary['id']}, version {version})")
    _print_exports(summary["id"], version, ar_policy_id, ar_policy_version, summary["arn"])


def main():
    # Deliberately NOT the full validate_config(): it requires GUARDRAIL_ID, which
    # is this script's OUTPUT. The full check made the script exit before doing
    # anything on a fresh deployment; it only ever ran because .env already carried
    # a GUARDRAIL_ID from the Terraform content-filter guardrail.
    if not validate_config(require_guardrail_id=False):
        raise SystemExit(1)

    print("=" * 60)
    print("Automated Reasoning — Guardrail Setup")
    print("=" * 60)
    print()

    bedrock = boto3.client("bedrock", region_name=REGION, config=BOTO_CONFIG)

    existing = _find_guardrail_by_name(_paged(bedrock.list_guardrails, "guardrails"), GUARDRAIL_NAME)
    if existing:
        _report_existing_guardrail(bedrock, existing)
        return

    try:
        policy_arn = _policy_version_for_guardrail(bedrock)
        # Validate the ARN BEFORE creating anything: a malformed or unversioned ARN
        # must not reach create_guardrail and must never be printed as an export.
        ar_policy_id, ar_policy_version = _versioned_ar_policy_parts(policy_arn)
    except KeyError:
        # A KeyError means this script read a response field the API does not return.
        logger.error("Unexpected Bedrock response shape — a response field this script reads is missing.")
        raise
    except Exception as e:
        logger.error(f"Could not prepare the Automated Reasoning policy ({type(e).__name__}): {e}")
        raise SystemExit(1) from e

    logger.info("Creating Bedrock Guardrail...")
    try:
        response = bedrock.create_guardrail(
            name=GUARDRAIL_NAME,
            description=(
                "Validates AI diagnostic outputs against clinical rules using"
                " Automated Reasoning. Ensures logical consistency, medication"
                " safety, and confidence thresholds."
            ),
            automatedReasoningPolicyConfig={"policies": [policy_arn]},
            # Same content filters as infra/security.tf so the agent's guardrail
            # blocks prompt attacks as well as running Automated Reasoning checks.
            contentPolicyConfig={
                "filtersConfig": [
                    {"type": "HATE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
                    {"type": "VIOLENCE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
                    {"type": "SEXUAL", "inputStrength": "HIGH", "outputStrength": "HIGH"},
                    {"type": "MISCONDUCT", "inputStrength": "HIGH", "outputStrength": "HIGH"},
                    {"type": "INSULTS", "inputStrength": "MEDIUM", "outputStrength": "MEDIUM"},
                    {"type": "PROMPT_ATTACK", "inputStrength": "HIGH", "outputStrength": "NONE"},
                ]
            },
            crossRegionConfig={"guardrailProfileIdentifier": "us.guardrail.v1:0"},
            blockedInputMessaging="This request cannot be processed by the diagnostic system.",
            blockedOutputsMessaging="The diagnostic output failed validation against clinical rules.",
        )
    except bedrock.exceptions.ConflictException:
        # Created concurrently since the lookup above.
        existing = _find_guardrail_by_name(_paged(bedrock.list_guardrails, "guardrails"), GUARDRAIL_NAME)
        if existing is None:
            logger.error(
                f"create_guardrail reported a conflict but list_guardrails returned no "
                f"guardrail named exactly '{GUARDRAIL_NAME}'. Resolve the name conflict "
                f"in the Bedrock console, then re-run."
            )
            raise SystemExit(1) from None
        _report_existing_guardrail(bedrock, existing)
        return
    except Exception as e:
        logger.error(f"Error creating guardrail ({type(e).__name__}): {e}")
        raise SystemExit(1) from e

    # An immutable guardrail version, so later DRAFT edits never change what the
    # deployed agent enforces.
    version = bedrock.create_guardrail_version(
        guardrailIdentifier=response["guardrailId"],
        description=f"Automated Reasoning policy {ar_policy_id} version {ar_policy_version}",
    )["version"]
    logger.info(f"Guardrail created and version {version} published.")
    _print_exports(response["guardrailId"], version, ar_policy_id, ar_policy_version, response["guardrailArn"])

    print()
    print("=" * 60)
    print("Setup complete. Next: deploy the agent with these exports.")
    print("=" * 60)


if __name__ == "__main__":
    main()
