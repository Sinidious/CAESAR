# Quickstart in 10 minutes

This is the literal 10-minute path from "fresh box" to "talking to
CAESAR through the dashboard". Pick Docker (the recommended path,
no Python on the box required) or `pip install` (for operators who
already manage Python services).

If anything in here doesn't work, the
[Operations runbook](OPERATIONS.md) is the authoritative reference.

## Prerequisites

- A Linux/macOS/Windows host with one of:
  - Docker Engine 24+ with the Compose plugin, **or**
  - Python 3.11 or 3.12.
- An LLM API key (Anthropic / OpenAI) **or** a running [Ollama](https://ollama.com/)
  instance if you want fully-local operation.
- An open TCP port (8000 by default) for the dashboard.

Total time budget: ~10 minutes if your LLM key is at hand and you
already have Docker (or Python) installed.

## Path A — Docker Compose (recommended)

### 1. Get the repo

```sh
git clone https://github.com/Sinidious/CAESAR.git
cd CAESAR
```

You only need `docker-compose.yml`, but cloning the repo is the
easiest way to get the file plus the `Dockerfile`, ADRs, and
examples for when you need them.

### 2. Generate a starter config

```sh
docker run --rm -v "$PWD:/var/lib/caesar" ghcr.io/sinidious/caesar:latest init
```

This drops `.env`, `policy.yaml`, `praetor.nkey`, and `./var/`
into the current directory. Every secret is freshly generated.

### 3. Edit one line in `.env`

Open `.env` and paste your API key on the matching line:

```sh
# .env
CAESAR_LLM__PROVIDER=anthropic
CAESAR_LLM__MODEL=claude-haiku-4-5-20251001
CAESAR_LLM__ANTHROPIC__API_KEY=sk-ant-...
```

(For OpenAI or Ollama, see [Picking a model](PICKING-A-MODEL.md).)

### 4. Bring up the stack

```sh
docker compose up -d
```

This starts CAESAR plus a `nats-server` sidecar. Both are bound
to loopback by default (`127.0.0.1:8000`).

### 5. Apply the schema and confirm it's healthy

```sh
docker compose exec caesar caesar praetor migrate
curl -s http://127.0.0.1:8000/healthz
# {"status":"ok"}
```

### 6. Log in

Open `http://127.0.0.1:8000/dashboard` in your browser. Paste the
`CAESAR_DASHBOARD__TOKEN` value from `.env`. You should land on the
audit timeline. **You're done.**

Ask the brain something via the API to confirm round-trip:

```sh
curl -s http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"what is 5 times 12"}]}' \
  | python -m json.tool
```

You should see CAESAR call the `calculator` tool (which is on the
allow-list out of the box) and return `60`.

### 7. Verify the image (optional)

The container image ships with a Sigstore provenance attestation
(per [ADR-0029](adr/0029-docker-distribution-and-init.md)). To
confirm the image came from this repo's CI:

```sh
gh attestation verify oci://ghcr.io/sinidious/caesar:latest \
  --repo Sinidious/CAESAR
```

A failure means the image was tampered with or built outside the
official pipeline. Needs `gh` 2.49+.

## Path B — pip install

For operators who'd rather run CAESAR as a Python service.

### 1. Install

```sh
python -m pip install caesar
```

Also install [`nats-server`](https://github.com/nats-io/nats-server)
if you plan to use Legion workers. Single-host single-process
operation works without it.

### 2. Generate a starter config

```sh
caesar init
```

### 3. Edit `.env` (same one line)

See Path A step 3.

### 4. Apply the schema and start

```sh
caesar praetor migrate
caesar praetor serve
```

That binds `127.0.0.1:8000`. The dashboard's at
`http://127.0.0.1:8000/dashboard`; log in with
`CAESAR_DASHBOARD__TOKEN`.

## What you have now

A homelab AI brain running on your box with:

- **Dashboard** for live audit log, intents, agent activity, and
  the system-prompt override.
- **/v1/chat** HTTP endpoint that runs prompts through the brain
  graph, with the calculator tool already on the allow-list.
- **Policy engine** (`policy.yaml`) gating any HA service call or
  tool invocation — locked down by default.
- **Audit log** in `./var/caesar.sqlite3` recording every brain
  decision, replayable.
- **Prometheus `/metrics`** for observability.

## What's next

Pick what you actually want CAESAR to *do* and wire it in:

- **Talk to Home Assistant** — uncomment `CAESAR_HA__URL` /
  `CAESAR_HA__TOKEN` in `.env`, add the services you want to allow
  to `policy.yaml`. See [SECURITY-MODEL.md](SECURITY-MODEL.md)
  for the trust boundaries.
- **Add tools beyond the calculator** — web search (SearXNG),
  calendar read (CalDAV), or write your own. See
  [Add your own tool](ADD-YOUR-OWN-TOOL.md).
- **Run a worker on another box** — multi-host Legion. See
  [Run a worker on another box](RUN-A-WORKER.md).
- **Switch LLM providers** — Anthropic / OpenAI / fully-local
  Ollama. See [Picking a model](PICKING-A-MODEL.md).
- **Daily operations** — backups, network exposure, metrics
  scrape, log rotation. See [Operations](OPERATIONS.md).

## Troubleshooting

- **`docker compose up` says `policy.yaml` is a directory** —
  you skipped `caesar init`. Stop the stack
  (`docker compose down`), delete the empty directory Docker
  created, run `init`, and try again.
- **Dashboard returns 404** — `CAESAR_DASHBOARD__TOKEN` isn't
  set. Open `.env`, confirm the value `caesar init` generated,
  and restart.
- **`/v1/chat` returns 401** or **the LLM never responds** —
  `CAESAR_LLM__ANTHROPIC__API_KEY` (or your provider's key)
  isn't set. Check `.env` and restart the container.
- **HA tool calls all return "Denied"** — `policy.yaml` doesn't
  allow that service. Add the entry under `allowed_services`
  (see [SR-005](SECURITY-REVIEW.md) for the constrained form
  with `target.entity_id`).
- **Anything else** — `docker compose logs caesar` (or
  `journalctl -u caesar` if you wired a systemd unit) is the
  first place to look.
