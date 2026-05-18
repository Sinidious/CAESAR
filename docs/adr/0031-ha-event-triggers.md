# 0031 — HA event triggers: subscribe, match, fire the brain

- Status: Accepted
- Date: 2026-05-17
- Deciders: @Sinidious
- Related issues / PRs: v1.6 milestone; extends
  [ADR-0007](0007-home-assistant-bridge.md) (HA bridge),
  [ADR-0030](0030-proactivity-schedules-and-ntfy.md) (Trigger shape +
  Scheduler + ProactiveRunner).

## Context

v1.5 shipped scheduled (cron-driven) proactive triggers — CAESAR can
do something at 7am on weekdays. The next obvious gate question is
**"can CAESAR react to my house without me asking?"** Motion at 11pm
when nobody should be there. Water leak sensor flipped. Garage door
open longer than usual. These are the "feels like a real assistant"
moments that schedules can't deliver.

The Trigger discriminated union from ADR-0030 was deliberately shaped
to grow: `ScheduleSource` was the first variant, with `HASource` and
`WebhookSource` slated for v1.6+. v1.6 ships the HA variant.

The hard question isn't "should we add HA triggers" — that was
settled in ADR-0030. The hard question is **what's CAESAR's role**
relative to Home Assistant's existing automation engine. HA already
has rich automation YAML, with triggers, conditions, and actions
honed over a decade. If v1.6 reimplements that, CAESAR becomes
"HA-automations-with-an-LLM-bolt-on" and the project's identity
collapses into chasing HA feature parity forever.

CAESAR's value is the brain. The brain runs with full audit-log
context, HA state via the Bridge, memory recall, all tool workers,
and any other capability the operator wires in. **That's already a
better rule engine than YAML conditions** — because it can reason
about state, context, and history together. So a v1.6 trigger should
be a coarse wake-up signal, not a rule. The trigger says "wake the
brain when motion happens"; the brain prompt handles "...but only
between 10pm and 6am, and only if the alarm is armed, and not if
I'm home — otherwise stay quiet."

That framing dictates everything below.

## Decision

CAESAR v1.6 will ship **`HASource`** as a second variant of
`TriggerSource`, with a deliberately small matcher grammar
(`event_type` + optional `entity_id` + optional `to` + optional
`time_window`), per-trigger `cooldown_seconds`, and a single
WS subscription per Praetor instance that demultiplexes to all
armed triggers.

### 1 — Trigger shape extension

```python
class HASource(BaseModel):
    kind: Literal["ha_event"] = "ha_event"
    event_type: str = "state_changed"   # the HA event to subscribe to
    entity_id: str | None = None         # constrain by entity (state_changed only)
    to: str | None = None                # constrain by new_state.state
    time_window: str | None = None       # "HH:MM-HH:MM" local time, optional

TriggerSource = Annotated[
    ScheduleSource | HASource,
    Field(discriminator="kind"),
]
```

`Trigger.cooldown_seconds: int | None = None` is added at the
trigger level (not the source) so a future webhook trigger can reuse
it. Default `None` means "fire every match" (correct for `event_type:
event.zwave_node_alive`-style one-shots); when set, the trigger
ignores matching events for the cooldown window after firing.

### 2 — Subscription model

**One** HA WebSocket subscription per Praetor instance, opened on
lifespan startup when at least one armed `HASource` trigger exists.
The subscriber demultiplexes incoming events to per-trigger matchers
in-process.

- Subscription type: `subscribe_events` with the **union** of
  `event_type` values across armed triggers (or omitted to receive
  all events, when at least one trigger asks for `state_changed`).
- Reconnect: exponential backoff (1s → 2s → 4s → ... up to 60s) with
  full jitter. The HA bridge's existing WS client (ADR-0007) already
  reconnects on transport errors; we extend it with this backoff.
- **Replay policy: drop everything during disconnect.** Triggers only
  fire on events received after the subscription is up. Operators
  trading reliability for HA semantics can use the HA built-in
  retention or a webhook source in v1.7. Documented loudly.

### 3 — Matcher grammar (the "simple matchers" decision)

The matcher fires when, in order:

1. `event_type` matches exactly.
2. For `state_changed`: if `entity_id` is set, the event's
   `data.entity_id` matches exactly. If `to` is set, the event's
   `data.new_state.state` matches exactly. `from` is *not* in v1.6;
   add when an operator asks.
3. For non-`state_changed` events: `entity_id` and `to` are ignored.
   Match on `event_type` alone — the brain decides what to do with
   the data.
