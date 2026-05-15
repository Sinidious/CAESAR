# 0007 — Home Assistant as the device control plane

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

CAESAR controls a real home. We could integrate with each device's
native API (Zigbee2MQTT, Hue Bridge, ESPHome, vendor clouds, …), or we
could ride a layer that has already done the integration work. Home
Assistant has thousands of integrations, a stable REST + WebSocket API,
local-first operation, and an active ecosystem (Wyoming, areas,
scripts, scenes).

Reimplementing device drivers is a road with no end. Picking a control
plane is the leverage move.

## Decision

CAESAR uses **Home Assistant as its device control plane**. CAESAR
does not talk to devices directly; it talks to HA, and HA talks to
devices. The integration lives in a single module — the **HA Bridge** —
that owns:

- A REST client for one-shot operations.
- A WebSocket client for live state, events, and service calls.
- A normalized internal representation of HA entities (lights,
  switches, sensors, scripts, scenes, automations) for the rest of
  CAESAR to consume.
- Auth via a single long-lived access token stored on Praetor.

All real-world side effects from CAESAR flow through this module, and
through the Policy Engine ([ADR-0013](0013-policy-engine.md)) before
that.

## Alternatives considered

- **Native per-device integration** — maximum control, infinite work,
  duplicates HA's job.
- **MQTT only** — workable for devices already on MQTT, but loses HA's
  abstraction layer (areas, scenes, automations).
- **OpenHAB / Hubitat** — credible peers but smaller integration
  ecosystems and unfamiliar territory for the maintainer.
- **Matter directly** — promising long-term, not yet sufficient for an
  existing homelab without a controller hub.

## Consequences

### Positive

- Praetor inherits every Home Assistant integration for free.
- Voice satellites and CAESAR share an ecosystem
  ([ADR-0008](0008-voice-wyoming.md)), reducing integration surface.
- Failure modes are well-understood: when HA is down, CAESAR is
  read-only.

### Negative

- HA is a hard dependency. If the user doesn't run HA, CAESAR is not
  useful.
- HA's API is stable but does change between major versions; the
  Bridge will need ongoing maintenance.

### Neutral

- The Bridge will eventually need to expose HA *events* (state
  changes) as a stream into CAESAR's memory/audit log. That is in
  scope for a later ADR if it grows beyond a simple subscription.

## References

- [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/)
- [Home Assistant WebSocket API](https://developers.home-assistant.io/docs/api/websocket/)
- [Long-lived access tokens](https://www.home-assistant.io/docs/authentication/#your-account-profile)
