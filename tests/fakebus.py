"""Helpers for tests that need a live NATS server.

Spins up a real ``nats-server`` subprocess on an ephemeral port so the
bus + registry can be exercised end-to-end. Skips the test when the
binary isn't installed (most useful on Windows where the dev may not
have downloaded it yet — CI installs it explicitly).

:func:`spawn_nats` runs an unauthenticated server (the v0.3 → v1.1
default).  :func:`spawn_nats_with_nkey_auth` (ADR-0027) runs an
authenticated server with two NKEY identities — one Praetor-shaped
admin, one CAESAR-worker-shaped restricted user — so the integration
test in :mod:`tests.test_legion_multihost` can prove the auth path
end-to-end.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from textwrap import dedent


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def _wait_for_port(port: int, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise TimeoutError(f"nats-server didn't open port {port} within {timeout}s")


def find_nats_binary() -> str | None:
    return shutil.which("nats-server")


def spawn_nats(binary: str) -> Iterator[str]:
    """Subprocess generator: yield the nats-server URL; terminate on close."""

    port = _free_port()
    proc = subprocess.Popen(
        [binary, "-p", str(port), "-a", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
        yield f"nats://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def spawn_nats_with_nkey_auth(
    binary: str,
    *,
    praetor_public_key: str,
    worker_public_key: str,
    worker_reply_subject: str,
) -> Iterator[str]:
    """Spin up nats-server with NKEY auth for one Praetor + one worker.

    Mirrors the v1.2 ADR-0027 example: Praetor is allowed on
    ``caesar.>``; the worker can only publish to
    ``caesar.registry.{hello,heartbeat}`` and ``worker_reply_subject``,
    and subscribe to ``caesar.dispatch.>`` and its own reply subject.

    Note: NATS rejects ``user:`` alongside ``nkey:`` — pure NKEY
    identities are matched by the public key alone. The client also
    doesn't pass a username when authenticating via NKEY; the
    challenge-response on the seed identifies the connection.
    """

    port = _free_port()
    conf = dedent(
        f"""\
        port: {port}
        host: "127.0.0.1"

        authorization {{
          users: [
            # Praetor (admin): no permissions block = full access.
            # The orchestrator's request/reply path uses _INBOX.>
            # subjects, so an allow-list scoped to caesar.> would
            # break dispatches. Praetor is trusted by definition;
            # constraining it serves no one.
            {{ nkey: "{praetor_public_key}" }},
            {{ nkey: "{worker_public_key}",
              permissions: {{
                publish: {{
                  allow: [
                    "caesar.registry.hello",
                    "caesar.registry.heartbeat",
                    "{worker_reply_subject}",
                  ],
                }},
                subscribe: {{
                  allow: [
                    "caesar.dispatch.>",
                    "{worker_reply_subject}",
                  ],
                }},
                # NATS request/reply uses a temporary _INBOX.> subject
                # for the reply; allow_responses grants the worker a
                # one-shot publish permission on it when it answers a
                # request. Without this, msg.respond() is silently
                # dropped and Praetor's request times out.
                allow_responses: true,
              }},
            }},
          ]
        }}
        """
    )
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False, encoding="utf-8") as fh:
        fh.write(conf)
        conf_path = Path(fh.name)

    # Capture stderr so a malformed config or auth error becomes a
    # readable assertion, not a silent 'port never opened' timeout.
    stderr_file = tempfile.TemporaryFile()  # noqa: SIM115 - lifetime owned by this fn's finally
    proc = subprocess.Popen(
        [binary, "-c", str(conf_path)],
        stdout=subprocess.DEVNULL,
        stderr=stderr_file,
    )
    try:
        try:
            _wait_for_port(port)
        except TimeoutError as exc:
            stderr_file.seek(0)
            stderr_text = stderr_file.read().decode("utf-8", errors="replace")
            raise TimeoutError(f"{exc}\nconfig was:\n{conf}\nstderr was:\n{stderr_text}") from None
        yield f"nats://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        stderr_file.close()
        conf_path.unlink(missing_ok=True)
