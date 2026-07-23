# tests/capability/test_observation_dogfood_matrix.py — offline observation dogfood matrix

**Wave:** D/F | **ADRs:** ADR-010 | **Imports (spec-tree):** capability evidence helpers,
session-stream and importer profiles | **Imported by:** none

## Purpose

Record offline dogfood evidence that generic structural observation works for a synthetic unknown
future Codex version and for registered older/current fixture profiles, without requiring a live
`codex-testing` binary.

## Public surface

Pytest module with optional `@pytest.mark.live` skip path.

## Behavior

Prove generic structural parsing of an unfamiliar future host record (validated stable facts plus
an unsupported-event coverage gap, without inventing success or injecting a pre-built unsupported
envelope into an old profile). Also walk registered `SUPPORTED_CODEX_PROFILES` through structural
ingest. Live cells skip unless `YOETZ_LIVE_CODEX=1`.

## Errors and edge cases

Missing live authorization skips; never silently passes live cells.

## Invariants

Evidence packets stay path/secret free; MCP tool count remains six.

## Tests

This file is the test.

## Open questions

E-013 live installed-artifact cells remain a later empirical gate.
