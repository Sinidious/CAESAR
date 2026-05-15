<h1 align="center">CAESAR</h1>

<p align="center">
  <strong>Centralized AI Engine for Smart Automation &amp; Response</strong><br/>
  A self-hosted, modular AI brain for your home.
</p>

<p align="center">
  <a href="https://github.com/sinidious/caesar/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/sinidious/caesar/actions/workflows/ci.yml/badge.svg"/></a>
  <a href="LICENSE"><img alt="License: PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm--NC--1.0.0-blue.svg"/></a>
  <a href="CLA.md"><img alt="CLA required" src="https://img.shields.io/badge/CLA-required-orange.svg"/></a>
  <a href="docs/ROADMAP.md"><img alt="Status: pre-alpha" src="https://img.shields.io/badge/status-pre--alpha-red.svg"/></a>
</p>

---

## What is CAESAR?

CAESAR is a self-hosted AI assistant designed to be the **central brain**
of your home. You run it on your own hardware — a Raspberry Pi, a NUC, a
server in your basement, a VM, whatever you want. It connects to
**Home Assistant** for device control, accepts **voice input** from
satellite microphones around your house, exposes a **dashboard** for
managing everything it sees, and lets you plug in **as many LLMs and
agents as you want**, each with its own personality and task priorities.

CAESAR aims to be:

- **Yours.** Self-hosted, no cloud dependency, no telemetry, no SaaS.
- **Modular.** Add, remove, or swap LLM providers and agent workers
  without touching the core.
- **Provider-agnostic.** Local models via Ollama or vLLM, or cloud APIs
  (Anthropic, OpenAI, Groq, …). You choose per task.
- **Safe.** A policy engine sits between agents and the real world; an
  audit log records every decision and is replayable.
- **Honest about state.** This repo is pre-alpha. See [ROADMAP](docs/ROADMAP.md).

## What CAESAR is *not*

- Not a Home Assistant replacement. HA owns devices; CAESAR is the brain.
- Not a smart-speaker. You bring your own microphones (we recommend
  Wyoming-protocol voice satellites — e.g. ESP32-S3 boards).
- Not a managed cloud service. It runs in your house, on your hardware.

## Quickstart

There is no quickstart yet. Application code lands in **v0.1** — see the
[Roadmap](docs/ROADMAP.md). For now this repo contains the architecture
decisions, the CI pipeline, the license, and the contributor framework
that the rest of the project will be built on.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the system diagram,
component descriptions, and data-flow walkthroughs.

Decisions are recorded as [Architecture Decision Records](docs/adr/).

## Contributing

CAESAR welcomes contributions from the homelab community for
**noncommercial use only** — see the License section below.

1. Read [CONTRIBUTING.md](CONTRIBUTING.md) for setup, ADR workflow, and
   commit conventions.
2. Sign the [Contributor License Agreement](CLA.md) on your first PR
   (the bot will prompt you).
3. Open a PR against `main`.

## Security

Please report security issues privately via **GitHub Security Advisories**
on this repository — *not* via public issues. See [SECURITY.md](SECURITY.md).

## License

Source code is licensed under the [**PolyForm Noncommercial License 1.0.0**](LICENSE).
You may freely use, modify, and redistribute CAESAR for any **noncommercial**
purpose (personal, homelab, research, education, hobby). **Commercial use
requires a separate license from Phillip Winnings.**

The name "CAESAR" and any associated marks are reserved. See [NOTICE](NOTICE).

> Copyright (c) 2026 Phillip Winnings. All rights reserved.