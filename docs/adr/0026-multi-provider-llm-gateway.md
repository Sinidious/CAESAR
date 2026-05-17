# 0026 — Multi-provider LLM gateway

- Status: Accepted
- Date: 2026-05-17
- Deciders: @Sinidious
- Related issues / PRs: v1.1 milestone (after [ADR-0011](0011-llm-gateway.md))

## Context

[ADR-0011](0011-llm-gateway.md) committed CAESAR to a provider-agnostic
LLM gateway: a single `LLMGateway` Protocol implemented by one
provider per backend. The v0.1 → v1.0 implementation ships only the
Anthropic provider. v1.1's gate question is "can I run CAESAR on the
model I picked — including a fully-local one — without forking the
brain?". To answer yes we need at least two more providers wired,
without diluting what the gateway already proves.

Three real differences between providers force decisions, not just
typing:

1. **Tool-calling shape.** Anthropic exposes `tool_use` + `tool_result`
   blocks; OpenAI exposes `function_call` + `tool` role messages
   (chat completions API) and now `responses.tool_calls`; Ollama (0.4+)
   exposes `tool_calls` on the assistant message but inside the
   non-streaming chat endpoint. The gateway protocol uses the
   Anthropic-shaped vocabulary internally; providers must translate
   both directions.
2. **Token accounting.** OpenAI emits `prompt_tokens` /
   `completion_tokens`; Anthropic emits `input_tokens` /
   `output_tokens`; Ollama emits `prompt_eval_count` /
   `eval_count`. Reasoning models (o1/o3, Anthropic extended
   thinking) add a third bucket. We need a single normalised shape
   so the existing audit log and Prometheus histogram stay coherent.
3. **Configuration surface.** Each provider has its own auth, base
   URL, and model namespace. An operator picking Ollama on
   `http://localhost:11434` needs different env vars from one
   picking OpenAI with a key. The gateway has to accept that
   without growing nested-knob explosion.

## Decision

CAESAR v1.1 ships **three providers** behind the existing
`LLMGateway` Protocol:

- `caesar.llm.anthropic.AnthropicProvider` (existing).
- `caesar.llm.openai.OpenAIProvider` (new). Targets the official
  OpenAI Chat Completions API. Compatible with Azure-OpenAI via base
  URL override.
- `caesar.llm.ollama.OllamaProvider` (new). Targets the local Ollama
  HTTP API. Fully-local operation; no third-party traffic.

All three implement the same `LLMGateway.complete(...)` method and
return the same `ChatResponse` shape. Translation lives entirely
inside each provider — the brain graph stays Anthropic-vocabularied
because that is the most expressive of the three (tool blocks
are first-class, not flattened into role names).

### Tool-calling normalisation

`ToolDefinition`, `ToolUse`, `ToolResult` stay as defined in
`caesar.llm.gateway`. Each provider:

- **At call time** maps `ToolDefinition` to the provider's tool
  schema (`tools[]` for Anthropic, `tools[].function` for OpenAI,
  `tools[]` for Ollama).
- **At response time** maps the provider's tool emission back to
  `ToolUse`. JSON arguments are parsed; malformed JSON is reported
  as an `is_error=True` `ToolResult` in the next turn (the brain
  graph already handles this path).
- **At follow-up time** maps `ToolResult` messages from the next
  turn back to the provider's expected shape (Anthropic
  `tool_result` block, OpenAI `tool` role, Ollama `tool` role).

The `_message_to_anthropic` translator that exists today is the
prototype; the OpenAI / Ollama equivalents live alongside, all
keyed on the same internal types.

### Token accounting

`ChatResponse.input_tokens` and `output_tokens` are populated from
each provider's native fields (`prompt_eval_count` → input for
Ollama; `prompt_tokens` → input for OpenAI; unchanged for
Anthropic). Reasoning tokens, when the provider exposes them, are
added to `output_tokens` so the existing Prometheus histogram
stays comparable. If finer-grained reasoning telemetry is needed
later it gets its own field in a follow-up ADR.

### Configuration surface

A new `CAESAR_LLM__PROVIDER` env var picks the default backend
(`anthropic` | `openai` | `ollama`). Per-provider settings live
under their own prefix:

- `CAESAR_LLM__ANTHROPIC__API_KEY` (current `CAESAR_LLM__API_KEY`
  becomes an alias for backward compat through v1.x).
