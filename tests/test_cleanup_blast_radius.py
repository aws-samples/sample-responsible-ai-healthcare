# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Blast-radius tests for scripts/cleanup.py.

The teardown script runs against an AWS account that may host other projects. These
tests pin the ownership predicates so a resource belonging to someone else can never
be selected for deletion. Every "foreign" fixture below is modelled on a real resource
observed in a shared sandbox account.

No AWS calls are made: the predicates under test are pure functions over the shapes the
AWS APIs return.
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO_ROOT)

_CLEANUP_PATH = os.path.join(_REPO_ROOT, "scripts", "cleanup.py")
_spec = importlib.util.spec_from_file_location("rai_cleanup_under_test", _CLEANUP_PATH)
cleanup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cleanup)

PROJECT_VPC = "vpc-0b9bda0228e14d7e0"
FOREIGN_VPC = "vpc-ffffffffffffffffff"
PROJECT_DOMAIN_ID = "d-nfu3afue8vst"
FOREIGN_DOMAIN_ID = "d-afh5domhlsyk"


# ---------------------------------------------------------------------------
# EFS mount targets — must be filtered by VpcId only
# ---------------------------------------------------------------------------


class TestMountTargetSelection:
    def test_only_mount_targets_in_the_project_vpc_are_selected(self):
        mount_targets = [
            {"MountTargetId": "fsmt-project", "VpcId": PROJECT_VPC, "SubnetId": "subnet-aaa"},
            {"MountTargetId": "fsmt-foreign", "VpcId": FOREIGN_VPC, "SubnetId": "subnet-bbb"},
        ]

        selected = cleanup._mount_targets_in_vpc(mount_targets, PROJECT_VPC)

        assert [mt["MountTargetId"] for mt in selected] == ["fsmt-project"]

    def test_a_foreign_mount_target_with_a_subnet_is_not_selected(self):
        """Regression: `VpcId == vpc_id or SubnetId` was always truthy.

        Every real mount target has a non-empty SubnetId, so the old expression
        selected every mount target in the Region. This fixture is the exact shape
        that used to slip through.
        """
        mount_targets = [{"MountTargetId": "fsmt-foreign", "VpcId": FOREIGN_VPC, "SubnetId": "subnet-bbb"}]

        assert cleanup._mount_targets_in_vpc(mount_targets, PROJECT_VPC) == []

    def test_a_mount_target_with_no_vpc_is_not_selected(self):
        assert cleanup._mount_targets_in_vpc([{"MountTargetId": "fsmt-x", "SubnetId": "subnet-c"}], PROJECT_VPC) == []


# ---------------------------------------------------------------------------
# EFS filesystems — "no name" must not mean "ours"
# ---------------------------------------------------------------------------


class TestFilesystemOwnership:
    def test_filesystem_named_for_the_project_is_owned(self):
        fs = {"FileSystemId": "fs-1", "Name": f"{cleanup.PROJECT_NAME}-scratch"}

        assert cleanup._is_project_filesystem(fs, {PROJECT_DOMAIN_ID}) is True

    def test_filesystem_tagged_with_the_project_studio_domain_is_owned(self):
        fs = {
            "FileSystemId": "fs-2",
            "Name": None,
            "Tags": [
                {
                    "Key": cleanup.SAGEMAKER_EFS_TAG,
                    "Value": f"arn:aws:sagemaker:us-east-1:123456789012:domain/{PROJECT_DOMAIN_ID}",
                }
            ],
        }

        assert cleanup._is_project_filesystem(fs, {PROJECT_DOMAIN_ID}) is True

    def test_unnamed_untagged_filesystem_is_NOT_owned(self):
        """Regression: `fs.get("Name") is None` treated any unnamed filesystem as ours."""
        fs = {"FileSystemId": "fs-3", "Name": None}

        assert cleanup._is_project_filesystem(fs, {PROJECT_DOMAIN_ID}) is False

    def test_filesystem_of_another_studio_domain_is_NOT_owned(self):
        fs = {
            "FileSystemId": "fs-4",
            "Name": None,
            "Tags": [
                {
                    "Key": cleanup.SAGEMAKER_EFS_TAG,
                    "Value": f"arn:aws:sagemaker:us-east-1:123456789012:domain/{FOREIGN_DOMAIN_ID}",
                }
            ],
        }

        assert cleanup._is_project_filesystem(fs, {PROJECT_DOMAIN_ID}) is False

    def test_foreign_named_filesystem_is_NOT_owned(self):
        for name in ("haf-shared-efs", "QuickSetupDomain-20260403T202070", "training-data"):
            fs = {"FileSystemId": "fs-5", "Name": name}
            assert cleanup._is_project_filesystem(fs, {PROJECT_DOMAIN_ID}) is False, name


