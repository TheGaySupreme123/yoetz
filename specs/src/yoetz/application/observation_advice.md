# src/yoetz/application/observation_advice.py — AdviceSnapshot construction and suppression

**Wave:** D | **ADRs:** ADR-005, ADR-009, ADR-010 | **Imports (spec-tree):**
`kernel/policies/observation_advice.md`, `domain/observation.py.md`, `protocol/coverage.md` |
**Imported by:** local observation store refresh, observe hooks/CLI status

## Purpose

Build `AdviceSnapshot` with ranked `AdviceItem` values from deterministic observation-advice
candidates plus optional additive semantic advice. Suppress duplicate delivery by finding identity
+ evidence frontier; reissue when evidence changes, severity increases, or prior recommendation
remains unresolved after material work.

## Public surface

- `build_observation_advice_snapshot(ObservationAdviceBuildInput) -> AdviceSnapshot | None`
- `should_reissue_advice(...)`
- `stable_advice_finding_id(...)`
- `minimized_semantic_evidence_packet(...)`
- `hook_advice_context(...)`
- `advice_items_for_ledger(...)`
- `SemanticAdvicePort` protocol
- `ObservationAdviceContextBuilder.build(...)`

## Behavior

Deterministic advice always works offline. Coverage is honest and observation-qualified: active
hook evidence may raise `hook_observed`; otherwise engine-derived + known gaps. Semantic add-ons
are optional and never required for basic correctness guidance. Recommended next actions are
closed tokens safe for hook `additionalContext`.

The context builder reads normalized envelopes/gaps, current durable verification/check facts,
changed-path inspection facts, production composition readiness, prior suppression history, and—
through the ordinary ledger/runtime path—claims, obligations, actions, results, findings, and
evidence. Missing sources remain explicit limitations. Production semantic readiness derives from
the generation-fenced provider registry. Optional semantic work is additive, privacy-gated, and
receives only a minimized redacted evidence packet; failure cannot erase deterministic advice.
Deterministic items materialize through existing `FindingKind`/`finding_recorded` identities.

## Errors and edge cases

Fail closed on consent, mapping, and validation errors; never leak secrets.

## Invariants

1. No seventh MCP tool.
2. Suppression identity binds ranked findings + evidence digest + next action.
3. Minimized semantic packets exclude repo access, transcripts, and ambient logs.

## Tests

`tests/unit/application/test_observation_advice.py`

## Open questions

None.
