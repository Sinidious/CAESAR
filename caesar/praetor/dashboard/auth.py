"""Single-token cookie auth for the dashboard (ADR-0021).

The operator sets ``CAESAR_DASHBOARD__TOKEN``. The login form accepts
a matching token and sets a signed cookie. The signature uses
``itsdangerous`` with the same token as the secret — rotating the
token automatically invalidates outstanding sessions.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeSerializer

from caesar.config import DashboardSettings

COOKIE_VALUE = "ok"


def _serializer(token: str) -> URLSafeSerializer:
    return URLSafeSerializer(token, salt="caesar.dashboard")


def make_session_cookie(token: str) -> str:
    """Return the signed cookie value for a valid token."""

    return _serializer(token).dumps(COOKIE_VALUE)


def verify_session_cookie(token: str, raw_cookie: str) -> bool:
    """True iff ``raw_cookie`` was signed by ``token``."""

    try:
        loaded = _serializer(token).loads(raw_cookie)
    except BadSignature:
        return False
    return bool(loaded == COOKIE_VALUE)


def require_session(request: Request) -> None:
    """FastAPI dependency: 401 unless the request carries a valid cookie."""

    settings: DashboardSettings = request.app.state.settings.dashboard
    if settings.token is None:  # pragma: no cover - router not mounted if so
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    raw = request.cookies.get(settings.cookie_name)
    if raw is None or not verify_session_cookie(settings.token.get_secret_value(), raw):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)


def token_matches(provided: str, settings: DashboardSettings) -> bool:
    """Constant-time compare against the configured token."""

    expected = settings.token.get_secret_value() if settings.token else ""
    return bool(expected) and secrets.compare_digest(provided, expected)