# ---------------------------------------------------------------------------
# Guardrails — exact names only
# ---------------------------------------------------------------------------


class TestGuardrailOwnership:
    @pytest.mark.parametrize(
        "name",
        ["rai-healthcare-demo-guardrail", "rai-healthcare-diagnostic-guardrail"],
    )
    def test_project_guardrails_are_owned(self, name):
        assert cleanup._is_project_guardrail(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "promptarmor-guardrail",  # real foreign guardrail; "guardrail" contains "rai"
            "my-training-guardrail",  # "training" contains "rai"
            "haf-content-guardrail",
            "healthcare-other-team-guardrail",  # old filter matched on "healthcare"
            "rai-healthcare-demo-guardrail-v2",  # near miss must not match
            "RAI-HEALTHCARE-DEMO-GUARDRAIL",  # case must not match
        ],
    )
    def test_foreign_guardrails_are_NOT_owned(self, name):
        assert cleanup._is_project_guardrail(name) is False


# ---------------------------------------------------------------------------
# Knowledge Bases — exact names only
# ---------------------------------------------------------------------------


class TestKnowledgeBaseOwnership:
    @pytest.mark.parametrize(
        "name",
        ["rai-healthcare-clinical-guidelines", "rai-healthcare-clinical-guidelines-v2"],
    )
    def test_project_knowledge_bases_are_owned(self, name):
        assert cleanup._is_project_knowledge_base(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "haf-clinical-kb",
            "training-corpus",  # contains "rai"
            "another-healthcare-kb",  # old filter matched on "healthcare"
            "rai-healthcare-clinical-guidelines-v3",
        ],
    )
    def test_foreign_knowledge_bases_are_NOT_owned(self, name):
        assert cleanup._is_project_knowledge_base(name) is False


# ---------------------------------------------------------------------------
# The ownership constants themselves
# ---------------------------------------------------------------------------


class TestOwnershipConstants:
    def test_no_predicate_relies_on_a_bare_substring_of_rai(self):
        """ "rai" alone matches "guardrail" and "training" — it must never be a filter."""
        assert "rai" not in cleanup.PROJECT_GUARDRAIL_NAMES
        assert "rai" not in cleanup.PROJECT_KNOWLEDGE_BASE_NAMES
        assert cleanup.SAGEMAKER_DOMAIN_NAME == f"{cleanup.PROJECT_NAME}-studio"

    def test_cleanup_module_never_shells_out(self):
        """The teardown script is boto3-only; CLI steps live in the Makefile."""
        with open(_CLEANUP_PATH) as fh:
            source = fh.read()
        assert "import subprocess" not in source
        assert "shell=True" not in source


