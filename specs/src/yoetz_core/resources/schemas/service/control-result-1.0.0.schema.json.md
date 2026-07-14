# src/yoetz_core/resources/schemas/service/control-result-1.0.0.schema.json — installed control-result schema copy

**Wave:** C/F | **ADRs:** ADR-007, ADR-008 | **Imports (spec-tree):** root control-result schema,
resource manifest | **Imported by:** installed control clients/server and packaging tests

## Purpose

Own the installed byte-identical copy of the reviewed control result envelope schema.

## Public surface

Logical resource `schemas/service/control-result-1.0.0.schema.json`; installed path is the heading
path; `$id` and media type equal the root artifact.

## Behavior

Build copies root bytes unchanged, manifests exact path/size/SHA-256, and validates the root
artifact's complete per-method success/error union offline. Six workflow result `$ref`s resolve
through the installed registry; every support success body and shared control error body is closed
inside the root artifact. Backup preview/execute therefore require the root schema's separate
privacy-audit object count and structural-sidecar digest fields; the installed copy cannot regress
to a privacy-blind total object count.

## Errors and edge cases

Missing, extra, symlinked, noncanonical, mismatched/divergent bytes, unresolved workflow `$ref`, or
an incomplete/mismatched method body fails before result delivery; no permissive/open-dict fallback
is allowed.

## Invariants

Installed bytes equal root bytes; resolution is offline/manifest-closed; root schema owns semantics.

## Tests

`tests/unit/protocol/test_service_control_schemas.py` and resource byte-parity tests.

## Open questions

None.
