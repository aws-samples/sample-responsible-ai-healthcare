# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Summarize every security scanner's output in one report
=======================================================
`make scan-all` runs the scanners and writes their raw output to .scan-output/;
this script only reads those files, so it starts no processes.

Scanners: ASH (bandit, cdk-nag, cfn-nag, checkov, detect-secrets, grype,
npm-audit, opengrep, semgrep, syft), checkov, bandit, semgrep, GitLab KICS,
Trivy, and cfn-nag on the synthesized AgentCore CDK template.

Writes .scan-output/summary.md and .scan-output/summary.json. Exits 1 if a
scanner produced no output (it was not installed or failed) or reported a
finding at Low or above. Three classes are listed by name but do not fail:
  - KICS Info findings (per-file heuristics such as "IAM Access Analyzer Not
    Enabled", which requires an analyzer resource in every .tf file)
  - the documented cdk-nag AwsSolutions-IAM5 exceptions with Resource "*", which
    cfn-nag reports as W13 on the AgentCore runtime telemetry policy and on the
    gateway's guardrail checks policy (see SECURITY.md)
  - the documented, time-boxed minimatch suppression (ASH grype; Trivy reads it
    from .trivyignore), listed from .trivyignore (see SECURITY.md)
"""

import json
import os
import sys
from datetime import UTC, datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, ".scan-output")
SEVERITIES = ("critical", "high", "medium", "low", "info")
FAILING = ("critical", "high", "medium", "low")

# cfn-nag reports the documented cdk-nag AwsSolutions-IAM5 exceptions as W13 on these policies
DOCUMENTED_EXCEPTIONS = (
    ("W13", "RuntimeExecutionRoleTelemetryPolicy"),
    ("W13", "DiagnosticGatewayGuardrailChecksPolicy"),
)
# The only ASH suppression allowed (.ash.yaml, SECURITY.md): (scanner, rule_id)
DOCUMENTED_SUPPRESSIONS = (("grype", "GHSA-g3hv-75wg-m249-minimatch"),)


def empty(name):
    return {"scanner": name, "status": "RAN", **dict.fromkeys(SEVERITIES, 0), "listed": [], "note": ""}


def not_run(name, reason):
    row = empty(name)
    row.update(status="NOT RUN", note=reason)
    return row


def failing(row):
    return row["status"] != "RAN" or any(row[s] for s in FAILING)


def _bump(row, severity):
    severity = severity.lower()
    row[severity if severity in SEVERITIES else "high"] += 1


def parse_ash(results, findings=None):
    """ASH aggregated results -> one row per ASH scanner.

    `findings` is ASH's flat finding list (reports/ash.flat.json); suppressed findings
    in it that match DOCUMENTED_SUPPRESSIONS are listed instead of counted.
    """
    documented = {}
    for f in findings or []:
        if f.get("is_suppressed") and (f.get("scanner"), f.get("rule_id")) in DOCUMENTED_SUPPRESSIONS:
            documented.setdefault(f["scanner"], []).append(f)
    rows = []
    for name, res in sorted(results.get("scanner_results", {}).items()):
        row = empty(f"ash/{name}")
        counts = res.get("severity_counts", {})
        for sev in SEVERITIES:
            row[sev] = counts.get(sev, 0)
        allowed = documented.get(name, [])
        for f in allowed:
            row["listed"].append(
                f"Documented exception: {f['rule_id']} in {f.get('file_path')} (.ash.yaml, SECURITY.md)"
            )
        unexplained = counts.get("suppressed", 0) - len(allowed)
        if unexplained > 0:
            # A suppressed finding still counts unless it is the documented one
            row["high"] += unexplained
            row["note"] = f"{unexplained} suppressed"
        if res.get("status") in ("MISSING", "ERROR"):
            row.update(status="NOT RUN", note=f"ASH status {res.get('status')}")
        rows.append(row)
    return rows or [not_run("ash", "no scanner results")]


def parse_checkov(data):
    row = empty("checkov")
    summaries = data if isinstance(data, list) else [data]
    passed = sum(s.get("summary", {}).get("passed", 0) for s in summaries)
    failed = sum(s.get("summary", {}).get("failed", 0) for s in summaries)
    skipped = sum(s.get("summary", {}).get("skipped", 0) for s in summaries)
    # Open-source checkov has no severities: every failed or skipped check counts as High
    row["high"] = failed + skipped
    row["note"] = f"{passed} passed, {failed} failed, {skipped} skipped"
    if passed + failed + skipped == 0:
        row.update(status="NOT RUN", note="no checks evaluated")
        return row
    # Canary: a broken checkov install (seen with Homebrew checkov 3.2.430 on Python 3.13)
    # runs no CKV2_* graph checks and still reports a clean result.
    ids = [
        c.get("check_id", "")
        for s in summaries
        for kind in ("passed_checks", "failed_checks", "skipped_checks")
        for c in s.get("results", {}).get(kind, [])
    ]
    if not any(i.startswith("CKV2_") for i in ids):
        row.update(status="NOT RUN", note=f"{row['note']}; no CKV2_* graph check ran, so checkov is broken")
    return row


def parse_bandit(data):
    row = empty("bandit")
    for r in data.get("results", []):
        _bump(row, r.get("issue_severity", "HIGH"))
    return row


def parse_semgrep(data):
    row = empty("semgrep")
    mapping = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
    for r in data.get("results", []):
        _bump(row, mapping.get(r.get("extra", {}).get("severity", "ERROR"), "high"))
    if data.get("errors"):
        row.update(status="NOT RUN", note=f"{len(data['errors'])} semgrep errors")
    return row


def parse_kics(data, log=""):
    row = empty("gitlab-kics")
    # KICS logs a file it could not parse as ERR and still exits cleanly; list each one.
    for line in log.splitlines():
        if " ERR " in line:
            row["listed"].append("KICS log error: " + line.split(" ERR ", 1)[1].strip().removesuffix("\x1b[0m"))
    for v in data.get("vulnerabilities", []):
        if v.get("severity", "").lower() == "info":
            row["info"] += 1
            row["listed"].append(f"KICS Info: {v.get('name')} ({v.get('location', {}).get('file')})")
        else:
            _bump(row, v.get("severity", "high"))
    row["note"] = f"analyzer {data.get('scan', {}).get('analyzer', {}).get('version', '?')}"
    return row


TRIVYIGNORE = os.path.join(ROOT, ".trivyignore")


def trivy_ignores(path=TRIVYIGNORE):
    """IDs ignored by .trivyignore (comments and blank lines skipped)."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]


def parse_trivy(data, ignores=()):
    row = empty("trivy")
    for entry in ignores:
        row["listed"].append(f"Documented exception: {entry} (.trivyignore, SECURITY.md)")
    for result in data.get("Results", []):
        for kind in ("Misconfigurations", "Vulnerabilities", "Secrets"):
            for finding in result.get(kind) or []:
                _bump(row, finding.get("Severity", "HIGH"))
    return row


def parse_cfn_nag(data, template_built=""):
    row = empty("cfn-nag")
    row["note"] = f"template synthesized {template_built}" if template_built else ""
    for item in data if isinstance(data, list) else [data]:
        for v in item.get("file_results", {}).get("violations", []):
            for resource in v.get("logical_resource_ids", []):
                if any(v.get("id") == rule and resource.startswith(prefix) for rule, prefix in DOCUMENTED_EXCEPTIONS):
                    row["listed"].append(f"Documented exception: {v['id']} on {resource} (SECURITY.md)")
                else:
                    row["high" if v.get("type") == "FAIL" else "medium"] += 1
    return row


def _load(name):
    path = os.path.join(OUT, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError:
        return None


def collect():
    ash = _load("ash/ash_aggregated_results.json")
    flat = _load("ash/reports/ash.flat.json")
    flat_findings = flat if isinstance(flat, list) else (flat or {}).get("findings", [])
    rows = parse_ash(ash, flat_findings) if ash is not None else [not_run("ash", "no ASH output")]
    for file_name, scanner, parser in (
        ("checkov.json", "checkov", parse_checkov),
        ("bandit.json", "bandit", parse_bandit),
        ("semgrep.json", "semgrep", parse_semgrep),
        ("trivy.json", "trivy", lambda d: parse_trivy(d, trivy_ignores())),
    ):
        data = _load(file_name)
        rows.append(parser(data) if data is not None else not_run(scanner, f"no {file_name}"))
    kics = _load("gl-kics.json")
    kics_log = os.path.join(OUT, "kics.log")
    log = open(kics_log, errors="replace").read() if os.path.exists(kics_log) else ""
    rows.insert(-1, parse_kics(kics, log) if kics is not None else not_run("gitlab-kics", "no gl-kics.json"))
    cfn = _load("cfn-nag.json")
    built = ""
    marker = os.path.join(OUT, "cfn-nag-template-mtime")
    if os.path.exists(marker):
        with open(marker) as f:
            built = f.read().strip()
    rows.append(parse_cfn_nag(cfn, built) if cfn is not None else not_run("cfn-nag", "no cfn-nag.json"))
    return rows


def render(rows, started):
    lines = [
        f"# Security scan report — {started}",
        "",
        "| Scanner | Result | Critical | High | Medium | Low | Info | Note |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        result = r["status"] if r["status"] != "RAN" else ("FAIL" if failing(r) else "PASS")
        lines.append(
            f"| {r['scanner']} | {result} | {r['critical']} | {r['high']} | {r['medium']} | {r['low']} "
            f"| {r['info']} | {r['note']} |"
        )
    listed = [item for r in rows for item in r["listed"]]
    if listed:
        lines += ["", "## Listed, not failing", ""] + [f"- {item}" for item in listed]
    failed = [r["scanner"] for r in rows if failing(r)]
    verdict = f"FAIL — {', '.join(failed)}" if failed else "PASS — no finding at Low or above, every scanner ran"
    return lines + ["", f"**Result:** {verdict}"], failed


def main():
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = collect()
    lines, failed = render(rows, started)
    with open(os.path.join(OUT, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(OUT, "summary.json"), "w") as f:
        json.dump({"started": started, "rows": rows, "failed": failed}, f, indent=2)
    print("\n".join(lines))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
