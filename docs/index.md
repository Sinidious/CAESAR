# CAESAR

Self-hosted homelab AI assistant. A central brain (**Praetor**)
coordinates a pool of pluggable worker agents (**Legion**), talks to
Home Assistant for device control, accepts voice input, drives a web
dashboard, and runs on whatever LLM you point it at.

Pre-alpha. Don't deploy this on anything you care about yet.

## Start here

- [Architecture](ARCHITECTURE.md) — the moving parts and how they fit.
- [Roadmap](ROADMAP.md) — what we're building, in order.
- [Glossary](GLOSSARY.md) — Praetor, Legion, and every other term.
- [Security model](SECURITY-MODEL.md) — trust boundaries and what the
  policy engine is for.
- [Configuration](CONFIGURATION.md) — every environment variable.
- [ADR index](adr/README.md) — every architecture decision so far.

## License

CAESAR is licensed under [PolyForm Noncommercial 1.0.0](../LICENSE).
Personal and noncommercial homelab use is welcome; commercial use
requires a separate agreement. See [README.md](../README.md) for the
short version.

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md). Every contributor signs the
[CLA](../CLA.md) the first time they open a PR.
