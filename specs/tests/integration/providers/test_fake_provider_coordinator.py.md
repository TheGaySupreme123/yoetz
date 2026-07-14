# tests/integration/providers/test_fake_provider_coordinator.py — scripted fake provider coordination

**Wave:** E | **ADRs:** ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz_core/adapters/providers/fake.md`, `src/yoetz_core/application/check.md`
**Imported by:** integration provider tests

## Purpose

Prove the fake provider coordinator can drive the application through the full semantic outcome
matrix without network access.

## Public surface

- `test_fake_success_refusal_timeout_invalid_and_late` — all scripted outcomes are surfaced.
- `test_fake_invented_ids_and_coverage_upgrades_are_rejected` — bad semantic results fail closed.
- `test_fake_coordinator_does_not_require_network` — no egress is needed for scripted runs.

## Behavior

The test injects a scripted fake provider and asserts:

- success, refusal, timeout, invalid, and late responses are all distinguishable;
- invented IDs, out-of-case text, and coverage upgrades are rejected by post-validation;
- the coordinator does not depend on DNS, HTTP, or external credentials.

## Errors and edge cases

- A fake outcome that bypasses post-validation fails.
- A network call in the fake path fails.

## Invariants

1. Fake provider behavior is scripted.
2. Semantic post-validation still applies.
3. The test remains offline.

## Tests

- `tests/integration/providers/test_fake_provider_coordinator.py`

## Open questions

None.
