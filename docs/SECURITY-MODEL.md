# Security model

CAESAR controls a real home. The security model is conservative on
purpose. This page describes the *intended* trust model; it will evolve
as application code lands and we discover where reality bites.

## Trust boundaries

```
┌─────────────────┐    ┌──────────────┐    ┌──────────────────────┐
│ Voice Satellite │    │  Dashboard   │    │ External LLM APIs    │
│ (untrusted)     │    │ (semi-trust) │    │ (data egress hazard) │
└────────┬────────┘    └──────┬───────┘    └──────────┬───────────┘
         │                    │                       │
         └──────── HTTPS / mTLS / signed ──────────────┘
                              │
                       ┌──────▼──────┐
                       │   Praetor   │ ◄── holds long-lived secrets
                       │  (trusted)  │     (HA token, provider keys)
                       └──────┬──────┘
                              │ NATS (auth required)
                  ┌───────────┼───────────┐
                  ▼           ▼           ▼
              Legion W1   Legion W2   HA Bridge
              (scoped)    (scoped)    (scoped)
```

## Principles

1. **One credential vault.** Only Praetor holds long-lived credentials
   (HA tokens, LLM provider keys). Workers receive short-lived,
   capability-scoped tokens at task-dispatch time.
2. **Side effects are mediated.** No worker, no LLM, no satellite ever
   calls Home Assistant directly. The Policy Engine sees every action
   request before the HA Bridge executes it.
3. **Declarative policy, not hard-coded.** Operators describe what is
   allowed in YAML; the engine evaluates. New rules should not require
   a code release.
4. **Decisions are auditable.** Every decision Praetor makes is written
   to the audit log with the inputs that produced it. We assume we
   will need to answer "why did it do that?" in production.
5. **Untrusted input is normalized early.** Voice transcripts and
   dashboard requests are coerced into structured intents before any
   policy decision is made. Free-text never reaches the action layer.

## Threat model (short version)

| Threat                                       | Mitigation |
| -------------------------------------------- | ---------- |
| A worker process is compromised              | Workers hold only short-lived, scoped tokens; cannot call HA Bridge directly. |
| LLM is prompt-injected via a tool output     | Tool outputs are not re-fed as instructions to the planning LLM without normalization. |
| Voice satellite is in untrusted hands        | Satellite tokens are revocable; intents are policy-checked. |
| Dashboard session token is leaked            | Short TTL; dashboard cannot bypass the Policy Engine. |
| Provider API key is exfiltrated by a worker  | Workers never see provider keys; LLM calls go through Praetor's gateway. |
| Operator's policy is misconfigured           | Audit log surfaces every action; dry-run mode for new policies. |
| Supply-chain compromise of a dependency      | Pinned versions; gitleaks pre-commit; Dependabot PRs reviewed. |

## Out of scope (today)

- Defending Praetor against an attacker who already has root on the
  host running it. Self-hosted homelab software cannot help here; we
  document hardening guidance in [CONFIGURATION.md](CONFIGURATION.md).
- Defending against a malicious operator. CAESAR trusts its owner.

## Reporting issues

See [SECURITY.md](../SECURITY.md) — use GitHub's private vulnerability
reporting, not public issues.
