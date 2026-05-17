# Configuration

CAESAR is configured by environment variables. The canonical example is
[`.env.example`](https://github.com/Sinidious/CAESAR/blob/main/.env.example) at the repo root — copy it to `.env`
and fill in real values.

> Pre-alpha: many variables below describe components that are not yet
> implemented. They are listed here so contributors and operators have
> a single source of truth as code lands.

## Praetor (core)

| Variable             | Default                  | Description |
| -------------------- | ------------------------ | ----------- |
| `CAESAR_ENV`         | `development`            | One of `development`, `staging`, `production`. Affects logging and safety defaults. |
| `CAESAR_LOG_LEVEL`   | `INFO`                   | Standard Python log levels. |
| `CAESAR_HTTP_HOST`   | `127.0.0.1`              | Bind address for the FastAPI server. |
| `CAESAR_HTTP_PORT`   | `8088`                   | TCP port. |
| `CAESAR_SQLITE_PATH` | `./var/caesar.sqlite`    | Episodic memory + audit log path. |

## Home Assistant Bridge

| Variable                | Default | Description |
| ----------------------- | ------- | ----------- |
| `HOME_ASSISTANT_URL`    | —       | Base URL of your HA instance, e.g. `http://homeassistant.local:8123`. |
| `HOME_ASSISTANT_TOKEN`  | —       | Long-lived access token. Treat as a high-value secret. |

## Message bus

| Variable    | Default                  | Description |
| ----------- | ------------------------ | ----------- |
| `NATS_URL`  | `nats://127.0.0.1:4222`  | NATS server URL. See [ADR-0009](adr/0009-message-bus-nats.md). |

## LLM Gateway

Set only the providers you intend to use. The gateway picks providers
based on per-agent configuration (which lives in CAESAR's database, not
env vars).

| Variable             | Default                       | Description |
| -------------------- | ----------------------------- | ----------- |
| `ANTHROPIC_API_KEY`  | —                             | For Claude models. |
| `OPENAI_API_KEY`     | —                             | For GPT models. |
| `GROQ_API_KEY`       | —                             | For Groq-hosted open models. |
| `OLLAMA_BASE_URL`    | `http://127.0.0.1:11434`      | Local Ollama. |
| `VLLM_BASE_URL`      | —                             | Local vLLM endpoint. |

## Memory

| Variable                | Default        | Description |
| ----------------------- | -------------- | ----------- |
| `CAESAR_VECTOR_BACKEND` | `sqlite-vss`   | `sqlite-vss`, `qdrant`, or `chroma`. |
| `QDRANT_URL`            | —              | Used when `CAESAR_VECTOR_BACKEND=qdrant`. |

## Voice Satellite (Wyoming)

| Variable        | Default   | Description |
| --------------- | --------- | ----------- |
| `WYOMING_HOST`  | `0.0.0.0` | Bind address for the Wyoming listener on Praetor's side. |
| `WYOMING_PORT`  | `10300`   | Wyoming port. |

## Operational guidance

- Keep `.env` out of git. The repo ships a `.gitignore` rule for this.
- Rotate `HOME_ASSISTANT_TOKEN` after any incident or maintainer
  handover.
- For production-style deployments, prefer a real secret manager
  (`docker secrets`, `sops`, `1Password`) over a flat `.env` file.
