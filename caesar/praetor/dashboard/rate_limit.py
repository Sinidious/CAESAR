"""In-memory sliding-window login throttle (SR-002).

Counts only *failed* login attempts per source IP. A successful login
doesn't consume the bucket — a legitimate operator who logs in
repeatedly (e.g. on a phone with short cookies) is never throttled.

Process-local: the state lives in a single ``LoginRateLimiter``
instance mounted on ``app.state``. Restarting Praetor resets all
buckets, which is fine for a homelab single-process deployment. If
CAESAR ever runs multi-process the limiter moves to the DB.

The limiter is allow/record-style rather than a middleware so the
caller decides what "failure" means (e.g. an empty token submission
is a failure here; a 500 wouldn't be).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class LoginRateLimiter:
    """Per-key sliding-window failure counter."""

    def __init__(self, *, max_failures: int = 5, window_seconds: float = 300.0) -> None:
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    @property
    def max_failures(self) -> int:
        return self._max_failures

    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._failures[key]
        while bucket and now - bucket[0] > self._window_seconds:
            bucket.popleft()
        return bucket

    def check(self, key: str, *, now: float | None = None) -> bool:
        """Return ``True`` if a new attempt under ``key`` is allowed."""

        moment = time.monotonic() if now is None else now
        bucket = self._prune(key, moment)
        return len(bucket) < self._max_failures

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        """Note one failed attempt under ``key``."""

        moment = time.monotonic() if now is None else now
        self._prune(key, moment)
        self._failures[key].append(moment)

    def retry_after_seconds(self, key: str, *, now: float | None = None) -> float:
        """Seconds the caller should wait before the next attempt.

        ``0.0`` when the bucket has room. Otherwise the time until the
        oldest in-window failure expires.
        """

        moment = time.monotonic() if now is None else now
        bucket = self._prune(key, moment)
        if len(bucket) < self._max_failures:
            return 0.0
        oldest = bucket[0]
        return max(0.0, self._window_seconds - (moment - oldest))
