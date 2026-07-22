# src/yoetz/adapters/observation_semantic_advice.py — optional privacy-gated semantic advice

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):**
`application/observation_advice.md`, `ports/semantic.md` | **Imported by:** observation advice
wiring when a semantic evaluator is configured in ready composition

## Purpose

Provide additive semantic advice over minimized approved evidence packets only. Deterministic
advice remains sufficient; this adapter is never required for basic correctness guidance.

## Public surface

- `NullSemanticAdvice` — always no-op
- `OptionalSemanticAdvice` — configured+ready evaluator callback
- `PrivacyGatedSemanticAdvice` — forwards only minimized packets

## Behavior

Reject packets containing transcript/stdout/stderr/path/cwd/command/reasoning keys. When ready,
return additive finding identities that project into `AdviceSnapshot` without replacing
deterministic results.

## Invariants

1. No repo access or ambient logs.
2. Fail closed when not configured or not ready.
3. Additive only.

## Tests

Covered by `tests/unit/application/test_observation_advice.py` deterministic-vs-semantic path.
