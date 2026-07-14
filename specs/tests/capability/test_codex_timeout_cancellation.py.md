# tests/capability/test_codex_timeout_cancellation.py — client timeout and cancellation observations

**Wave:** D/F | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):** capability evidence,
application/MCP/subprocess cancellation specs | **Imported by:** Codex reliability claim

## Purpose

Observe real Codex behavior when a Yoetz call is cancelled or exceeds client timeout around
validation, durable commit, response delivery, semantic wait, and subsequent calls.

## Public surface

Cases cancel before send, during validation/application/provider fake, immediately before/after
commit, during stdout delivery, after client timeout, and during shutdown; each exact Codex version
is a separate cell.

## Behavior

Use marker-controlled installed server and deterministic operation. Trigger client cancellation/
timeout via Codex-supported mechanism, capture public status, then inspect durable state and retry
same request. Pre-commit has no partial effect; post-commit ambiguity returns stored one effect;
provider cancellation/late result cannot steer twice or publish stale output.

Send a following valid status/tool call to prove session framing remains usable or observe orderly
termination/restart as the version-specific contract. Cancellation must not appear as Yoetz internal
error. Evidence records phase class, client observation code, durable result class, retry equality,
and next-call outcome.

## Errors and edge cases

- Timing is marker-synchronized; a missed deadline is inconclusive/fail, not handled timeout.
- Codex's freeform UI text remains private; structural event/tool observations support evidence.
- No automatic test rerun turns a flaky timeout pass.
- Test caps prevent hung children/provider fake.

## Invariants

1. Timeout never proves operation failure.
2. Same request resolves ambiguity without duplicate.
3. Cancellation is not wrapped as internal error.
4. Next-call behavior is observed and version-scoped.

## Tests

Critical pre/post-commit cells run on each advertised Codex/platform pair; equivalent slow-provider
cells may be platform-reduced only by explicit policy.

## Open questions

None.
