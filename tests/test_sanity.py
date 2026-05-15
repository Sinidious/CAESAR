"""Smoke tests that prove the toolchain wiring works.

These do not exercise any application code (there is none yet); they
exist so the CI matrix has something to run and so `just check` is
non-trivial from day one.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_python_version_is_supported() -> None:
    assert sys.version_info >= (3, 11), "CAESAR targets Python 3.11+"


def test_repo_layout_has_expected_top_level_files() -> None:
    for name in (
        "README.md",
        "LICENSE",
        "CLA.md",
        "CHANGELOG.md",
        "pyproject.toml",
        "Justfile",
        ".pre-commit-config.yaml",
    ):
        assert (REPO_ROOT / name).is_file(), f"missing top-level file: {name}"


def test_adr_template_exists() -> None:
    template = REPO_ROOT / "docs" / "adr" / "0000-template.md"
    assert template.is_file(), "ADR template must live at docs/adr/0000-template.md"
