# tests/capability/test_codex_conduit_harness.py — Gate 2 fail-closed Codex conduit skeleton

**Wave:** D/F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** capability evidence,
`codex_capability_harness` | **Imported by:** planned Gate-2 policy cells

## Purpose

Prove the Gate-2 harness fails closed with `codex_artifact_unavailable` when no exact Codex
artifact is discoverable, and never silently passes. App-server protocol driving is out of scope.

## Public surface

One capability case `CODEX-G2-CONDUIT` / `codex_conduit_app_server`. Listed under policy
`planned_cases` only — never under `required_cases` until a real Codex artifact and driver exist.

## Behavior

Call `evaluate_codex_conduit_availability()`. On unavailable, record unsupported evidence with
reason `codex_artifact_unavailable` and `pytest.skip` with that structural reason. If an artifact
is present, record unsupported with `codex_conduit_driver_not_implemented` and skip — still no
support claim.

## Errors and edge cases

Silent pass is forbidden in every branch.

## Invariants

1. Gate 2 is fail-closed without an exact artifact.
2. No supported Codex profile is claimed.

## Tests

This file is the Gate-2 skeleton suite.

## Open questions

None.