class TestNetworkFirewallOwnership:
    def test_constant_is_the_exact_terraform_name(self):
        # infra/vpc.tf: name = "${var.project_name}-egress"
        assert cleanup.NETWORK_FIREWALL_NAME == "rai-healthcare-demo-egress"

    def test_project_firewall_is_owned(self):
        assert cleanup._is_project_firewall("rai-healthcare-demo-egress")

    @pytest.mark.parametrize(
        "name",
        ["rai-healthcare-demo-egress-2", "other-team-egress", "rai-healthcare-demo", "egress", ""],
    )
    def test_any_other_firewall_is_not_owned(self, name):
        assert not cleanup._is_project_firewall(name)

    def test_turns_off_delete_protection_with_the_update_token(self, monkeypatch):
        nfw = MagicMock()
        nfw.describe_firewall.return_value = {
            "UpdateToken": "token-1",
            "Firewall": {"FirewallName": "rai-healthcare-demo-egress", "DeleteProtection": True},
        }
        monkeypatch.setattr(cleanup.boto3, "client", lambda *a, **k: nfw)
        cleanup.disable_firewall_delete_protection()
        nfw.update_firewall_delete_protection.assert_called_once_with(
            FirewallName="rai-healthcare-demo-egress", DeleteProtection=False, UpdateToken="token-1"
        )

    def test_already_off_makes_no_change(self, monkeypatch):
        nfw = MagicMock()
        nfw.describe_firewall.return_value = {
            "UpdateToken": "t",
            "Firewall": {"FirewallName": "rai-healthcare-demo-egress", "DeleteProtection": False},
        }
        monkeypatch.setattr(cleanup.boto3, "client", lambda *a, **k: nfw)
        cleanup.disable_firewall_delete_protection()
        nfw.update_firewall_delete_protection.assert_not_called()

    def test_missing_firewall_is_not_an_error(self, monkeypatch):
        nfw = MagicMock()
        nfw.describe_firewall.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "gone"}}, "DescribeFirewall"
        )
        monkeypatch.setattr(cleanup.boto3, "client", lambda *a, **k: nfw)
        cleanup.disable_firewall_delete_protection()
        nfw.update_firewall_delete_protection.assert_not_called()


class TestVerifyRoleListing:
    def test_project_roles_are_found_on_any_page(self):
        pages = [[{"RoleName": "other-team-role"}] * 100, [{"RoleName": "rai-healthcare-demo-lambda-role"}]]
        assert cleanup._project_role_names(r for page in pages for r in page) == ["rai-healthcare-demo-lambda-role"]

    def test_foreign_roles_are_not_listed(self):
        assert cleanup._project_role_names([{"RoleName": "haf-useast1-dev-role"}, {"RoleName": "rai-other"}]) == []


class TestMlflowAndJobOwnership:
    def test_constants_match_the_scripts_that_create_them(self):
        assert cleanup.MLFLOW_APP_NAME == "rai-healthcare-demo-mlflow"
        assert cleanup.BIAS_JOB_PREFIX == "rai-healthcare-demo-bias-"
        assert cleanup.EVAL_JOB_PREFIX == "rai-healthcare-demo-eval-"

    def test_only_the_exact_mlflow_app_is_selected(self):
        apps = [
            {"Arn": "arn:own", "Name": "rai-healthcare-demo-mlflow", "Status": "Created"},
            {"Arn": "arn:x1", "Name": "rai-healthcare-demo-mlflow-2", "Status": "Created"},
            {"Arn": "arn:x2", "Name": "other-team-mlflow", "Status": "Created"},
            {"Arn": "arn:x3", "Name": "rai-healthcare-demo-mlflow", "Status": "Deleting"},
        ]
        assert cleanup._project_mlflow_apps(apps) == ["arn:own"]

    @pytest.mark.parametrize(
        "name", ["other-rai-healthcare-demo-eval-1", "rai-healthcare-demo-evaluation", "my-rai-healthcare-demo-bias-1"]
    )
    def test_jobs_need_the_exact_prefix(self, name):
        assert cleanup._project_jobs([name], cleanup.EVAL_JOB_PREFIX) == []
        assert cleanup._project_jobs([name], cleanup.BIAS_JOB_PREFIX) == []

    def test_evaluation_jobs_are_batch_deleted_25_at_a_time(self, monkeypatch):
        bedrock = MagicMock()
        own = [
            {"jobArn": f"arn:{i}", "jobName": f"rai-healthcare-demo-eval-{i}", "status": "Completed"} for i in range(30)
        ]
        bedrock.list_evaluation_jobs.return_value = {
            "jobSummaries": own
            + [
                {"jobArn": "arn:run", "jobName": "rai-healthcare-demo-eval-run", "status": "InProgress"},
                {"jobArn": "arn:foreign", "jobName": "xrai-healthcare-demo-eval-1", "status": "Completed"},
            ]
        }
        bedrock.batch_delete_evaluation_job.return_value = {"errors": []}
        monkeypatch.setattr(cleanup.boto3, "client", lambda *a, **k: bedrock)
        cleanup.delete_evaluation_jobs()
        batches = [c.kwargs["jobIdentifiers"] for c in bedrock.batch_delete_evaluation_job.call_args_list]
        assert [len(b) for b in batches] == [25, 5]
        assert "arn:run" not in sum(batches, []) and "arn:foreign" not in sum(batches, [])

    def test_mlflow_app_delete_waits_until_gone(self, monkeypatch):
        sm = MagicMock()
        sm.list_mlflow_apps.side_effect = [
            {"Summaries": [{"Arn": "arn:own", "Name": "rai-healthcare-demo-mlflow", "Status": "Created"}]},
            {"Summaries": []},
        ]
        monkeypatch.setattr(cleanup.boto3, "client", lambda *a, **k: sm)
        cleanup.delete_mlflow_app(wait_seconds=60)
        sm.delete_mlflow_app.assert_called_once_with(Arn="arn:own")

    def test_evaluation_name_filter_matches_the_api_pattern(self):
        import re

        import boto3

        shape = boto3.client("bedrock", region_name="us-east-1").meta.service_model
        pattern = shape.operation_model("ListEvaluationJobs").input_shape.members["nameContains"].metadata["pattern"]
        assert re.fullmatch(pattern, cleanup.EVAL_NAME_FILTER)
        assert cleanup.EVAL_JOB_PREFIX.startswith(cleanup.EVAL_NAME_FILTER)


