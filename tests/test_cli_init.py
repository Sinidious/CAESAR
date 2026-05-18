"""Tests for ``caesar init`` (ADR-0029).

Two layers:

1. Unit tests on :func:`caesar.cli_init.init_workspace` verify what
   gets written to disk, what's overwrite-safe, and the SR-006 /
   SR-007 / NKEY shape of the generated files.
2. A CLI surface test invokes the Typer command end-to-end via
   :class:`typer.testing.CliRunner` against a tmp_path.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import nkeys
import pytest
import yaml
from typer.testing import CliRunner

from caesar.cli import app
from caesar.cli_init import (
    ENV_FILENAME,
    NKEY_FILENAME,
    POLICY_FILENAME,
    VAR_DIRNAME,
    InitPlan,
    compute_plan,
    existing_artifacts,
    init_workspace,
)

# --- compute_plan / existing_artifacts -------------------------------------


def test_compute_plan_paths_are_under_target(tmp_path: Path) -> None:
    plan = compute_plan(tmp_path)
    assert plan.env_path == tmp_path / ENV_FILENAME
    assert plan.policy_path == tmp_path / POLICY_FILENAME
    assert plan.nkey_path == tmp_path / NKEY_FILENAME
    assert plan.var_dir == tmp_path / VAR_DIRNAME
    assert plan.files == [plan.env_path, plan.policy_path, plan.nkey_path]


def test_existing_artifacts_lists_only_present_files(tmp_path: Path) -> None:
    plan = compute_plan(tmp_path)
    (plan.env_path).write_text("placeholder", encoding="utf-8")
    assert existing_artifacts(plan) == [plan.env_path]


# --- init_workspace happy path --------------------------------------------


def test_init_writes_all_artifacts(tmp_path: Path) -> None:
    plan = init_workspace(tmp_path)
    for path in plan.files:
        assert path.is_file(), f"expected {path} to exist"
    assert plan.var_dir.is_dir()


def test_init_creates_target_dir_if_missing(tmp_path: Path) -> None:
    target = tmp_path / "fresh-box"
    plan = init_workspace(target)
    assert target.is_dir()
    assert plan.env_path.is_file()


def test_init_env_contains_fresh_token_and_signing_key(tmp_path: Path) -> None:
    plan = init_workspace(tmp_path)
    env = plan.env_path.read_text(encoding="utf-8")

    token = re.search(r"^CAESAR_DASHBOARD__TOKEN=(\S+)$", env, re.MULTILINE)
    signing_key = re.search(r"^CAESAR_DASHBOARD__SIGNING_KEY=(\S+)$", env, re.MULTILINE)
    assert token is not None and len(token.group(1)) >= 32
    assert signing_key is not None and len(signing_key.group(1)) >= 32
    # SR-006: separate key, never the same value as the token.
    assert token.group(1) != signing_key.group(1)


def test_init_env_points_policy_at_local_file(tmp_path: Path) -> None:
    env = init_workspace(tmp_path).env_path.read_text(encoding="utf-8")
    assert f"CAESAR_POLICY__RULES_PATH=./{POLICY_FILENAME}" in env


def test_init_env_advertises_three_provider_options(tmp_path: Path) -> None:
    env = init_workspace(tmp_path).env_path.read_text(encoding="utf-8")
    assert "CAESAR_LLM__PROVIDER=anthropic" in env
    assert "openai" in env.lower()
    assert "ollama" in env.lower()


def test_init_env_anthropic_key_is_an_empty_placeholder(tmp_path: Path) -> None:
    """The operator's editor highlights the empty field; no fake secret."""

    env = init_workspace(tmp_path).env_path.read_text(encoding="utf-8")
    # Match the LIVE line (no leading "#"), and assert it's the empty value.
    line = re.search(r"^CAESAR_LLM__ANTHROPIC__API_KEY=(.*)$", env, re.MULTILINE)
    assert line is not None
    assert line.group(1) == ""


