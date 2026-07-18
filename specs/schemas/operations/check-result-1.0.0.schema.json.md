# schemas/operations/check-result-1.0.0.schema.json — check result schema

**Wave:** D/E | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/application/check.md`, `src/yoetz/domain/findings.md`,
`src/yoetz/protocol/errors.md`
**Imported by:** CLI, MCP, and parity tests

## Purpose

Describe the public result shape for `check`, including verdict, findings, and public-error fallback.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/operations/check-result-1.0.0.schema.json`.
- Owning model: `CheckResultModel`.

## Behavior

Union of success and common public-error branches. The success branch carries:

- verdict;
- selected findings;
- suppressed count;
- the required closed `semantic_status` and `semantic_reason` pair;
- optional receipt-finalized `semantic_provenance`, never provisional adapter provenance;
- coverage and version identities;
- the frozen subject frontier and any result-frontier bookkeeping the public contract exposes.

The schema must admit deterministic-only, semantic-optional, semantic-required, and fallback
error paths without widening the public contract.

`semantic_reason` is the `SemanticReason` enum owned by `ports/semantic.md` and is validated with
`semantic_status` as a closed pair. `semantic_provenance` is `null` for all predispatch outcomes
and for unavailable reasons `credential_unavailable`, `endpoint_profile_unavailable`,
`retry_budget_exhausted`, `audit_reservation_unavailable`, and `receipt_persistence_unknown`.
It is required for `succeeded`, `refused`, `timeout`, `invalid`, `late`, `stale`, and unavailable
reasons `transport_unavailable`, `provider_rate_limited`, and `provider_quota_exhausted`; it is
optional only for `failed/coordinator_failure`. When present it must validate against
`findings/semantic-provenance-1.0.0`, and its nested status/reason must equal the top-level selected
final pair; earlier late/non-selected attempts remain audit rows and never appear here. A
`semantic_required` provider/policy failure is still this
success branch: it contains the deterministic findings, no semantic findings,
`verdict=incomplete_check`, and the exact status/reason. It is not a public error branch.

Verdict/status/reason enums, IDs, priorities, frontiers, coverage vectors, counts, digests, and
version/provenance identities are structural. Finding `summary`/`detail`, evidence excerpts, and
other user/task-derived prose are content-bearing leaves and admit only their exact original type
or the common omission marker. The success branch requires the common `agent_context` privacy
projection and durable local-disclosure receipt; omission never removes the semantic reason code.

## Errors and edge cases

- A success branch that omits verdict, coverage, semantic status, or semantic reason fails.
- An invalid status/reason pair, provisional provenance, predispatch provenance, or nested/top-level
  provenance identity mismatch fails.
- A fallback branch that is not shared fails.

## Invariants

1. Verdict is explicit.
2. Findings remain bounded.
3. Error fallback is shared.
4. Every completed semantic path is machine-explainable without parsing prose or coverage gaps.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/operations/test_check_contract.py`

## Open questions

None.
