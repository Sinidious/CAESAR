"""Helpers for tests that need a live NATS server.

Spins up a real ``nats-server`` subprocess on an ephemeral port so the
bus + registry can be exercised end-to-end. Skips the test when the
binary isn't installed (most useful on Windows where the dev may not
have downloaded it yet — CI installs it explicitly).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import closing


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
