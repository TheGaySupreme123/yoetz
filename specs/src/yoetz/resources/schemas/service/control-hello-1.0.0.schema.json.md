# src/yoetz/resources/schemas/service/control-hello-1.0.0.schema.json — installed control-hello schema copy

**Wave:** C/F | **ADRs:** ADR-007, ADR-008 | **Imports (spec-tree):** root control-hello schema,
resource manifest | **Imported by:** installed control handshake and packaging tests

## Purpose

Own the installed byte-identical copy of the reviewed control-hello schema.

## Public surface

Logical resource `schemas/service/control-hello-1.0.0.schema.json`; installed path is the heading path;
`$id` and media type equal the root artifact.

## Behavior

Build copies root bytes unchanged, manifests exact path/size/SHA-256, and resolves offline before a
local control connection accepts JSON.

## Errors and edge cases

Missing, extra, symlinked, noncanonical, mismatched or divergent bytes fail startup and packaging;
no generated/permissive fallback is allowed.

## Invariants

Installed bytes equal root bytes; resolution is offline/manifest-closed; root schema owns semantics.

## Tests

`tests/unit/protocol/test_service_control_schemas.py` and resource byte-parity tests.

## Open questions

None.
