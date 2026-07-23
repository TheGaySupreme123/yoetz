# src/yoetz/application/observation_verification.py — inspect + approved-check orchestration

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):** `ports/workspace_inspect.py.md`,
`adapters/approved_checks.py.md`, `kernel/policies/observation_advice.md` |
**Imported by:** observation advice refresh paths and composition tests

## Purpose

Convert observed changed-file commitments into bounded inspection requests and run standing
approved checks bound to exact subject-state digests.

## Public surface

- `orchestrate_changed_path_inspection(...)`
- `run_bound_approved_check(...)`

## Behavior

Pre-run subject-state mismatch → `STALE` without executing. Post-run state change records a result
but marks `is_current=False` so deterministic advice treats verification as stale. Successful
current checks feed `ObservationCheckFact` into advice automatically.

## Tests

Covered by approved-check and observation-advice unit tests; composition harness owns E2E.
