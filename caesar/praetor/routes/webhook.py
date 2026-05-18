"""``/v1/hook/{trigger_id}`` — the v1.7 webhook intake route (ADR-0032).

Authenticates inbound POSTs against the per-trigger bearer token,
enforces per-trigger cooldown, audits the request, and schedules the
brain run as a fire-and-forget background task via
:class:`WebhookDispatcher`.

The route is registered unconditionally at app startup. When no
webhook triggers are armed, every POST gets a 404 with a
``webhook.unknown_trigger`` audit row — that's the stable debugging
contract from ADR-0032 §2.
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Header, Request, Response

from caesar.proactive.webhook_dispatcher import MAX_BODY_BYTES, WebhookDispatcher

router = APIRouter(tags=["webhook"])


def _get_dispatcher(request: Request) -> WebhookDispatcher | None:
    return cast(
        "WebhookDispatcher | None",
        getattr(request.app.state, "webhook_dispatcher", None),
    )


def _source_ip(request: Request) -> str | None:
    """Best-effort source IP for audit. ``request.client`` is None
    when the test client uses ASGI transport directly."""

    if request.client is None:  # pragma: no cover - ASGITransport always populates
        return None
    return request.client.host


def _strip_bearer(header: str | None) -> str | None:
    if header is None:
        return None
    prefix = "Bearer "
    if not header.startswith(prefix):
        return None
    return header[len(prefix) :]


@router.post("/v1/hook/{trigger_id}", status_code=202)
async def receive_webhook(
    trigger_id: str,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
) -> Response:
    dispatcher = _get_dispatcher(request)
    source_ip = _source_ip(request)

    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return Response(status_code=413)

    # No dispatcher = proactive subsystem off; pretend the trigger
    # doesn't exist (operator's config didn't wire one).
    if dispatcher is None:
        return Response(status_code=404)

    trigger = dispatcher.get(trigger_id)
    if trigger is None:
        await dispatcher.record_unknown_trigger(trigger_id, source_ip=source_ip)
        return Response(status_code=404)

    supplied = _strip_bearer(authorization)
    if not dispatcher.verify_bearer(trigger, supplied):
        await dispatcher.record_unauthorized(trigger_id, source_ip=source_ip)
        return Response(status_code=401)

    if dispatcher.is_in_cooldown(trigger):
        dispatcher.record_suppression(trigger_id)
        return Response(status_code=429)

    await dispatcher.record_received(
        trigger,
        body_bytes=len(body),
        source_ip=source_ip,
    )
    dispatcher.spawn_fire(trigger, body)

    # Force 202 even though the default body is empty; some frameworks
    # rewrite to 200 when the response body is empty.
    response.status_code = 202
    return Response(status_code=202)
