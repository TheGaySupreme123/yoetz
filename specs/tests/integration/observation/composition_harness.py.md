# tests/integration/observation/composition_harness.py — DoD composition test helpers

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):** observation adapters,
lifecycle, setup, sqlite observation | **Imported by:**
`tests/integration/observation/test_production_composition.py`

## Purpose

Provide the in-process contract composition harness for non-live Definition of Done tests:
fake Codex install, unified setup layers, local→SQLite outbox drain, and production API
resolution probes for ObservationCoordinator / CodexSessionStreamLocator / AdviceItem /
CheckSandboxPort.

## Public surface

Python helpers: `FakeCodexInstall`, `run_unified_setup`, `ContractObservationPipeline`,
`resolve_production_surface`, `ready_composition_uses_memory_observation_store`,
`assert_no_plaintext_canaries`.

## Behavior

Prefers production APIs when importable. Otherwise exercises the intended pipeline shape with a
thin interim local outbox that acknowledges only after SqliteObservationStore commit. Documents
Agent A/B/C replacement paths in module docstring.

## Errors and edge cases

Outbox overflow sets an explicit `outbox_overflow` gap and never silently drops coverage.
Setup failure leaves consent inactive.

## Invariants

No plaintext secrets/paths in asserted surfaces; no seventh MCP tool; MemoryObservationStore is
never the durable task-bundle store in the harness.

## Tests

Exercised by `test_production_composition.py`.

## Open questions

None.
