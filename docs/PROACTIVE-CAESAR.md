# Proactive CAESAR

By default CAESAR is reactive — it answers `/v1/chat` requests and
dashboard messages. **Proactive mode** lets the brain *start* runs on
its own, on a schedule, and reach your phone via
[ntfy.sh](https://ntfy.sh). The canonical example is a morning
brief: every weekday at 7am, CAESAR summarises your calendar +
weather + overnight HA state changes and pushes a notification.

This page walks you through enabling that. ADR-0030 covers the
underlying design.

## What's in scope

After this page you'll have:

- A `schedules.yaml` declaring when CAESAR wakes itself up.
- A configured `notify` Legion worker so the brain can reach you.
- A policy entry allowing the `notify` tool.
- An audit log row for every proactive fire — `trigger.scheduled`,
  `trigger.fired`, `trigger.completed`, plus any tools the brain called.

Proactivity is **opt-in** at every layer:

- `CAESAR_PROACTIVE__SCHEDULES_PATH` unset → no scheduler runs.
- `enabled: false` on a trigger → it's loaded but never fires.
- `notify` not on `allowed_tools` → the brain can't push to your phone
  even if a schedule fires.

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

## Step 3 — Author `schedules.yaml`

`caesar init` writes a starter file with one disabled example:

```yaml
version: 1

schedules:
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
```

### Field reference

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Snake-case identifier. Used in `trigger_id` on audit rows; pick something greppable. |
| `enabled` | no (default `true`) | `false` keeps the trigger loaded but unarmed — useful to reserve an id without firing yet. |
| `cron` | yes | Standard 5-field cron (`min hr dom mon dow`). [crontab.guru](https://crontab.guru/) is great for sanity-checking. |
| `timezone` | no (default `UTC`) | Any IANA name. DST is handled by croniter; daylight-saving boundaries don't double-fire. |
| `max_runtime_seconds` | no (default `300`, max `3600`) | Hard cap on one fire's brain run. Beyond this, the scheduler audits `trigger.timeout` and moves on. |
| `prompt` | yes | What the brain sees as the user message. Write it like a one-shot request. |

### Flat vs nested form

Both shapes load identically; pick whichever reads better:

```yaml
# Flat (recommended for the common case)
schedules:
  - id: hourly_ping
    cron: "0 * * * *"
    prompt: tell me one fact

# Nested (forward-compat with v1.6 HA-event and webhook sources)
schedules:
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
CAESAR_PROACTIVE__SCHEDULES_PATH=./schedules.yaml
```

If you're upgrading a hand-rolled install, add it yourself. When this
variable is **unset**, the scheduler subsystem isn't constructed and
CAESAR behaves exactly like v1.4 — reactive only.

## Step 5 — Arm a trigger and restart

Edit `schedules.yaml`, flip `enabled: true`, restart Praetor.
At startup you'll see one audit row per armed trigger:

```
event_type: trigger.scheduled
payload:
  trigger_id: morning_brief
  cron: "0 7 * * 1-5"
  timezone: America/Los_Angeles
  next_fire_at: "2026-05-18T14:00:00+00:00"
```

When the cron expression matches, the scheduler:

1. Writes `trigger.fired` (`trigger_id`, `prompt`, `scheduled_for`).
2. Runs `trigger.prompt` through the brain graph, with a
   `proactive=true` system-prompt bias that pushes the LLM toward
   *summarise-and-notify, don't act on the house*.
3. Each tool the brain calls (`notify`, `calculator`, `web_search`,
   `calendar_read`, …) goes through the policy engine and writes its
   normal `tool.called` / `tool.denied` audit rows.
4. Writes `trigger.completed` (`duration_seconds`, …) on success, or
   `trigger.timeout` / `trigger.error` on failure.

Every audit row carries a `decision_id` prefixed
`proactive-<trigger_id>-`, so filtering the dashboard timeline to
proactive activity is one substring match away.

## How proactive runs differ from `/v1/chat`

Same brain graph, same policy engine, same audit log. The only
differences are:

- **System-prompt bias.** A second preamble between the SR-004 safety
  preamble and your operator prompt tells the LLM "you're running on
  a schedule, don't touch HA unless this prompt explicitly asks". You
  can still ask a schedule to act on the house (e.g. "at sunset, turn
  on the entry light") — the policy allow-list still has the final
  say.
- **No HA bias from the start.** Reactive `/v1/chat` runs include the
  HA tool when configured. Proactive runs include it too, but the bias
  pushes the LLM to prefer `notify`. To make a schedule act, write the
  prompt as an instruction, not a question.
- **Decision-id prefix.** Reactive runs use a bare UUID; proactive
  runs use `proactive-<trigger_id>-<rand>`. Grep accordingly.

## Disabling proactivity entirely

Two flavours:

- **Pause everything**: comment out
  `CAESAR_PROACTIVE__SCHEDULES_PATH` in `.env` and restart. The
  scheduler isn't constructed.
- **Pause one trigger**: set `enabled: false` on that schedule.
  Praetor still reads the file at startup but the trigger isn't armed.

There's no live "stop the scheduler" endpoint in v1.5 — the operator
edits the file and restarts. v1.6 may add hot-reload if the demand
appears.

## Failure modes

| Symptom | Likely cause |
|---|---|
| No `trigger.scheduled` row at startup | `CAESAR_PROACTIVE__SCHEDULES_PATH` unset, or every trigger has `enabled: false`. |
| Trigger fires but brain doesn't notify | `notify` worker not running (check `CAESAR_LEGION__INPROCESS_WORKERS`) or `notify` missing from `allowed_tools`. |
| `trigger.error` with "ntfy returned HTTP 4xx" | `CAESAR_TOOLS__NOTIFY__TOPIC` wrong or self-hosted server requires a token. |
| Notifications arrive but with no body | The LLM emitted an empty message. Tighten the prompt — "Summarise X in 2-3 sentences; if there's nothing to say, send 'nothing to report'." |
| Notification spam | A cron expression like `* * * * *` plus a non-trivial prompt. Lower the cadence; v1.5 has no global rate limit. |
| DST surprise | `croniter` respects the trigger's IANA zone, so 7am local stays 7am local across the spring/fall flip. If you set `timezone: UTC` and live somewhere with DST, set it to your local zone instead. |

## What's next

- **Add a tool the brain can call during proactive runs.** See
  [Add your own tool](ADD-YOUR-OWN-TOOL.md).
- **Switch LLM providers per task** — v1.1's task routing lets you
  point proactive runs at a cheaper local model. See
  [Picking a model](PICKING-A-MODEL.md).
- **Run the brain on one box, workers on another** — the scheduler is
  single-instance, but the `notify` worker isn't bound to it. See
  [Run a worker on another box](RUN-A-WORKER.md).
