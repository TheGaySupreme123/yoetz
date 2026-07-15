# src/yoetz/resources/schemas/service/control-hello-result-1.0.0.schema.json — installed hello-result schema copy

**Wave:** C/F | **ADRs:** ADR-007, ADR-008 | **Imports (spec-tree):** root hello-result schema,
resource manifest | **Imported by:** installed control clients/server and packaging tests

## Purpose

Own the installed byte-identical copy of the reviewed control-hello-result schema.

## Public surface

Logical resource `schemas/service/control-hello-result-1.0.0.schema.json`; installed path is the
heading path; `$id` and media type equal the root artifact.

## Behavior

Build copies root bytes unchanged, manifests exact path/size/SHA-256, and resolves its service-status
reference only through the verified installed registry.

## Errors and edge cases

Missing, extra, symlinked, noncanonical, mismatched/divergent bytes or unresolved local `$ref` fail
startup and packaging; no fallback is allowed.

## Invariants

Installed bytes equal root bytes; resolution is offline/manifest-closed; root schema owns semantics.

## Tests

`tests/unit/protocol/test_service_control_schemas.py` and resource byte-parity tests.

## Open questions

None.