# --- policy.yaml shape ------------------------------------------------------


def test_init_policy_parses_and_allows_calculator_only(tmp_path: Path) -> None:
    plan = init_workspace(tmp_path)
    rules = yaml.safe_load(plan.policy_path.read_text(encoding="utf-8"))
    assert rules["version"] == 1
    # HA services are commented out — safe default, operator opts in.
    assert rules.get("allowed_services") in (None, [])
    # Calculator is the only out-of-the-box tool; no creds needed.
    tools = rules.get("allowed_tools") or []
    assert [t for t in tools if isinstance(t, str)] == ["calculator"]


# --- praetor.nkey -----------------------------------------------------------


def test_init_nkey_decodes_to_user_keypair(tmp_path: Path) -> None:
    plan = init_workspace(tmp_path)
    seed = plan.nkey_path.read_text(encoding="utf-8").strip()
    # NKEY USER seeds start with SU…
    assert seed.startswith("SU")
    kp = nkeys.from_seed(seed.encode("ascii"))
    assert kp.public_key.decode("ascii").startswith("U")


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only chmod check")
def test_init_nkey_has_restrictive_mode(tmp_path: Path) -> None:
    plan = init_workspace(tmp_path)
    mode = plan.nkey_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_init_each_invocation_mints_a_fresh_nkey(tmp_path: Path) -> None:
    """Two boxes get two different seeds; the operator can't accidentally
    reuse one Praetor identity on two homes."""

    a = init_workspace(tmp_path / "boxA")
    b = init_workspace(tmp_path / "boxB")
    assert a.nkey_path.read_text() != b.nkey_path.read_text()


# --- idempotency / --force --------------------------------------------------


def test_init_refuses_to_overwrite_existing_config(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    with pytest.raises(FileExistsError) as info:
        init_workspace(tmp_path)
    assert "refusing to overwrite" in str(info.value)


def test_init_force_replaces_existing_config(tmp_path: Path) -> None:
    first = init_workspace(tmp_path)
    original_seed = first.nkey_path.read_text(encoding="utf-8")
    second = init_workspace(tmp_path, force=True)
    assert second.nkey_path.read_text(encoding="utf-8") != original_seed


def test_init_force_does_not_clobber_var_directory_contents(tmp_path: Path) -> None:
    """User data under ./var/ must survive a forced re-init."""

    plan = init_workspace(tmp_path)
    sentinel = plan.var_dir / "caesar.sqlite3"
    sentinel.write_text("imagine a database", encoding="utf-8")
    init_workspace(tmp_path, force=True)
    assert sentinel.is_file()
    assert sentinel.read_text(encoding="utf-8") == "imagine a database"


# --- CLI surface ------------------------------------------------------------


def test_cli_init_runs_in_empty_dir(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / ENV_FILENAME).is_file()
    assert (tmp_path / POLICY_FILENAME).is_file()
    assert (tmp_path / NKEY_FILENAME).is_file()
    assert (tmp_path / VAR_DIRNAME).is_dir()
    assert "Next steps" in result.stdout


def test_cli_init_without_force_refuses_to_overwrite(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--dir", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "refusing to overwrite" in result.output.lower()


def test_cli_init_with_force_succeeds(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--dir", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["init", "--dir", str(tmp_path), "--force"])
    assert result.exit_code == 0, result.stdout


# --- direct InitPlan construction ------------------------------------------


def test_init_plan_files_property_is_a_stable_list(tmp_path: Path) -> None:
    plan = InitPlan(
        env_path=tmp_path / "a",
        policy_path=tmp_path / "b",
        nkey_path=tmp_path / "c",
        var_dir=tmp_path / "var",
    )
    files = plan.files
    files.append(tmp_path / "extra")
    # Mutating the snapshot shouldn't affect future calls.
    assert plan.files == [tmp_path / "a", tmp_path / "b", tmp_path / "c"]
