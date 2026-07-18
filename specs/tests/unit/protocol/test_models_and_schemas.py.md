# tests/unit/protocol/test_models_and_schemas.py — boundary model and schema parity

**Wave:** A/B | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/protocol/models.py`, `src/yoetz/protocol/schemas.py`,
`specs/schemas/README.md`
**Imported by:** the protocol unit suite

## Purpose

Prove that the public Pydantic models and the frozen JSON Schema artifacts describe the same
contract for requests, results, common wrappers, and event branches.

### Implementation ordering

The B0 implementation materializes only assertions whose direct owners are `protocol.models` and
`protocol.schemas`. Identity/parity assertions that require B1 `domain.events`, `domain.privacy`, or
their event-payload models remain in this file as the later cross-layer gate but are collected only
after those B1 owners exist. B0 neither imports a later-wave domain module nor stubs its types.

## Public surface

- `test_operation_models_match_frozen_schemas` — each request/result model matches its released
  schema artifact.
- `test_operation_model_field_inventory_is_exact` — every top-level and named data-object `$defs`
  model has exactly the frozen properties, required set, scalar types, and array bounds; every
  remaining `$defs` key is exhaustively classified as a constrained alias, omission alias, or outer
  predicate.
- `test_application_aliases_are_identity_aliases` — the twelve un-suffixed application types are
  the exact request/result model objects, not parallel DTOs.
- `test_result_models_are_discriminated_object_roots` — all six result models are `RootModel`s
  over the closed `ok`-discriminated success/common-failure union and emit no `root` wire key.
- `test_operation_wire_round_trip_preserves_presence` — valid objects survive parse/dump/parse,
  including absent versus explicitly-null fields.
- `test_public_model_to_wire_is_the_validated_boundary` — the exact twelve model types select their
  frozen schemas, preserve presence, validate locally, and return a fresh ordinary dictionary.
- `test_operation_cross_field_matrix` — start, respond, status, check, projected-content, and
  receipt conditional branches agree with the frozen `if`/`then` constraints.
- `test_unknown_field_rejection_is_strict` — extra keys fail in every closed model.
- `test_known_vs_opaque_event_branching` — the event draft schema keeps the known and unknown
  branches disjoint.
- `test_shared_protocol_constants_match_frozen_values` — protocol/schema versions, byte/count caps,
  finding limits, and the genesis predecessor match the central registry exactly.
- `test_result_field_classification_is_closed` — every current result leaf classifies exactly once;
  content categories, discriminants, pointer rejection, and the four projection bounds are exact.
- `test_actor_and_client_models_validate_shape_without_granting_assurance` — caller assertions stay
  bounded, strict, and non-authoritative.
- `test_schema_catalog_reports_complete_registry` — the schema catalog exposes the reviewed set.
- `test_schema_uri_and_path_resolution_are_stable` — schema lookup is deterministic and offline.
- `test_schema_catalog_record_shape_and_indexes_are_exact` — frozen field order, immutable indexes,
  deeply frozen schema trees, manifest identity/digest, and shared document identity match the B0
  contract.
- `test_schema_name_derivation_and_version_maps_are_exact` — hyphenated artifact names produce the
  31 request/result keys and the 16 event-payload lower-snake keys only.
- `test_schema_manifest_failure_reason_matrix` — every manifest/catalog failure emits its exact
  central reason without depending on import order.
- `test_schema_instance_validation_is_closed_and_bounded` — valid instances pass; structural,
  conditional, and checked-format failures use one bounded reason with zero retrieval attempts.
- `test_check_semantic_status_reason_and_provenance_matrix` — the B0 check-result model and schema
  admit every registered pair, reject cross-pairs, enforce provenance stage, and require any
  nested provenance status/reason to equal the selected top-level pair. The table explicitly covers
  every `unavailable` reason's required/forbidden branch plus optional pre/post-dispatch
  `failed/coordinator_failure`, and malformed non-object/missing identity input fails boundedly.
- `test_frontier_model_enforces_genesis_cross_field_identity` — direct model validation rejects
  sequence zero with a digest and a positive sequence with `genesis` before serialization.
- `test_finding_policy_identity_partition_is_exhaustive` — semantic-review never appears as a
  policy token; final finding identity is derived from the chosen kind's owning built-in pack.

## Behavior

The suite checks:

- every operation model has a matching released schema and exact `model_fields`/requiredness;
- all ordinary object branch models are frozen/strict with `extra="forbid"`; result roots use the
  Pydantic-supported frozen/strict root configuration, their already-closed branches reject extras,
  an exact JSON `bool` `ok` selects one branch, and serialization never adds a `root` key;
- `StartRequest is StartRequestModel` / `StartResult is StartResultModel` and the equivalent five
  pairs hold by object identity;
- request optional-non-null fields use the registered before-validator field sets: omission remains
  omitted on dump, explicit null is rejected, required-nullable fields remain present, and
  `mode="json", by_alias=True, exclude_unset=True, exclude_none=False` round-trips the same JSON
  value before canonical byte serialization;
- `public_model_to_wire` accepts only the exact twelve public request/result model types, selects
  each immutable schema-name/version mapping without class-name parsing, uses that exact dump
  profile, validates through `validate_schema_instance`, and returns a fresh ordinary dictionary;
  a support model, branch model, subclass, or arbitrary object raises exactly
  `TypeError("public_model_wrong_type")`, and direct `model_dump` is not treated as a public boundary;
- named data-object support models match the exact `$defs` property and required sets, including all
  eight status page models and their outer `view` relation, plus the receipt policy/schema version
  entry records and exact 11-field receipt version slice; every other named definition is covered
  once by the exact alias/omission/predicate classification and no open predicate is accidentally
  materialized as an `extra="forbid"` DTO;
- result fallbacks remain admissible across the expected public-error branch;
- known event families and opaque unknown events are distinguishable and both valid under their own
  contract;
- every shared constant has one exact value and is imported rather than duplicated by operation
  models or surfaces;
- `classify_result_leaf` keeps its private registry immutable, classifies every leaf of every
  current schema-valid result exactly once, uses the enclosing status view and publish-work event
  schema discriminants, gives exact-pointer rules precedence over one-index patterns, and rejects
  malformed, missing, overlapping, or unmatched pointers with `invalid_json_pointer`; candidate
  finding summary/detail and receipt gap/section prose are content-bearing `finding_summary`, the
  private container shape is not API, and the four public projection bounds equal the owning model
  contract; receipt JSON classification never implies that the digest-bound document admits
  field-level omission markers;
- actor/client models accept only the frozen enum/ID/bounded-string shapes, reject extras and
  coercion, keep caller assertion IDs distinct from durable `agt_` IDs, and never convert caller
  labels into server-assigned authentication or assurance;
- B0 asserts that `ActorType`, `DataCategory`, `ClientKind`, and `IntegrationKind` are owned by
  `protocol.models` and that no B0 protocol module imports `yoetz.domain`; later privacy/event
  conversion or re-export identity is tested when the B1 owners materialize, not by the B0 test DAG;
- `SchemaDocument` and `SchemaCatalog` expose exactly the fields and immutable map semantics frozen
  in `protocol/schemas.md`; all 52 members are ASCII path-sorted and the manifest digest hashes the
  exact packaged bytes;
- recursively walking `json_schema` reaches no `dict`, `list`, or `set`; root, nested
  `properties`, nested `$defs`, and array mutation attempts fail and cannot change canonical bytes,
  digest, or a later catalog load;
- `action-recorded-1.0.0.schema.json` derives `schema_name="action-recorded"`; slash, underscore,
  traversal, percent-escaped, non-ASCII, and noncanonical SemVer lookups fail before resource I/O;
- request/result versions contain exactly the 31 `SchemaKind.REQUEST_RESULT` hyphenated names;
  event versions contain exactly the 16 `event-payload` names converted to lower snake case and
  exclude accepted/draft/opaque envelope schemas;
- catalog paths, HTTPS identifiers, absolute refs, and fragments resolve exclusively from packaged
  `yoetz/resources/schemas` bytes rooted through `importlib.resources.files("yoetz")`, with
  sockets/DNS disabled; changing the current directory or removing the source tree cannot change
  resolution;
- an uncached isolated-process pass statically checks all 1,276 current `$ref` occurrences, while
  mutations to external HTTPS, HTTP, `file`, relative, same-namespace-missing, query-bearing, and
  bad-fragment references all raise `schema_reference_unresolved` and a network sentinel records
  zero socket/URL calls;
- the packaged-resource census walks every regular descendant and equals exactly `manifest.json`
  plus the 52 declared members, so an extra `.txt`, extensionless, or other non-schema resource
  raises `schema_manifest_member_mismatch`; every member also passes the pinned
  `Draft202012Validator.check_schema` gate, and an otherwise canonical invalid schema raises
  `schema_bytes_invalid` without metaschema retrieval;
- `validate_schema_instance` accepts a valid canonical instance for its selected schema and rejects
  missing required fields, extra fields, wrong types, failed conditionals, and invalid `date-time`
  values with only `schema_instance_invalid`; a socket/URL sentinel records zero calls and the
  exception does not echo the value, path, validator message, or URI;
- an installed-wheel subprocess started from an unrelated directory with the source tree absent and
  networking denied loads all 52 members through the regular `yoetz` package anchor;
- B0 `CheckResultModel` requires the exact semantic status/reason pair and policy-execution
  outcome/reason shape; predispatch cases forbid provenance and attempted cases admit only
  receipt-finalized provenance whose nested status/reason equal the selected final pair. Earlier
  non-selected attempt outcomes never enter that field. Parity with B1 `CheckRecordedPayload`,
  including normalized scope and policy/execution identity/order, is deferred to the B1 domain
  event-payload test.
- The finding policy partition is fixed, disjoint, and derived from kind ownership rather than
  reviewer output.

## Errors and edge cases

- A model/schema mismatch blocks release.
- A changed constant, duplicated shadow constant, or caller assertion that upgrades assurance fails.
- A schema lookup that depends on network resolution fails the test.
- Fixtures mutate one manifest/catalog condition at a time and assert the exact reasons:
  `schema_manifest_missing`, `schema_manifest_invalid`, `schema_manifest_duplicate_path`,
  `schema_manifest_member_mismatch`, `schema_path_unsafe`, `schema_name_invalid`,
  `schema_not_found`, `schema_bytes_invalid`, `schema_digest_mismatch`, `schema_id_mismatch`,
  `schema_draft_unsupported`, `schema_version_mismatch`, `schema_kind_mismatch`,
  `schema_artifact_role_invalid`, `schema_artifact_role_mismatch`,
  `schema_reference_unresolved`, `schema_catalog_incomplete`, and `schema_duplicate_identity`.
- Invalid selected instances, including model/schema drift at the public dump boundary, raise only
  `schema_instance_invalid`; canonical-value failures retain their canonical reason and artifact or
  metaschema failures remain `schema_bytes_invalid`.
- Missing/unknown/cross-paired semantic reason, provisional provenance, or provenance before a
  durable receipt fails both model and generated-schema validation.
- Missing scope/executions, empty current policy selection, a policy/execution identity or order
  mismatch, or an illegal execution outcome/reason pair fails the event model/schema parity gate.
- A string/integer `ok` (including integer `0`), result wrapper containing a `root` property,
  explicit null in an optional-non-null field, mismatched status view/page,
  partial start attachment pair, invalid response disposition fields, or mismatched receipt
  format/body fails both model and frozen-schema validation.

## Invariants

1. Model and schema artifacts must agree on shape and version.
2. The frozen schema catalog is complete for the released set.
3. Runtime resolution stays offline.
4. Shared constants have one frozen source of truth.
5. Actor/client boundary validation never becomes an authentication claim.
6. Application aliases and result root semantics cannot drift from the public wire models.
7. Parse/dump preserves JSON value and field presence; canonical bytes are produced separately.
8. Public model serialization and direct schema-instance validation use the same local-only catalog
   and bounded failure mapping.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`

## Open questions

None.
