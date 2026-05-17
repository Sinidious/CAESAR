"""Single-token cookie auth for the dashboard (ADR-0021).

The operator sets ``CAESAR_DASHBOARD__TOKEN``. The login form accepts
a matching token and sets a signed cookie. The signature uses
``itsdangerous`` with a key that is either:

- ``CAESAR_DASHBOARD__SIGNING_KEY`` when the operator set one
  explicitly. The cookie HMAC is then independent of the auth token;
  rotating the auth token alone does NOT log existing sessions out
  (the operator must rotate the signing key separately to do that).
- Derived from the auth token via HMAC-SHA256 with a fixed salt when
  the operator did not set a signing key. This preserves the legacy
  "rotate the token, log everyone out" behaviour while still keeping
  the on-disk signing material one step removed from the bearer
  secret (SR-006).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeSerializer

from caesar.config import DashboardSettings

COOKIE_VALUE = "ok"
_KDF_SALT = b"caesar.dashboard.signing.v1"


def derive_signing_key(settings: DashboardSettings) -> str:
    """Return the cookie signing key for the configured dashboard.

    Raises ``ValueError`` if neither a token nor a signing key is set
    — the dashboard router only mounts when ``token`` is non-None, so
    this is a guardrail.
    """

    if settings.signing_key is not None:
        return settings.signing_key.get_secret_value()
    if settings.token is None:
        raise ValueError("Cannot derive dashboard signing key: token is unset.")
    token_bytes = settings.token.get_secret_value().encode("utf-8")
    return hmac.new(_KDF_SALT, token_bytes, hashlib.sha256).hexdigest()


def _serializer(signing_key: str) -> URLSafeSerializer:
    return URLSafeSerializer(signing_key, salt="caesar.dashboard")


def make_session_cookie(signing_key: str) -> str:
    """Return the signed cookie value for a valid login."""

    return _serializer(signing_key).dumps(COOKIE_VALUE)


def verify_session_cookie(signing_key: str, raw_cookie: str) -> bool:
    """True iff ``raw_cookie`` was signed by ``signing_key``."""

    try:
        loaded = _serializer(signing_key).loads(raw_cookie)
    except BadSignature:
        return False
    return bool(loaded == COOKIE_VALUE)


def require_session(request: Request) -> None:
    """FastAPI dependency: 401 unless the request carries a valid cookie."""

    settings: DashboardSettings = request.app.state.settings.dashboard
    if settings.token is None:  # pragma: no cover - router not mounted if so
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    raw = request.cookies.get(settings.cookie_name)
    if raw is None or not verify_session_cookie(derive_signing_key(settings), raw):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)


def token_matches(provided: str, settings: DashboardSettings) -> bool:
    """Constant-time compare against the configured token."""

    expected = settings.token.get_secret_value() if settings.token else ""
    return bool(expected) and secrets.compare_digest(provided, expected)