4. `time_window` (if set) — `"HH:MM-HH:MM"` in the trigger's local
   timezone. Cross-midnight windows allowed (`"22:00-06:00"`).
   Window resolution is minute-level; HA fires events at sub-second
   resolution but trigger semantics are "during quiet hours" not
   "during this specific second."

Rejected matcher features (v1.6 ships without them):

- Multi-field AND. *"motion AND lights off"* should be in the prompt,
  where the brain has full state access. The matcher is for the
  cheap part of the filter; the brain is for the smart part.
- Multi-field OR. Just declare two triggers with the same prompt.
- `from` state matching. Adds 30 lines of code for one in a hundred
  use cases; add it when someone asks.
- Numeric thresholds (`above`/`below`). Same reasoning. The prompt
  can ask `"is the temperature in the basement above 80?"` and the
  brain reads the state via the HA bridge.

The escape hatch for the 1% of operators who genuinely need
multi-condition fan-in is **two triggers + a prompt that bails out**,
not a richer matcher.

### 4 — Cooldown semantics

`Trigger.cooldown_seconds`:

- `None` (default): no cooldown — every matching event fires the
  brain. Right default for low-frequency events
  (`event.water_leak_detected`).
- `N > 0`: after a successful fire, the trigger ignores matching
  events for N seconds. The matcher still runs (to update internal
  counters) and audits `trigger.suppressed` so operators can see
  how often suppression kicks in.

The cooldown is **per-trigger**, not global. Two motion sensors on
two triggers each have their own clock. Multiple matches during the
cooldown window are coalesced into one `trigger.suppressed` row with
a count (so the dashboard shows "suppressed 11 redundant matches"
rather than 11 separate rows).

### 5 — `triggers.yaml` (or extended `schedules.yaml`)

Two reasonable shapes:

- **Reuse `schedules.yaml`** for both scheduled and HA-event sources,
  discriminated by `source.kind`. Pro: one file, one mental model
  for "things that fire the brain on their own". Con: the filename
  becomes a misnomer.
- **Split into `triggers.yaml`** and deprecate `schedules.yaml`. Pro:
  honest naming. Con: breaks v1.5 operators' files.

**Decision: rename to `triggers.yaml`, accept `schedules.yaml` as a
deprecated alias for one release.** Praetor reads
`triggers.yaml` first, falls back to `schedules.yaml` with a
deprecation log line, and the v1.7 release drops the alias. ADR-0030
called the file `schedules.yaml` only because there was no other
source type at the time; honesty trumps backward-compat over a
one-release window. `caesar init` writes `triggers.yaml` immediately.

Within the file:

```yaml
version: 1

triggers:
  - id: late_office_motion
    enabled: true
    cooldown_seconds: 600
    prompt: |
      Motion in the office at this hour is unusual.
      Check whether anyone's home (state of person.* entities) and
      send a one-liner via notify with what you see.
    source:
      kind: ha_event
      event_type: state_changed
      entity_id: binary_sensor.office_motion
      to: "on"
      time_window: "22:00-06:00"
      timezone: "America/Los_Angeles"
```

The flat-form lift from ADR-0030 still applies: top-level `cron` /
`timezone` lift under `source.kind=schedule`; top-level
`event_type` / `entity_id` / `to` lift under `source.kind=ha_event`
when the operator omits the explicit `source:` block. The disambiguator
is whether the entry has `cron` (→ schedule) or `event_type` (→
ha_event); having both is an error.

### 6 — Audit log

New event types:

- `trigger.subscribed` — one row per armed HA trigger at startup,
  listing the event_type / entity_id / to / time_window it watches.
- `trigger.suppressed` — one row per cooldown-window suppression
  (coalesced; carries `count` and `first_event_at` / `last_event_at`).
- `ha.subscription.opened` / `ha.subscription.closed` /
  `ha.subscription.reconnected` — bridge-level lifecycle so the
  operator can see why triggers stopped firing.

Existing v1.5 audit types (`trigger.fired`, `trigger.completed`,
`trigger.error`, `trigger.timeout`) reused unchanged. The brain's
tool calls (`notify`, `call_service`, etc.) keep their existing
audit rows.

### 7 — Reuse, not parallel implementation

`ProactiveRunner` from v1.5 is reused unchanged. The HA driver
constructs a `Trigger` per matching event and hands it to
`runner.fire(trigger)`. Same brain graph, same proactive system
prompt bias, same decision-id prefix
(`proactive-<trigger_id>-<rand>`).

