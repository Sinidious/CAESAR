# Add your own tool

CAESAR's brain talks to the world through *tools*. Three ship with
v1.3 — `calculator`, `web_search`, `calendar_read` — and the
architecture is deliberately open so you can plug in your own
without forking. This page walks through what's involved.

If you haven't read it yet, [ADR-0028](adr/0028-tools-beyond-ha.md)
covers the *why*; this page is the *how*.

## The five moving parts

Every CAESAR tool has the same anatomy:

1. **A capability string** — `tool.<name>`. The brain finds workers
   by capability via the registry.
2. **A `Worker` subclass** — runs in a process somewhere (in-process
   under Praetor, or on another box per
   [ADR-0027](adr/0027-nats-auth-multihost-legion.md)).
3. **A `ToolDefinition`** in the brain graph — describes the tool
   to the LLM in its native JSON-schema vocabulary.
4. **A dispatch branch** in the brain graph — routes the LLM's
   `tool_use` to your worker.
5. **A policy rule** — an entry in `allowed_tools` so the call is
   actually authorised.

A reasonable first tool can be 60-80 lines plus tests.

## Worker skeleton

The smallest useful worker subclasses `caesar.legion.worker.Worker`:

```python
# caesar/legion/uptime.py
from typing import Any, ClassVar
import time

from caesar.bus.client import Bus
from caesar.legion.protocol import TaskDispatch
from caesar.legion.worker import Worker

CAPABILITY = "tool.uptime"
WORKER_ID = "uptime"
_STARTED_AT = time.monotonic()


class UptimeWorker(Worker):
    """Reports how long the worker process has been running."""

    worker_id: ClassVar[str] = WORKER_ID
    capabilities: ClassVar[list[str]] = [CAPABILITY]
    version: ClassVar[str] = "0.1.0"

    def __init__(self, bus: Bus) -> None:
        super().__init__(bus)

    async def handle(self, task: TaskDispatch) -> dict[str, Any]:
        return {"seconds": time.monotonic() - _STARTED_AT}
```

Three rules of thumb for the handler:

- **Validate inputs explicitly.** The LLM emits whatever it likes;
  raise `ValueError` for anything you didn't ask for. The brain
  graph surfaces the message to the model so it can correct course.
- **Return JSON-serialisable dicts.** They flow through NATS and
  into the audit log; the SR-008 clamp will quietly trim very long
  string values, but exotic types (datetime, set, custom classes)
  serialise badly. Stringify dates yourself.
- **Don't hold network resources across requests** unless you also
  add an `aclose()` and wire it into the worker's shutdown path
  (see `web_search.WebSearchWorker.aclose` for the pattern).

## Wire the brain graph

In `caesar/praetor/graph.py`, add a `ToolDefinition` describing
your tool to the LLM:

```python
from caesar.legion.uptime import CAPABILITY as UPTIME_CAPABILITY

UPTIME_TOOL = ToolDefinition(
    name="uptime",
    description=(
        "Return how long the homelab brain has been running, in "
        "seconds. Use when the user asks 'how long have you been "
        "up?' or wants a sanity check that CAESAR didn't restart."
    ),
    input_schema={"type": "object", "properties": {}},
)
```

Then register it when a worker advertises the capability:

```python
if registry is not None and registry.find(UPTIME_CAPABILITY):
    tools.append(UPTIME_TOOL)
```

…and dispatch it through `_handle_generic_tool` — the same helper
the v1.3 workers use:

```python
elif use.name == "uptime":
    results.append(
        await _handle_generic_tool(
            use,
            decision_id,
            tool="uptime",
            capability=UPTIME_CAPABILITY,
        )
    )
```

That's it for the brain side. The helper does the policy check
(`GenericToolCall` → `Policy.evaluate`), writes the
`tool.called` / `tool.denied` audit row, and shapes the worker's
reply into a `ToolResult` the LLM can read on its next turn.

## Wire the worker into Praetor's startup

If your worker is light enough to live in-process (no heavy deps,
no per-host credentials), wire it into `_build_inprocess_worker`
in `caesar/praetor/app.py`:

```python
from caesar.legion.uptime import UptimeWorker

# inside _build_inprocess_worker(...)
if name == "uptime":
    return UptimeWorker(bus)
```

The operator enables it via:

```sh
export CAESAR_LEGION__INPROCESS_WORKERS='["memory_recall","uptime"]'
```

If your worker is heavier — needs credentials, hits the network,
or runs better with isolation — ship it as a separate process.
Operators use [`caesar legion new-worker`](RUN-A-WORKER.md) to mint
a fresh NATS NKEY for the worker host, then run the worker's own
entry point there. See `WebSearchWorker` for an in-process pattern
with external HTTP; CalDAV could just as well live on another box.

## Policy entry

The Policy Engine denies any tool that isn't on the allow-list,
regardless of whether a worker advertises the capability. That's
the v1.3 posture (ADR-0028): one source of truth for *what
CAESAR is allowed to do*.

