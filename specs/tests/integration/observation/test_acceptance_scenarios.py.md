# tests/integration/observation/test_acceptance_scenarios.py — decisive observation/advice acceptance

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):** observation ports/adapters,
advice policies, observe hooks | **Imported by:** none

## Purpose

Prove the decisive acceptance criterion: even with zero cooperative Yoetz MCP publications, Yoetz
still knows material actions, identifies verification gaps, and gives timely coverage-qualified
advice. Also proves observation control handlers call a durable `ObservationPort`.

## Public surface

Pytest module covering the thirteen required scenarios plus support-handler wiring.

## Behavior

Hermetic fakes exercise SessionStart auto-binding, zero-coop advice, vault outage/recovery without
plaintext spool, structural lifecycle/unpaired gaps, approved-check digest staleness,
deterministic-vs-semantic advice, failed first publication with continued tracking, edits after
green checks, completion without verification, subagent defect advice, stream reconciliation,
unknown-future gaps, and secret absence across status/advice/semantic surfaces.

## Errors and edge cases

Hooks always exit 0; service gaps record degraded codes without blocking.

## Invariants

No seventh MCP tool; no secret-like command output in asserted surfaces; no live Codex required.

## Tests

This file is the test.

## Open questions

None.
