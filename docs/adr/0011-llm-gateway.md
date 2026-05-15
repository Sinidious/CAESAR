# 0011 — Provider-agnostic LLM Gateway

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

CAESAR will use multiple LLMs for different jobs: a fast local model
for intent classification, a stronger hosted model for ambiguous
turns, a tiny model for embedding generation, and probably a
fine-tuned model for specific Legion workers. The maintainer wants
to swap providers without rewriting agents, and wants to be able to
say "this agent uses model X with personality Y" in configuration.

If every worker imports `anthropic` and `openai` directly, the
project will end up with N×M coupling and credentials sprawled
across processes.

## Decision

CAESAR has a single **LLM Gateway** module that all LLM calls flow
through. Properties:

- **One interface**, parameterized by capability (chat, completion,
  embedding, tool-call), not by provider.
- **Per-agent configuration**, stored in CAESAR's database, that
  decides which provider/model an agent uses, with what personality,
  at what priority.
- **Credentials live only in Praetor**, never in workers. Workers
  request an LLM call over the message bus; Praetor's gateway
  performs the call and streams the response back.
- **Retries, rate limits, timeouts, and provider failover** belong in
  the gateway, not in callers.
- **Cost / token telemetry** is collected here, not by individual
  agents.

Initial providers: Anthropic, OpenAI, Groq, Ollama, vLLM. Adding a
provider is a small PR against the gateway, not an ADR.

## Alternatives considered

- **No gateway; each worker imports the provider it wants** — simple,
  but credentials sprawl, retries are reinvented N times, switching
  providers becomes a refactor.
- **LiteLLM as the gateway** — strong contender; we keep this option
  open by making our gateway interface "shaped like LiteLLM
  loosely" so a future swap is just a few hundred lines. Rejected
  as the *required* dependency for now because we want clear control
  of the audit hooks and cost telemetry.
- **A separate gateway service over HTTP** — over-engineered for a
  single-host service today. We may extract one later.

## Consequences

### Positive

- Workers never see provider SDKs or keys.
- "Use Claude for X and Llama for Y" is a config change, not a code
  change.
- One place to add audit-log entries for every LLM call.

### Negative

- The gateway is a chokepoint; bugs here affect everything.
- We carry the complexity of a multi-provider abstraction even when
  most installs use one provider.

### Neutral

- Streaming responses through the gateway and back over NATS will
  need care. The gateway can degrade to non-streaming where the
  caller doesn't need it.

## References

- [LiteLLM](https://github.com/BerriAI/litellm)
- [Anthropic API](https://docs.anthropic.com/)
- [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md)
- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
