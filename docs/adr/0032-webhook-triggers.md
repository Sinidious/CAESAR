# 0032 — Webhook triggers: third Trigger source variant

- Status: Accepted
- Date: 2026-05-18
- Deciders: @Sinidious
- Related issues / PRs: v1.7 milestone; extends
  [ADR-0006](0006-praetor-runtime.md) (FastAPI surface),
  [ADR-0012](0012-audit-log.md) (new event types),
  [ADR-0030](0030-proactivity-schedules-and-ntfy.md) (Trigger
  discriminated union + ProactiveRunner),
  [ADR-0031](0031-ha-event-triggers.md) ("matcher coarse, prompt
  smart" principle).

## Context

v1.5 shipped scheduled triggers (cron). v1.6 shipped HA event
triggers (Home Assistant WS subscription). The natural third source
is **HTTP webhooks** — operators wire any external system (n8n,
IFTTT, GitHub, calendar services, custom shell scripts) by POSTing
JSON to a CAESAR endpoint.

Webhooks aren't novel — IFTTT and n8n have existed for years. The
question for CAESAR isn't "should we accept HTTP POSTs" — that's
trivial. The question is **how to fit webhooks into the trigger
model we already built** and **how to authenticate inbound POSTs
without inventing PKI**.

Webhooks also close a documented gap from ADR-0031 §7: the HA WS
subscription drops events during reconnect. Webhooks have durable
delivery semantics (the sender retries on failure), so operators
who care about reliability for a specific event can route it
through a webhook source instead of an HA event subscription.

## Decision

CAESAR v1.7 will ship **`WebhookSource`** as the third variant of
`TriggerSource`, expose a single FastAPI route
`POST /v1/hook/{trigger_id}` authenticated by per-trigger bearer
token, dispatch matching POSTs to `ProactiveRunner` as a
fire-and-forget background task, and surface the POST body to the
brain prompt as additional user-message context.

### 1 — Trigger shape extension

```python
class WebhookSource(BaseModel):
    kind: Literal["webhook"] = "webhook"
    bearer_token: SecretStr   # required; ≥32 chars after the prefix

TriggerSource = Annotated[
    ScheduleSource | HASource | WebhookSource,
    Field(discriminator="kind"),
]
```

`Trigger.cooldown_seconds` is reused unchanged — the same field that
suppresses repeat HA-event fires also suppresses repeat webhook fires
within the cooldown window.

### 2 — Endpoint

A single static route, registered at app startup:

```
POST /v1/hook/{trigger_id}
  Authorization: Bearer <token>
  Content-Type: application/json
  <free-form JSON body>
```

Response codes:

- **202 Accepted** — auth checked, trigger fired (asynchronously).
  Empty body. The sender doesn't wait for the brain run.
- **401 Unauthorized** — missing or wrong `Authorization` header.
  Audited as `webhook.unauthorized` (without the supplied token).
- **404 Not Found** — `trigger_id` doesn't match any armed webhook
  trigger. Audited as `webhook.unknown_trigger`.
- **413 Request Entity Too Large** — body exceeds 64 KiB. The audit
  log clamps strings at 16 KiB anyway (SR-008); rejecting at the
  edge keeps a wayward sender from filling Praetor's memory.
- **429 Too Many Requests** — only when `cooldown_seconds` is set
  AND the trigger is in cooldown. The body is dropped; the
  suppression coalesces into a `trigger.suppressed` audit row the
  same way HA cooldown does.

The route is registered **even when no webhook triggers are armed**.
This is intentional: a 404 for an unknown trigger is a stable
contract; surfacing it as "endpoint doesn't exist at all" depending
on config is hostile to operators debugging at 2am.

### 3 — Fire-and-forget dispatch

The route returns 202 as soon as auth + trigger-id checks pass. The
brain run happens in a background asyncio task:

```python
async def _route_handler(trigger_id: str, body: bytes, auth: str | None):
    trigger = registry.get(trigger_id)
    if trigger is None:
        await audit.record("webhook.unknown_trigger", {...})
        return 404
    if not _bearer_matches(auth, trigger):
        await audit.record("webhook.unauthorized", {"trigger_id": ...})
        return 401
    if dispatcher.is_in_cooldown(trigger):
        dispatcher.record_suppression(trigger, body)
        return 429
    await audit.record("webhook.received", {"trigger_id": ...})
    asyncio.create_task(dispatcher.fire(trigger, body))
    return 202
```

Why fire-and-forget:

- LLM calls take seconds-to-tens-of-seconds; a webhook sender that
  waits for 202 will time out and retry, causing duplicate fires.
- The dispatcher is the right owner of fire ordering and cooldown
  state, not the HTTP request handler.
- Errors inside the brain run land in `trigger.error` / `tool.error`
  audit rows; the sender doesn't see them. If you want delivery
  confirmation, look at the audit log.

### 4 — Body in the prompt

The dispatcher constructs the brain's user-message context as:

```
<trigger.prompt>

Event body:
<body, JSON-formatted, truncated to 16 KiB per SR-008>
```

The trigger.prompt is the operator's instructions; the body is data
the brain reads. The "matcher coarse, prompt smart" rule from
ADR-0031 §3 carries over: the matcher is just "POST to this URL with
this bearer"; the brain prompt decides what to do with whatever
arrived. There is no JSON-path filter, no field-level matching, no
templating syntax. Operators who want a webhook to be picky write
the prompt to bail out:

```yaml
prompt: |
  A GitHub event arrived. If it isn't a PR being opened against
  the main branch, reply "nothing to report". Otherwise summarise
  via notify.
```

The brain has the full body in its user message and can decide.

### 5 — Auth: per-trigger bearer token

Each `WebhookSource` carries a `bearer_token` (Pydantic `SecretStr`
so it doesn't leak into logs). Verification:

- Header `Authorization: Bearer <token>` is required.
- `<token>` is compared with `hmac.compare_digest` for
  constant-time equality.
- Mismatches emit a `webhook.unauthorized` row with the
  `trigger_id` (NEVER the supplied token; that's how leaked tokens
  end up in dashboard screenshots).

`caesar init` generates one fresh token per webhook trigger using
`secrets.token_urlsafe(36)` (288 bits of entropy). Operators rotate
by editing `triggers.yaml` and restarting Praetor.

**No HMAC body signing in v1.7.** Bearer over HTTPS defends the
realistic homelab threats (random scanners, hostile peers,
casual forgery). HMAC's marginal benefit (defends against a leaked
token reused without the body the sender originally signed) is
real but not worth the per-sender format quirks. We add HMAC in
v1.8 IF a specific sender forces our hand (e.g. an operator wires a
real GitHub webhook and wants signature verification).

**Rate limiting is per-trigger cooldown** — same field as HA
events. There's no global webhook rate limit; the loopback-bind
default (SR-001) is the only ambient defence against random
scanners. Operators who expose CAESAR to the Internet via
Tailscale/Cloudflare/proxy must rely on that proxy for global rate
limiting.

### 6 — `triggers.yaml`

Flat-form, consistent with v1.5/v1.6:

```yaml
triggers:
  - id: n8n_calendar_invite
    enabled: true
    cooldown_seconds: 30
    bearer_token: "wht_<48-char-random>"
    prompt: |
      A calendar invite arrived via n8n. The body has the event
      details — summarise time, attendees, and (if you can tell)
      whether it's worth flagging via notify.
```

The disambiguator extends:

- `cron` → schedule
- `event_type` → ha_event
- `bearer_token` → webhook

Having two of these in one entry is an error (existing v1.6 check,
expanded).

### 7 — Audit log

New event types:

- `webhook.received` — one row per accepted POST. Carries
  `trigger_id`, body size, source IP (if available from FastAPI).
- `webhook.unauthorized` — bad/missing bearer. Carries
  `trigger_id`, source IP. **Not the supplied token.**
- `webhook.unknown_trigger` — POST to an unknown id. Carries the
  path id, source IP.

`trigger.fired` / `trigger.completed` / `trigger.error` /
`trigger.suppressed` are reused unchanged.

### 8 — Reuse, not parallel implementation

`ProactiveRunner` (v1.5) handles the brain run unchanged — the
dispatcher constructs a Trigger with the body-augmented prompt and
hands it over. `WebhookDispatcher` shares the cooldown/suppression
shape from v1.6's HAEventDriver (per-trigger cooldown, coalesced
`trigger.suppressed` rows). All three drivers (Scheduler,
HAEventDriver, WebhookDispatcher) talk to the same runner and audit
log; they're independent on the input side and converge at the
brain.

