"""Dashboard routes (ADR-0021).

All routes live under ``/dashboard``. The router is built only when
``CAESAR_DASHBOARD__TOKEN`` is configured (see ``caesar.praetor.app``);
when absent, the routes don't exist at all.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.config import CaesarSettings, DashboardSettings
from caesar.db.audit import AuditLogger
from caesar.db.schema import audit_log
from caesar.db.settings_store import SettingsStore
from caesar.legion.registry import WorkerRegistry
from caesar.log import get_logger
from caesar.praetor.audit_bus import AuditEventBus
from caesar.praetor.dashboard.auth import (
    make_session_cookie,
    require_session,
    token_matches,
)
from caesar.praetor.dashboard.rate_limit import LoginRateLimiter
from caesar.praetor.dashboard.views import load_agent_activity, load_intents

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_ROOT / "templates"
STATIC_DIR = PACKAGE_ROOT / "static"

logger = get_logger("caesar.praetor.dashboard")


def _settings_from(request: Request) -> DashboardSettings:
    return cast(DashboardSettings, request.app.state.settings.dashboard)


def _engine_from(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.engine)


def _bus_from(request: Request) -> AuditEventBus:
    return cast(AuditEventBus, request.app.state.audit_bus)


def _registry_from(request: Request) -> WorkerRegistry | None:
    return cast("WorkerRegistry | None", request.app.state.registry)


def _settings_store_from(request: Request) -> SettingsStore:
    return cast(SettingsStore, request.app.state.settings_store)


def _audit_from(request: Request) -> AuditLogger:
    return cast(AuditLogger, request.app.state.audit)


def _caesar_settings_from(request: Request) -> CaesarSettings:
    return cast(CaesarSettings, request.app.state.settings)


def _login_rate_limiter_from(request: Request) -> LoginRateLimiter:
    return cast(LoginRateLimiter, request.app.state.login_rate_limiter)


def _client_key(request: Request) -> str:
    """Identifier for rate-limit bucketing. Source IP, or 'unknown'."""

    return request.client.host if request.client else "unknown"


SettingsDep = Annotated[DashboardSettings, Depends(_settings_from)]
EngineDep = Annotated[AsyncEngine, Depends(_engine_from)]
BusDep = Annotated[AuditEventBus, Depends(_bus_from)]
RegistryDep = Annotated["WorkerRegistry | None", Depends(_registry_from)]
SettingsStoreDep = Annotated[SettingsStore, Depends(_settings_store_from)]
AuditDep = Annotated[AuditLogger, Depends(_audit_from)]
CaesarSettingsDep = Annotated[CaesarSettings, Depends(_caesar_settings_from)]
LoginRateLimiterDep = Annotated[LoginRateLimiter, Depends(_login_rate_limiter_from)]
SessionDep = Annotated[None, Depends(require_session)]


def build_router() -> APIRouter:
    """Construct the dashboard router. Mounts no static files itself."""

    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    router = APIRouter(prefix="/dashboard", tags=["dashboard"])

    @router.get("/login", response_class=HTMLResponse)
    async def get_login(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @router.post("/login", response_model=None)
    async def post_login(
        request: Request,
        token: Annotated[str, Form()],
        settings: SettingsDep,
        limiter: LoginRateLimiterDep,
    ) -> HTMLResponse | RedirectResponse:
        client = _client_key(request)
        if not limiter.check(client):
            retry_after = int(limiter.retry_after_seconds(client)) + 1
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": f"Too many failed attempts. Try again in {retry_after}s."},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(retry_after)},
            )
        if not token_matches(token, settings):
            limiter.record_failure(client)
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Invalid token."},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        assert settings.token is not None
        cookie = make_session_cookie(settings.token.get_secret_value())
        response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            settings.cookie_name,
            cookie,
            max_age=settings.cookie_max_age_seconds,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.post("/logout")
    async def post_logout(settings: SettingsDep) -> RedirectResponse:
        response = RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie(settings.cookie_name)
        return response

    @router.get("", response_class=HTMLResponse)
    async def home(request: Request, _: SessionDep) -> HTMLResponse:
        return templates.TemplateResponse(request, "home.html", {})

    @router.get("/audit", response_class=HTMLResponse)
    async def audit_rows(
        request: Request,
        _: SessionDep,
        settings: SettingsDep,
        engine: EngineDep,
    ) -> HTMLResponse:
        stmt = select(audit_log).order_by(desc(audit_log.c.id)).limit(settings.history_limit)
        async with engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        items = [
            {
                "id": int(row["id"]),
                "ts": row["ts"].isoformat() if row["ts"] is not None else "",
                "event_type": row["event_type"],
                "payload": json.dumps(row["payload"] or {}, default=str)[:200],
            }
            for row in rows
        ]
        return templates.TemplateResponse(request, "_audit_rows.html", {"items": items})

    @router.get("/intents", response_class=HTMLResponse)
    async def intents(
        request: Request,
        _: SessionDep,
        settings: SettingsDep,
        engine: EngineDep,
    ) -> HTMLResponse:
        intents = await load_intents(engine, limit=settings.history_limit)
        return templates.TemplateResponse(request, "intents.html", {"intents": intents})

    @router.get("/agents", response_class=HTMLResponse)
    async def agents(
        request: Request,
        _: SessionDep,
        settings: SettingsDep,
        engine: EngineDep,
        registry: RegistryDep,
    ) -> HTMLResponse:
        workers = registry.workers if registry is not None else {}
        agent_rows, dispatches = await load_agent_activity(
            engine, workers=workers, history_limit=settings.history_limit
        )
        return templates.TemplateResponse(
            request,
            "agents.html",
            {
                "agents": agent_rows,
                "dispatches": dispatches,
                "bus_enabled": registry is not None,
            },
        )

    @router.get("/settings", response_class=HTMLResponse)
    async def get_settings_page(
        request: Request,
        _: SessionDep,
        store: SettingsStoreDep,
        caesar_settings: CaesarSettingsDep,
    ) -> HTMLResponse:
        current = await store.get_system_prompt()
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "system_prompt": current
                if current is not None
                else caesar_settings.llm.system_prompt,
                "is_overridden": current is not None,
                "default_prompt": caesar_settings.llm.system_prompt,
                "saved": False,
            },
        )

    @router.post("/settings", response_model=None)
    async def post_settings(
        request: Request,
        _: SessionDep,
        store: SettingsStoreDep,
        audit: AuditDep,
        caesar_settings: CaesarSettingsDep,
        system_prompt: Annotated[str, Form()],
    ) -> HTMLResponse:
        prompt = system_prompt.strip()
        if prompt:
            await store.set_system_prompt(prompt)
            await audit.record(
                "settings.updated",
                {"key": "llm.system_prompt", "length": len(prompt)},
            )
            # SR-012: surface the override in operator process logs as
            # well as the audit log. An attacker who reaches the
            # settings page (post-SR-002) shouldn't be able to rewrite
            # the brain's persona silently.
            logger.warning(
                "dashboard.system_prompt_override_set",
                source_ip=_client_key(request),
                length=len(prompt),
            )
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "system_prompt": prompt or caesar_settings.llm.system_prompt,
                "is_overridden": bool(prompt),
                "default_prompt": caesar_settings.llm.system_prompt,
                "saved": True,
            },
        )

    @router.get("/audit/stream")
    async def audit_stream(_: SessionDep, bus: BusDep) -> StreamingResponse:
        # The generator body itself can't be exercised through httpx's
        # ASGITransport (it buffers the entire response before returning),
        # so the streaming is verified by AuditEventBus unit tests + the
        # auth check on this route. End-to-end SSE is manually tested
        # with a real uvicorn process.
        async def _generate() -> AsyncIterator[bytes]:  # pragma: no cover
            async for event in bus.subscribe():
                payload = json.dumps(event, default=str)
                yield f"event: audit\ndata: {payload}\n\n".encode()

        return StreamingResponse(_generate(), media_type="text/event-stream")

    return router


def static_dir_or_404() -> Path:
    """Return the static directory; 404 if it doesn't exist."""

    if not STATIC_DIR.is_dir():  # pragma: no cover - directory always exists
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return STATIC_DIR
