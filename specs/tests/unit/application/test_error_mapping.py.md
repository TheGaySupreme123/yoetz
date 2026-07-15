# tests/unit/application/test_error_mapping.py — public error mapping and fallback rules

**Wave:** C/D | **ADRs:** ADR-003, ADR-004, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/service.md`, `src/yoetz/protocol/errors.md`,
`src/yoetz/cli/exits.md`
**Imported by:** the application unit suite

## Purpose

Lock the mapping from internal failures to public error codes and CLI-style exit behavior.

## Public surface

- `test_known_failure_families_map_to_expected_public_codes` — validation, frontier, storage,
  provider, and cancellation classes map correctly.
- `test_retryability_tracks_operation_state` — retryable means the operation may continue unchanged.
- `test_safe_details_stay_bounded` — no raw payloads, SQL, or secrets leak into the mapping.
- `test_last_resort_fallback_is_constructible` — the prebuilt fallback error path does not call
  helpers.

## Behavior

The suite proves:

- the application facade never invents a new public error code;
- retryability follows operation state, not process crash noise;
- mapping results stay bounded and safe for CLI/MCP output;
- the last-resort failure envelope can be built without the normal helper path.

## Errors and edge cases

- A mapping that depends on traceback text fails.
- A fallback that requires helper logic fails.

## Invariants

1. Public error mapping is stable.
2. Retryability is about operation state.
3. Fallbacks stay bounded.

## Tests

- `tests/unit/application/test_error_mapping.py`

## Open questions

None.
