# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Tests for scripts/setup_guardrail.py.

This script creates the Automated Reasoning guardrail the agent runtime attaches.
Every bug it carried was silent: a wrong response key, a wrong parameter name, an
ARN parse that produced plausible-looking garbage, and a substring guardrail match
that could return the unrelated content-filter guardrail. Each test below pins one
of those.

No AWS calls are made. The boto3 client is replaced by a recording fake whose
responses contain exactly the fields the Bedrock API documents, so a caller reading
a field the API does not return raises KeyError here just as it would in production.
Recorded request parameters are additionally validated against botocore's own
service model, so a wrong parameter name fails the test rather than reaching AWS.

All identifiers below are obviously fake placeholders.
"""

import importlib.util
import os
import sys

import botocore.session
import pytest
from botocore.validate import ParamValidator

import config

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO_ROOT)

_SETUP_PATH = os.path.join(_REPO_ROOT, "scripts", "setup_guardrail.py")
_spec = importlib.util.spec_from_file_location("rai_setup_guardrail_under_test", _SETUP_PATH)
setup_guardrail = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup_guardrail)

# ---------------------------------------------------------------------------
# Fake placeholders — not real AWS identifiers
# ---------------------------------------------------------------------------
FAKE_ACCOUNT = "000000000000"
FAKE_REGION = "us-east-1"
FAKE_POLICY_ID = "fakearpolicy"
FAKE_BUCKET = "fake-bucket-for-tests"
DEFINITION_HASH = "0" * 128

BASE_POLICY_ARN = f"arn:aws:bedrock:{FAKE_REGION}:{FAKE_ACCOUNT}:automated-reasoning-policy/{FAKE_POLICY_ID}"
VERSIONED_POLICY_ARN = f"{BASE_POLICY_ARN}:7"

DIAGNOSTIC_GUARDRAIL_NAME = "rai-healthcare-diagnostic-guardrail"
DEMO_GUARDRAIL_NAME = "rai-healthcare-demo-guardrail"
FAKE_DIAGNOSTIC_GUARDRAIL_ID = "fakediagid001"
FAKE_DEMO_GUARDRAIL_ID = "fakedemoid002"


def _guardrail_arn(guardrail_id):
    return f"arn:aws:bedrock:{FAKE_REGION}:{FAKE_ACCOUNT}:guardrail/{guardrail_id}"


AR_POLICY_NAME = "healthcare-diagnostic-rules"
FAKE_WORKFLOW_ID = "00000000-0000-0000-0000-000000000000"
FAKE_DEFINITION = {
    "version": "1.0",
    "types": [],
    "rules": [{"id": "FAKERULE0001", "expression": "(=> isLowConfidence requiresPhysicianReview)"}],
    "variables": [
        {"name": "isLowConfidence", "type": "BOOL", "description": "Confidence is below the threshold."},
        {"name": "requiresPhysicianReview", "type": "BOOL", "description": "A physician must review."},
    ],
}
EMPTY_DEFINITION = {"version": "1.0", "types": [], "rules": [], "variables": []}


def _policy_summary(name=AR_POLICY_NAME):
    """A list_automated_reasoning_policies summary — base ARN under "policyArn"."""
    return {
        "policyArn": BASE_POLICY_ARN,
        "name": name,
        "version": "DRAFT",
        "policyId": FAKE_POLICY_ID,
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
    }


def _guardrail_summary(name, guardrail_id):
    return {
        "id": guardrail_id,
        "arn": _guardrail_arn(guardrail_id),
        "status": "READY",
        "name": name,
        "version": "DRAFT",
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# Recording fake Bedrock client
# ---------------------------------------------------------------------------
class _FakeBedrock:
    """Stand-in for the boto3 ``bedrock`` client. Records calls, contacts nothing.

    Responses carry only the fields the Bedrock API documents. In particular
    ``create_automated_reasoning_policy`` and
    ``create_automated_reasoning_policy_version`` return ``policyArn`` and have no
    ``automatedReasoningPolicyArn`` / ``automatedReasoningPolicyId`` field, so code
    reading those raises KeyError here exactly as it does against AWS.
    """

    class exceptions:
        class ConflictException(Exception):
            pass

    def __init__(
        self,
        policies,
        guardrails=None,
        conflict=False,
        version_arn=VERSIONED_POLICY_ARN,
        build_status="COMPLETED",
        build_updates_draft=True,
        attached=None,
    ):
        self.policies = policies
        self.guardrails = guardrails if guardrails is not None else []
        self.conflict = conflict
        self.version_arn = version_arn
        self.build_status = build_status
        self.build_updates_draft = build_updates_draft
        # A reused policy already has rules; a new one is empty until built.
        self.draft = FAKE_DEFINITION if policies else EMPTY_DEFINITION
        self.attached = attached if attached is not None else [VERSIONED_POLICY_ARN]
        self.calls = []

    def kwargs_for(self, operation):
        return [kwargs for name, kwargs in self.calls if name == operation]

    def _record(self, operation, kwargs):
        self.calls.append((operation, kwargs))

    def list_automated_reasoning_policies(self, **kwargs):
        self._record("list_automated_reasoning_policies", kwargs)
        return {"automatedReasoningPolicySummaries": self.policies}

    def create_automated_reasoning_policy(self, **kwargs):
        self._record("create_automated_reasoning_policy", kwargs)
        return {
            "policyArn": BASE_POLICY_ARN,
            "version": "DRAFT",
            "name": kwargs.get("name", ""),
            "definitionHash": DEFINITION_HASH,
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
        }

    def get_automated_reasoning_policy(self, **kwargs):
        self._record("get_automated_reasoning_policy", kwargs)
        return {
            "policyArn": kwargs["policyArn"],
            "name": "fake-healthcare-diagnostic-rules",
            "version": "DRAFT",
            "policyId": FAKE_POLICY_ID,
            "definitionHash": DEFINITION_HASH,
            "updatedAt": "2024-01-01T00:00:00Z",
        }

    def create_automated_reasoning_policy_version(self, **kwargs):
        self._record("create_automated_reasoning_policy_version", kwargs)
        return {
            "policyArn": self.version_arn,
            "version": "7",
            "name": "fake-healthcare-diagnostic-rules",
            "definitionHash": DEFINITION_HASH,
            "createdAt": "2024-01-01T00:00:00Z",
        }

    def export_automated_reasoning_policy_version(self, **kwargs):
        self._record("export_automated_reasoning_policy_version", kwargs)
        return {"policyDefinition": self.draft}

    def start_automated_reasoning_policy_build_workflow(self, **kwargs):
        self._record("start_automated_reasoning_policy_build_workflow", kwargs)
        return {"policyArn": kwargs["policyArn"], "buildWorkflowId": FAKE_WORKFLOW_ID}

    def get_automated_reasoning_policy_build_workflow(self, **kwargs):
        self._record("get_automated_reasoning_policy_build_workflow", kwargs)
        if self.build_status == "COMPLETED" and self.build_updates_draft:
            self.draft = FAKE_DEFINITION
        return {
            "policyArn": kwargs["policyArn"],
            "buildWorkflowId": kwargs["buildWorkflowId"],
            "status": self.build_status,
            "buildWorkflowType": "INGEST_CONTENT",
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
        }

    def get_automated_reasoning_policy_build_workflow_result_assets(self, **kwargs):
        self._record("get_automated_reasoning_policy_build_workflow_result_assets", kwargs)
        return {
            "policyArn": kwargs["policyArn"],
            "buildWorkflowId": kwargs["buildWorkflowId"],
            "buildWorkflowAssets": {"policyDefinition": FAKE_DEFINITION},
        }

    def update_automated_reasoning_policy(self, **kwargs):
        self._record("update_automated_reasoning_policy", kwargs)
        self.draft = kwargs["policyDefinition"]
        return {"policyArn": kwargs["policyArn"], "name": AR_POLICY_NAME, "definitionHash": DEFINITION_HASH}

    def create_guardrail_version(self, **kwargs):
        self._record("create_guardrail_version", kwargs)
        return {"guardrailId": kwargs["guardrailIdentifier"], "version": "1"}

    def get_guardrail(self, **kwargs):
        self._record("get_guardrail", kwargs)
        return {
            "guardrailId": kwargs["guardrailIdentifier"],
            "version": kwargs.get("guardrailVersion", "DRAFT"),
            "automatedReasoningPolicy": {"policies": self.attached},
        }

    def create_guardrail(self, **kwargs):
        self._record("create_guardrail", kwargs)
        if self.conflict:
            raise self.exceptions.ConflictException("guardrail already exists")
        return {
            "guardrailId": FAKE_DIAGNOSTIC_GUARDRAIL_ID,
            "guardrailArn": _guardrail_arn(FAKE_DIAGNOSTIC_GUARDRAIL_ID),
            "version": "DRAFT",
            "createdAt": "2024-01-01T00:00:00Z",
        }

    def list_guardrails(self, **kwargs):
        self._record("list_guardrails", kwargs)
        if "guardrailIdentifier" in kwargs:
            summary = next(g for g in self.guardrails if g["id"] == kwargs["guardrailIdentifier"])
            return {"guardrails": [summary, {**summary, "version": "1"}]}
        guardrails = self.guardrails
        if self.conflict and self.kwargs_for("create_guardrail"):
            return {"guardrails": guardrails}
        # Before create_guardrail, a conflict fake hides the guardrail it will
        # report as existing, so the script reaches the create call.
        return {"guardrails": [] if self.conflict else guardrails}


def _run_main(monkeypatch, client, guardrail_id=""):
    """Run setup_guardrail.main() against the fake client.

    ``guardrail_id`` defaults to empty: a fresh deployment has no GUARDRAIL_ID,
    because this script is what produces it.
    """
    monkeypatch.setattr(config, "AWS_ACCOUNT_ID", FAKE_ACCOUNT)
    monkeypatch.setattr(config, "S3_BUCKET", FAKE_BUCKET)
    monkeypatch.setattr(config, "GUARDRAIL_ID", guardrail_id)
    monkeypatch.setattr(config, "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
    monkeypatch.setattr(setup_guardrail.boto3, "client", lambda *args, **kwargs: client)
    monkeypatch.setattr(setup_guardrail.time, "sleep", lambda *args, **kwargs: None)
    setup_guardrail.main()


# ---------------------------------------------------------------------------
# Bug 1 — the config gate must not require the value this script produces
# ---------------------------------------------------------------------------
class TestConfigGate:
    def test_the_script_runs_when_guardrail_id_is_unset(self, monkeypatch):
        """Regression: main() called the full validate_config(), which fails when
        GUARDRAIL_ID is missing — and GUARDRAIL_ID is this script's output. On a
        fresh deployment the script exited before creating anything; it only ever
        worked because .env already carried an id from the Terraform guardrail.
        """
        client = _FakeBedrock(policies=[_policy_summary()])
        _run_main(monkeypatch, client, guardrail_id="")
        assert client.kwargs_for("create_guardrail"), "script exited before creating the guardrail"

    def test_validate_config_still_requires_guardrail_id_for_every_other_caller(self, monkeypatch):
        """The other 16 callers consume GUARDRAIL_ID and must keep failing without it."""
        monkeypatch.setattr(config, "AWS_ACCOUNT_ID", FAKE_ACCOUNT)
        monkeypatch.setattr(config, "S3_BUCKET", FAKE_BUCKET)
        monkeypatch.setattr(config, "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
        monkeypatch.setattr(config, "GUARDRAIL_ID", "")

        assert config.validate_config() is False
        assert config.validate_config(require_guardrail_id=False) is True

    def test_the_lighter_check_still_requires_account_and_bucket(self, monkeypatch):
        monkeypatch.setattr(config, "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
        monkeypatch.setattr(config, "GUARDRAIL_ID", "")
        monkeypatch.setattr(config, "S3_BUCKET", FAKE_BUCKET)
        monkeypatch.setattr(config, "AWS_ACCOUNT_ID", "")
        assert config.validate_config(require_guardrail_id=False) is False

        monkeypatch.setattr(config, "AWS_ACCOUNT_ID", FAKE_ACCOUNT)
        monkeypatch.setattr(config, "S3_BUCKET", "")
        assert config.validate_config(require_guardrail_id=False) is False

        monkeypatch.setattr(config, "S3_BUCKET", FAKE_BUCKET)
        monkeypatch.setattr(config, "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
        assert config.validate_config(require_guardrail_id=False) is False


# ---------------------------------------------------------------------------
# Bugs 2 and 3 — one API, one parameter name, one response key
# ---------------------------------------------------------------------------
_VERSION_OP = (
    botocore.session.get_session().get_service_model("bedrock").operation_model("CreateAutomatedReasoningPolicyVersion")
)


class TestPolicyVersionCall:
    def test_both_branches_call_the_version_api_identically(self, monkeypatch):
        """Regression: the reuse branch passed policyArn and read response["policyArn"];
        the new-policy branch passed automatedReasoningPolicyIdentifier and read
        response["automatedReasoningPolicyArn"]. One of the two had to raise, and the
        broad except swallowed it into policy_arn = None.
        """
        reuse = _FakeBedrock(policies=[_policy_summary()])
        _run_main(monkeypatch, reuse)

        fresh = _FakeBedrock(policies=[])
        _run_main(monkeypatch, fresh)

        reuse_calls = reuse.kwargs_for("create_automated_reasoning_policy_version")
        fresh_calls = fresh.kwargs_for("create_automated_reasoning_policy_version")

        assert len(reuse_calls) == 1
        assert len(fresh_calls) == 1
        assert set(reuse_calls[0]) == set(fresh_calls[0]) == {"policyArn", "lastUpdatedDefinitionHash"}

    def test_version_request_parameters_are_valid_against_the_real_api_model(self, monkeypatch):
        """Validate the recorded kwargs against botocore's own input shape.

        create_automated_reasoning_policy_version takes policyArn and a REQUIRED
        lastUpdatedDefinitionHash concurrency token. automatedReasoningPolicyIdentifier
        and description are not parameters of this API.
        https://docs.aws.amazon.com/bedrock/latest/APIReference/API_CreateAutomatedReasoningPolicyVersion.html
        """
        validator = ParamValidator()
        for policies in ([_policy_summary()], []):
            client = _FakeBedrock(policies=policies)
            _run_main(monkeypatch, client)
            calls = client.kwargs_for("create_automated_reasoning_policy_version")
            assert len(calls) == 1
            report = validator.validate(calls[0], _VERSION_OP.input_shape)
            assert not report.has_errors(), report.generate_report()

    def test_the_attached_policy_is_the_versioned_arn_from_the_response(self, monkeypatch):
        """The guardrail must attach the versioned ARN the API returned under
        "policyArn" — not the base ARN, which resolves to the mutable DRAFT.
        """
        for policies in ([_policy_summary()], []):
            client = _FakeBedrock(policies=policies)
            _run_main(monkeypatch, client)
            created = client.kwargs_for("create_guardrail")
            assert len(created) == 1
            assert created[0]["automatedReasoningPolicyConfig"] == {"policies": [VERSIONED_POLICY_ARN]}

    def test_the_version_call_uses_the_definition_hash_as_concurrency_token(self, monkeypatch):
        client = _FakeBedrock(policies=[_policy_summary()])
        _run_main(monkeypatch, client)
        call = client.kwargs_for("create_automated_reasoning_policy_version")[0]
        assert call["lastUpdatedDefinitionHash"] == DEFINITION_HASH


# ---------------------------------------------------------------------------
# Bug 4 — an unparseable ARN must fail loudly, never export garbage
# ---------------------------------------------------------------------------
class TestVersionedPolicyArnParsing:
    def test_a_versioned_arn_splits_into_id_and_version(self):
        assert setup_guardrail._versioned_ar_policy_parts(VERSIONED_POLICY_ARN) == (FAKE_POLICY_ID, "7")

    @pytest.mark.parametrize(
        "malformed",
        [
            BASE_POLICY_ARN,  # no version suffix — resolves to DRAFT, cannot be attached
            f"{BASE_POLICY_ARN}:DRAFT",  # DRAFT is not a numeric version
            f"{BASE_POLICY_ARN}:0",  # versions start at 1
            f"{BASE_POLICY_ARN}:",  # empty version
            _guardrail_arn(FAKE_DIAGNOSTIC_GUARDRAIL_ID),  # a guardrail ARN, not a policy ARN
            "arn:aws:bedrock:us-east-1:000000000000:foundation-model/fake.model",
            "not-an-arn-at-all",
            "",
        ],
    )
    def test_a_malformed_arn_raises_instead_of_returning_garbage(self, malformed):
        """Regression: ``split(":automated-reasoning-policy/", 1)[-1]`` returns the
        WHOLE string when the separator is absent, and ``partition(":")`` then split
        on the scheme colon — yielding ("arn", "aws:bedrock:...") with no error.
        """
        with pytest.raises(ValueError):
            setup_guardrail._versioned_ar_policy_parts(malformed)

    def test_a_non_string_arn_raises(self):
        with pytest.raises(ValueError):
            setup_guardrail._versioned_ar_policy_parts(None)

    @pytest.mark.parametrize("returned_arn", [BASE_POLICY_ARN, "arn:aws:bedrock:us-east-1:000000000000:guardrail/x"])
    def test_main_aborts_without_exporting_anything_when_the_arn_is_unusable(self, monkeypatch, capsys, returned_arn):
        client = _FakeBedrock(policies=[_policy_summary()], version_arn=returned_arn)

        with pytest.raises(SystemExit):
            _run_main(monkeypatch, client)

        out = capsys.readouterr().out
        assert "AR_POLICY_ID" not in out
        assert "AR_POLICY_VERSION" not in out
        assert not client.kwargs_for("create_guardrail"), "a bad policy ARN reached create_guardrail"


# ---------------------------------------------------------------------------
# Bug 5 — exact guardrail name, and an explicit not-found path
# ---------------------------------------------------------------------------
class TestGuardrailSelection:
    def test_the_diagnostic_guardrail_is_selected_regardless_of_list_order(self):
        """Two guardrails contain "healthcare": the Terraform content-filter one and
        this Automated Reasoning one. list_guardrails does not guarantee ordering, so
        a substring match could return the content-filter guardrail and the agent
        would run with no logical-consistency checking.
        """
        demo = _guardrail_summary(DEMO_GUARDRAIL_NAME, FAKE_DEMO_GUARDRAIL_ID)
        diagnostic = _guardrail_summary(DIAGNOSTIC_GUARDRAIL_NAME, FAKE_DIAGNOSTIC_GUARDRAIL_ID)

        for summaries in ([demo, diagnostic], [diagnostic, demo]):
            found = setup_guardrail._find_guardrail_by_name(summaries, setup_guardrail.GUARDRAIL_NAME)
            assert found["id"] == FAKE_DIAGNOSTIC_GUARDRAIL_ID

    @pytest.mark.parametrize(
        "name",
        [
            DEMO_GUARDRAIL_NAME,
            "promptarmor-healthcare-guardrail",
            "rai-healthcare-diagnostic-guardrail-v2",
            "RAI-HEALTHCARE-DIAGNOSTIC-GUARDRAIL",
            "healthcare",
        ],
    )
    def test_a_substring_neighbour_is_never_selected(self, name):
        summaries = [_guardrail_summary(name, FAKE_DEMO_GUARDRAIL_ID)]
        assert setup_guardrail._find_guardrail_by_name(summaries, setup_guardrail.GUARDRAIL_NAME) is None

    def test_an_empty_list_returns_none(self):
        assert setup_guardrail._find_guardrail_by_name([], setup_guardrail.GUARDRAIL_NAME) is None

    def test_a_summary_without_a_name_is_not_selected(self):
        assert setup_guardrail._find_guardrail_by_name([{"id": "fakeid000003"}], setup_guardrail.GUARDRAIL_NAME) is None

    def test_on_conflict_the_content_filter_guardrail_id_is_never_exported(self, monkeypatch, capsys):
        client = _FakeBedrock(
            policies=[_policy_summary()],
            conflict=True,
            guardrails=[
                _guardrail_summary(DEMO_GUARDRAIL_NAME, FAKE_DEMO_GUARDRAIL_ID),
                _guardrail_summary(DIAGNOSTIC_GUARDRAIL_NAME, FAKE_DIAGNOSTIC_GUARDRAIL_ID),
            ],
        )
        _run_main(monkeypatch, client)

        out = capsys.readouterr().out
        assert f'export GUARDRAIL_ID="{FAKE_DIAGNOSTIC_GUARDRAIL_ID}"' in out
        assert FAKE_DEMO_GUARDRAIL_ID not in out

    def test_on_conflict_with_no_exact_match_the_script_exits(self, monkeypatch, capsys):
        """Regression: with no match the loop left guardrail_id unbound or stale and
        the script still reported success.
        """
        client = _FakeBedrock(
            policies=[_policy_summary()],
            conflict=True,
            guardrails=[_guardrail_summary(DEMO_GUARDRAIL_NAME, FAKE_DEMO_GUARDRAIL_ID)],
        )

        with pytest.raises(SystemExit):
            _run_main(monkeypatch, client)

        assert FAKE_DEMO_GUARDRAIL_ID not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# A missing response field must not be swallowed into "no guardrail created"
# ---------------------------------------------------------------------------
class TestResponseShapeFailuresAreLoud:
    def test_a_missing_response_field_propagates(self, monkeypatch):
        """The broad ``except Exception`` used to turn a KeyError — this script
        reading a field the API does not return — into policy_arn = None and a
        silent skip of guardrail creation. That is how bugs 2 and 3 stayed hidden.
        """
        client = _FakeBedrock(policies=[_policy_summary()])
        monkeypatch.setattr(
            client,
            "get_automated_reasoning_policy",
            lambda **kwargs: {"policyArn": kwargs["policyArn"]},  # no definitionHash
        )

        with pytest.raises(KeyError):
            _run_main(monkeypatch, client)

        assert not client.kwargs_for("create_guardrail")


# ---------------------------------------------------------------------------
# Fresh account: the policy is built with the documented build-workflow API
# ---------------------------------------------------------------------------
_BEDROCK_MODEL = botocore.session.get_session().get_service_model("bedrock")


def _pascal(operation):
    return "".join(part.capitalize() for part in operation.split("_"))


class TestFreshAccountPolicyBuild:
    def test_every_request_is_valid_against_the_real_api_model(self, monkeypatch):
        """Regression: create_automated_reasoning_policy was called with sourceDocuments,
        which the API does not accept, so a fresh account never got a guardrail.
        """
        client = _FakeBedrock(policies=[])
        _run_main(monkeypatch, client)
        validator = ParamValidator()
        for operation, kwargs in client.calls:
            report = validator.validate(kwargs, _BEDROCK_MODEL.operation_model(_pascal(operation)).input_shape)
            assert not report.has_errors(), f"{operation}: {report.generate_report()}"

    def test_rules_come_from_an_ingest_content_build_of_clinical_rules(self, monkeypatch):
        client = _FakeBedrock(policies=[])
        _run_main(monkeypatch, client)
        assert client.kwargs_for("create_automated_reasoning_policy")[0]["name"] == AR_POLICY_NAME
        (build,) = client.kwargs_for("start_automated_reasoning_policy_build_workflow")
        assert build["buildWorkflowType"] == "INGEST_CONTENT"
        (document,) = build["sourceContent"]["workflowContent"]["documents"]
        with open(setup_guardrail.RULES_PATH, "rb") as f:
            assert document["document"] == f.read()
        assert document["documentContentType"] == "txt"

    def test_the_extracted_definition_is_applied_if_the_build_left_the_draft_empty(self, monkeypatch):
        client = _FakeBedrock(policies=[], build_updates_draft=False)
        _run_main(monkeypatch, client)
        (update,) = client.kwargs_for("update_automated_reasoning_policy")
        assert update["policyDefinition"] == FAKE_DEFINITION
        assert client.kwargs_for("create_guardrail")

    @pytest.mark.parametrize("status", ["FAILED", "CANCELLED"])
    def test_a_failed_build_exits_non_zero_and_creates_no_guardrail(self, monkeypatch, status):
        """Regression: every failure used to print "Setup complete" and exit 0."""
        client = _FakeBedrock(policies=[], build_status=status)
        with pytest.raises(SystemExit) as exc:
            _run_main(monkeypatch, client)
        assert exc.value.code == 1
        assert not client.kwargs_for("create_guardrail")

    def test_a_guardrail_create_error_exits_non_zero(self, monkeypatch):
        client = _FakeBedrock(policies=[])

        def boom(**kwargs):
            raise RuntimeError("AccessDenied")

        monkeypatch.setattr(client, "create_guardrail", boom)
        with pytest.raises(SystemExit) as exc:
            _run_main(monkeypatch, client)
        assert exc.value.code == 1

    def test_an_immutable_guardrail_version_is_published_and_exported(self, monkeypatch, capsys):
        client = _FakeBedrock(policies=[])
        _run_main(monkeypatch, client)
        assert client.kwargs_for("create_guardrail_version")
        assert 'export GUARDRAIL_VERSION="1"' in capsys.readouterr().out


class TestPolicySelection:
    def test_an_unrelated_policy_listed_first_is_not_reused(self, monkeypatch):
        """Regression: the script reused whichever policy the account listed first."""
        client = _FakeBedrock(policies=[_policy_summary(name="some-other-teams-policy")])
        client.draft = EMPTY_DEFINITION
        _run_main(monkeypatch, client)
        assert client.kwargs_for("create_automated_reasoning_policy")

    def test_the_policy_with_the_exact_name_is_reused_without_a_rebuild(self, monkeypatch):
        client = _FakeBedrock(policies=[_policy_summary(name="other"), _policy_summary()])
        _run_main(monkeypatch, client)
        assert not client.kwargs_for("create_automated_reasoning_policy")
        assert not client.kwargs_for("start_automated_reasoning_policy_build_workflow")


class TestExistingGuardrail:
    def test_reports_the_policy_version_actually_attached(self, monkeypatch, capsys):
        """Regression: on a re-run the script published a NEW policy version and
        exported it, while the existing guardrail still evaluated the old one, so
        the agent's IAM grant named a policy version the guardrail never used.
        """
        attached = f"{BASE_POLICY_ARN}:3"
        client = _FakeBedrock(
            policies=[_policy_summary()],
            guardrails=[_guardrail_summary(DIAGNOSTIC_GUARDRAIL_NAME, FAKE_DIAGNOSTIC_GUARDRAIL_ID)],
            attached=[attached],
        )
        _run_main(monkeypatch, client)
        out = capsys.readouterr().out
        assert 'export AR_POLICY_VERSION="3"' in out
        assert 'export GUARDRAIL_VERSION="1"' in out
        assert not client.kwargs_for("create_guardrail")
        assert not client.kwargs_for("create_automated_reasoning_policy_version")

    def test_a_guardrail_without_exactly_one_policy_exits(self, monkeypatch):
        client = _FakeBedrock(
            policies=[_policy_summary()],
            guardrails=[_guardrail_summary(DIAGNOSTIC_GUARDRAIL_NAME, FAKE_DIAGNOSTIC_GUARDRAIL_ID)],
            attached=[],
        )
        with pytest.raises(SystemExit):
            _run_main(monkeypatch, client)
