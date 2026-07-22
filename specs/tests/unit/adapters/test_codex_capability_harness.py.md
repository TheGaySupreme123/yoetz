# tests/unit/adapters/test_codex_capability_harness.py — exact Codex artifact identity

**Wave:** D/F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/src/yoetz/adapters/integrations/codex_capability_harness.py.md` | **Imported by:** none

## Purpose

Lock the pure identity-capture function and fail-closed availability evaluation for the Gate-2
Codex conduit harness skeleton.

## Public surface

Temp fake binaries, prerelease version retention, digest equality, and empty-discovery unavailable.

## Behavior

Write a temporary executable payload, call `capture_codex_artifact_identity` with a full prerelease
version string, and assert path/version/digest. Monkeypatch discovery to empty and assert
`codex_artifact_unavailable`.

## Errors and edge cases

Missing files raise the fixed `executable_not_regular_file` reason.

## Invariants

1. Digests bind to file bytes, not version strings alone.
2. Empty discovery cannot pass.

## Tests

This file is the unit suite.

## Open questions

None.
