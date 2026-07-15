# tests/conformance/protocol/test_frozen_schemas.py — frozen schema parity and registry completeness

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/protocol/models.md`, `src/yoetz/protocol/schemas.md`,
`specs/schemas/README.md`
**Imported by:** conformance protocol tests

## Purpose

Prove the released JSON Schema artifacts, schema registry, and public models are in exact parity.

## Public surface

- `test_schema_registry_is_complete` — every released schema is listed.
- `test_model_and_schema_parity` — each model matches its frozen schema artifact.
- `test_schema_uris_and_versions_are_stable` — URIs and semver values are frozen.

## Behavior

The test asserts:

- the schema catalog is complete and offline-resolvable;
- the frozen release artifacts match the owning models;
- unknown-field and fallback branches are present where required;
- no runtime-generated schema widens the released contract.
- the egress-receipt schema rejects nonterminal `awaiting_human|approved|dispatched`, freezes
  `audit_store_version=1` and the exact two-field commitment object, and requires/forbids
  `request_body_bytes` with `dispatch_id`; it also requires one compatible reason for every
  non-`completed` outcome, forbids a reason on `completed`, and rejects every cross-pair for both
  egress and local-disclosure receipt shapes.

## Errors and edge cases

- A missing registry entry fails.
- A schema that widens the model fails.

## Invariants

1. Frozen schemas are release artifacts.
2. Registry completeness is required.
3. Model and schema versions stay aligned.

## Tests

- `tests/conformance/protocol/test_frozen_schemas.py`

## Open questions

None.