## Alternatives considered

- **HMAC body signing as the default.** Rejected for the homelab
  audience: per-sender format quirks make it gnarly to ship, and
  bearer over HTTPS defends the realistic threats. Reserved as a
  v1.8 add-on if a sender forces it.
- **mTLS at a reverse proxy, no auth in CAESAR.** Smallest
  CAESAR-side surface but hostile to operators without PKI. Most
  homelab operators don't run a CA. Reverse proxy *can* still
  do mTLS in front of CAESAR; the bearer in CAESAR is then
  belt-and-suspenders.
- **JSON-path / JMESPath body matcher.** Rejected for the same
  reason ADR-0031 rejected it for HA events: the brain prompt has
  full body access and can reason about it. The trigger should
  stay a coarse wake-up signal.
- **Synchronous brain run inside the HTTP handler.** Rejected: LLM
  latency would time out most webhook senders, causing duplicate
  deliveries. Fire-and-forget with the audit log as the
  diagnostics surface is the right shape.
- **A queue (Redis / SQLite-backed) instead of `asyncio.create_task`.**
  Tempting for durability. Rejected for v1.7 because a Praetor
  restart only loses in-flight fires, not pending ones — webhook
  senders retry on failure, so durability lives in the sender, not
  in CAESAR. Reconsider if multi-Praetor HA ever lands.