For most tools the bare-string entry is enough:

```yaml
allowed_tools:
  - uptime
```

When the tool's input has values worth constraining, switch to
the object form and add an `input:` block. The v1.3 matchers
recognise `domain_allowlist` (used by `web_search`); new
constraint kinds are a small addition to
`caesar/policy/allowlist.py::_input_matches`:

```yaml
allowed_tools:
  - tool: web_search
    input:
      domain_allowlist:
        - wikipedia.org
        - docs.searxng.org
```

Adding your own constraint kind (e.g. a `max_length` for a
text-summariser tool, or a regex `query_pattern`) is one branch in
`_input_matches`. Keep the per-tool matcher simple — push complex
rules into the worker itself, where you can write real Python.

## Per-tool settings

If your tool needs configuration (credentials, base URLs,
defaults), add a nested model under `caesar.config.ToolsSettings`:

```python
class UptimeToolSettings(BaseModel):
    # uptime needs nothing, but if it did:
    decimal_places: int = 1


class ToolsSettings(BaseModel):
    web_search: WebSearchToolSettings = Field(default_factory=WebSearchToolSettings)
    calendar: CalendarToolSettings = Field(default_factory=CalendarToolSettings)
    uptime: UptimeToolSettings = Field(default_factory=UptimeToolSettings)
```

The operator's env vars follow the standard
`CAESAR_TOOLS__<NAME>__<FIELD>` shape:

```sh
export CAESAR_TOOLS__UPTIME__DECIMAL_PLACES=2
```

For sensitive fields use `SecretStr` so the value doesn't show up
in `repr()`, logs, or audit rows.

## Tests

The v1.3 workers ship with three test layers; copying the pattern
keeps coverage at the project's 98% gate.

1. **Unit tests on the handler.** Pass a `TaskDispatch` directly;
   exercise input validation, default values, and the wire-shape
   contract. No bus needed. See `tests/test_calculator.py`.
2. **Unit tests on any external collaborator.** If your worker
   talks to HTTP, wrap an `httpx.AsyncClient` and pass a
   `MockTransport` from tests. If it talks to a third-party SDK,
   isolate the calls behind a small client class and use a Protocol
   so tests can pass a stub. See `tests/test_web_search.py` and
   `tests/test_calendar_read.py`.
3. **Brain-graph integration tests.** Use the `_FakeRegistry`
   helper at the bottom of `tests/test_praetor_graph.py` to drive
   the full LLM-emits-tool-use → policy → dispatch → audit →
   tool_result path. No NATS bus, no real worker process, ~30
   lines per test.

If the tool talks to a real upstream system you don't want CI to
talk to, mark the production client's I/O method with
`# pragma: no cover - needs live <thing>` and cover the surface
that *is* testable (parsers, validators, normalisers).

## Audit log shape

Tool invocations land in the audit log with `event_type =
"tool.called"` (success or worker failure) or `"tool.denied"`
(policy rejection). The payload carries the tool id, the input
dict, and either the worker's result or the failure reason.
Operators can query past tool calls from the dashboard or with
SQL:

```sql
SELECT
  json_extract(payload, '$.tool') AS tool,
  json_extract(payload, '$.success') AS success,
  COUNT(*) AS calls
FROM audit_log
WHERE event_type IN ('tool.called', 'tool.denied')
  AND ts >= datetime('now', '-7 days')
GROUP BY tool, success
ORDER BY calls DESC;
```

The SR-008 clamp truncates each string value in the payload to
`CAESAR_MEMORY__AUDIT_MAX_STRING_CHARS` (default 16KB) so a
runaway tool can't bloat the table.

## When *not* to add a tool

Three patterns that look like tools but really aren't:

- **Read-only state queries the brain already has.** If the LLM
  could answer from the chat history, don't add a tool — the
  `recall_memory` worker already covers what's in the audit log.
- **Anything that needs root or filesystem write access on
  Praetor's host.** Tools run with whatever permissions their
  worker process has; pushing privileged side effects through the
  brain widens the blast radius the policy engine has to gate.
- **Pure code execution.** The calculator deliberately uses an
  AST whitelist instead of `eval()`. If you want code execution,
  isolate it (container, restricted subprocess) and treat the
  isolation boundary — not the policy engine — as the real
  security control.

## References

- [ADR-0028](adr/0028-tools-beyond-ha.md) — the design this page
  documents.
- [ADR-0009](adr/0009-message-bus-nats.md) — bus / dispatch shape.
- [ADR-0013](adr/0013-policy-engine.md) — policy engine the new
  tools plug into.
- [ADR-0027](adr/0027-nats-auth-multihost-legion.md) — how a
  cross-host worker authenticates.
- [SECURITY-REVIEW.md](SECURITY-REVIEW.md) — the SR-008 clamp +
  threat-model context.