- `CAESAR_LLM__OPENAI__API_KEY`, `CAESAR_LLM__OPENAI__BASE_URL`.
- `CAESAR_LLM__OLLAMA__BASE_URL` (default `http://localhost:11434`,
  no key required), `CAESAR_LLM__OLLAMA__MODEL`.

Model names are passed through verbatim — no normalisation layer.
Operators write `gpt-4o-mini`, `claude-haiku-4-5-20251001`, or
`llama3.1:8b-instruct`, and the right provider gets the right
string.

### Per-task routing

Some workers don't need the full chat model. `memory_recall` and
`semantic_recall` workers don't talk to an LLM at all today, but
when a future worker needs a *cheap* model (summary, intent
classification) we want to point it elsewhere without redeploying.

A new `CaesarSettings.llm.task_routing` dict (default empty) maps
task names to `LLMTaskConfig(provider=..., model=...)`. The brain
graph reads it when constructing the gateway. An empty dict — the
default — routes every task to the configured default provider.

```python
class LLMTaskConfig(BaseModel):
    provider: Literal["anthropic", "openai", "ollama"]
    model: str

class LLMSettings(BaseModel):
    provider: Literal["anthropic", "openai", "ollama"] = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    # ...
    task_routing: dict[str, LLMTaskConfig] = Field(default_factory=dict)
```

The factory that constructs the gateway picks a provider per call
based on the task name passed in by the brain graph. For v1.1
shipping, only `chat` and (optionally) `recall_summary` are
expected to use this; the dict is the operator's escape hatch for
the future.

## Alternatives considered

- **Single provider, instructions to "swap manually"** — Operators
  fork `caesar/llm/anthropic.py` to point at their preferred
  backend. Rejected as the whole point of ADR-0011 was to avoid
  this.
- **LiteLLM or LangChain as a meta-gateway** — Could plug in many
  providers behind one interface. Rejected: pulls a large
  dependency tail (LiteLLM has ~50 transitive deps; LangChain even
  more) and abstracts the tool-calling boundary in ways that hide
  legitimate provider differences. CAESAR's gateway is small enough
  that hand-rolling each provider is cheaper to maintain than
  fighting an abstraction.
- **Per-provider `Gateway` types instead of Protocol** — Stricter
  typing but loses the symmetry tests (`FakeGateway` already
  satisfies the Protocol structurally). Stick with the Protocol.
- **Provider-specific gateways in subprocess workers** — Each
  provider runs in its own Legion worker, called via NATS.
  Rejected: massive complexity for a homelab and unrelated to
  the choice of provider. Workers are for capabilities, not for
  isolating SDKs.
- **OpenAI only (drop Ollama)** — Ollama is what unlocks "fully
  local" operation; without it, the privacy posture is just
  "trust Anthropic or trust OpenAI". Rejected — Ollama is
  the headline feature of v1.1.
- **Do nothing** — Anthropic stays the only supported provider.
  Rejected: v1.1's gate question literally asks for the opposite.

## Consequences

### Positive

- Operators choose per-task between three providers covering
  cloud-frontier (Anthropic / OpenAI) and fully-local (Ollama).
- Privacy posture improves: an Ollama-only deployment doesn't
  send a token outside the box.
- ADR-0011's bet pays off; provider-agnostic was the right
  ceiling.

### Negative

- Three SDK-shaped translators to keep in sync as upstream APIs
  drift. Mitigated by integration tests against each provider's
  documented response shape (deterministic, no live calls in CI).
- Tool-calling differences will leak through if a provider does
  something exotic (parallel tool calls, streaming-only tools,
  etc.). v1.1 explicitly targets the *common* subset — sequential
  tool calls, JSON arguments, named tools.

### Neutral

- The current `CAESAR_LLM__API_KEY` env var keeps working as an
  alias for `CAESAR_LLM__ANTHROPIC__API_KEY` through v1.x. A
  deprecation removes it in v2.x.
- The default provider is still Anthropic so existing deployments
  upgrade seamlessly. Switching is opt-in via env.
- Reasoning-model token accounting is bucketed into
  `output_tokens` for v1.1. A more granular split (visible
  reasoning vs. completion) waits for a follow-up.

## References

- [ADR-0011](0011-llm-gateway.md) — provider-agnostic gateway
  decision this ADR extends.
- [Anthropic tool use](https://docs.anthropic.com/claude/docs/tool-use)
- [OpenAI function calling](https://platform.openai.com/docs/guides/function-calling)
- [Ollama tool support](https://ollama.com/blog/tool-support)
