# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Pass/fail rules of scripts/scan_summary.py.

The report must fail on any finding at Low or above, on a suppressed finding,
and on a scanner that produced no output. Only KICS Info findings, the documented
cfn-nag W13 exceptions and the one documented, time-boxed ASH suppression may be
listed without failing.
"""

import importlib.util
import os

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "scan_summary.py")


@pytest.fixture(scope="module")
def summary():
    spec = importlib.util.spec_from_file_location("scan_summary_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ash(**counts):
    base = dict.fromkeys(("suppressed", "critical", "high", "medium", "low", "info"), 0)
    return {"scanner_results": {"bandit": {"status": "PASSED", "severity_counts": {**base, **counts}}}}


class TestFailingRule:
    def test_clean_rows_pass(self, summary):
        assert not summary.failing(summary.empty("x"))

    @pytest.mark.parametrize("severity", ["critical", "high", "medium", "low"])
    def test_any_finding_at_low_or_above_fails(self, summary, severity):
        row = summary.empty("x")
        row[severity] = 1
        assert summary.failing(row)

    def test_info_alone_does_not_fail(self, summary):
        row = summary.empty("x")
        row["info"] = 3
        assert not summary.failing(row)

    def test_not_run_fails(self, summary):
        assert summary.failing(summary.not_run("trivy", "not installed"))


class TestAsh:
    def test_low_finding_fails(self, summary):
        (row,) = summary.parse_ash(_ash(low=242))
        assert row["low"] == 242 and summary.failing(row)

    def test_suppressed_finding_fails(self, summary):
        (row,) = summary.parse_ash(_ash(suppressed=1))
        assert summary.failing(row)

    def test_documented_suppression_is_listed_not_counted(self, summary):
        results = {"scanner_results": {"grype": {"status": "PASSED", "severity_counts": {"suppressed": 1}}}}
        flat = [
            {
                "scanner": "grype",
                "rule_id": "GHSA-g3hv-75wg-m249-minimatch",
                "is_suppressed": True,
                "file_path": "src/agent/agentcore/cdk/package-lock.json",
            }
        ]
        (row,) = summary.parse_ash(results, flat)
        assert not summary.failing(row)
        assert any("GHSA-g3hv-75wg-m249-minimatch" in item for item in row["listed"])

    def test_any_other_suppression_still_fails(self, summary):
        results = {"scanner_results": {"grype": {"status": "PASSED", "severity_counts": {"suppressed": 2}}}}
        flat = [
            {"scanner": "grype", "rule_id": "GHSA-g3hv-75wg-m249-minimatch", "is_suppressed": True},
            {"scanner": "grype", "rule_id": "GHSA-xxxx-other", "is_suppressed": True},
        ]
        (row,) = summary.parse_ash(results, flat)
        assert row["high"] == 1 and summary.failing(row)

    def test_documented_rule_suppressed_in_another_scanner_fails(self, summary):
        results = {"scanner_results": {"semgrep": {"status": "PASSED", "severity_counts": {"suppressed": 1}}}}
        flat = [{"scanner": "semgrep", "rule_id": "GHSA-g3hv-75wg-m249-minimatch", "is_suppressed": True}]
        (row,) = summary.parse_ash(results, flat)
        assert summary.failing(row)

    def test_missing_scanner_is_not_run(self, summary):
        results = {"scanner_results": {"cfn-nag": {"status": "MISSING", "severity_counts": {}}}}
        (row,) = summary.parse_ash(results)
        assert row["status"] == "NOT RUN"


GRAPH_RAN = {"passed_checks": [{"check_id": "CKV_AWS_18"}, {"check_id": "CKV2_AWS_6"}]}


class TestParsers:
    def test_checkov_failed_and_skipped_fail(self, summary):
        row = summary.parse_checkov({"summary": {"passed": 10, "failed": 1, "skipped": 1}, "results": GRAPH_RAN})
        assert row["high"] == 2 and summary.failing(row)

    def test_checkov_that_ran_no_graph_check_is_not_run(self, summary):
        """Canary: a broken checkov ran only CKV_ checks and still reported a clean result."""
        results = {"passed_checks": [{"check_id": "CKV_AWS_18"}], "failed_checks": []}
        row = summary.parse_checkov({"summary": {"passed": 363, "failed": 0, "skipped": 0}, "results": results})
        assert row["status"] == "NOT RUN" and summary.failing(row)

    def test_checkov_with_graph_checks_passes(self, summary):
        row = summary.parse_checkov({"summary": {"passed": 477, "failed": 0, "skipped": 0}, "results": GRAPH_RAN})
        assert not summary.failing(row)

    def test_kics_log_errors_are_listed(self, summary):
        log = "x ▶ 2:56AM ERR Error trying to decode data source block: value is not known\nINFO ok"
        row = summary.parse_kics({"vulnerabilities": []}, log)
        assert row["listed"] == ["KICS log error: Error trying to decode data source block: value is not known"]

    def test_checkov_with_no_checks_is_not_run(self, summary):
        assert summary.parse_checkov({"summary": {}})["status"] == "NOT RUN"

    def test_bandit_low_fails(self, summary):
        assert summary.failing(summary.parse_bandit({"results": [{"issue_severity": "LOW"}]}))

    def test_semgrep_info_counts_as_low(self, summary):
        row = summary.parse_semgrep({"results": [{"extra": {"severity": "INFO"}}], "errors": []})
        assert row["low"] == 1 and summary.failing(row)

    def test_semgrep_errors_mean_not_run(self, summary):
        assert summary.parse_semgrep({"results": [], "errors": [{"message": "x"}]})["status"] == "NOT RUN"

    def test_trivy_findings_fail(self, summary):
        data = {"Results": [{"Misconfigurations": [{"Severity": "LOW"}], "Vulnerabilities": None}]}
        assert summary.failing(summary.parse_trivy(data))


class TestListedNotFailing:
    def test_kics_info_is_listed_and_low_fails(self, summary):
        data = {
            "scan": {"analyzer": {"version": "6.13.0"}},
            "vulnerabilities": [
                {"severity": "Info", "name": "IAM Access Analyzer Not Enabled", "location": {"file": "infra/s3.tf"}},
            ],
        }
        row = summary.parse_kics(data)
        assert row["info"] == 1 and row["listed"] and not summary.failing(row)
        data["vulnerabilities"].append({"severity": "Low", "name": "x", "location": {}})
        assert summary.failing(summary.parse_kics(data))

    def test_only_the_documented_cfn_nag_exception_is_listed(self, summary):
        documented = {"id": "W13", "type": "WARN", "logical_resource_ids": ["RuntimeExecutionRoleTelemetryPolicyABC"]}
        row = summary.parse_cfn_nag([{"file_results": {"violations": [documented]}}])
        assert row["listed"] and not summary.failing(row)

    @pytest.mark.parametrize(
        "violation",
        [
            {"id": "W13", "type": "WARN", "logical_resource_ids": ["SomeOtherPolicy"]},
            {"id": "W12", "type": "WARN", "logical_resource_ids": ["RuntimeExecutionRoleTelemetryPolicyABC"]},
            {"id": "F1000", "type": "FAIL", "logical_resource_ids": ["OpenSecurityGroup"]},
        ],
    )
    def test_any_other_cfn_nag_violation_fails(self, summary, violation):
        assert summary.failing(summary.parse_cfn_nag([{"file_results": {"violations": [violation]}}]))


class TestDocumentedExceptions:
    def test_both_w13_policies_are_listed_not_failing(self, summary):
        cfn = [
            {
                "file_results": {
                    "violations": [
                        {
                            "id": "W13",
                            "type": "WARN",
                            "logical_resource_ids": [
                                "RuntimeExecutionRoleTelemetryPolicy" + "1",
                                "DiagnosticGatewayGuardrailChecksPolicy" + "2",
                            ],
                        }
                    ]
                }
            }
        ]
        row = summary.parse_cfn_nag(cfn)
        assert not summary.failing(row) and len(row["listed"]) == 2

    def test_w13_on_any_other_policy_fails(self, summary):
        cfn = [
            {
                "file_results": {
                    "violations": [{"id": "W13", "type": "WARN", "logical_resource_ids": ["SomeOtherPolicy"]}]
                }
            }
        ]
        assert summary.failing(summary.parse_cfn_nag(cfn))

    def test_trivyignore_holds_only_the_documented_cve_with_an_expiry(self, summary):
        assert summary.trivy_ignores() == ["CVE-2026-118764 exp:2027-01-31"]

    def test_trivy_lists_its_ignores(self, summary):
        row = summary.parse_trivy({"Results": []}, ["CVE-2026-118764 exp:2027-01-31"])
        assert not summary.failing(row) and row["listed"]
