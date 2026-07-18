# schemas/events/check-recorded-1.0.0.schema.json — check-recorded payload schema

**Wave:** D/E | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/domain/findings.md`, `src/yoetz/protocol/coverage.md`
**Imported by:** check and replay tests

## Purpose

Describe the payload that records a completed check and its verdict metadata.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/check-recorded-1.0.0.schema.json`.
- Owning model: `CheckRecordedPayload`.

## Behavior

Closed payload object with:

- `mode`;
- nonempty `policies`;
- required normalized `scope` with `claim_ids` and `obligation_ids`;
- required `policy_executions` using the exact check-result execution shape;
- `subject_frontier`;
- `verdict`;
- `returned_finding_ids`;
- `suppressed_count`;
- `coverage`;
- `semantic_status`;
- `semantic_reason`;
- optional final `semantic_provenance` under the presence rules in `domain/events.md`;
- `engine_version`;
- `projection_version`.

The schema admits exactly one built-in policy or the canonical two-policy tuple and requires one
execution per policy with the same identity, version, and order. Execution outcome/reason pairs are
exactly `run/completed`, `skipped/material_unavailable|not_applicable|scope_excluded`, or
`failed/policy_failure`. Scope arrays are typed, bounded, and unique; the domain codec enforces
their normalized unsigned-ASCII ordering, and both empty means whole-case. The schema keeps the
selected findings, component-wise weakest material `Coverage`, and status/reason explicit and bounded. The
status/reason pair uses the closed matrix in `ports/semantic.md`; no free-form reason is allowed.
Predispatch outcomes forbid provenance. The unavailable reasons `credential_unavailable`,
`endpoint_profile_unavailable`, `retry_budget_exhausted`, `audit_reservation_unavailable`, and
`receipt_persistence_unknown` also forbid it; `transport_unavailable`, `provider_rate_limited`, and
`provider_quota_exhausted` require it. The other attempted terminal statuses require only
receipt-finalized `SemanticProvenance`, except `failed/coordinator_failure`, where it is optional.
A present provenance record repeats the top-level selected/final status/reason exactly; earlier
non-selected attempt outcomes are not event fields.

## Errors and edge cases

- Unknown semantic status fails.
- Missing scope/executions, empty policies, policy/execution count/identity/order mismatch, or an
  illegal execution outcome/reason pair fails.
- Missing, malformed, or stronger-than-the-checked-input coverage fails.
- Unknown semantic reason, an invalid status/reason pair, provisional/predispatch provenance, or a
  nested/top-level selected-outcome mismatch fails.
- Hidden or extra findings fail.

## Invariants

1. Check result is explicit.
2. Semantic status is closed.
3. Extra keys are forbidden.
4. Semantic incompleteness has one exact machine-readable cause.
5. Recorded coverage is the conservative coverage summary consumed by replay/projections.
6. Scope and policy applicability are durable event facts, never reconstructed from verdict or
   returned finding IDs.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/conformance/operations/test_check_contract.py`

## Open questions

None.
