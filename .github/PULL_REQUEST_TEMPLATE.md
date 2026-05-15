<!--
Thanks for contributing to CAESAR.

Title: please use Conventional Commits, e.g.
  feat(praetor): wire LangGraph state machine
  fix(bridge/ha): handle reconnect after auth refresh
  docs(adr): accept ADR-0006

The pr-lint workflow will reject titles that don't match.
-->

## Summary

<!-- One or two sentences. What changes and, more importantly, why. -->

## ADR reference

<!--
If this PR introduces application code in a new area (a new module,
external dependency, or architectural pattern), link the ADR that
approved it. Small fixes / refactors / tests / docs do not need an ADR.
-->

- Related ADR: <!-- e.g. docs/adr/0007-…md, or "n/a — small change" -->

## Test plan

<!-- Bulleted checklist of how you verified this. -->

- [ ] `just check` is green locally
- [ ] Added/updated tests where it made sense
- [ ] Updated docs (`docs/…`, ADRs, README) if behaviour or contracts changed

## Checklist

- [ ] I have signed the [CLA](../CLA.md) (the CLA bot will confirm)
- [ ] My commits follow [Conventional Commits](https://www.conventionalcommits.org)
- [ ] I did not include AI-attribution lines in commits, code, or this PR
