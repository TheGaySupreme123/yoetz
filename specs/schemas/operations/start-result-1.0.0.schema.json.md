# schemas/operations/start-result-1.0.0.schema.json — start result schema

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004 | **Imports (spec-tree):**
`src/yoetz/application/start.md`, `src/yoetz/protocol/models.md`,
`src/yoetz/protocol/errors.md`
**Imported by:** CLI, MCP, and packaging/parity tests

## Purpose

Describe the public result shape for the `start` operation.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/operations/start-result-1.0.0.schema.json`.
- Owning model: `StartResultModel`.

## Behavior

Union of:

- success branch containing the accepted route/session/task/writer identity, start frontier, and
  structural compact payload of refs/counts/enums/closed gap codes;
- failure branch containing the common public-error schema.

The success branch must keep the allocated IDs stable across retry and must not expose private
bundle paths, raw task text, descriptions, summaries, excerpts, warnings prose, or any other user
content. The fallback error branch must remain admissible to keep startup and
retry safe.

All IDs, frontiers, version/profile enums, booleans, and digests are structural. Any echoed task,
workspace, external-reference, label, or other user-origin text is content-bearing and admits only
its exact original type or the common omission marker. The success branch requires the common
`agent_context` privacy projection and durable local-disclosure receipt.

## Errors and edge cases

- A schema that rejects the fallback error object fails release.
- A result that smuggles raw path or secret data fails.

## Invariants

1. Retry identity is preserved.
2. Fallback error shape is admitted.
3. Result shape remains bounded.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/operations/test_start_contract.py`

## Open questions

None.
