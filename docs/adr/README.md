# Architecture Decision Records

We record non-trivial technical choices as ADRs in
[MADR](https://adr.github.io/madr/) format. They live in this folder,
numbered sequentially, and never change status once accepted — instead,
a new ADR supersedes the old one and links back.

## Process

1. Discuss the idea in an issue using the
   [ADR proposal](https://github.com/Sinidious/CAESAR/blob/main/.github/ISSUE_TEMPLATE/adr_proposal.yml)
   template.
2. Run `just adr-new "<short title>"` to copy
   [`0000-template.md`](0000-template.md) to the next number.
3. Open a PR adding only the ADR. Get review. Set status to **Accepted**
   on merge (or **Rejected**, if appropriate — rejected ADRs still get
   merged so the reasoning isn't lost).
4. If the decision is later reversed, write a new ADR that supersedes
   the old one; update the old one's status to **Superseded by ADR-NNNN**.

ADRs are required before introducing application code in a new area
(new module, new external dependency, new architectural pattern).
Small fixes, refactors, tests, and doc-only changes do not need an ADR.

## Index

| #    | Title                                                                     | Status   |
| ---- | ------------------------------------------------------------------------- | -------- |
| 0001 | [Record architecture decisions](0001-record-architecture-decisions.md)    | Accepted |
| 0002 | [License under PolyForm Noncommercial 1.0.0](0002-license-polyform-nc.md) | Accepted |
| 0003 | [Require a CLA for outside contributions](0003-require-cla.md)            | Accepted |
| 0004 | [Conventional Commits + release-please](0004-conventional-commits-and-release-please.md) | Accepted |
| 0005 | [Python 3.11 as the primary runtime](0005-python-3-11-runtime.md)         | Accepted |
| 0006 | [Praetor on FastAPI + LangGraph](0006-praetor-runtime.md)                 | Accepted |
| 0007 | [Home Assistant as the device control plane](0007-home-assistant-bridge.md) | Accepted |
| 0008 | [Voice input via client-side transcription](0008-voice-wyoming.md)        | Accepted |
| 0009 | [NATS as the message bus](0009-message-bus-nats.md)                       | Accepted |
| 0010 | [Hybrid memory: SQLite + vector store](0010-memory-hybrid.md)             | Accepted |
| 0011 | [Provider-agnostic LLM Gateway](0011-llm-gateway.md)                      | Accepted |
| 0012 | [Audit every brain decision](0012-audit-log.md)                           | Accepted |
| 0013 | [Policy engine guards real-world side effects](0013-policy-engine.md)     | Accepted |
| 0014 | [Trunk-based development with release branches as needed](0014-trunk-based-development.md) | Accepted |
| 0015 | [Ruff + mypy + pytest as the Python toolchain](0015-python-toolchain.md)  | Accepted |
| 0016 | [Repository and package layout](0016-package-layout.md)                   | Accepted |
| 0017 | [Configuration via pydantic-settings with layered sources](0017-configuration.md) | Accepted |
| 0018 | [Structured logging with structlog](0018-structured-logging.md)           | Accepted |
| 0019 | [SQLite persistence via SQLAlchemy Core and Alembic](0019-sqlite-persistence.md) | Accepted |
| 0020 | [Episodic memory retention via time-based TTL](0020-memory-retention-ttl.md) | Accepted |
| 0021 | [Dashboard: server-rendered HTMX, SSE, single-token auth](0021-dashboard-htmx.md) | Accepted |
| 0022 | [Backup and restore via SQLite Online Backup API](0022-backup-restore.md) | Accepted |
| 0023 | [OpenTelemetry tracing as an opt-in extra](0023-opentelemetry-tracing.md) | Accepted |
| 0024 | [Documentation site on mkdocs-material + GitHub Pages](0024-docs-site-mkdocs.md) | Accepted |
| 0025 | [Lightweight security review as a living document](0025-security-review.md) | Accepted |
| 0026 | [Multi-provider LLM gateway](0026-multi-provider-llm-gateway.md) | Accepted |
| 0027 | [NATS auth and multi-host Legion](0027-nats-auth-multihost-legion.md) | Accepted |
| 0028 | [Tools beyond HA: workers + generalised Policy Engine](0028-tools-beyond-ha.md) | Accepted |
