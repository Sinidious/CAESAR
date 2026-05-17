"""End-to-end integration test for ADR-0027 multi-host Legion auth.

Spins up a real ``nats-server`` with two NKEY identities — one
Praetor admin, one CAESAR-worker — and proves that:

1. Both can connect when wired with the right seed.
2. The worker can publish and subscribe within its scoped
   permissions (a dispatch round-trip works).
3. The worker is rejected when it tries to publish outside its
   scope (permission denial fires as documented).
4. A bus presenting an unknown NKEY fails to connect.

Gated on ``nats-server`` being on ``PATH`` (same pattern as the
existing single-host bus tests). Skips cleanly otherwise.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest

from caesar.bus.client import Bus, BusAuth
from caesar.legion.bootstrap import WorkerIdentity, generate_worker_identity
from tests.fakebus import find_nats_binary, spawn_nats_with_nkey_auth


@dataclass(frozen=True)
class _AuthedFixture:
    """All the moving pieces a multi-host test needs in one place."""

    nats_url: str
    praetor: WorkerIdentity  # admin identity (we reuse the same generator)
    worker: WorkerIdentity


@pytest.fixture
def authed_legion() -> Iterator[_AuthedFixture]:
    """Spin up nats-server with NKEY auth for two identities."""

    binary = find_nats_binary()
    if binary is None:
        pytest.skip("nats-server not on PATH; install it to run bus tests.")

    praetor = generate_worker_identity(name="praetor")
    worker = generate_worker_identity(name="test-worker")

    gen = spawn_nats_with_nkey_auth(
        binary,
        praetor_public_key=praetor.public_key,
        worker_user_name=worker.nats_user_name,
        worker_public_key=worker.public_key,
        worker_reply_subject=worker.reply_subject_glob,
    )
    url = next(gen)
    try:
        yield _AuthedFixture(nats_url=url, praetor=praetor, worker=worker)
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


async def _connect(nats_url: str, *, seed: str, user: str) -> Bus:
    bus = Bus(nats_url, auth=BusAuth(nkey_seed=seed, user=user))
    await bus.connect()
    return bus


@pytest.fixture
async def buses(authed_legion: _AuthedFixture) -> AsyncIterator[tuple[Bus, Bus]]:
    """Two connected, authed buses: one Praetor-shaped, one worker-shaped."""

    praetor = await _connect(
        authed_legion.nats_url,
        seed=authed_legion.praetor.seed,
        user="caesar-praetor",
    )
    worker = await _connect(
        authed_legion.nats_url,
        seed=authed_legion.worker.seed,
        user=authed_legion.worker.nats_user_name,
    )
    try:
        yield praetor, worker
    finally:
        await worker.close()
        await praetor.close()


# --- positive path ---------------------------------------------------------


async def test_both_identities_connect_authenticated(
    buses: tuple[Bus, Bus],
) -> None:
    praetor, worker = buses
    assert praetor.is_connected
    assert worker.is_connected
    assert praetor.authenticated
    assert worker.authenticated


async def test_worker_serves_a_dispatch_from_praetor(
    buses: tuple[Bus, Bus],
) -> None:
    """The brain-to-worker round-trip works under NKEY auth."""

    from nats.aio.msg import Msg

    praetor, worker = buses

    received: list[bytes] = []

    async def handler(msg: Msg) -> None:
        received.append(msg.data)
        await msg.respond(b"pong")

    await worker.subscribe("caesar.dispatch.test", handler)
    # Give the server a moment to register the subscription.
    await asyncio.sleep(0.05)
    reply = await praetor.request("caesar.dispatch.test", b"ping", timeout=2.0)
    assert reply == b"pong"
    assert received == [b"ping"]


async def test_worker_can_publish_registry_hello(
    buses: tuple[Bus, Bus],
) -> None:
    """The worker's NKEY allows ``caesar.registry.hello`` per ADR-0027."""

    praetor, worker = buses

    received: list[bytes] = []

    from nats.aio.msg import Msg

    async def collector(msg: Msg) -> None:
        received.append(msg.data)

    await praetor.subscribe("caesar.registry.hello", collector)
    await asyncio.sleep(0.05)
    await worker.publish("caesar.registry.hello", b'{"id":"test-worker"}')
    await asyncio.sleep(0.1)
    assert received == [b'{"id":"test-worker"}']


# --- negative paths --------------------------------------------------------


async def test_worker_cannot_publish_outside_its_subjects(
    authed_legion: _AuthedFixture,
    buses: tuple[Bus, Bus],
) -> None:
    """Publishing to ``caesar.>`` outside the worker's scope is denied.

    The worker's connection isn't killed (NATS just drops the message
    server-side) so the API doesn't raise; we verify the negative
    behaviour by confirming the message never reaches a subscriber
    that Praetor (with broader perms) is listening on.
    """

    praetor, worker = buses

    seen: list[bytes] = []

    from nats.aio.msg import Msg

    async def collector(msg: Msg) -> None:
        seen.append(msg.data)

    # Praetor (admin) subscribes on a subject only Praetor may publish to.
    await praetor.subscribe("caesar.admin.something", collector)
    await asyncio.sleep(0.05)
    # Worker attempts to publish there. NATS will silently reject.
    await worker.publish("caesar.admin.something", b"forbidden")
    await asyncio.sleep(0.2)
    assert seen == []


async def test_unknown_nkey_is_rejected_at_connect(
    authed_legion: _AuthedFixture,
) -> None:
    """A fresh, unrelated NKEY can't authenticate against this server."""

    rogue = generate_worker_identity(name="rogue")
    bus = Bus(
        authed_legion.nats_url,
        auth=BusAuth(nkey_seed=rogue.seed, user="caesar-worker-rogue"),
    )
    # nats-py raises one of several Exception subclasses depending on
    # version (NoServersError, AuthorizationError, ErrAuthorization).
    # Pinning the precise type would couple us to nats-py internals;
    # the contract we care about is "doesn't connect", so we accept
    # any exception here.
    with pytest.raises(Exception):  # noqa: B017 — see comment above
        await bus.connect()