# ---------------------------------------------------------------------------
# OpenSearch Serverless vector store (billed per OCU while it exists)
# ---------------------------------------------------------------------------
def _clients(monkeypatch, **by_service):
    monkeypatch.setattr(cleanup.boto3, "client", lambda service, **k: by_service[service])
    monkeypatch.setattr(cleanup.time, "sleep", lambda s: None)


class TestOpenSearchServerlessCleanup:
    def test_names_match_the_deployed_vector_store(self):
        assert cleanup.AOSS_COLLECTION_NAME == "rai-healthcare-demo-kb"
        assert cleanup.AOSS_ACCESS_POLICY_NAME == "rai-healthcare-demo-access"

    def test_collection_and_policies_are_deleted_by_exact_name(self, monkeypatch):
        agent, aoss = MagicMock(), MagicMock()
        agent.list_knowledge_bases.return_value = {"knowledgeBaseSummaries": []}
        aoss.batch_get_collection.side_effect = [
            {"collectionDetails": [{"id": "abc123", "name": "rai-healthcare-demo-kb"}]},
            {"collectionDetails": [{"id": "abc123", "name": "rai-healthcare-demo-kb", "status": "DELETING"}]},
            {"collectionDetails": []},
        ]
        _clients(monkeypatch, **{"bedrock-agent": agent, "opensearchserverless": aoss})
        cleanup.cleanup_opensearch_serverless()
        aoss.batch_get_collection.assert_called_with(names=["rai-healthcare-demo-kb"])
        aoss.delete_collection.assert_called_once_with(id="abc123")
        deleted = {c.kwargs["name"] for c in aoss.delete_security_policy.call_args_list}
        assert deleted == {"rai-healthcare-demo-enc", "rai-healthcare-demo-net"}
        aoss.delete_access_policy.assert_called_once_with(type="data", name="rai-healthcare-demo-access")

    def test_kept_while_a_project_knowledge_base_remains(self, monkeypatch):
        """Deleting the vector store first leaves the Knowledge Base DELETE_UNSUCCESSFUL."""
        agent, aoss = MagicMock(), MagicMock()
        agent.list_knowledge_bases.return_value = {
            "knowledgeBaseSummaries": [{"name": "rai-healthcare-clinical-guidelines-v2", "status": "DELETING"}]
        }
        _clients(monkeypatch, **{"bedrock-agent": agent, "opensearchserverless": aoss})
        cleanup.cleanup_opensearch_serverless()
        aoss.delete_collection.assert_not_called()

    def test_knowledge_base_deletion_is_awaited(self, monkeypatch):
        agent = MagicMock()
        gone = ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "GetKnowledgeBase")
        agent.get_knowledge_base.side_effect = [{"knowledgeBase": {"status": "DELETING"}}, gone]
        monkeypatch.setattr(cleanup.time, "sleep", lambda s: None)
        cleanup._wait_for_knowledge_base_deletion(agent, "KB12345678")
        assert agent.get_knowledge_base.call_count == 2

    def test_a_stuck_knowledge_base_stops_teardown_of_its_store(self, monkeypatch):
        agent = MagicMock()
        agent.get_knowledge_base.return_value = {"knowledgeBase": {"status": "DELETE_UNSUCCESSFUL"}}
        with pytest.raises(RuntimeError, match="DELETE_UNSUCCESSFUL"):
            cleanup._wait_for_knowledge_base_deletion(agent, "KB12345678")