Scheduler (v1.5) keeps doing scheduled triggers. A separate
`HAEventDriver` does HA triggers. They share `ProactiveRunner` and
the audit log; otherwise they're independent.

## Alternatives considered

- **HA-automation YAML grammar** (platform / trigger / condition /
  action). Rejected: ties CAESAR's identity to HA's automation
  engine, implies feature parity is a goal, and the vocabulary
  (`platform: state`, `condition: time`) is wrong for "wake the
  brain on this event." HA already has automations; operators who
  want a pure rule-engine response should write it in HA.
- **JMESPath / Python predicates.** Rejected as the default — beginner-
  hostile and the YAML reads like code. Reasonable as a v1.7 escape
  hatch (`source.filter:` field accepting a JMESPath) IF an operator
  hits a real wall with simple matchers; not pre-built.
- **Multi-field matcher (AND/OR composition in YAML).** Rejected:
  the brain prompt is the right place for multi-condition logic
  because it has full state access. The matcher should stay coarse.
- **Per-trigger HA WS subscriptions** (instead of one shared). Rejected:
  HA imposes subscription limits and per-trigger reconnect makes the
  audit log noisy. One subscription with in-process demuxing is
  simpler and behaves more predictably under HA restarts.
- **Event replay on reconnect.** Rejected for v1.6: HA's WS API
  doesn't expose a stable "events since cursor" primitive, and
  emulating replay via the REST `history` API is fragile (different
  event types have different retention rules). Operators who need
  reliability use a webhook (v1.7) where they control delivery.
- **Coalesce events into one fire** (instead of cooldown). Rejected:
  cooldown matches operator mental model better (the trigger is "an
  alert"); coalescing fits better for "summarise the burst" use
  cases which aren't the v1.6 target. Reconsider in v1.7.
- **Keep the file named `schedules.yaml`.** Rejected: by v1.6 the
  file holds both schedules and HA triggers; the name becomes
  misleading. Renaming with a one-release deprecation alias keeps
  honesty without breaking existing operators.

## Consequences

### Positive

- Closes the v1.5 → v1.6 progression cleanly: same Trigger discriminated
  union, same ProactiveRunner, same audit log shape. The trust the
  operator built with scheduled triggers transfers to HA triggers
  unchanged.
- The "matcher is coarse, brain is smart" rule keeps CAESAR firmly
  on its own track instead of chasing HA's automation engine.
- Single-WS-subscription posture keeps HA happy on operators with
  many triggers; the cost is one shared reconnect path.
- ntfy.sh notify (v1.5) is the obvious response action and works
  unchanged. Operators get "motion at 11pm → phone alert" with no
  new tool work.

### Negative

- Replay-on-reconnect dropped is a real correctness gap for security-
  sensitive triggers (water leak during a 30s WS reconnect would be
  missed). Mitigated by HA's own automation engine handling the
  hard-real-time path; CAESAR's role is the LLM-mediated reactive
  layer on top.
- Simple matchers can't express AND/OR. Some operators will reach
  for multi-condition rules and find them missing. Mitigated by the
  "two triggers + bail-out prompt" pattern, documented loudly.
- Renaming `schedules.yaml` → `triggers.yaml` is the kind of change
  operators dislike. Mitigated by reading both for one release and
  emitting a deprecation log line on `schedules.yaml`.

### Neutral

- One more YAML file to ship via `caesar init`. Same write pattern as
  v1.5; ~10 lines of CLI changes.
- New audit event types (`trigger.subscribed`, `trigger.suppressed`,
  `ha.subscription.*`) join the existing `trigger.*` family. The
  dashboard's existing "trigger" filter chip picks them up unchanged.
- The shared HA WS subscription is the only single point of failure
  for all HA triggers; the bridge's existing reconnect machinery is
  the mitigation.

## References

- [ADR-0007](0007-home-assistant-bridge.md) — HA WS client that v1.6
  extends with a backoff-aware reconnect path.
- [ADR-0013](0013-policy-engine.md) — proactive runs hit the same
  policy engine as reactive ones.
- [ADR-0028](0028-tools-beyond-ha.md) — `notify` is the obvious tool
  for HA-triggered alerts.
- [ADR-0030](0030-proactivity-schedules-and-ntfy.md) — Trigger
  discriminated union, ProactiveRunner, audit-log conventions.
- [crontab.guru](https://crontab.guru/) — irrelevant to HA triggers
  but documented in the v1.5 docs page for the schedule source;
  v1.6 docs link the same.
