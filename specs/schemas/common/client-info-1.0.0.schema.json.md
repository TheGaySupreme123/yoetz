# schemas/common/client-info-1.0.0.schema.json — client metadata boundary

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/protocol/models.md`, `specs/schemas/README.md`
**Imported by:** request schemas, MCP/CLI envelopes, and validation fixtures

## Purpose

Describe the bounded client metadata attached to public requests for tracing and compatibility.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/common/client-info/1.0.0`.
- Owning model: `ClientInfoModel`.

## Behavior

Closed object with required fields:

- `kind` — `codex_cli`, `cooperative_agent`, `yoetz_cli`, `test_client`, or `importer`.
- `version` — bounded version string.
- `integration` — `cooperative_mcp`, `local_cli`, or `codex_jsonl_import`.

`cooperative_agent` is the transport-neutral identity for any agent harness without a first-party
Yoetz integration. It is the honest value for an arbitrary MCP host and is valid with either
`cooperative_mcp` or `local_cli`. Because the enum is closed, a harness Yoetz has not profiled has
exactly one correct value to send and never has to misreport itself as `codex_cli` or
`test_client`. Recognising a new harness first-party is an additive `kind` change; it is not
required for that harness to work.

The schema is for provenance and wording only. It does not authorize operations or imply trust.
Coverage and authorship assurance derive from `integration` and server-side observation, never from
`kind`, so no `kind` value can strengthen a claim. Extra properties are forbidden.

## Errors and edge cases

- Unknown kind/integration values fail.
- Version values that exceed the bounded string contract fail.
- `kind: codex_cli` is a capability claim bound to an installed Codex profile (ADR-005), not a
  synonym for "some agent"; an unprofiled harness sending it is a provenance error, not a shortcut.
- `kind: test_client` is reserved for Yoetz's own test surfaces and is never the correct value for
  a real third-party host.

## Invariants

1. Client metadata is descriptive, not authoritative.
2. Closed shape and explicit enums are required.
3. Extra keys are forbidden.
4. Every supported client, profiled or not, has exactly one honest `kind`.
5. No `kind` value participates in coverage or assurance.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/protocol/test_frozen_schemas.py`

## Open questions

None.
