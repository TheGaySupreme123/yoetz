# src/yoetz/kernel/policies/observation_advice.py — deterministic live-observation advice pack

**Wave:** D | **ADRs:** ADR-005, ADR-009, ADR-010 | **Imports (spec-tree):**
`domain/observation.py.md`, `domain/findings.md` | **Imported by:**
`application/observation_advice.py`, unit policy tests

## Purpose

Detect verification gaps from observation envelopes (and optional inspect/check/composition
facts) without requiring cooperative Yoetz MCP publications. Reuses existing `FindingKind`
identities with observation-advice rule codes.

## Public surface

- `observation_advice_findings(ObservationAdviceContext) -> tuple[ObservationAdviceCandidate, ...]`
- Fact helpers: check/inspect/composition facts
- Policy id `observation-advice/0.1.0`

## Behavior

Rules (rule_code → FindingKind):

- `failed_command_unresolved` → `failed_work_omitted`
- `edit_after_successful_check` → `stale_evidence_for_changed_state`
- `completion_without_verification` → `claim_without_admissible_evidence`
- `static_test_for_live_claim` → `evidence_does_not_support_claim`
- `subagent_finding_unaddressed` → `failed_work_omitted`
- `change_outside_plan` → `diff_does_not_match_account`
- `observation_gap_or_stale` → `ledger_stale_or_incomplete`
- `provider_not_ready` → `material_limitation_omitted`
- `semantic_claim_without_attempt` → `requested_item_never_attempted`

Advice is nonblocking and never prevents Codex actions.

## Errors and edge cases

Fail closed on consent, mapping, and validation errors; never leak secrets.

## Invariants

1. Works offline from structural envelopes alone.
2. No transcript/secret command output in candidates.
3. Ranking uses existing FindingKind priorities.

## Tests

`tests/unit/kernel/test_observation_advice_policies.py`

## Open questions

None.
