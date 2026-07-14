# schemas/common/frontier-1.0.0.schema.json — frontier identity schema

**Wave:** A/B | **ADRs:** ADR-002, ADR-003, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/protocol/models.md`, `src/yoetz_core/domain/events.md`
**Imported by:** operation, event, and result schemas

## Purpose

Describe the canonical frontier boundary used across request, event, and result schemas.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/common/frontier/1.0.0`.
- Owning model: `Frontier`.

## Behavior

Closed object with required fields:

- `sequence` — canonical decimal string, `0` or non-zero without leading zeros.
- `head_digest` — `sha256:` digest string or the reserved genesis digest when sequence is zero.

The frontier identifies the accepted ledger position. Sequence zero is the genesis frontier. The
schema does not accept alternative numeric spellings, floats, or extra properties.

## Errors and edge cases

- Leading zeros, negative values, and noncanonical digests fail.
- Extra keys fail.

## Invariants

1. Frontier is a closed identity object.
2. Sequence and head digest travel together.
3. Canonical decimal spelling is required.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/protocol/test_idempotency_and_frontiers.py`

## Open questions

None.
