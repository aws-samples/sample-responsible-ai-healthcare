# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
README and docs must describe the solution as built.

These tests fail when the documentation drifts from the repository: a path,
script, test, Make target or alarm it names no longer exists, or a statement
that is known to be false (and was once shipped) comes back.
"""

import glob
import os
import re

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOCS = ["README.md", "SECURITY.md", *sorted(os.path.relpath(p, ROOT) for p in glob.glob(f"{ROOT}/docs/*.md"))]


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _section(text, heading):
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return match.group(1) if match else ""


def _tree_paths(tree_block):
    """Turn the README's box-drawing tree into repository-relative paths."""
    paths, stack = [], []
    for line in tree_block.splitlines():
        match = re.match(r"^([│ ]*)(?:├──|└──) (\S+)", line)
        if not match:
            continue
        depth = len(match.group(1)) // 4
        name = match.group(2)
        stack = stack[:depth] + [name.rstrip("/")]
        paths.append("/".join(stack))
    return paths


README = _read("README.md")


class TestProjectStructure:
    def test_every_path_in_the_tree_exists(self):
        block = re.search(r"## Project Structure\n+```\n(.*?)```", README, re.S)
        assert block, "README has no Project Structure tree"
        root_line, *_ = block.group(1).splitlines()
        assert root_line.startswith("sample-responsible-ai-healthcare/")
        missing = [p for p in _tree_paths(block.group(1)) if not os.path.exists(os.path.join(ROOT, p))]
        assert not missing, f"README tree lists paths that do not exist: {missing}"


class TestReferencedFilesExist:
    @pytest.mark.parametrize("doc", DOCS)
    def test_referenced_scripts_tests_and_docs_exist(self, doc):
        text = _read(doc)
        refs = set(re.findall(r"\b((?:scripts|tests|docs|infra)/[\w./-]+\.(?:py|md|tf|png|txt))\b", text))
        missing = sorted(r for r in refs if not os.path.exists(os.path.join(ROOT, r)))
        assert not missing, f"{doc} references files that do not exist: {missing}"

    @pytest.mark.parametrize("doc", DOCS)
    def test_referenced_make_targets_exist(self, doc):
        makefile = _read("Makefile")
        targets = set(re.findall(r"^([a-z][\w-]*):", makefile, re.M))
        used = set(re.findall(r"`make ([a-z][\w-]*)", _read(doc)))
        assert used <= targets, f"{doc} uses missing make targets: {sorted(used - targets)}"


class TestTestTable:
    def test_every_test_module_is_documented(self):
        documented = set(re.findall(r"`tests/(test_\w+\.py)`", _section(README, "Testing")))
        actual = {os.path.basename(p) for p in glob.glob(f"{ROOT}/tests/test_*.py")}
        assert actual <= documented, f"README Testing table is missing: {sorted(actual - documented)}"


class TestNamesMatchInfrastructure:
    def test_alarm_names_in_docs_exist(self):
        alarms = set(re.findall(r'alarm_name\s*=\s*"\$\{var\.project_name\}-([\w-]+)"', _read("infra/cloudwatch.tf")))
        for doc in DOCS:
            for name in re.findall(r"rai-healthcare-demo-([a-z-]+-alert|high-[a-z-]+|accuracy-drift)\b", _read(doc)):
                assert name in alarms, f"{doc} names an alarm that infra/cloudwatch.tf does not define: {name}"


# Statements that were shipped in the docs and are false for this solution.
FALSE_CLAIMS = [
    ("README.md", r"(through|via) (a |the )?NAT gateway|\| NAT Gateway \|", "the VPC has no NAT gateway"),
    ("README.md", r"LowConfidenceAlarm", "the alarm is rai-healthcare-demo-low-confidence-alert"),
    ("README.md", r"instance profiles", "no EC2 instances are deployed"),
    ("README.md", r"Lambda functions and layers", "no Lambda layers are deployed"),
    ("README.md", r"docs/architecture\.md", "that document does not exist"),
    ("README.md", r"such as[^.]*CloudTrail[^.]*would be required", "CloudTrail is deployed"),
    ("README.md", r"Policy Engine with Cedar", "Cedar policies are illustrative and not enforced"),
]


