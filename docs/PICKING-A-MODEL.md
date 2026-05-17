# Picking a model

CAESAR's brain talks to whichever LLM you point it at. v1.1 ships
three providers behind a single gateway ([ADR-0026](adr/0026-multi-provider-llm-gateway.md)):

- **Anthropic** — Claude family. Hosted; tool-calling is first-class
  here because the brain graph was built around it.
- **OpenAI** — GPT and Azure-OpenAI. Hosted; well-known surface,
  good function-calling support, easy to swap to vLLM / LiteLLM /
  Together via `base_url`.
- **Ollama** — fully-local. The model runs on your hardware; no
  third-party traffic. Tool support requires Ollama 0.4+ and a
  tools-capable model.

You pick a *default* for everything, and optionally a different
provider per task (see [Per-task routing](#per-task-routing) below).

## The three trade-offs

| Axis            | Anthropic / OpenAI                     | Ollama (local)                                  |
| --------------- | -------------------------------------- | ------------------------------------------------ |
| **Privacy**     | Prompts leave your house               | Stays on your hardware                           |
| **Cost**        | Per-token billing                       | Hardware + electricity (sunk)                    |
| **Latency**     | ~hundreds of ms (network)              | Depends on GPU / quant; can be slower or faster |
| **Quality**     | State-of-the-art on hard tasks         | Catching up; great for routine tasks             |
| **Tool calling**| Mature                                 | Recent (Ollama 0.4+); model-dependent            |
| **Reasoning**   | Anthropic + OpenAI reasoning models    | Some Ollama models (deepseek-r1, etc.)           |

The honest summary: use Ollama when *the contents of your prompts*
matter (anything touching your home, the audit log, schedules,
HA call shapes). Use Anthropic or OpenAI when you want the best
result on a one-shot reasoning task and you've decided the privacy
trade-off is acceptable.

## Configure the default provider

The two env vars that matter:

```sh
export CAESAR_LLM__PROVIDER=anthropic     # or openai / ollama
export CAESAR_LLM__MODEL=claude-haiku-4-5-20251001
```

Then set the credentials for the chosen provider:

=== "Anthropic"

    ```sh
    export CAESAR_LLM__ANTHROPIC__API_KEY=sk-ant-...
    ```

    Pre-v1.1 deployments using `CAESAR_LLM__API_KEY` keep working;
    that env var is an alias for the Anthropic key through v1.x.

=== "OpenAI"

    ```sh
    export CAESAR_LLM__OPENAI__API_KEY=sk-...
    # Optional: point at Azure-OpenAI / vLLM / LiteLLM proxy / etc.
    export CAESAR_LLM__OPENAI__BASE_URL=https://openai.azure.example/openai/v1
    ```

=== "Ollama"

    ```sh
    export CAESAR_LLM__MODEL=llama3.1:8b-instruct
    # Optional — defaults to http://localhost:11434
    export CAESAR_LLM__OLLAMA__BASE_URL=http://gpu-box.lan:11434
    ```

    No API key needed. Make sure `ollama serve` is running and the
    model has been pulled (`ollama pull llama3.1:8b-instruct`).

## Per-task routing

Operators can route specific tasks to different providers without
code changes via `CAESAR_LLM__TASK_ROUTING`. The env var is parsed
as JSON; keys are task names the brain emits, values pick a
provider + model. Auth/base_url for the chosen provider still
comes from the matching `LLMSettings.<provider>` sub-settings.

```sh
# Default to a hosted frontier model, but use a local Ollama
# instance for cheap recall summaries when a future worker emits
# task="recall_summary".
export CAESAR_LLM__PROVIDER=anthropic
export CAESAR_LLM__MODEL=claude-haiku-4-5-20251001
export CAESAR_LLM__TASK_ROUTING='{
  "recall_summary": {
    "provider": "ollama",
    "model": "llama3.1:8b-instruct"
  }
}'
```

Current tasks emitted by the brain:

| Task name       | Where it fires                                 | Configurable today |
| --------------- | ----------------------------------------------- | ------------------ |
| `chat`          | `/v1/chat` brain graph                          | yes                |

Workers in future releases will emit their own task names
(`recall_summary`, `intent_classification`, …); the dict is the
operator's escape hatch waiting for them.

## Tool-calling caveats

The brain graph relies on the model emitting structured tool calls
when it wants to do anything in the real world (`call_service` for
HA, `recall_memory` for the audit log). All three providers
translate to/from the same `ToolUse` / `ToolResult` shape, but the
*model* needs to actually support tool calling.

- **Anthropic** — every Claude model supports tools.
- **OpenAI** — every GPT-4 family model supports tools (`gpt-4o`,
  `gpt-4o-mini`, `o1`, `o3-mini`). Older `gpt-3.5-turbo` is fine
  too.
- **Ollama** — pick a tools-capable model. As of writing:
  - `llama3.1:8b-instruct` and `llama3.1:70b-instruct`
  - `qwen2.5:7b-instruct` / `qwen2.5:14b-instruct`
  - `mistral-nemo`
  - `mistral-small`

  Smaller models or older releases may silently ignore the
  `tools` field, which means CAESAR can chat but can't actually
  *do* anything. If your kitchen light isn't responding, check
  the Ollama model first.

## Cost and accounting

CAESAR's `caesar_chat_duration_seconds` Prometheus histogram is
provider-agnostic — every backend's tokens are normalised to
`input_tokens` / `output_tokens` on the audit row, and reasoning
tokens (when the provider emits them separately) are rolled into
`output_tokens` per [ADR-0026](adr/0026-multi-provider-llm-gateway.md).

For dollar cost, derive from the audit log:

```sql
SELECT
  json_extract(payload, '$.model') AS model,
  SUM(json_extract(payload, '$.input_tokens')) AS in_tokens,
  SUM(json_extract(payload, '$.output_tokens')) AS out_tokens
FROM audit_log
WHERE event_type = 'chat.completed'
  AND ts >= datetime('now', '-1 day')
GROUP BY model;
```

Multiply by your provider's published price. Ollama rows will show
up too with zero dollar cost.

## Privacy posture

The default behaviour ([ADR-0008](adr/0008-voice-wyoming.md)) is
that voice transcription happens client-side (phone keyboard
dictation, browser SpeechRecognition, etc.) — so the *audio*
never leaves your devices. The *text* prompts that reach Praetor
*do* go wherever your chosen LLM provider lives. Switching to
Ollama closes that hole entirely.

If you mix providers via `task_routing`, only the tasks routed to
hosted providers see prompts. A common pattern:

- `chat` (the main brain) on Anthropic for quality
- Everything else on Ollama for privacy and zero per-token cost

That's exactly what `task_routing` is for.

## When in doubt

Start with the provider whose credentials you already have. CAESAR
is happy to swap later — the gateway abstraction means there's no
data migration involved, just an env-var change and a restart.

## References

- [ADR-0011](adr/0011-llm-gateway.md) — original gateway design.
- [ADR-0026](adr/0026-multi-provider-llm-gateway.md) — multi-provider
  decision this page describes.
- [`CONFIGURATION.md`](CONFIGURATION.md) — every env var.
- [`SECURITY-MODEL.md`](SECURITY-MODEL.md) — what leaves the house.
