# Proactive CAESAR

By default CAESAR is reactive — it answers `/v1/chat` requests and
dashboard messages. **Proactive mode** lets the brain *start* runs on
its own and reach your phone via [ntfy.sh](https://ntfy.sh). Three
trigger sources, all living in the same `triggers.yaml`:

- **Schedules** (cron-like) — wake the brain at a fixed time. The
  canonical example is a morning brief: every weekday at 7am,
  summarise your calendar + weather + overnight HA state and push
  a notification.
- **HA events** (v1.6+) — wake the brain when Home Assistant fires
  an event. Motion at 11pm. Water leak. Garage door open longer
  than usual.
- **Webhooks** (v1.7+) — wake the brain when *any external system*
  POSTs JSON to CAESAR. n8n, IFTTT, GitHub repos, custom shell
  scripts. Each trigger gets its own bearer token; `caesar init`
  generates one per trigger.

All three share the same brain entry and audit-log under the same
`proactive-<trigger_id>-` decision-id prefix. The matcher is
**deliberately coarse** — wake the brain on "motion happens" or
"a POST arrived", let the brain prompt decide *whether to do
anything about it*.

ADR-0030, ADR-0031, and ADR-0032 cover the underlying design.

## What's in scope

After this page you'll have:

- A `triggers.yaml` declaring when CAESAR wakes itself up.
- A configured `notify` Legion worker so the brain can reach you.
- A policy entry allowing the `notify` tool.
- An audit log row for every proactive fire — `trigger.scheduled` /
  `trigger.subscribed`, `trigger.fired`, `trigger.completed`, plus any
  tools the brain called.

Proactivity is **opt-in** at every layer:

- `CAESAR_PROACTIVE__TRIGGERS_PATH` unset → no scheduler, no HA driver.
- `enabled: false` on a trigger → loaded but never fires.
- `notify` not on `allowed_tools` → the brain can't push to your phone
  even if a trigger fires.

So the failure mode of "I configured this wrong" is silence, not a
runaway alert at 3am.

## Step 1 — Decide on a topic

Pick (or sign up for) an ntfy topic. The public server at
`https://ntfy.sh` is fine for personal use; self-host if you'd rather
keep notifications inside your homelab.

- **Public ntfy.sh**: pick a long, random topic name. Public topics
  have no auth — anyone who guesses the name can subscribe. A
  20+ character random string is the practical fix.
- **Self-hosted**: see [ntfy docs](https://docs.ntfy.sh/install/).
  Once running, set `base_url` to your server and (optionally)
  `token` to a per-CAESAR bearer token.

Install the ntfy iOS or Android app and subscribe to your topic; that's
how the notifications reach you.

## Step 2 — Configure `notify` in `.env`

```sh
# .env
CAESAR_TOOLS__NOTIFY__TOPIC=caesar-<your-long-random-string>
# Optional — defaults to https://ntfy.sh:
# CAESAR_TOOLS__NOTIFY__BASE_URL=https://ntfy.example.com
# Optional — only needed for self-hosted with auth:
# CAESAR_TOOLS__NOTIFY__TOKEN=tk_...
```

Then add `notify` to the in-process worker list (most operators run
the brain and the notify worker on the same box):

```sh
CAESAR_LEGION__INPROCESS_WORKERS=["memory_recall","notify"]
```

`caesar init` already lists `notify` under `allowed_tools` in the
generated `policy.yaml`, so the brain is allowed to call it as soon
as the worker is running.

## Step 3 — Author `triggers.yaml`

`caesar init` writes a starter file with three disabled examples —
one per source type:

```yaml
version: 1

triggers:
  # Schedule (cron) source — fires at a fixed time.
  - id: morning_brief
    enabled: false                    # flip to true to arm
    cron: "0 7 * * 1-5"               # 7am, weekdays
    timezone: "America/Los_Angeles"   # your IANA timezone
    max_runtime_seconds: 120
    prompt: |
      It's 7am on a weekday. Summarise today's calendar (if
      calendar_read is configured), the weather (if web_search
      is configured), and any overnight HA state changes worth
      flagging. Then send a 2-3 sentence brief via `notify`.

  # HA-event source — fires on a Home Assistant event.
  - id: late_office_motion
    enabled: false
    event_type: state_changed
    entity_id: binary_sensor.office_motion
    to: "on"
    time_window: "22:00-06:00"
    timezone: "America/Los_Angeles"
    cooldown_seconds: 600
    prompt: |
      Motion in the office at this hour is unusual. Check whether
      anyone's home (state of person.* entities) and send a one-liner
      via notify with what you see.

  # Webhook source — external systems POST to /v1/hook/<trigger_id>
  # with the bearer token. caesar init mints a fresh 48-char token.
  - id: external_event
    enabled: false
    bearer_token: "<48-char-random-from-caesar-init>"
    cooldown_seconds: 30
    prompt: |
      An external event arrived. Summarise what happened in 1-2
      sentences via notify, or stay quiet ("nothing to report")
      if it doesn't look worth flagging.
```

### Schedule field reference

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Snake-case identifier. Used in `trigger_id` on audit rows; pick something greppable. |
| `enabled` | no (default `true`) | `false` keeps the trigger loaded but unarmed. |
| `cron` | yes | Standard 5-field cron (`min hr dom mon dow`). [crontab.guru](https://crontab.guru/) is great for sanity-checking. |
| `timezone` | no (default `UTC`) | Any IANA name. DST is handled by croniter; daylight-saving boundaries don't double-fire. |
| `max_runtime_seconds` | no (default `300`, max `3600`) | Hard cap on one fire's brain run. |
| `prompt` | yes | What the brain sees as the user message. |

### HA-event field reference

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Same as schedule — snake-case identifier. |
| `enabled` | no (default `true`) | Same. |
| `event_type` | yes | The HA event to subscribe to. `state_changed` is the common case; others (`zwave_node_alive`, `automation_triggered`, custom events) work too. |
| `entity_id` | no | Constrains `state_changed` events to one entity (`domain.entity` format). Ignored for non-state events. |
| `to` | no | Constrains `state_changed` events to a specific new-state value (e.g. `"on"`, `"home"`, `"unavailable"`). Ignored for non-state events. |
| `time_window` | no | `"HH:MM-HH:MM"` in 24h time. Inclusive start, exclusive end. Cross-midnight allowed (`"22:00-06:00"` means 10pm–6am). |
| `timezone` | no (default `UTC`) | IANA name. Used only for `time_window` evaluation. |
| `cooldown_seconds` | no (default `None`) | After firing, suppress matching events for N seconds. Coalesced suppressions land in one `trigger.suppressed` audit row. |
| `max_runtime_seconds` | no (default `300`, max `3600`) | Same cap as schedule triggers. |
| `prompt` | yes | What the brain sees. |

### Webhook field reference

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Same as the others. Used as the URL path: `POST /v1/hook/{id}`. |
| `enabled` | no (default `true`) | Same. |
| `bearer_token` | yes | ≥32 characters. `caesar init` generates a fresh 48-char token via `secrets.token_urlsafe(36)`. Stored as a `SecretStr` — never logged. |
| `cooldown_seconds` | no (default `None`) | Same suppression semantics as HA events. Inside the cooldown window, repeat POSTs return 429 and coalesce into one `trigger.suppressed` audit row. |
| `max_runtime_seconds` | no (default `300`, max `3600`) | Same cap as the others. |
| `prompt` | yes | What the brain sees. The POST body is appended as `"Event body:\n<JSON-formatted or raw text>"`. |

### Flat vs nested form

Both shapes load identically; pick whichever reads better:

```yaml
# Flat (recommended for the common case)
triggers:
  - id: hourly_ping
    cron: "0 * * * *"
    prompt: tell me one fact

# Nested (forward-compat with v1.7's webhook source)
triggers:
  - id: hourly_ping
    prompt: tell me one fact
    source:
      kind: schedule
      cron: "0 * * * *"
      timezone: UTC
```

## Step 4 — Point Praetor at the file

`caesar init` already writes this line:

```sh
# .env
CAESAR_PROACTIVE__TRIGGERS_PATH=./triggers.yaml
```

When this variable is **unset**, neither the scheduler nor the
HA-event driver is constructed and CAESAR behaves exactly like v1.4
— reactive only.

## Step 5 — Arm a trigger and restart

Edit `triggers.yaml`, flip `enabled: true` on the trigger you want,
restart Praetor. At startup you'll see one audit row per armed
trigger:

```
event_type: trigger.scheduled                event_type: trigger.subscribed
payload:                                     payload:
  trigger_id: morning_brief                    trigger_id: late_office_motion
  cron: "0 7 * * 1-5"                          event_type: state_changed
  timezone: America/Los_Angeles                entity_id: binary_sensor.office_motion
  next_fire_at: "2026-05-19T14:00:00+00:00"    to: "on"
                                               time_window: "22:00-06:00"
                                               cooldown_seconds: 600
```

When the trigger fires, the scheduler/driver:

1. Writes `trigger.fired` (`trigger_id`, `prompt`, …).
2. Runs `trigger.prompt` through the brain graph, with a
   `proactive=true` system-prompt bias that pushes the LLM toward
   *summarise-and-notify, don't act on the house*.
3. Each tool the brain calls (`notify`, `calculator`, `web_search`,
   `calendar_read`, …) goes through the policy engine and writes its
   normal `tool.called` / `tool.denied` audit rows.
4. Writes `trigger.completed` on success, or
   `trigger.timeout` / `trigger.error` on failure.

Every audit row carries a `decision_id` prefixed
`proactive-<trigger_id>-`, so filtering the dashboard timeline to
proactive activity is one substring match away.

## Reacting to HA events

This is the heart of v1.6. Two design decisions are worth knowing
because they shape how you write triggers:

### The matcher is coarse on purpose

Each `triggers.yaml` entry can constrain on **one** entity and **one**
new-state value. You cannot AND multiple conditions in the matcher,
and you cannot write boolean expressions. This is deliberate
(ADR-0031 §3): CAESAR's value is the brain, and the brain has full
HA-state access. Put the cheap part of the filter in YAML (event
type, entity, time window) and the smart part in the prompt:

```yaml
- id: garage_left_open
  event_type: state_changed
  entity_id: binary_sensor.garage_door
  to: "on"               # door opened
  cooldown_seconds: 1800 # don't spam within 30 minutes
  prompt: |
    The garage door just opened. Look up the state of:
      - person.* (who's home)
      - sun.sun (is it night?)
      - alarm_control_panel.home (is the alarm armed?)
    If it's the middle of the night with nobody home and the
    alarm is armed, alert me with the time and what you found.
    Otherwise stay quiet — reply "nothing to report".
```

The brain has access to the HA bridge via the `call_service` /
state tools, so multi-condition logic ("door open AND it's night
AND nobody home AND alarm armed") lives in the prompt where it
gets fresh state every fire — not in stale YAML that can't see
what changed since the last reload.

### Cooldown is per-trigger and in-memory

`cooldown_seconds: 600` means "after this trigger fires, ignore
matching events for 10 minutes". The 11 motion events your sensor
emits while someone walks across the room get coalesced into one
`trigger.suppressed` audit row carrying `count: 11`. The next
allowed fire flushes that row.

The cooldown clock lives in memory — a Praetor restart resets every
cooldown. That's fine for the common case (operators rarely restart
in the middle of an alert burst), but it does mean a triggered alert
can theoretically refire within seconds of a restart. If you care
about that gap, raise it in an issue.

### What happens during HA reconnects

CAESAR keeps one shared WebSocket subscription to HA and reconnects
with exponential backoff if it drops (ADR-0031 §2). **Events emitted
during the disconnect are not replayed.** This is a deliberate
correctness gap: HA's WS API doesn't expose a stable cursor, and
emulating replay via the REST `history` endpoint is fragile. If
you need durable delivery for a security-sensitive trigger (water
leak, smoke detector), write the automation in HA itself — that's
HA's job, not CAESAR's.

A small set of audit events tracks the WS lifecycle:

- `ha.subscription.opened` — first successful connect.
- `ha.subscription.reconnected` — every subsequent connect, with
  rising `connect_count`.
- `ha.subscription.closed` — on stop or unrecoverable error.

If your HA triggers stop firing, those rows are the first place to
look.

### Worked examples

The morning-brief example covers schedules. For HA events:

**Motion in office between 10pm and 6am.** Coarse matcher; brain
prompt checks whether anyone's home before alerting:

```yaml
- id: late_office_motion
  event_type: state_changed
  entity_id: binary_sensor.office_motion
  to: "on"
  time_window: "22:00-06:00"
  timezone: "America/Los_Angeles"
  cooldown_seconds: 600
  prompt: |
    Motion in the office during quiet hours. Look up the state of
    person.* entities — if anyone's home, stay quiet ("nothing to
    report"). Otherwise send a brief alert via `notify` saying
    when it happened and where.
```

**Water leak detected.** No cooldown — every leak event matters:

```yaml
- id: water_leak
  event_type: state_changed
  entity_id: binary_sensor.basement_leak
  to: "on"
  prompt: |
    Water leak detected in the basement. Send an URGENT notify
    (priority: 5) with the sensor name, the time, and any nearby
    sensor state that might be relevant.
```

**ZWave node went offline.** Non-state event; only `event_type`
matches:

```yaml
- id: zwave_node_offline
  event_type: zwave_js_node_status
  cooldown_seconds: 3600   # one alert per hour max
  prompt: |
    A ZWave node changed status. Summarise which one and what
    happened in one sentence via `notify`. If the new status is
    "alive" after a "dead", say "back online" — those are noise.
```

## Reacting to webhooks

The third source (v1.7+) lets any external system fire the brain by
POSTing JSON. The wire shape:

```
POST /v1/hook/{trigger_id}
  Authorization: Bearer <bearer_token from triggers.yaml>
  Content-Type: application/json
  <free-form JSON body>
```

Response codes:

- **202 Accepted** — auth + trigger-id check passed; brain fires in
  a background task. The HTTP response returns immediately so a slow
  LLM run can't time out the sender.
- **401 Unauthorized** — missing, malformed, or wrong
  `Authorization` header. Audited as `webhook.unauthorized` —
  **without ever logging the supplied bearer**.
- **404 Not Found** — `trigger_id` doesn't match any armed webhook
  trigger. Same response for "no webhook triggers configured at
  all" so probing operators get a stable contract.
- **413 Request Entity Too Large** — body exceeded 64 KiB. The
  audit-log clamp (SR-008) is 16 KiB anyway; rejecting at the edge
  keeps a wayward sender from filling Praetor's memory.
- **429 Too Many Requests** — only when `cooldown_seconds` is set
  and the trigger is currently in cooldown. The body is dropped;
  suppressions coalesce into one `trigger.suppressed` row with a
  count, flushed on the next allowed fire.

### Auth: per-trigger bearer token

Each webhook trigger has its own opaque bearer token. `caesar init`
generates one per trigger using `secrets.token_urlsafe(36)` — 288
bits of entropy, well above any realistic guessing threat. Tokens
are stored as Pydantic `SecretStr` so they don't leak into structlog
output or stack traces.

Verification uses `hmac.compare_digest` (constant-time) so an
attacker can't probe the token byte-by-byte via timing.

**No HMAC body signing in v1.7.** Bearer over HTTPS defends the
realistic homelab threats (random scanners, hostile peers, casual
forgery). HMAC's marginal benefit (defends a leaked token from
replay if the sender signed a different body) isn't worth the
per-sender format quirks. ADR-0032 documents the deferral; v1.8 may
add HMAC if a specific sender forces it.

### Body in the prompt

The body is appended to the brain's user message as:

```
<trigger.prompt>

Event body:
<body, pretty-printed if JSON, raw text otherwise>
```

JSON bodies get sorted-key, indented formatting so the prompt is
readable. Non-JSON bodies pass through as UTF-8 text. The brain has
the full body (up to 64 KiB) and decides what to do with it. There
is **no JSON-path filter, no templating syntax** — the matcher
stays coarse and the brain prompt does the work:

```yaml
prompt: |
  A GitHub event arrived. If it isn't a PR being opened against
  the main branch, reply "nothing to report". Otherwise summarise
  via notify.
```

### Network exposure

By default CAESAR binds to **loopback only** (SR-001). Webhooks
from outside your box don't reach it until you expose CAESAR
through one of:

- **Tailscale Funnel** — easiest for personal use; auth is your
  tailnet; the public URL has the bearer in the path. Set
  `CAESAR_SERVER__HOST=0.0.0.0` and let Funnel front it.
- **Cloudflare Tunnel** — TLS terminates at Cloudflare's edge;
  authentication is your bearer.
- **Reverse proxy on your LAN** — nginx / Caddy / Traefik. Add
  rate-limiting and IP allow-lists here; CAESAR doesn't do
  global rate-limiting itself.
- **Direct port-forward** — works but ill-advised; no edge layer
  to mitigate scanners.

Whichever you pick, the bearer token in `triggers.yaml` is the auth
boundary. Don't reuse one bearer across multiple senders; let
`caesar init` mint one per trigger.

### Worked examples

**n8n calendar invite.** n8n posts the event JSON to CAESAR; the
brain summarises and notifies:

```yaml
- id: n8n_calendar_invite
  bearer_token: "wht_<48-char-random>"
  cooldown_seconds: 30
  prompt: |
    A calendar invite arrived via n8n. The body has the event
    details. Summarise time, attendees, and (if you can tell)
    whether it looks worth flagging. Then notify with a one-liner.
    Stay quiet if it's routine.
```

n8n setup: HTTP Request node → POST `https://caesar.example/v1/hook/n8n_calendar_invite`
→ Add header `Authorization: Bearer wht_...` → send the event body.

**GitHub PR opened.** GitHub posts the full repo event payload;
the prompt does the filter:

```yaml
- id: github_repo_event
  bearer_token: "wht_<48-char-random>"
  prompt: |
    A GitHub webhook arrived for one of my repos. If this is a PR
    being opened against `main`, summarise the title and author
    via notify. If it's anything else (push, issue, comment,
    review, label), reply "nothing to report" and don't fire
    notify.
```

GitHub's webhook UI: target URL + `Authorization: Bearer …` is
NOT supported via the standard webhook delivery (GitHub uses HMAC
or no auth). Use a small relay (Cloudflare Worker, n8n) that
verifies GitHub's HMAC, then forwards to CAESAR with the bearer
header. This is the deliberate v1.7 trade-off; HMAC support lands
in v1.8 if anyone wires the demand.

**Custom shell script.** A cron job posts a sensor reading every
hour; CAESAR decides whether it's anomalous:

```sh
curl -sS -X POST https://caesar.example/v1/hook/sensor_reading \
  -H "Authorization: Bearer wht_<random>" \
  -H "Content-Type: application/json" \
  -d '{"sensor": "garage_co", "ppm": 19, "ts": "2026-05-18T10:00:00Z"}'
```

```yaml
- id: sensor_reading
  bearer_token: "wht_<48-char-random>"
  cooldown_seconds: 3600       # one alert per hour max
  prompt: |
    A custom sensor reading arrived. Check: is the value above the
    sensor's safe threshold? (Garage CO above 30ppm is unsafe.)
    If unsafe, notify URGENT. Otherwise stay quiet.
```

### Reliability vs HA events

Webhook senders **own retry**. n8n, GitHub, and most homelab tools
retry on 5xx and timeout. That makes webhooks the right source for
**delivery-critical events** like water-leak detectors or smoke
alarms — much better than HA events, which CAESAR drops during the
WS reconnect window (ADR-0031 §7). If you've got both options for
the same sensor, prefer the webhook path for the security-critical
ones.

## How proactive runs differ from `/v1/chat`

Same brain graph, same policy engine, same audit log. Only:

- **System-prompt bias.** A second preamble between the SR-004 safety
  preamble and your operator prompt tells the LLM "you're running
  proactively — bias toward summarise-and-notify, don't touch HA
  unless this prompt explicitly asks". To make a trigger act, write
  the prompt as an instruction.
- **Decision-id prefix.** Reactive runs use a bare UUID; proactive
  runs use `proactive-<trigger_id>-<rand>`. Grep accordingly.

## Disabling proactivity entirely

Several flavours, applied to whichever sources you care about:

- **Pause everything**: comment out `CAESAR_PROACTIVE__TRIGGERS_PATH`
  in `.env` and restart. None of the drivers construct; the
  `/v1/hook/*` route still answers 404 (stable contract).
- **Pause one trigger**: set `enabled: false` on that trigger.
  Praetor still reads the file at startup but the trigger isn't armed.
- **Pause only HA triggers**: leave HA unconfigured (omit
  `CAESAR_HA__URL` / `CAESAR_HA__TOKEN`). The HA driver isn't
  constructed without an HA bridge; schedules and webhooks keep working.
- **Pause only webhook triggers**: set `enabled: false` on all
  webhook triggers. The route stays mounted but every POST returns
  404 + `webhook.unknown_trigger`.

There's no live "stop the driver" endpoint — edit the file and
restart. Hot-reload may land in a future release if demand appears.

## Migrating from v1.5

v1.6 renames the file and matching env var. The old names still work
for one release with a deprecation warning:

| v1.5 (deprecated)                    | v1.6 (canonical)                    |
|--------------------------------------|--------------------------------------|
| `schedules.yaml`                     | `triggers.yaml`                      |
| `CAESAR_PROACTIVE__SCHEDULES_PATH`   | `CAESAR_PROACTIVE__TRIGGERS_PATH`    |
| Top-level YAML key `schedules:`      | Top-level YAML key `triggers:`       |

To migrate: rename the file, rename the env var, change the top-level
YAML key from `schedules:` to `triggers:`. The trigger entries
themselves don't change.

If you only rename some of them (or none), Praetor logs a deprecation
warning at startup and keeps working. The v1.7 release drops the
fallback — by then you should be on the new names.

## Failure modes

| Symptom | Likely cause |
|---|---|
| No `trigger.scheduled` / `trigger.subscribed` rows at startup | `CAESAR_PROACTIVE__TRIGGERS_PATH` unset, or every trigger has `enabled: false`. |
| HA triggers loaded but never fire | HA bridge not configured (`CAESAR_HA__URL` / `CAESAR_HA__TOKEN` missing). Check for `ha.subscription.opened` audit rows; absence means the driver wasn't constructed. |
| Webhook POSTs all get 404 | Either trigger_id is wrong in the URL, or the webhook trigger has `enabled: false`, or `CAESAR_PROACTIVE__TRIGGERS_PATH` isn't set. Check for `trigger.subscribed` rows with `source_kind: webhook` at startup. |
| Webhook POSTs get 401 | Wrong or missing `Authorization: Bearer …` header. Check `webhook.unauthorized` audit rows (these never carry the supplied token). |
| Webhook POSTs get 413 | Body exceeded 64 KiB. Trim the payload at the sender or split into multiple events. |
| Webhook POSTs get 429 | The trigger is in `cooldown_seconds` after a recent fire. Either raise the cooldown to suppress less, lower to suppress more, or remove it. |
| Webhook fires but reaches no one externally | `notify` topic not configured, or operator isn't subscribed in the ntfy app. Test by inspecting `notify.called` audit rows. |
| Trigger fires but brain doesn't notify | `notify` worker not running (check `CAESAR_LEGION__INPROCESS_WORKERS`) or `notify` missing from `allowed_tools`. |
| `trigger.error` with "ntfy returned HTTP 4xx" | `CAESAR_TOOLS__NOTIFY__TOPIC` wrong, or self-hosted server requires a token. |
| Notifications arrive but with no body | The LLM emitted an empty message. Tighten the prompt — "Summarise X in 2-3 sentences; if there's nothing to say, reply 'nothing to report'." |
| Notification spam from motion sensor | Raise `cooldown_seconds` on the trigger. v1.6 has no global rate limit. |
| HA triggers stop firing after a network blip | Check the audit log for `ha.subscription.reconnected` rows; the WS likely came back. If not, check `ha.subscription.opened` was emitted at startup — without it, the driver never connected. |
| DST surprise | `croniter` respects the trigger's IANA zone, so 7am local stays 7am local across the spring/fall flip. If you set `timezone: UTC` and live somewhere with DST, set it to your local zone instead. |

## What's next

- **Add a tool the brain can call during proactive runs.** See
  [Add your own tool](ADD-YOUR-OWN-TOOL.md).
- **Switch LLM providers per task** — v1.1's task routing lets you
  point proactive runs at a cheaper local model. See
  [Picking a model](PICKING-A-MODEL.md).
- **Run the brain on one box, workers on another** — the scheduler
  and HA driver are single-instance per Praetor, but the `notify`
  worker isn't bound to them. See
  [Run a worker on another box](RUN-A-WORKER.md).