@pytest.mark.parametrize(("doc", "pattern", "why"), FALSE_CLAIMS)
def test_known_false_claims_stay_out(doc, pattern, why):
    assert not re.search(pattern, _read(doc), re.I), f"{doc} matches /{pattern}/ but {why}"


CLARIFY_REPLACED = re.compile(r"no longer (open|available)|replac|closed to new customers|availability-change", re.I)


@pytest.mark.parametrize("doc", ["README.md", "SECURITY.md", *sorted(glob.glob("docs/*.md", root_dir=ROOT))])
def test_clarify_appears_only_as_replaced(doc):
    """SageMaker Clarify is closed to new customers; a public sample must not tell readers to use it."""
    for paragraph in re.split(r"\n\s*\n", _read(doc)):
        if re.search(r"\bClarify\b", paragraph):
            assert CLARIFY_REPLACED.search(paragraph), f"{doc} mentions Clarify as current: {paragraph[:160]!r}"


class TestTerraformScope:
    def test_terraform_section_lists_only_what_terraform_creates(self):
        block = re.search(r"Terraform will provision:\n(.*?)\n\n", README, re.S)
        assert block, "README lost its 'Terraform will provision' list"
        claims = {
            r"^- (Amazon )?Bedrock Knowledge Base\b": "the Knowledge Base itself",
            r"Automated Reasoning": "the Automated Reasoning guardrail",
            r"AgentCore agent runtime": "the AgentCore runtime",
        }
        for pattern, what in claims.items():
            assert not re.search(pattern, block.group(1), re.M), f"Terraform does not create {what}"


class TestCleanup:
    def test_cleanup_mentions_firewall_delete_protection(self):
        assert re.search(r"delete protection", _section(README, "Cleanup"), re.I)


# ─────────────────────────────────────────────────────────────────────────────
# Low-level architecture diagram vs. the deployed configuration
# ─────────────────────────────────────────────────────────────────────────────
LLD = os.path.join(ROOT, "docs", "architecture-lld-v2.drawio")

# Diagram label for each VPC endpoint service in infra/vpc.tf
ENDPOINT_LABELS = {
    "s3": "S3 gateway",
    "bedrock-runtime": "Bedrock Runtime",
    "monitoring": "CloudWatch",
    "xray": "X-Ray",
}


def _attr(name, text):
    match = re.search(rf'\b{name}="([^"]*)"', text)
    return match.group(1) if match else ""


def _diagram_cells():
    """id -> (plain-text label, (x, y, w, h)) for every vertex in the LLD.

    Parsed with regular expressions rather than an XML library: the file is a
    tracked draw.io export and only vertex ids, labels and geometry are needed.
    """
    import html as _html

    cells = {}
    for attrs, body in re.findall(r"<mxCell ([^>]*?)>(.*?)</mxCell>", _read(LLD), re.S):
        if 'vertex="1"' not in attrs:
            continue
        geo = re.search(r"<mxGeometry ([^>]*?)/?>", body)
        if not geo:
            continue
        label = re.sub(r"<[^>]+>", " ", _html.unescape(_html.unescape(_attr("value", attrs))))
        box = tuple(float(_attr(k, geo.group(1)) or 0) for k in ("x", "y", "width", "height"))
        cells[_attr("id", attrs)] = (re.sub(r"\s+", " ", label).strip(), box)
    return cells


def _inside(inner, outer):
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    return ox <= ix and oy <= iy and ix + iw <= ox + ow and iy + ih <= oy + oh


def _cell(cells, text):
    found = [(cid, c) for cid, c in cells.items() if c[0].startswith(text)]
    assert found, f"the diagram has no box labelled '{text}...'"
    return found[0][1]


