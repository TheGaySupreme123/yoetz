# src/yoetz/adapters/integrations/observation_local.py — durable local observation state

**Wave:** D | **ADRs:** ADR-010, ADR-009 | **Imports (spec-tree):** `domain/observation.py.md`,
`config/paths.md` | **Imported by:** `cli/observe.py.md`, `cli/observe_hooks.py.md`,
`cli/setup.py.md`

## Purpose

Owner-private durable store under `state_dir()/observation/` for observation consent, session
bindings, generation-fenced cursors, structural envelope retention, pre/post pairing, and advice
suppression. Used by hooks and `yoetz observe` when service observation handlers are unavailable.

## Public surface

- `LocalObservationStore`, `LocalObservationConsent`
- `HOOK_MAPPING_VERSION`, `STREAM_MAPPING_VERSION`, `YOETZ_TOOL_NAMES`
- `workspace_commitment_for_path`, `session_commitment_from_codex_id`, `observation_dir`

## Behavior

Consent is recorded as a workspace commitment only (never a raw path). Ingest fails closed without
active consent. Pause/resume/revoke match `ObservationPort` semantics. Evidence is retained on
revoke. No transcript prose is stored. `refresh_advice` rebuilds `AdviceSnapshot` from retained
envelopes via deterministic observation-advice policies (optional semantic add-on). 
`peek_advice_for_delivery` suppresses duplicates by suppression identity.

## Errors and edge cases

Fail closed on consent, mapping, and validation errors; never leak secrets.

## Invariants

1. Owner-only directory and files (`0700` / `0600`).
2. Path-free commitments in all persisted state and logs.
3. Bounded envelope/dedup retention.

## Tests

`tests/unit/cli/test_observe_cli.py`, `tests/unit/cli/test_observe_hooks.py`.

## Open questions

None.