class TestTeardownNeedsNoGuardrail:
    def test_cleanup_does_not_require_guardrail_id(self, monkeypatch):
        """Regression: cleanup called validate_config(), which requires GUARDRAIL_ID, so
        `make full-cleanup` failed in a fresh shell."""
        seen = {}

        def fake_validate(**kwargs):
            seen.update(kwargs)
            return False

        monkeypatch.setattr(cleanup, "validate_config", fake_validate)
        monkeypatch.setattr(sys, "argv", ["cleanup.py", "--phase", "verify"])
        with pytest.raises(SystemExit):
            cleanup.main()
        assert seen == {"require_guardrail_id": False}


class TestKnowledgeBaseTeardownOrder:
    def _agent(self):
        agent = MagicMock()
        agent.list_knowledge_bases.return_value = {
            "knowledgeBaseSummaries": [
                {"knowledgeBaseId": "KB00000001", "name": "rai-healthcare-clinical-guidelines-v2", "status": "ACTIVE"},
                {"knowledgeBaseId": "KB00000002", "name": "someone-elses-kb", "status": "ACTIVE"},
            ]
        }
        agent.get_knowledge_base.side_effect = [
            {
                "knowledgeBase": {
                    "storageConfiguration": {
                        "opensearchServerlessConfiguration": {
                            "collectionArn": "arn:aws:aoss:us-east-1:000000000000:collection/consolemade1"
                        }
                    }
                }
            },
            ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "GetKnowledgeBase"),
        ]
        agent.list_data_sources.return_value = {"dataSourceSummaries": [{"dataSourceId": "DS00000001"}]}
        agent.get_data_source.return_value = {
            "dataSource": {
                "name": "clinical-guidelines",
                "dataSourceConfiguration": {"type": "S3", "s3Configuration": {"bucketArn": "arn:aws:s3:::b"}},
                "dataDeletionPolicy": "DELETE",
            }
        }
        return agent

    def test_vector_data_is_retained_before_the_data_source_is_deleted(self, monkeypatch):
        """Regression: with dataDeletionPolicy DELETE the August Knowledge Base got stuck in
        DELETE_UNSUCCESSFUL ("Unable to delete data from vector store")."""
        import botocore.session
        from botocore.validate import ParamValidator

        agent = self._agent()
        _clients(monkeypatch, **{"bedrock-agent": agent})
        assert cleanup.cleanup_knowledge_base() == ["arn:aws:aoss:us-east-1:000000000000:collection/consolemade1"]
        (update,) = agent.update_data_source.call_args_list
        assert update.kwargs["dataDeletionPolicy"] == "RETAIN"
        names = [c[0] for c in agent.method_calls]
        assert names.index("update_data_source") < names.index("delete_data_source")
        agent.delete_knowledge_base.assert_called_once_with(knowledgeBaseId="KB00000001")
        shape = botocore.session.get_session().get_service_model("bedrock-agent").operation_model("UpdateDataSource")
        report = ParamValidator().validate(update.kwargs, shape.input_shape)
        assert not report.has_errors(), report.generate_report()

    def test_the_collection_a_knowledge_base_used_is_deleted(self, monkeypatch):
        agent, aoss = MagicMock(), MagicMock()
        agent.list_knowledge_bases.return_value = {"knowledgeBaseSummaries": []}
        made = {"id": "consolemade1", "name": "bedrock-knowledge-base-x1y2z3", "status": "ACTIVE"}
        aoss.batch_get_collection.side_effect = lambda **k: {
            "collectionDetails": [made]
            if k.get("ids") == ["consolemade1"] and not aoss.delete_collection.called
            else []
        }
        _clients(monkeypatch, **{"bedrock-agent": agent, "opensearchserverless": aoss})
        cleanup.cleanup_opensearch_serverless(["arn:aws:aoss:us-east-1:000000000000:collection/consolemade1"])
        aoss.delete_collection.assert_called_once_with(id="consolemade1")