class TestArchitectureDiagram:
    def test_every_vpc_endpoint_is_drawn_and_no_other(self):
        services = set(
            re.findall(r'service_name\s*=\s*"com\.amazonaws\.\$\{var\.aws_region\}\.([\w.-]+)"', _read("infra/vpc.tf"))
        )
        labels = [c[0] for c in _diagram_cells().values() if c[0].endswith("endpoint")]
        assert services <= set(ENDPOINT_LABELS), (
            f"add a diagram label for new endpoints: {services - set(ENDPOINT_LABELS)}"
        )
        drawn = {svc for svc, name in ENDPOINT_LABELS.items() if any(label.startswith(name) for label in labels)}
        assert drawn == services, f"diagram endpoints {sorted(drawn)} != infra/vpc.tf endpoints {sorted(services)}"
        assert len(labels) == len(services), f"the diagram draws endpoints that infra/vpc.tf does not create: {labels}"

    def test_network_firewall_and_no_nat_match_terraform(self):
        vpc_tf = _read("infra/vpc.tf")
        labels = " ".join(c[0] for c in _diagram_cells().values())
        assert ('resource "aws_networkfirewall_firewall"' in vpc_tf) == ("Network Firewall" in labels)
        assert 'resource "aws_nat_gateway"' not in vpc_tf and "no NAT gateway" in labels

    def test_only_the_lambda_is_inside_the_vpc(self):
        cells = _diagram_cells()
        vpc = _cell(cells, "VPC 10.0.0.0/16")[1]
        assert _inside(_cell(cells, "Preprocessing Lambda")[1], vpc)
        # agentcore.json runs the runtime in an AWS-managed network, so it must be drawn outside the VPC
        assert '"networkMode": "PUBLIC"' in _read("src/agent/agentcore/agentcore.json")
        for outside in ("AgentCore Runtime", "Amazon Bedrock Claude", "Bedrock Knowledge Base"):
            assert not _inside(_cell(cells, outside)[1], vpc), f"{outside} is drawn inside the VPC"

    def test_model_label_matches_the_allowlisted_model(self):
        assert "us.anthropic.claude-sonnet-4-6" in _read("src/lambda/preprocessing/index.py")
        assert "Claude Sonnet 4.6" in _cell(_diagram_cells(), "Amazon Bedrock Claude")[0]

    def test_gateway_and_policy_engine_are_drawn_only_because_cdk_deploys_them(self):
        gateway_ts = _read("src/agent/agentcore/cdk/lib/diagnostic-gateway.ts")
        labels = [c[0] for c in _diagram_cells().values()]
        drawn = " ".join(labels)
        # Deployed by the CDK stack: drawn as part of the solution
        assert "new CfnGateway(" in gateway_ts and "AgentCore Gateway" in drawn
        assert "new CfnPolicyEngine(" in gateway_ts and "Policy in AgentCore" in drawn
        assert 'authorizerType: "AWS_IAM"' in gateway_ts and "SigV4" in drawn
        # Not deployed by this sample: may appear only as "Not deployed"
        for label in labels:
            if re.search(r"AgentCore Identity", label, re.I):
                assert label.startswith("Not deployed"), f"'{label}' is drawn as part of the deployed solution"


def _kms_keys_by_region():
    """(primary, replica) counts of aws_kms_key resources across infra/*.tf."""
    primary = replica = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "infra", "*.tf"))):
        for body in re.findall(r'resource "aws_kms_key" "\w+" \{(.*?)\n\}', open(path).read(), re.S):
            if re.search(r"^\s*provider\s*=\s*aws\.replica\b", body, re.M):
                replica += 1
            else:
                primary += 1
    return primary, replica


class TestKmsKeyCounts:
    def test_diagram_counts_keys_per_region(self):
        primary, replica = _kms_keys_by_region()
        labels = [c[0] for c in _diagram_cells().values()]
        assert any(label.startswith(f"AWS KMS ({primary} customer managed keys)") for label in labels)
        assert any(label.startswith(f"KMS: {replica} customer managed") for label in labels)

    def test_readme_total_matches_terraform(self):
        words = {6: "six", 3: "three", 9: "nine"}
        primary, replica = _kms_keys_by_region()
        assert f"{words[primary + replica]} customer managed KMS keys" in README
        assert f"{words[primary]} in us-east-1" in README
