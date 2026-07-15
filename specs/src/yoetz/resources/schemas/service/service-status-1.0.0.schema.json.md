# src/yoetz/resources/schemas/service/service-status-1.0.0.schema.json — installed service-status schema copy

**Wave:** C/F | **ADRs:** ADR-004, ADR-007, ADR-008 | **Imports (spec-tree):** root service-status
schema, resource manifest | **Imported by:** installed status/hello surfaces and packaging tests

## Purpose

Own the installed byte-identical copy of the reviewed safe service-status schema.

## Public surface

Logical resource `schemas/service/service-status-1.0.0.schema.json`; installed path is the heading
path; `$id` and media type equal the root artifact.

## Behavior

Build copies root bytes unchanged, manifests exact path/size/SHA-256, and resolves it offline for
locked/ready status even when decrypted application state is unavailable.

## Errors and edge cases

Missing, extra, symlinked, noncanonical, mismatched or divergent bytes fail safe status readiness and
packaging; no free-text/permissive fallback is allowed.

## Invariants

Installed bytes equal root bytes; resolution is offline/manifest-closed; root schema owns semantics.

## Tests

`tests/unit/protocol/test_service_control_schemas.py` and resource byte-parity tests.

## Open questions

None.
