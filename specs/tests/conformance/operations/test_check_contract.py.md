# tests/conformance/operations/test_check_contract.py — check public contract

**Wave:** D/E | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/check.md`, `src/yoetz/kernel/ranking.md`
**Imported by:** conformance operations tests

## Purpose

Prove check returns the same findings, verdicts, coverage, and safety boundaries across surfaces.

## Public surface

- `test_check_request_result_parity` — application/CLI/MCP agree on the structured result.
- `test_verdict_and_top_findings_parity` — verdict and ranking match.
- `test_semantic_mode_and_fallback_parity` — semantic and fallback paths behave the same way.
- `test_semantic_status_reason_matrix_and_provenance_stage` — every valid pair is admitted, every
  invalid pair fails, and predispatch results cannot carry attempt provenance.
- `test_provisional_and_unreceipted_provenance_reasons_are_surface_neutral` — both coordinator
  stage failures use their registered internal reason on every application path and never leak as
  provider/public prose.
- `test_recorded_scope_and_policy_execution_parity` — the durable event carries the normalized
  request scope and the same exact policy accounting as the application/CLI/MCP result.
- `test_application_owns_policy_execution_accounting` — kernel results contain assessments only;
  application skip/invoke/failure paths emit the sole closed execution records.
- `test_availability_snapshot_is_dependency_bound` — event/captured-object facts are frozen into the
  case/dependency digest and a generation change is fenced before commit.

## Behavior

The test uses the same frozen case and policy settings across surfaces and asserts:

- the structured result is identical;
- the same finding order and suppressed count appear everywhere;
- semantic-required and semantic-optional modes map to the same contract decisions;
- `semantic_required` failure preserves deterministic findings, returns no semantic findings,
  selects `incomplete_check`, and exposes the exact status/reason code;
- all closed status/reason pairs round-trip identically through application, event, JSON Schema,
  CLI JSON, and MCP projection; a cross-paired reason is rejected;
- adapter provisional provenance is rejected, predispatch provenance is absent, and any published
  attempt provenance names a durable privacy receipt; the exact internal failures are
  `provider_attempt_provenance_is_not_final` for an adapter value in a final slot and
  `privacy_receipt_not_durable` when the matching terminal receipt cannot yet be durably verified;
- omitted and explicit-empty request scopes produce the same required empty/empty event scope;
  nonempty scope IDs are recorded in canonical order, and the event's nonempty `policies` and
  `policy_executions` match the result one-for-one by identity, version, order, outcome, and reason;
- scope-excluded/not-applicable/material-unavailable packs are not invoked, normally returned empty
  assessment tuples are `run/completed`, and raised evaluation is `failed/policy_failure`;
- human summaries remain weaker than structured results.

## Errors and edge cases

- A surface that upgrades coverage fails.
- A surface that drops or rewrites the semantic reason, invents provenance, or turns the completed
  fallback into an operation error fails.
- A surface that omits scope/execution accounting, treats an empty scope as no-op, or attributes an
  execution to a different/reordered policy fails.

## Invariants

1. Check contract is surface-neutral.
2. Structured result is authoritative.
3. Coverage never upgrades through a wrapper.

## Tests

- `tests/conformance/operations/test_check_contract.py`

## Open questions

None.
