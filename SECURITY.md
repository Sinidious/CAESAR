# Security Policy

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report security issues privately via GitHub's built-in private
vulnerability reporting:

**<https://github.com/sinidious/caesar/security/advisories/new>**

This sends the report directly to the maintainers and is not visible to
the public until a coordinated disclosure is published.

### What to include

- A clear description of the vulnerability and the impact.
- Reproduction steps (a minimal proof of concept is ideal).
- The version, commit SHA, or branch you tested against.
- Your assessment of severity (CVSS or qualitative).
- Whether you intend to publish the finding (and when).

### What you can expect

| Step                     | Target time          |
| ------------------------ | -------------------- |
| Acknowledgement          | Within 3 business days |
| Initial triage + severity | Within 7 business days |
| Fix or mitigation plan   | Within 30 business days |
| Coordinated disclosure   | Negotiated case-by-case |

CAESAR is a pre-alpha project run by a single maintainer; timelines may
slip and are best-effort, not contractual.

## Supported versions

CAESAR has not had a stable release yet. The `main` branch is the only
supported version. After v1.0 ships, the most recent **minor** release
will receive security fixes.

| Version       | Supported          |
| ------------- | ------------------ |
| `main`        | :white_check_mark: |
| pre-`v1.0`    | not applicable     |

## Scope

In scope for security reports:

- Vulnerabilities in CAESAR code (Praetor, Legion, HA Bridge, etc.).
- Vulnerabilities in CAESAR's CI workflows or build/release tooling.
- Supply-chain issues in pinned dependencies.
- Authentication, authorization, or policy-engine bypass.

Out of scope:

- Vulnerabilities in Home Assistant, NATS, Ollama, or other upstream
  projects — please report those to their respective maintainers.
- Issues that require physical access to the user's hardware.
- Denial of service through resource exhaustion in self-hosted
  deployments under user control.

## Safe harbor

Good-faith security research conducted within this policy will not be
pursued legally. Please:

- Avoid privacy violations, data destruction, or service degradation.
- Only interact with accounts you own or have explicit permission to test.
- Give us a reasonable window to respond before disclosure.
