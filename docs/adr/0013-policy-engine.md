# 0013 — Policy engine guards real-world side effects

- Status: Accepted
- Date: 2026-05-15
- Deciders: @sinidious

## Context

CAESAR will, sooner or later, be asked to do something it shouldn't.
Unlock the front door at 3 AM. Crank the thermostat to a setting the
HVAC can't sustain. Disable a smoke detector. Send a payment.

We need a layer that says "no" — and says it for reasons we can read,
review, and version — between the agents and the HA Bridge.
Hard-coding refusals in agent code is brittle, hides the policy
across a dozen files, and ties policy changes to code releases.

## Decision

CAESAR has a **Policy Engine** that sits between agent-decided actions
and the HA Bridge. Properties:

- **Declarative rules**, loaded from YAML files in a configured
  directory. Operators write rules; the engine evaluates.
- **Rule shape**: predicate (which actions does this rule apply to?)
  + condition (when does it apply?) + verdict (allow / deny /
  require-confirm).
- **Default deny for action types not whitelisted by any rule.**
  Read-only operations (state queries) are allow-by-default.
- **Every evaluation is audit-logged**, including which rule fired.
- **Dry-run mode** for new rules: log the verdict, don't enforce, for
  a configurable window.

Initial rule examples (sketched, not authoritative):

```yaml
- name: no-unlock-after-midnight-when-away
  actions: ["lock.unlock"]
  when: time.between("00:00", "06:00") and presence.away
  verdict: deny

- name: confirm-bulk-light-changes
  actions: ["light.turn_on", "light.turn_off"]
  when: action.scope == "all"
  verdict: require_confirm
```

## Alternatives considered

- **Hard-coded checks in each Legion worker** — duplication, drift,
  and changes require code releases.
- **HA automations as the policy layer** — works, but moves policy
  outside CAESAR's audit log and outside the Policy Engine's
  language. We may *also* express policies as HA automations, but
  CAESAR's own choices must go through this engine.
- **OPA / Rego** — a heavyweight, well-trodden policy engine. Worth
  reconsidering for v1.0; rejected initially because YAML rules are
  enough for the action shapes we have, and OPA is a hefty
  dependency for a single-host service.

## Consequences

### Positive

- Operators can change policy without a release.
- Audit log shows *why* something was allowed or denied.
- New action types fail closed by default.

### Negative

- We carry policy as code complexity from day one, even before there
  are interesting policies to write.
- Rules can conflict; the engine needs deterministic precedence
  rules. Documented when implemented.

### Neutral

- We will likely outgrow YAML predicates eventually. The interface
  between agent and engine (action descriptor) is designed to make
  swapping to OPA/Rego later a tractable change.

## References

- [OPA / Rego](https://www.openpolicyagent.org/)
- [HA conditions reference](https://www.home-assistant.io/docs/scripts/conditions/)
