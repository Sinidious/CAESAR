"""Tests for ``caesar legion new-worker`` (ADR-0027)."""

from __future__ import annotations

import nkeys
import pytest
from typer.testing import CliRunner

from caesar.cli import app
from caesar.legion.bootstrap import WorkerIdentity, generate_worker_identity

# --- WorkerIdentity formatting helpers ---------------------------------------


def test_generate_worker_identity_returns_valid_nkey_pair() -> None:
    identity = generate_worker_identity(name="kitchen-pi")
    assert identity.name == "kitchen-pi"

    # Seed encodes back to a usable KeyPair.
    kp = nkeys.from_seed(identity.seed.encode("ascii"))
    assert kp.public_key.decode("ascii") == identity.public_key

    # Seeds are USER NKEYs (start with "SU…").
    assert identity.seed.startswith("SU")
    # Public keys are user-prefixed.
    assert identity.public_key.startswith("U")


def test_generate_worker_identity_is_random_each_call() -> None:
    a = generate_worker_identity(name="x")
    b = generate_worker_identity(name="x")
    assert a.seed != b.seed
    assert a.public_key != b.public_key


def test_users_block_comment_includes_worker_label(  # NATS rejects user: + nkey: so the label lives in a comment
) -> None:
    identity = generate_worker_identity(name="kitchen-pi")
    block = identity.nats_users_block()
    assert "# caesar-worker-kitchen-pi" in block


def test_reply_subject_glob_scopes_to_name() -> None:
    identity = generate_worker_identity(name="kitchen-pi")
    assert identity.reply_subject_glob == "caesar.reply.kitchen-pi.>"


def test_nats_users_block_contains_nkey_and_label() -> None:
    identity = generate_worker_identity(name="kitchen-pi")
    block = identity.nats_users_block()
    # NATS rejects user: alongside nkey:, so the worker label flows
    # into a comment rather than a field.
    assert "# caesar-worker-kitchen-pi" in block
    assert "user:" not in block
    assert f'nkey: "{identity.public_key}"' in block
    # Subject permissions are scoped to this worker.
    assert "caesar.reply.kitchen-pi.>" in block
    # No other worker's reply subtree is in the block.
    assert "caesar.reply.>" not in block


def test_nats_users_block_publishes_only_to_safe_subjects() -> None:
    """Sanity-check that the printed config doesn't accidentally grant
    a worker broad publish permission on the registry namespace."""

    identity = generate_worker_identity(name="kitchen-pi")
    block = identity.nats_users_block()

    # Workers publish: hello + heartbeat + their own reply subtree.
    assert "caesar.registry.hello" in block
    assert "caesar.registry.heartbeat" in block
    # And NOT every registry subject.
    assert "caesar.registry.>" not in block


def test_worker_env_vars_references_seed_path_by_name() -> None:
    identity = generate_worker_identity(name="kitchen-pi")
    env = identity.worker_env_vars()
    assert "CAESAR_BUS__ENABLED=true" in env
    assert "CAESAR_BUS__AUTH__ENABLED=true" in env
    # NATS rejects user: alongside nkey:, so we don't ship USER env.
    assert "CAESAR_BUS__AUTH__USER=" not in env
    assert "/etc/caesar/kitchen-pi.nkey" in env


def test_format_for_operator_does_not_print_seed_anywhere_else() -> None:
    """The seed should only appear once in the operator output —
    making it obvious where to copy from."""

    identity = generate_worker_identity(name="kitchen-pi")
    text = identity.format_for_operator()
    assert text.count(identity.seed) == 1


def test_format_for_operator_includes_security_warnings() -> None:
    identity = generate_worker_identity(name="kitchen-pi")
    text = identity.format_for_operator()
    assert "DO NOT commit the seed to git" in text


# --- CLI surface -------------------------------------------------------------


def test_cli_new_worker_prints_full_operator_block() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["legion", "new-worker", "--name", "office-pc"])
    assert result.exit_code == 0, result.stdout
    assert "office-pc" in result.stdout
    # The "caesar-worker-<name>" label shows up in the conf-block comment.
    assert "caesar-worker-office-pc" in result.stdout
    assert "caesar.reply.office-pc.>" in result.stdout
    # Seed (USER NKEY) appears in the output.
    assert "SU" in result.stdout


def test_cli_new_worker_requires_name() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["legion", "new-worker"])
    assert result.exit_code != 0
    # Typer reports the missing option.
    assert "name" in result.output.lower()


def test_worker_identity_is_immutable() -> None:
    """Sanity-check the dataclass frozen=True invariant."""

    import dataclasses

    identity = generate_worker_identity(name="kitchen-pi")
    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.name = "other"  # type: ignore[misc]


def test_worker_identity_class_round_trips_from_explicit_values() -> None:
    """Verify WorkerIdentity is constructible without the generator
    (useful if a future flow imports an existing seed)."""

    explicit = WorkerIdentity(
        name="explicit",
        seed="SUAIWR3EHVNBI6PSWW4MN7TWQSD7QHEA2TYJK7CWCRRVWCTK5NUFBEZ7CA",
        public_key="UB3JKIFCJN57TDMYEWRTAENS4TCDW3AAFUN7CFPIAK7CH6ZEC5BRUS6U",
    )
    assert explicit.reply_subject_glob == "caesar.reply.explicit.>"
