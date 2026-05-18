# 0033 — Personal facts: making CAESAR remember what you told it

- Status: Accepted
- Date: 2026-05-18
- Deciders: @Sinidious
- Related issues / PRs: v1.8 milestone; extends
  [ADR-0010](0010-memory-hybrid.md) (memory stores),
  [ADR-0020](0020-memory-retention-ttl.md) (retention),
  [SR-008](../SECURITY-REVIEW.md) (audit-log size).

## Context

Through v1.7 CAESAR has *technical* memory — the audit log records
every exchange, the semantic memory indexer can recall by similarity,
and the `memory.recall` / `memory.semantic_recall` Legion workers
surface old rows when the brain asks for them. What CAESAR *doesn't*
have is **memory in the way an operator means the word**: when you
told it your dog's name is Beans last week, today's chat should know.

Try it now:

```
Mon: "Hey CAESAR — by the way my dog's name is Beans."
Thu: "What should I get Beans for his birthday?"
```

Thursday's brain run sees the user message, calls no tools, and
either guesses or asks who Beans is. The Monday context exists in
`audit_log` but the brain doesn't fetch it automatically — and even
if `recall_memory` is wired, the brain doesn't know to look. The
operator experience is "CAESAR is amnesiac between sessions".

v1.8 fixes this. The user picks the gate ("remember facts I told
it"); v1.9 will handle the related-but-larger problem (episode
summarisation + decay/importance ranking).

## Decision

CAESAR v1.8 will ship a **personal-facts layer** that runs entirely
asynchronously from `/v1/chat`: a background Legion worker
(`memory.extract`) reads recent `chat.completed` rows, asks an LLM
"what facts about the user does this conversation reveal?", and
writes structured `{key, value, confidence}` rows to a dedicated
`personal_facts` table. The brain auto-injects relevant facts into
the next chat's system prompt.

### 1 — Schema (`personal_facts`)

```sql
CREATE TABLE personal_facts (
    id                 INTEGER PRIMARY KEY,
    key                TEXT    NOT NULL,
    value              TEXT    NOT NULL,
    confidence         REAL    NOT NULL DEFAULT 1.0,  -- 0.0..1.0
    first_seen_at      DATETIME NOT NULL,
    last_confirmed_at  DATETIME NOT NULL,
    source_audit_id    INTEGER REFERENCES audit_log(id),
    UNIQUE(key)
);
CREATE INDEX idx_personal_facts_key ON personal_facts(key);
```

- `key` is freeform but conventionally dot-namespaced (`dog.name`,
  `spouse.name`, `preference.coffee`, `address.city`). The extraction
  prompt suggests this convention but doesn't enforce it.
- `UNIQUE(key)` means one row per fact; updates replace, never
  duplicate. New value writes `last_confirmed_at`; conflicting value
  overwrites (with audit row recording the previous value).
- `confidence` is the extraction-LLM's self-rating. The retrieval
  step uses it to break ties when too many facts compete for space
  in the system prompt.
- `source_audit_id` links to the `chat.completed` row that produced
  the fact, so operators can trace "where did CAESAR learn this".

### 2 — Extraction (`memory.extract` Legion worker)

Same shape as the existing `SemanticIndexer` (ADR-0010 amendment):

- **Cursor table** `memory_extract_cursor` (one row, `last_audit_id`).
- **Poll** every `interval_seconds` (default 60) for new
  `chat.completed` rows since the cursor.
- **For each row**, run one LLM call via the **task-routed gateway**
  (ADR-0026) with `task="memory_extract"`. Operators can route this
  to a cheap local Ollama model so extraction doesn't bloat the
  Anthropic bill.
- **Prompt** instructs the model to return JSON of the form:

  ```json
  [
    {"key": "dog.name", "value": "Beans", "confidence": 0.95},
    {"key": "schedule.morning_routine", "value": "coffee then walk", "confidence": 0.7}
  ]
  ```

  An empty list is the common case — most exchanges contain no
  durable facts.
- **Apply** each extracted fact via the `FactsStore`:
  - If `key` is new: INSERT, audit row `memory.fact.added`.
  - If `key` exists and `value` matches: UPDATE
    `last_confirmed_at`, audit row `memory.fact.confirmed`.
  - If `key` exists and `value` differs: UPDATE (replace), audit
    row `memory.fact.updated` with both old and new value.
- **Advance** cursor on success. Failures (LLM down, bad JSON) skip
  the row but don't advance the cursor — retried on next poll.

Why a Legion worker (not in-process):

- Reuses v1.3's worker registry + dispatch + audit conventions.
- Operators can run extraction on a separate box (multi-host
  Legion, ADR-0027) to keep the brain's box CPU-free.
- Failure-isolated: extraction errors don't affect chat throughput.

### 3 — Retrieval (system-prompt auto-inject)

At the start of every `/v1/chat` turn (and every proactive run):

1. `FactsStore.list_all()` returns current facts ordered by
   `last_confirmed_at DESC`.
2. The system-prompt composer prepends a section:

   ```
   What you've told me:
     - Your dog's name is Beans.
     - You prefer coffee black with no sugar.
     - You live in Portland.
     ...
   ```

   Phrased as natural-language sentences, generated from `{key,
   value}` via a simple template-per-namespace registry (see §4).
3. Capped at `CAESAR_MEMORY__FACTS__MAX_INJECTED` facts (default 30)
   and `CAESAR_MEMORY__FACTS__MAX_CHARS` characters (default 2048)
   so a chatty operator can't blow the context window. Older / lower-
   confidence facts drop first.
4. Disabled entirely when `CAESAR_MEMORY__FACTS__ENABLED=false`
   (default `true` when `triggers_path` is set, false otherwise).