- **Per-trigger URL paths the operator chooses** (e.g.
  `/v1/hook/morning_brief_handle` distinct from the trigger id).
  Rejected: trigger id is already a stable identifier, and adding a
  separate "URL slug" field is one more thing to keep in sync.
- **A "webhook payload schema" field** that validates body shape.
  Rejected: bloats the YAML and shifts smarts away from the brain.
  If a sender posts garbage, the brain prompt can say "if this
  doesn't look like a calendar invite, reply nothing to report."

## Consequences

### Positive

- Closes the durability gap from ADR-0031 §7. Operators who want
  reliable delivery (water leak, smoke alarm) can route via
  webhooks where the sender owns retry.
- Single architectural shape across all three trigger sources;
  ProactiveRunner unchanged, audit log unchanged, policy engine
  unchanged.
- `caesar init` generates a fresh token per trigger; the operator
  is never typing or pasting their own webhook secret. Reduces
  the "weak secret" failure mode.
- The route exists even with no webhook triggers configured —
  consistent 404 contract for an operator probing during setup.

### Negative

- The webhook endpoint is the first CAESAR surface explicitly
  intended for non-loopback exposure. Operators who expose CAESAR
  must understand SR-001 (the default is loopback-only). Documented
  loudly in the v1.7 operator guide.
- No replay protection in v1.7. A captured POST + bearer can be
  replayed forever. Acceptable for the homelab threat model;
  operators who care add timestamp + nonce signing in v1.8.
- Per-trigger bearer in `triggers.yaml` means rotating one token
  needs a file edit + restart. Acceptable at homelab cadence.

### Neutral

- One more YAML key (`bearer_token`) and one more flat-form
  disambiguator. Same flat-vs-nested pattern as the existing
  variants.
- 64 KiB body limit is a soft constant in the FastAPI route, not a
  configurable field. Operators who need bigger can raise an issue.
- `caesar init` adds a third disabled example (webhook). Generated
  config now ships three examples — one per source kind — all
  disabled.

## References

- [ADR-0006](0006-praetor-runtime.md) — FastAPI route surface.
- [ADR-0012](0012-audit-log.md) — `webhook.*` event types join the
  existing `trigger.*` family.
- [ADR-0013](0013-policy-engine.md) — proactive webhook fires hit
  the same policy engine as reactive runs.
- [ADR-0028](0028-tools-beyond-ha.md) — `notify` remains the obvious
  output for webhook-driven alerts.
- [ADR-0030](0030-proactivity-schedules-and-ntfy.md) — Trigger
  discriminated union, ProactiveRunner, audit conventions.
- [ADR-0031](0031-ha-event-triggers.md) — "matcher coarse, prompt
  smart" rule that v1.7 inherits.
- [SR-001](../SECURITY-REVIEW.md) — loopback-by-default bind is the
  default network exposure model.
