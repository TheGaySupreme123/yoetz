# tests/unit/domain/test_findings.py — finding shape, provenance, and ordering inputs

**Wave:** B | **ADRs:** ADR-002, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/findings.py`, `src/yoetz/protocol/coverage.py`
**Imported by:** the domain and kernel unit suite

## Purpose

Lock the immutable finding shape used by deterministic checks, semantic evaluation, ranking, and
receipt rendering.

## Public surface

- `test_finding_requires_bounded_subject_refs` — subject refs are exact and canonical.
- `test_priority_and_origin_validation` — the shared priority/origin scheme is enforced.
- `test_semantic_provenance_is_required_when_semantic` — semantic findings must carry audit trail.
- `test_rank_key_is_deterministic` — ordering input is stable and tie-broken by ID.
- `test_coverage_never_exceeds_subject_support` — finding coverage stays conservative.

## Behavior

The suite proves that findings:

- remain frozen and bounded;
- carry explicit origin and provenance;
- use the shared priority scheme;
- preserve stable subject references without free text;
- never claim stronger coverage than the refs justify.

## Errors and edge cases

- A semantic finding without receipt-finalized provenance is invalid; provisional adapter
  provenance and a receipt ID not yet durable are invalid.
- An unknown finding kind fails at the boundary.

## Invariants

1. Findings are sparse, bounded, and auditable.
2. Origin and provenance remain explicit.
3. Stable IDs and refs drive ordering, not prose.

## Tests

- `tests/unit/domain/test_findings.py`

## Open questions

None.