### 4 — Natural-language templates

Raw `{key, value}` pairs read like a database; the brain sees them
as conversation context. v1.8 ships a small template registry:

```python
TEMPLATES = {
    "dog.name":            "Your dog's name is {value}.",
    "spouse.name":         "Your spouse's name is {value}.",
    "preference.*":        "You prefer {value}.",  # wildcard
    "address.city":        "You live in {value}.",
    # ...
}
```

A built-in fallback renders unknown keys as `"{key}: {value}"` so the
brain still gets the data even when the template is missing. Operators
can extend the registry via a YAML file (deferred to v1.9 unless an
operator hits a wall).

### 5 — Dashboard surface

A new `/dashboard/facts` page lists all current facts with:

- **Edit** — correct an extracted value (e.g. "Beans" → "Bowser").
- **Delete** — remove a fact CAESAR shouldn't remember.
- **Confidence display** — surface the extraction-LLM's self-rating
  so operators see which facts are uncertain.
- **Source link** — click through to the `chat.completed` audit row
  that produced the fact.

Every edit / delete emits an audit row (`memory.fact.user_edited` /
`memory.fact.user_deleted`) so the change history is replayable.

### 6 — Privacy and bounds

Facts are durable data about the operator. They warrant special
care:

- **Audit-log only**: facts are stored in their own table, NOT
  re-indexed into the semantic store. The brain reads them via the
  system prompt; there's no separate "fact recall" tool to dispatch.
- **Operator review**: the dashboard is the canonical surface for
  "what CAESAR knows about me". Every fact came from a conversation
  the operator had; the dashboard makes the inference loop legible.
- **Default-on but capped**: enabled by default when the proactive
  subsystem is configured, but the 30-fact / 2048-char cap means a
  pathological case never bloats the context window.
- **No fact sharing across users**: v1.8 is single-user (multi-user
  is the deferred v1.x candidate). When multi-user lands, facts
  become per-identity and don't cross between household members.

## Alternatives considered

- **Synchronous extraction at end of /v1/chat.** Simpler wiring,
  but adds an LLM call's latency to every chat turn. Rejected: the
  brain's response shouldn't slow down because of memory bookkeeping.
- **No new table — store facts as audit_log event_type entries.**
  Tempting (one table simpler than two). Rejected: facts have
  UNIQUE(key) semantics that audit_log's append-only shape doesn't
  serve. Updating "dog.name" should overwrite, not append.
- **Re-index facts into the semantic memory store.** Adds a second
  retrieval path. Rejected for v1.8: facts are short enough that
  injecting all of them into the system prompt is cheaper than a
  semantic recall RTT. Revisit if the cap proves too tight.
- **LLM-decided "is this worth remembering" flag instead of
  extraction.** The brain marks notable turns with a special token;
  a worker harvests them. Rejected: doubles up on what extraction
  already does and makes the brain prompt longer.
- **JSON-path or YAML schema for fact keys (typed taxonomy).**
  Rejected: the brain extracts free-form snake_case keys; rigid
  taxonomy would force the extraction-LLM to pick between mismatched
  buckets and inflate the prompt. Templates (§4) handle pretty-
  printing without forcing a schema.
- **Cross-user fact merging.** Pure v2.x territory.

## Consequences

### Positive

- Closes the "CAESAR doesn't remember anything" gap. The first
  obvious operator experience after this milestone: telling CAESAR
  something once and having it stick.
- Reuses every primitive from v1.0–v1.7: Legion worker pattern,
  task-routed gateway, audit log, dashboard scaffolding. No new
  architecture.
- Operator-visible facts dashboard makes the extraction loop
  legible — operators see what CAESAR believes about them and can
  correct.
- Cheap to run when wired through Ollama for the extraction model;
  the Anthropic LLM only sees the auto-inject (a few hundred extra
  tokens per chat).

### Negative

- Extraction can be wrong. A casual "imagine if my dog was named
  Beans" reads as a fact statement to a generous LLM. The
  dashboard's edit/delete is the mitigation; the docs page documents
  the pattern.
- One more LLM call per `chat.completed` (in the background). For
  operators on Anthropic-only the cost shows up in the Anthropic
  bill; for operators on Ollama-only it's free; for mixed setups
  it's whatever the `memory_extract` task is routed to.
- The system prompt grows by ~500-1500 chars on average. Counted
  against the model's context budget but well within all
  contemporary model limits.

### Neutral

- One Alembic migration (`personal_facts` table + cursor table).
- One new dashboard page; same auth + signing-key flow as the rest.
- New audit event types: `memory.fact.added`, `memory.fact.updated`,
  `memory.fact.confirmed`, `memory.fact.user_edited`,
  `memory.fact.user_deleted`.
- v1.9 builds on this: episode summarisation auto-injects similarly,
  and the decay/importance layer touches both facts and episodes.

## References

- [ADR-0010](0010-memory-hybrid.md) — episodic + semantic memory
  foundations that v1.8 sits alongside.
- [ADR-0011](0011-llm-gateway.md) — the gateway abstraction that
  task-routes the extraction model.
- [ADR-0020](0020-memory-retention-ttl.md) — TTL sweep; v1.8
  doesn't change it but v1.9's decay layer will.
- [ADR-0021](0021-dashboard-htmx.md) — dashboard scaffolding for
  the facts view.
- [ADR-0026](0026-multi-provider-llm-gateway.md) — task-routing
  per gateway, used by `memory_extract` to pick a cheap model.
- [SR-008](../SECURITY-REVIEW.md) — audit-log string clamp; facts
  payload writes pass through it unchanged.
