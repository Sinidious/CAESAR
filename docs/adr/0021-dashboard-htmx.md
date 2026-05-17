# 0021 — Dashboard: server-rendered HTMX, SSE realtime, single-token auth

- Status: Accepted
- Date: 2026-05-17
- Deciders: @sinidious

## Context

v0.5 of the roadmap ([ROADMAP](../ROADMAP.md)) asks: *"Can I see what
Praetor decided and why, in a browser?"* CAESAR has been API-only so
far. The dashboard is the first user-facing surface and the first time
we have to make frontend choices.

The maintainer is one person. The deploy target is a single homelab
machine. There is no requirement for a hundred dashboard users, no
mobile-first SLA, no requirement to render a 3D house. The view is
mostly an append-only feed (audit log + intents + activity) plus a
small amount of config UI.

What we are NOT trying to do:

- Ship a SPA with its own build pipeline, lockfile, and bundle.
- Set up a separate Node project alongside the Python service.
- Manage browser state (router, store, optimistic mutations).

## Decision

The dashboard is **server-rendered HTML with HTMX**, served by Praetor
under `/dashboard`, with **Server-Sent Events** for live updates and
a **single-token cookie** for auth.

Concretely:

- **Templates**: Jinja2, FastAPI's built-in support, files at
  `caesar/praetor/dashboard/templates/`.
- **HTMX**: vendored at `caesar/praetor/dashboard/static/htmx.min.js`
  so the dashboard works offline. Included in `base.html`.
- **Realtime**: the SSE stream at `/dashboard/audit/stream` is fed
  from an in-process `AuditEventBus`. The `AuditLogger` publishes
  each new row to the bus right after the DB write succeeds.
  Lagging subscribers drop events instead of blocking the writer.
- **Auth**: `CAESAR_DASHBOARD__TOKEN` is a single shared secret. On
  first visit the user pastes it on `/dashboard/login`; a signed
  cookie carries it on subsequent requests. When the setting is
  empty the dashboard refuses to mount (operator must opt in by
  setting a token).
- **Bind**: default `127.0.0.1`. The operator points a reverse proxy
  at it (or exposes deliberately).
- **No JS framework**, no router, no state management. Each fragment
  request returns plain HTML.

## Alternatives considered

- **React/Svelte SPA + REST API.** Better UX once built; adds a
  node build pipeline and a separate deploy story for one user. Worth
  reconsidering if more contributors join or a mobile app appears.
- **WebSocket realtime.** Bidirectional and richer; overkill for the
  read-only feed that v0.5 needs. We may upgrade later when the
  dashboard sends commands (acknowledge, override, pause).
- **Polling.** Simplest, wastes idle resources, visible lag. SSE is a
  small enough lift to skip.
- **OAuth / OIDC.** Right answer for multi-tenant; wrong shape for a
  homelab service the maintainer runs alone.
- **Defer the dashboard.** Possible but the v0.5 gate is specifically
  about visibility, and the audit log is most useful when you can
  watch it scroll.

## Consequences

### Positive

- One deploy artefact: Praetor ships the dashboard. No second build,
  no second container.
- View source and `Ctrl+F` work; the entire UI is plain HTML.
- The same templates can be SSR'd by a future framework if we ever
  outgrow HTMX.
- Adding a panel = adding a route + a template. Low ceremony.

### Negative

- Heavy client interactions (drag-drop, large lists with virtual
  scrolling, complex forms) are awkward in HTMX. We'll learn the
  ceiling when we hit it.
- SSE through some reverse proxies needs `proxy_buffering off` or
  similar. Documented when we publish a deployment guide.

### Neutral

- Auth is intentionally minimal. Multi-user/role-based auth is a
  later ADR; expect a dependency on whatever HA-side identity the
  operator already uses.

## References

- [HTMX](https://htmx.org/)
- [Jinja2](https://jinja.palletsprojects.com/)
- [Server-Sent Events](https://developer.mozilla.org/docs/Web/API/Server-sent_events)
- [ADR-0006 — Praetor runtime](0006-praetor-runtime.md)
- [ADR-0012 — Audit log](0012-audit-log.md)
