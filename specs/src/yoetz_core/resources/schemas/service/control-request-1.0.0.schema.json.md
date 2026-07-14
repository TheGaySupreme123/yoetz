# src/yoetz_core/resources/schemas/service/control-request-1.0.0.schema.json — installed control-request schema copy

**Wave:** C/F | **ADRs:** ADR-007, ADR-008 | **Imports (spec-tree):** root control-request schema,
resource manifest | **Imported by:** installed control server/clients and packaging tests

## Purpose

Own the installed byte-identical copy of the reviewed control request envelope schema.

## Public surface

Logical resource `schemas/service/control-request-1.0.0.schema.json`; installed path is the heading
path; `$id` and media type equal the root artifact.

## Behavior

Build copies root bytes unchanged, manifests exact path/size/SHA-256, and validates the root
artifact's complete twenty-three-call-plus-cancel union offline. The six workflow body `$ref`s
resolve through the installed registry; every support body definition is already closed inside the
root artifact.

## Errors and edge cases

Missing, extra, symlinked, noncanonical, mismatched/divergent bytes, unresolved workflow `$ref`, or
an incomplete method branch fails before dispatch; no external/open-dict registry fallback exists.

## Invariants

Installed bytes equal root bytes; resolution is offline/manifest-closed; root schema owns semantics.

## Tests

`tests/unit/protocol/test_service_control_schemas.py` and resource byte-parity tests.

## Open questions

None.
