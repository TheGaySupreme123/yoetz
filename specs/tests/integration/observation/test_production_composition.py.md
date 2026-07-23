# tests/integration/observation/test_production_composition.py — non-live DoD composition

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):** composition harness,
observation advice, approved checks, session stream | **Imported by:** none

## Purpose

Enforce the required non-live Definition of Done for Codex observation/advice: with project
consent and zero cooperative `publish_work` calls, simulated hooks and a selective session stream
produce durable task-bundle observations that survive restart and yield evidence-linked status
advice. Also covers the additional required cases (setup layers, outbox, dedup, lifecycle,
sandbox/checks, semantic minimization, plaintext absence).

## Public surface

Pytest module. Primary case:
`test_dod_zero_coop_durable_observation_advice_composition`. Production wiring probe:
`test_production_ready_composition_must_not_use_memory_store`.

## Behavior

Runs the scripted DoD flow through the contract composition harness (preferring production APIs
when landed). Additional cases assert setup/consent, rejected gaps, pre-mapping drain, distinct
identical calls, hook/stream dedup, stream repair, unknown future shapes, compaction/resume,
outbox overflow, lifecycle transitions, approved checks, stale verification, deterministic and
semantic advice, and secret absence.

## Errors and edge cases

Production gaps fail with explicit Agent A/B/C messages (MemoryObservationStore wiring, setup
early-return, CheckSandboxPort / `/bin/true` sandbox). Live Codex proof is out of scope.

## Invariants

No seventh MCP tool; no plaintext secrets/paths/transcripts in status, hooks, advice, or semantic
packets; outbox never silently drops coverage.

## Tests

This file is the test.

## Open questions

None.
