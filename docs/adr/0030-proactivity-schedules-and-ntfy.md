# 0030 — Proactivity: scheduled triggers and ntfy.sh notifications

- Status: Accepted
- Date: 2026-05-17
- Deciders: @Sinidious
- Related issues / PRs: v1.5 milestone; extends
  [ADR-0006](0006-praetor-runtime.md) (brain graph entry points),
  [ADR-0012](0012-audit-log.md) (new event types),
  [ADR-0013](0013-policy-engine.md) (proactive calls are policy-gated),
  [ADR-0028](0028-tools-beyond-ha.md) (notify is a Legion tool).

## Context

Through v1.4, CAESAR is reactive only. Every brain run is initiated by
an operator-facing request — a `/v1/chat` POST, a dashboard message, or
a (future) voice input. CAESAR can do useful things, but only when
asked.

A homelab assistant that doesn't act on its own is missing the
animating idea. v1.5's gate question — *"can CAESAR do something
without me asking?"* — pushes us into the proactivity space that v0.0's
README already promised under the **Praetor** vocabulary ("owns
intent"). Intent without initiative is a chatbot.

The smallest demonstrable proof is **a scheduled morning brief**:
every weekday at 7am, CAESAR runs a prompt, gathers what it needs
(weather, calendar, HA state), and pushes a notification to the
operator's phone.

We want to ship that one slice end-to-end before generalising to
HA-event triggers, webhook intakes, or operator-defined cron-style
goals. Each of those is a richer mode that reuses the same plumbing.

## Decision

CAESAR v1.5 will ship a **scheduler subsystem** that fires
declaratively-configured triggers into the existing brain graph, and a
**ntfy.sh notification sink** that the brain can call as a Legion tool
to reach the operator's phone. The new surface is opt-in; an operator
who never edits `schedules.yaml` sees no behavioural change.

### 1 — `Trigger` shape

A trigger is the v1.5 generalisation of "thing that starts a brain
run". One shape, multiple sources later:

```python
class Trigger(BaseModel):
    id: str                  # stable, used in audit + dedup
    enabled: bool = True
    prompt: str              # what the brain runs
    max_runtime_seconds: int = 300
    source: TriggerSource    # ScheduleSource for v1.5; HASource/WebhookSource later
```

`TriggerSource` is a discriminated union with one variant initially:

```python
class ScheduleSource(BaseModel):
    kind: Literal["schedule"] = "schedule"
    cron: str                # standard 5-field cron expression
    timezone: str = "UTC"
```

The brain entry point is `praetor.proactive.fire(trigger)` — a coroutine
that runs the trigger's prompt through the same graph as `/v1/chat`,
with three differences:

- Initial system message includes a `proactive=true` flag the safety
  preamble (per SR-004) uses to bias toward *summarise + notify*
  rather than *act on the house*.
- Brain decisions are audit-logged with `trigger_id` set, so the
  dashboard can show "what fired and what it did".
- The brain's first tool call must be `notify` unless an HA service
  call is also explicitly allowed for this trigger (see policy
  section). This prevents a runaway schedule from silently toggling
  devices.

### 2 — Scheduler runtime

A single in-process asyncio task per Praetor instance:

- Reads `schedules.yaml` at startup, watches it for changes via the
  same mechanism `policy.yaml` uses (mtime poll, not inotify; works
  on Windows + Docker bind mounts).
- For each enabled trigger with a `ScheduleSource`, computes the
  next fire time using
  [croniter](https://pypi.org/project/croniter/) (MIT-licensed,
  pure-Python, no native deps; matches POSIX cron semantics).
- Sleeps until the next fire, then `await`s
  `praetor.proactive.fire(trigger)` under
  `asyncio.timeout(max_runtime_seconds)`.
- Audit-logs `trigger.scheduled` at startup (so an operator can see
  what's armed) and `trigger.fired` / `trigger.skipped` /
  `trigger.timeout` at runtime.
- **Single-instance** for v1.5. Distributed scheduling (multi-Praetor
  HA) is explicitly out of scope. The Legion bus already supports
  multi-host workers, but only one process owns the schedule.

We do **not** adopt APScheduler. It's larger surface area than we
need, brings job-persistence semantics we don't want (every fire
must be reproducible from `schedules.yaml`, not a DB row), and pulls
in SQLAlchemy event hooks that complicate our Alembic story. The
asyncio + croniter combination is ~80 lines of code.

### 3 — `schedules.yaml`

Lives beside `policy.yaml`. Declarative YAML:

```yaml
schedules:
  - id: morning_brief
    enabled: false                    # ship disabled by default
    cron: "0 7 * * 1-5"
    timezone: "America/Los_Angeles"
    max_runtime_seconds: 120
    prompt: |
      It's 7am on a weekday. Summarise today's calendar, the weather,
      and any overnight HA state changes worth flagging. Then notify
      me with a 2-3 sentence brief.
```

`caesar init` writes this file with the example above
**disabled** — an operator who skips the file never wakes up to a
surprise 7am notification.

### 4 — `notify` Legion tool (ntfy.sh)

A new Legion worker — same shape as v1.3's calculator / web_search /
calendar_read:

- Capability: `notify`
- Backend: [ntfy.sh](https://ntfy.sh) — open-source, self-hostable,
  has iOS+Android apps, one HTTP POST to publish. Apache 2.0.
- Settings: `topic` (required), `base_url` (default
  `https://ntfy.sh`), `token` (optional, for self-hosted with auth),
  `default_priority` (1-5, default 3).
- Tool schema:

  ```python
  class NotifyInput(BaseModel):
      title: str = Field(min_length=1, max_length=200)
      message: str = Field(min_length=1, max_length=4096)
      priority: int | None = Field(None, ge=1, le=5)
      tags: list[str] = Field(default_factory=list, max_length=10)
  ```

- Output: `{ "delivered_at": "<iso>", "id": "<ntfy message id>" }`.

The sink is wired as a tool (not a side-channel) so:

- Audit log captures every notification (`notify.called` with the
  message body subject to the SR-008 16 KiB clamp).
- Policy can deny notifications based on content patterns —
  important for "don't leak guest-network HA state".
- Future sinks (Pushover, generic webhook, dashboard feed) are
  alternate `notify_*` tools sharing the same input shape, gated
  independently.

### 5 — Policy

Proactive runs hit the policy engine via the same `evaluate(call)`
path as reactive runs. The generalised `ToolCall` shape from ADR-0028
already covers `notify`; no engine changes.

The default policy seeded by `caesar init` adds:

```yaml
allowed_tools:
  - calculator         # existing
  - notify             # NEW — proactive output channel
```

HA service calls remain locked down by default. An operator who wants
their morning brief to also turn on the kitchen light must add the
service to `allowed_services` themselves.

### 6 — Audit log

Three new event types:

- `trigger.scheduled` — emitted at scheduler startup for each enabled
  trigger. Carries trigger id, cron, next fire time, timezone.
- `trigger.fired` — emitted when a trigger starts running. Carries
  trigger id and the prompt (subject to SR-008 clamp).
- `trigger.completed` / `trigger.timeout` / `trigger.error` —
  terminal states. Carry duration, tool calls made, final brain
  output.

Dashboard renders these in the existing timeline, with a filter chip
for "proactive only".

## Alternatives considered

- **HA state-change trigger first.** Higher "feels real" factor —
  CAESAR reacting to motion at 11pm is the canonical demo. Rejected
  as the *first* slice because the failure modes (WS reconnect, event
  replay, double-fire on flaky sensors) make the abstraction harder
  to design correctly. Scheduled-first lets us shape `Trigger` against
  a deterministic source, then add `HASource` cleanly in v1.6.
- **APScheduler.** Industry-standard, well-maintained. Rejected:
  larger surface than we need, persistent-job semantics we explicitly
  don't want (the YAML file is the source of truth), and an Alembic
  migration story we'd rather skip.
- **Pushover / Pushbullet / Discord webhook as the default sink.**
  All work; none are open-source + self-hostable. ntfy.sh fits the
  homelab/PolyForm-NC ethos.
- **Dashboard-feed-only notifications.** Lowest cost, no external
  dependency. Rejected as the *primary* sink because "homelab
  assistant that can't reach my phone" misses the point. The
  dashboard-feed sink is a sensible v1.6 follow-up.
- **Side-channel sink (not a tool).** Could short-circuit the policy
  engine and audit log. Rejected on principle: every external effect
  goes through `evaluate(call)` and is auditable. The brain treats
  "send a notification" with the same gravity as "turn on a light".
- **Cron syntax extensions (jitter, `@every 5m`).** Tempting for
  homelab use cases. Rejected for v1.5; standard 5-field cron is what
  operators already know.

## Consequences

### Positive

- Closes the original-vision gap: CAESAR can finally do something
  without being asked.
- Reuses brain graph, policy engine, audit log, Legion tool pattern
  unchanged. Net new surface is small (scheduler + one Legion
  worker + one YAML file + one safety-preamble flag).
- `notify` is useful in reactive runs too — the brain can choose to
  notify the operator at the end of a long-running `/v1/chat`
  conversation.
- ntfy.sh free server is fine for personal use; self-hosting
  documented for the privacy-conscious.

### Negative

- One more YAML file an operator can mis-configure. Mitigated by
  `caesar init` shipping a sensible disabled-by-default example.
- A misbehaving `schedules.yaml` (e.g. cron `* * * * *` plus a long
  prompt) can chew through LLM tokens. Mitigated by per-trigger
  `max_runtime_seconds` and the `trigger.timeout` audit row, but not
  by a global rate limit in v1.5 — that's a SECURITY-REVIEW follow-up
  if it becomes a real problem.
- Scheduler is single-instance. A second Praetor process would
  double-fire schedules. Mitigated by documenting "one Praetor per
  installation" (already the implicit assumption); revisit if
  multi-Praetor HA ever lands.

### Neutral

- croniter joins the default dependency set (~50KB, no native deps).
- New Legion worker means new policy entry. Operators upgrading from
  v1.4 with an existing `policy.yaml` need to add `notify` under
  `allowed_tools` themselves before the brain can use it; documented
  in the v1.5 release notes.
- `schedules.yaml` lives beside `policy.yaml`, not inside it. They
  evolve independently and conflate poorly — the operator picks
  *what's allowed* in one place and *what's automatic* in another.

## References

- [ADR-0006](0006-praetor-runtime.md) — brain graph; proactive runs
  enter at a new helper but execute the same nodes.
- [ADR-0012](0012-audit-log.md) — adds `trigger.*` event types.
- [ADR-0013](0013-policy-engine.md) — proactive tool calls are
  policy-gated like every other call.
- [ADR-0028](0028-tools-beyond-ha.md) — `notify` is a Legion tool of
  the same shape as calculator / web_search / calendar_read.
- [SR-004](../SECURITY-REVIEW.md) — proactive runs ship the safety
  preamble with a `proactive=true` bias.
- [SR-008](../SECURITY-REVIEW.md) — audit-log string clamp applies to
  trigger prompts and notification bodies.
- [croniter](https://pypi.org/project/croniter/) — chosen cron
  expression library.
- [ntfy.sh](https://ntfy.sh) — chosen notification backend.
