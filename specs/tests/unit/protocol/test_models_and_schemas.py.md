# tests/unit/protocol/test_models_and_schemas.py — boundary model and schema parity

**Wave:** A/B | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/protocol/models.py`, `src/yoetz_core/protocol/schemas.py`,
`specs/schemas/README.md`
**Imported by:** the protocol unit suite

## Purpose

Prove that the public Pydantic models and the frozen JSON Schema artifacts describe the same
contract for requests, results, common wrappers, and event branches.

## Public surface

- `test_operation_models_match_frozen_schemas` — each request/result model matches its released
  schema artifact.
- `test_unknown_field_rejection_is_strict` — extra keys fail in every closed model.
- `test_known_vs_opaque_event_branching` — the event draft schema keeps the known and unknown
  branches disjoint.
- `test_shared_protocol_constants_match_frozen_values` — protocol/schema versions, byte/count caps,
  finding limits, and the genesis predecessor match the central registry exactly.
- `test_actor_and_client_models_validate_shape_without_granting_assurance` — caller assertions stay
  bounded, strict, and non-authoritative.
- `test_schema_catalog_reports_complete_registry` — the schema catalog exposes the reviewed set.
- `test_schema_uri_and_path_resolution_are_stable` — schema lookup is deterministic and offline.
- `test_check_semantic_status_reason_and_provenance_matrix` — check-result/check-recorded models
  and schemas admit every registered pair, reject cross-pairs, and enforce provenance stage.

## Behavior

The suite checks:

- every operation model has a matching released schema;
- result fallbacks remain admissible across the expected public-error branch;
- known event families and opaque unknown events are distinguishable and both valid under their own
  contract;
- every shared constant has one exact value and is imported rather than duplicated by operation
  models or surfaces;
- actor/client models accept only the frozen enum/ID/bounded-string shapes, reject extras and
  coercion, and never convert caller labels into server-assigned authentication or assurance;
- schema catalog paths and URIs remain frozen and offline-resolvable.
- `CheckResultModel` and `CheckRecordedPayload` require the same semantic status/reason pair;
  predispatch cases forbid provenance and attempted cases admit only receipt-finalized provenance.

## Errors and edge cases

- A model/schema mismatch blocks release.
- A changed constant, duplicated shadow constant, or caller assertion that upgrades assurance fails.
- A schema lookup that depends on network resolution fails the test.
- Missing/unknown/cross-paired semantic reason, provisional provenance, or provenance before a
  durable receipt fails both model and generated-schema validation.

## Invariants

1. Model and schema artifacts must agree on shape and version.
2. The frozen schema catalog is complete for the released set.
3. Runtime resolution stays offline.
4. Shared constants have one frozen source of truth.
5. Actor/client boundary validation never becomes an authentication claim.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`

## Open questions

None.
