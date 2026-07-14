# schemas/common/client-info-1.0.0.schema.json — client metadata boundary

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/protocol/models.md`, `specs/schemas/README.md`
**Imported by:** request schemas, MCP/CLI envelopes, and validation fixtures

## Purpose

Describe the bounded client metadata attached to public requests for tracing and compatibility.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/common/client-info/1.0.0`.
- Owning model: `ClientInfoModel`.

## Behavior

Closed object with required fields:

- `kind` — `codex_cli`, `yoetz_cli`, `test_client`, or `importer`.
- `version` — bounded version string.
- `integration` — `cooperative_mcp`, `local_cli`, or `codex_jsonl_import`.

The schema is for provenance and wording only. It does not authorize operations or imply trust.
Extra properties are forbidden.

## Errors and edge cases

- Unknown kind/integration values fail.
- Version values that exceed the bounded string contract fail.

## Invariants

1. Client metadata is descriptive, not authoritative.
2. Closed shape and explicit enums are required.
3. Extra keys are forbidden.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/protocol/test_frozen_schemas.py`

## Open questions

None.
