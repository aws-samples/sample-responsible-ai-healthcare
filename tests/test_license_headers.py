# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Every tracked source file carries the MIT-0 SPDX header that aws-samples requires.
"""

import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HEADER = "SPDX-License-Identifier: MIT-0"
EXTENSIONS = (".py", ".ts", ".tf", ".sh")
# Generated, installed or local-only trees; none is tracked by git
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    ".terraform",
    "cdk.out",
    ".cache",
    ".agents",
    ".ash",
    "dist",
    "__pycache__",
    ".scan-output",
    ".verify-output",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}


def _source_files():
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith("ash-scan-output")]
        for name in filenames:
            if name.endswith(EXTENSIONS) and not name.endswith(".d.ts"):
                found.append(os.path.relpath(os.path.join(dirpath, name), ROOT))
    return sorted(found)


def test_there_are_source_files():
    assert len(_source_files()) > 50


@pytest.mark.parametrize("path", _source_files())
def test_source_file_has_the_mit0_header(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        head = "".join(f.readline() for _ in range(4))
    assert HEADER in head, f"{path} lacks '{HEADER}' in its first lines"
