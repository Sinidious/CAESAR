# Changelog

All notable changes to CAESAR will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file is maintained automatically by
[release-please](https://github.com/googleapis/release-please) from
[Conventional Commits](https://www.conventionalcommits.org) on `main`.

## [0.3.0](https://github.com/Sinidious/CAESAR/compare/v0.2.0...v0.3.0) (2026-05-17)


### Features

* **bus:** v1.2 — NATS NKEY auth wiring (ADR-0027) ([#60](https://github.com/Sinidious/CAESAR/issues/60)) ([5987070](https://github.com/Sinidious/CAESAR/commit/598707067507ced979ac7bdb5299d617dc779edc))
* **legion:** v1.2 — worker bootstrap CLI + example NATS conf + docs (ADR-0027) ([#61](https://github.com/Sinidious/CAESAR/issues/61)) ([8fcc37c](https://github.com/Sinidious/CAESAR/commit/8fcc37cf2c682a1608091c4e0ffa53894ce881d5))
* **llm:** v1.1 — Ollama provider for fully-local operation (ADR-0026) ([#56](https://github.com/Sinidious/CAESAR/issues/56)) ([ceeca2d](https://github.com/Sinidious/CAESAR/commit/ceeca2ddd41e4bf8b380db5b1f5d7df8b54dbb31))
* **llm:** v1.1 — OpenAI provider + multi-provider config (ADR-0026) ([#55](https://github.com/Sinidious/CAESAR/issues/55)) ([f6cf0d0](https://github.com/Sinidious/CAESAR/commit/f6cf0d0df7f997e8101ba2271474b44e18acfcba))
* **llm:** v1.1 — per-task LLM routing (ADR-0026) ([#57](https://github.com/Sinidious/CAESAR/issues/57)) ([0d3f537](https://github.com/Sinidious/CAESAR/commit/0d3f5375044bb22a4301398ee9a0ec05a2666ca1))


### Bug Fixes

* **brain:** always inject safety preamble before LLM calls (SR-004) ([#48](https://github.com/Sinidious/CAESAR/issues/48)) ([7ccde7a](https://github.com/Sinidious/CAESAR/commit/7ccde7abdbd6f8d48f99bef07460f01a1c1e06d9))
* **dashboard:** bundle SR-007/010/012 hardening ([#49](https://github.com/Sinidious/CAESAR/issues/49)) ([9f95c75](https://github.com/Sinidious/CAESAR/commit/9f95c75ae048991a3f854010bd9921c88b153f91))
* **dashboard:** decouple cookie signing from auth token (SR-006) ([#52](https://github.com/Sinidious/CAESAR/issues/52)) ([de104fb](https://github.com/Sinidious/CAESAR/commit/de104fb81046ab7afa46eb41e89c6fed97b5cb40))
* **dashboard:** rate-limit /dashboard/login (SR-002) ([#46](https://github.com/Sinidious/CAESAR/issues/46)) ([deedaff](https://github.com/Sinidious/CAESAR/commit/deedaffd459ebf59cc53e0d7c9db43066c12b56c))
* **db:** clamp audit-payload string size at write (SR-008) ([#50](https://github.com/Sinidious/CAESAR/issues/50)) ([7709f5f](https://github.com/Sinidious/CAESAR/commit/7709f5feb3655b7c1f774a8346551e513948da31))
* **metrics:** optional bearer auth on /metrics (SR-003) ([#53](https://github.com/Sinidious/CAESAR/issues/53)) ([5330e9b](https://github.com/Sinidious/CAESAR/commit/5330e9bb44824ec722a0d36409890e38f0455176))
* **policy:** constrain target.entity_id per allow-list rule (SR-005) ([#47](https://github.com/Sinidious/CAESAR/issues/47)) ([9a0dd11](https://github.com/Sinidious/CAESAR/commit/9a0dd11abce58cfc778dd936ecaa31ac37f1126c))
* **server:** bind to loopback by default (SR-001) ([#44](https://github.com/Sinidious/CAESAR/issues/44)) ([74ef20c](https://github.com/Sinidious/CAESAR/commit/74ef20c0ec5bd89b3c3e9bfa839d99ccfa3e7bc5))


### Documentation

* **llm:** v1.1 — 'How to pick a model' page ([#58](https://github.com/Sinidious/CAESAR/issues/58)) ([29fe89e](https://github.com/Sinidious/CAESAR/commit/29fe89e5247992290f4f07942d6e13905ee1c7a0))
* plan v1.1 — provider flexibility (ADR-0026) ([#54](https://github.com/Sinidious/CAESAR/issues/54)) ([9101e24](https://github.com/Sinidious/CAESAR/commit/9101e249369fd6ceaa2851cb030237da56997c30))
* plan v1.2 — multi-host Legion (ADR-0027) ([#59](https://github.com/Sinidious/CAESAR/issues/59)) ([5794794](https://github.com/Sinidious/CAESAR/commit/579479472b5a3fc6f9c016df6ff59c5806231c94))
* plan v1.3 — tools beyond HA (ADR-0028) ([#63](https://github.com/Sinidious/CAESAR/issues/63)) ([59ab07b](https://github.com/Sinidious/CAESAR/commit/59ab07baf96b68f5559e2ab832b0757654cfbd60))

## [0.2.0](https://github.com/Sinidious/CAESAR/compare/v0.1.2...v0.2.0) (2026-05-17)


### Features

* **bus:** v0.3 PR A — NATS bus, Legion protocol, worker registry ([#29](https://github.com/Sinidious/CAESAR/issues/29)) ([89bdb34](https://github.com/Sinidious/CAESAR/commit/89bdb34486523270d05187ec1f9615fe0cb19aa5))
* **dashboard:** v0.5 PR A — live audit log dashboard (ADR-0021) ([#33](https://github.com/Sinidious/CAESAR/issues/33)) ([c16986c](https://github.com/Sinidious/CAESAR/commit/c16986ca7f5433f61c4b7a56e534504755fbd516))
* **dashboard:** v0.5 PR B — intent timeline + agent activity ([#34](https://github.com/Sinidious/CAESAR/issues/34)) ([daa9349](https://github.com/Sinidious/CAESAR/commit/daa93491e0c5a666c206dfaf4063093276240f3e))
* **dashboard:** v0.5 PR C — settings UI to edit the brain's system prompt (closes v0.5) ([#35](https://github.com/Sinidious/CAESAR/issues/35)) ([578ee73](https://github.com/Sinidious/CAESAR/commit/578ee7313f114fdc19dda56dc313b49361a341cb))
* **db:** v1.0 — backup/restore CLI via SQLite Online Backup API ([#36](https://github.com/Sinidious/CAESAR/issues/36)) ([fa5765d](https://github.com/Sinidious/CAESAR/commit/fa5765d6ef3653823164a068f42f7adba7e5531d))
* **docs:** v1.0 — mkdocs-material site on GitHub Pages (ADR-0024) ([#39](https://github.com/Sinidious/CAESAR/issues/39)) ([78931e4](https://github.com/Sinidious/CAESAR/commit/78931e4a3c6a04b296170ea42333195d4ac84c6f))
* **ha:** v0.2 PR A — HA Bridge (REST + WS) + policy stub + device routes ([#26](https://github.com/Sinidious/CAESAR/issues/26)) ([c0a7202](https://github.com/Sinidious/CAESAR/commit/c0a7202375421ce3854e273cedc1cd6d41b37f9b))
* **legion:** v0.3 PR B — memory-recall worker + recall_memory brain tool ([#30](https://github.com/Sinidious/CAESAR/issues/30)) ([cae6098](https://github.com/Sinidious/CAESAR/commit/cae6098fc34b3c8c751256424b3f33525dad5170))
* **memory:** v0.4 PR A — episodic TTL retention sweep (ADR-0020) ([#31](https://github.com/Sinidious/CAESAR/issues/31)) ([a0720f7](https://github.com/Sinidious/CAESAR/commit/a0720f7df1efb2e4e609e7d56787a6dd54fe5347))
* **memory:** v0.4 PR B — semantic recall via embeddings (closes v0.4) ([#32](https://github.com/Sinidious/CAESAR/issues/32)) ([c73ec09](https://github.com/Sinidious/CAESAR/commit/c73ec09c70233cbfa64c1c58db4a0a34ab3b6e32))
* **metrics:** v1.0 — Prometheus /metrics endpoint ([#37](https://github.com/Sinidious/CAESAR/issues/37)) ([ef263e1](https://github.com/Sinidious/CAESAR/commit/ef263e1aa0e07e9a4a3c56efabdcbb11aa8e1059))
* **policy:** v0.2 PR B — YAML allow-list policy replaces DenyAllPolicy ([#27](https://github.com/Sinidious/CAESAR/issues/27)) ([ff5238d](https://github.com/Sinidious/CAESAR/commit/ff5238d077964fe9fcde26497b67427bce68c9f9))
* **tracing:** v1.0 — opt-in OpenTelemetry tracing (ADR-0023) ([#38](https://github.com/Sinidious/CAESAR/issues/38)) ([d73e5b1](https://github.com/Sinidious/CAESAR/commit/d73e5b1d849f6cd1a95403462adb86f629ad4174))
* v0.1 Praetor heartbeat — FastAPI + LangGraph echo + audit ([#25](https://github.com/Sinidious/CAESAR/issues/25)) ([476e876](https://github.com/Sinidious/CAESAR/commit/476e8766a6a3f7be862911eeca2747019d4806b6))
* v0.2 PR C — LLM tool-use closes the speak-to-the-house gate ([#28](https://github.com/Sinidious/CAESAR/issues/28)) ([00b8a93](https://github.com/Sinidious/CAESAR/commit/00b8a931bad9b79430dbbca818b04618ec318f36))


### Bug Fixes

* **ci:** explicitly disable CLA lock-on-merge ([#24](https://github.com/Sinidious/CAESAR/issues/24)) ([dde5c00](https://github.com/Sinidious/CAESAR/commit/dde5c00ea0e03545e97041d0bd086d2e8383aaf6))


### Documentation

* **security:** v1.0 — lightweight security review (ADR-0025) ([#40](https://github.com/Sinidious/CAESAR/issues/40)) ([f58adb5](https://github.com/Sinidious/CAESAR/commit/f58adb5a79b6b512b15accb14b16cf27fde275ac))

## [0.1.2](https://github.com/Sinidious/CAESAR/compare/v0.1.1...v0.1.2) (2026-05-16)


### Bug Fixes

* allow release-please-- branches and replace broken CI badge ([#23](https://github.com/Sinidious/CAESAR/issues/23)) ([b8716f3](https://github.com/Sinidious/CAESAR/commit/b8716f3c59addf38403f37238efa2efe96ef819d))


### Documentation

* correct CLA required check name (cla, not CLA Assistant Lite) ([#17](https://github.com/Sinidious/CAESAR/issues/17)) ([48bc6a1](https://github.com/Sinidious/CAESAR/commit/48bc6a17e611a610473054b01d699a83c559c537))

## [0.1.1](https://github.com/Sinidious/CAESAR/compare/v0.1.0...v0.1.1) (2026-05-16)


### Documentation

* **adr:** record configuration loader and layered sources (ADR-0017) ([#10](https://github.com/Sinidious/CAESAR/issues/10)) ([de04180](https://github.com/Sinidious/CAESAR/commit/de04180c5d68008d81cb298fcf2f88a242e2f819))
* **adr:** record SQLite persistence via SQLAlchemy Core and Alembic (ADR-0019) ([#13](https://github.com/Sinidious/CAESAR/issues/13)) ([93f95dd](https://github.com/Sinidious/CAESAR/commit/93f95ddc00310d322ca4c8ebeb739907d33341d2))
* **adr:** record structured logging with structlog (ADR-0018) ([#12](https://github.com/Sinidious/CAESAR/issues/12)) ([e6baa2f](https://github.com/Sinidious/CAESAR/commit/e6baa2fefb813d823ae6b1737162d7e9cc4024ab))


### Build System

* **deps-dev:** bump the dev-dependencies group across 1 directory with 8 updates ([#8](https://github.com/Sinidious/CAESAR/issues/8)) ([3c7769b](https://github.com/Sinidious/CAESAR/commit/3c7769b87a027ddddc98f2e7d910fc7121290594))

## 0.1.0 (2026-05-16)


### Documentation

* add architecture, roadmap, glossary, security model, configuration ([7adaf65](https://github.com/Sinidious/CAESAR/commit/7adaf65974da8b699ba6ed38088dbf09408e8c66))
* **adr:** record first 15 architecture decisions ([84192d8](https://github.com/Sinidious/CAESAR/commit/84192d838cd2503c53ecdce9371e4e7f433757a0))
* **adr:** record repository and package layout (ADR-0016) ([b4a9743](https://github.com/Sinidious/CAESAR/commit/b4a9743e22c31c692da613abfdecfd98bed9457c))
* **adr:** repository and package layout (ADR-0016) ([15b9d1a](https://github.com/Sinidious/CAESAR/commit/15b9d1ae85ba7eef3ed7d28faa2cb966147f2ea5))

## [Unreleased]

### Added

- Initial project scaffolding: license (PolyForm Noncommercial 1.0.0),
  CLA, contributor docs, security policy, code of conduct, and the
  first batch of architecture ADRs.
