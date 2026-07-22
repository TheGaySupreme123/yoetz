# src/yoetz/application/observation_control.py — observation_* support handlers

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):** `ports/observation.py.md`,
`ports/control.md`, `domain/observation.py.md` | **Imported by:** ready composition and
observation acceptance tests

## Purpose

Bind the five ordinary-control methods `observation_ingest|status|pause|resume|revoke` to one
`ObservationPort` so they are not empty stubs. These methods remain CLI/UI-only; they are never
MCP tools.

## Public surface

- `build_observation_support_handlers(port) -> Mapping[ControlMethod, handler]`

## Behavior

Parse request bodies through domain JSON helpers (normalizing wire lists to tuples). Forward to
the port and return path-free status/ingest result JSON. Map public operation errors to bounded
`ControlError` reasons.

## Errors and edge cases

Invalid bodies raise `invalid_request`. Port consent/session failures surface as non-retryable
control errors.

## Invariants

1. No seventh MCP tool.
2. Handlers never invent transcript prose.
3. Ready composition always installs a non-empty observation handler map.

## Tests

`tests/integration/observation/test_acceptance_scenarios.py` service-wiring case.

## Open questions

None.
