# src/yoetz/cli/observe_hooks.py — unified Codex hook observation ingress

**Wave:** D | **ADRs:** ADR-010, ADR-009 | **Imports (spec-tree):** `cli/hooks.py.md`,
`observation_local.py.md`, `codex_lifecycle.py.md` | **Imported by:** `cli/app.md`, compatibility
wrappers in `cli/hooks.py.md`

## Purpose

Single bounded observation ingress for Codex lifecycle events. Maps stdin JSON to structural
`ObservationEnvelope` values, never retains transcript prose, excludes Yoetz self-tools from advice
loops, fails soft (exit 0) on service/vault outage, pairs pre/post via correlation id, and
auto-attaches on consented SessionStart when feasible.

## Public surface

- `handle_observe`, `map_hook_payload_to_envelope`
- `SUPPORTED_HOOK_EVENTS`, `ADVICE_SAFE_EVENTS`

## Behavior

Always exit 0. On outage emit structural gap codes (`service_unavailable` / `vault_locked`) without
plaintext spool. After local ingest, refresh deterministic `AdviceSnapshot` from retained envelopes
(works with zero cooperative MCP publications). Advice `additionalContext` only at safe events when
a new `AdviceSnapshot` suppression identity is available.

## Errors and edge cases

Fail closed on consent, mapping, and validation errors; never leak secrets.

## Invariants

1. No plaintext transcript spool.
2. No seventh MCP tool.
3. Coverage-qualified advice only.

## Tests

`tests/unit/cli/test_observe_hooks.py`.

## Open questions

None.
