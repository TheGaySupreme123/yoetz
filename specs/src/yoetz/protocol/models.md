# src/yoetz/protocol/models.py — shared protocol constants, boundary models, and envelopes

**Wave:** A/B | **ADRs:** ADR-002, ADR-005, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
`protocol/errors.md`, `protocol/ids.md`, `protocol/canonical.md`, `protocol/coverage.md`,
`protocol/schemas.md`
**Imported by:** most protocol, domain, application, CLI, and MCP modules

## Purpose

This file is the shared constant-and-boundary layer for the protocol. It keeps request/result
schemas, request/value bounds, timestamp rules, and actor assertions consistent across the entire
implementation. It is also where the six public workflow operations agree on the shared envelope
shape before any operation-specific fields are considered.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `PROTOCOL_VERSION` | `str = "0.1"` |
| `MAX_EVENTS_PER_BATCH` | `int = 100` |
| `MAX_CANONICAL_REQUEST_BYTES` | `int = 1_048_576` |
| `MAX_FINDINGS_DEFAULT` | `int = 3` |
| `MAX_FINDINGS_LIMIT` | `int = 10` |
| `MAX_REASON_BYTES` | `int = 4_096` |
| `MAX_OBJECT_PLAINTEXT_BYTES` | `int = 4_194_304` |
| `MAX_SEMANTIC_ITEM_BYTES` | `int = 16_384` |
| `MAX_SEMANTIC_CASE_BYTES` | `int = 262_144` |
| `MAX_REVIEW_TEXT_BYTES` | `int = 4_096` |
| `MAX_REVIEW_TIMELINE_ITEMS` | `int = 64` |
| `MAX_REVIEW_ASSESSMENTS` | `int = 64` |
| `MAX_REVIEW_CHANGE_OBSERVATIONS` | `int = 32` |
| `MAX_REVIEW_EXCERPTS` | `int = 16` |
| `MAX_REVIEW_OMISSIONS` | `int = 64` |
| `MAX_REVIEW_CHALLENGES` | `int = 3` |
| `GENESIS_PREDECESSOR_DIGEST` | `str = "genesis"` |
| `ActorType` | enum of public actor kinds |
| `PublicationChannel` | enum of observed publication channels |
| `JsonValue` | recursive alias of strict JSON-compatible values |
| `PublicEnvelopeModel` | shared fixed protocol/schema-version base for ordinary object models |
| `PublicRequestModel` | abstract base for operation requests |
| `PublicResultModel` | abstract base for operation results |
| `OmittedContentModel` | exact field-level marker for content blocked by local-disclosure policy |
| `PrivacyProjectionModel` | receipt-bound metadata attached to every ordinary projected result |
| `DataCategory` | shared closed content-category enum used by result projection and later privacy policy |
| `ActorAssertionModel` | caller-asserted actor boundary model |
| `ClientInfoModel` | caller-asserted client boundary model |
| `StartRequestModel` / `StartResultModel` | public models for the `start` operation |
| `PublishWorkRequestModel` / `PublishWorkResultModel` | public models for `publish_work` |
| `CheckRequestModel` / `CheckResultModel` | public models for `check` |
| `RespondRequestModel` / `RespondResultModel` | public models for `respond` |
| `StatusRequestModel` / `StatusResultModel` | public models for `status` |
| `ReceiptRequestModel` / `ReceiptResultModel` | public models for `receipt` |
| `StartRequest` / `StartResult`, etc. | application-facing aliases of the twelve public models; no second dataclass family |
| `public_model_to_wire(model: object) -> dict[str, JsonValue]` | exact twelve-model dump, local schema-validation, and copy boundary |
| `ReceiptFormat` / `ReceiptInclude` / `ReceiptRedactionProfile` | closed receipt boundary enums |
| `SemanticStatus` / `SemanticReason` | the one shared semantic outcome vocabulary |
| `VALID_SEMANTIC_REASONS` | immutable exhaustive status-to-reason relation |
| `validate_semantic_outcome(status, reason)` | reject a status/reason pair outside that relation |
| `validate_semantic_provenance_binding(status, reason, provenance_status, provenance_reason)` | enforce provenance presence and exact final-attempt identity |
| `classify_result_leaf(method, validated_result, pointer)` | classify one validated result leaf as `public_structural` or its exact `DataCategory` |
| `MAX_PROJECTION_CONTENT_LEAVES` | `int = 512` |
| `MAX_PROJECTION_POINTER_BYTES` | `int = 256` |
| `MAX_INTERNAL_PROJECTABLE_RESULT_BYTES` | `int = 524_288` |
| `MAX_PROJECTED_RESULT_BYTES` | `int = 1_048_576` |

## Behavior

`PROTOCOL_VERSION` is the top-level protocol family version. Individual request/result/event
schemas carry their own `schema_version` values, but the protocol family stays stable at `0.1`
until ADR-002 changes it. In v0.1 every public operation request/result schema uses
`schema_version = "1.0.0"`.

The numeric caps in this file are shared across request parsing, validation, and receipt
construction. They are not suggestions; the rest of the implementation must enforce them at the
boundary where the data enters the system. Transport frame caps remain owned by their adapters and
are not mirrored here.

Boundary models import the one `IdKind` enum from `protocol/ids.py`; this file neither defines nor
mirrors a second enum. Its exact wire values are `request`, `installation`, `task`, `session`,
`writer`, `event`, `obligation`, `claim`, `action`, `result`, `evidence`, `finding`, `object`,
`receipt`, `correlation`, `semantic_job`, `semantic_attempt`, `maintenance_pin`,
`service_instance`, `control_rpc`, `privacy_policy`, `privacy_setup_session`, `privacy_proposal`,
`outbound_case`, `egress_authorization`, `egress_dispatch`, `egress_receipt`, and `actor`, as owned
by `protocol/ids.py`.

`ActorType` is owned and defined in this B0 boundary module. It is the boundary-facing actor
classification used in request envelopes and imported observations. It must stay broad enough to
represent humans, harnesses, subagents, imported runs, and Yoetz-engine-authored internal events,
but it must not pretend to establish authentication by itself. The later B1 `domain/values.py`
defines its own pure-domain actor enum and converts an already validated boundary member by exact
wire value; this module never imports a domain module, and the domain module never needs to import
Pydantic through this boundary.

`PublicationChannel` names how an event or observation entered the ledger. The ordering and
specific members are owned by `protocol/coverage.md`; this file only exposes the boundary model
needed by request and event envelopes.

`JsonValue` is the strict recursive JSON alias used by boundary models and canonicalization. It
includes only JSON primitives, arrays, and objects with string keys. It excludes Python-specific
containers, datetimes, decimals, and any non-JSON coercion path.

`PublicEnvelopeModel` is the minimal closed base containing the two fixed protocol/schema version
fields. `PublicRequestModel` extends that base with request identity and actor/client assertions.
`PublicResultModel` is instead the generic root base for one success/failure union; it does not
inherit request-only fields. The exact class architecture and fields are frozen below.

These boundary shapes enforce:

- schema versions are explicit and validated, not inferred;
- IDs are validated against the registry prefix and canonical lowercase UUID form;
- canonical base-10 strings are used for integer-like wire values that cross the protocol;
- no field may accept ambiguous coercions from whitespace, floats, `null` sentinels, or
  mixed-type collections;
- extra keys are rejected where the contract is strict.

The operation-specific request/result models are the six public workflow boundary pairs used by the
application service:

- `StartRequestModel` / `StartResultModel` carry the installation/session/bootstrap request for a
  new or attached workspace;
- `PublishWorkRequestModel` / `PublishWorkResultModel` carry one ordered batch of event drafts and
  the acceptance summary for that batch;
- `CheckRequestModel` / `CheckResultModel` carry the frozen case, returned finding set, required
  closed `semantic_status`/`semantic_reason` pair, and required-nullable receipt-finalized semantic
  provenance; a predispatch success always carries explicit null provenance;
- `RespondRequestModel` / `RespondResultModel` carry one response to one finding;
- `StatusRequestModel` / `StatusResultModel` carry the read-only projection query and page result;
- `ReceiptRequestModel` / `ReceiptResultModel` carry the frozen-frontier receipt query and the
  canonical receipt payload. Their exact closed tokens are `format=json|markdown|text`,
  `include=summary|standard|full`, and
  `redaction_profile=full_local|default_local_export|redacted_share`; no unknown token is
  approximated.

### Exact Pydantic v2 operation-model contract

The committed Draft 2020-12 documents are normative for field spelling, requiredness, scalar
constraints, array bounds, `uniqueItems`, and conditional validation. The Python models are a
typed rendering of those documents, not a looser convenience DTO. Every ordinary nested data
object in the closed support-model inventory below is a closed Pydantic model. In the signatures
below, `Ref(uri#pointer)` means the exact schema-backed value at that packaged schema node; it never
means an unchecked `dict`. A data-object definition named `x_y` in that inventory becomes
`<Operation>XYModel` (for example, `status-result#/$defs/finding_item` becomes
`StatusFindingItemModel`). The fixed naming exceptions are
`success -> <Operation>SuccessModel`, `check_scope -> CheckScopeModel`, and the common definitions
named below. External refs use their owning boundary model or an exact schema-backed root model
when the owner is a domain codec.

Ordinary `BaseModel` object models share
`ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)`. Generic and concrete
`RootModel` classes instead use exactly
`ConfigDict(frozen=True, strict=True, validate_default=True)`: Pydantic 2.13 rejects an `extra`
setting on a root model, while the root's success/failure branch models already reject extra keys.
Wire enum adapters
accept only an actual string containing one registered token; ID, integer-string, timestamp,
digest, commitment, bounded-string, and bounded-array aliases apply the exact constraints at the
referenced schema node without trimming, normalization, or general Python coercion. A field marked
`?` below is omittable but does **not** admit JSON `null` unless its declared wire type also contains
`None`. Internally every such field is annotated `T | None = None` so omission is compatible with
`validate_default=True`. Each owning model declares its exact
`ClassVar[frozenset[str]]` of optional-non-null field names, and one inherited
`model_validator(mode="before")` rejects a mapping that explicitly contains any named key with a
`None` value. `exclude_unset=True` then preserves omission. Required-nullable fields are absent from
that set and remain required. No model has a catch-all field or uses a mutable/magic missing
sentinel.

`PublicEnvelopeModel` is the closed `BaseModel` containing only
`protocol_version: Literal["0.1"]` and `schema_version: Literal["1.0.0"]`.
`PublicRequestModel` extends it with only `request_id`, `actor`, and `client`.
`PublicResultModel[T]` is the generic `RootModel[T]` base and declares no ordinary fields; its six
concrete type arguments are the discriminated unions below. Thus no result inherits request-only
actor/client fields and no root wrapper can leak onto the wire.

This module also owns `ActorType`, `DataCategory`, `ClientKind`, and `IntegrationKind` because
ordinary operation models need those boundary enums in B0. Their exact members are registered in
`specs/INTERFACES.md`. The later `domain/privacy.py` imports and re-exports `DataCategory`, and
`domain/events.py` imports and re-exports the client enums. `domain/values.py` keeps the distinct
pure-domain `ActorType` described above. No B0 protocol module imports a later domain module, and no
later module creates an early protocol-to-domain dependency.

The common closed models are exactly:

```text
ActorAssertionModel(actor_id: ActorAssertionIdWire, actor_type: ActorType,
                    asserted_by: String1To256? = None,
                    display_name: String1To256? = None)
ClientInfoModel(kind: ClientKind, version: String1To256, integration: IntegrationKind)
FrontierModel(sequence: CanonicalUInt64Wire, head_digest: GenesisOrSha256Digest)
CoverageModel(publication_channels, authorship_assurance, artifact_observation,
              evidence_immutability, ledger_freshness, check_types, known_gaps)
SubjectStateRefModel(tree_digest: Sha256Digest? = None,
                     diff_digest: Sha256Digest? = None,
                     described_state: String1To256? = None)
PublicErrorModel(code: PublicErrorCode, message: String1To4096, retryable: bool,
                 correlation_id: CorrelationIdWire, safe_details: SafeDetails? = None)
OmittedContentModel(omitted: Literal[True], category: DataCategory,
                    reason: Literal["local_disclosure_not_authorized",
                                    "never_send_redacted"])
PrivacyProjectionModel(sink: Literal["agent_context", "local_human_view"],
                       local_disclosure_receipt_id: EgressReceiptIdWire,
                       policy_id: PrivacyPolicyIdWire,
                       policy_version: CanonicalPositiveUInt64Wire,
                       policy_digest: Sha256Digest,
                       included_categories: tuple[DataCategory, ...],
                       blocked_categories: tuple[DataCategory, ...],
                       omitted_pointers: tuple[JsonPointer, ...],
                       projection_commitment: HmacSha256Commitment)
OperationFailureModel(protocol_version: Literal["0.1"],
                      schema_version: Literal["1.0.0"], ok: Literal[False],
                      error: PublicErrorModel, request_id: RequestIdWire | None = None)
```

`ActorAssertionIdWire` is the schema's caller-asserted ASCII token
`^[A-Za-z0-9._:-]{1,128}$`; it is deliberately not the durable `agt_` `ActorId` type and does not
grant assurance. Every other `*IdWire` name above uses the exact prefix-bound canonical UUID type
owned by `protocol/ids.py`.

`CoverageModel` is exactly `common/coverage-1.0.0`: its publication-channel tuple must be one of
the 63 explicitly enumerated nonempty canonical combinations, its check-type tuple must be one of
the four explicitly enumerated combinations, and its other fields and `known_gaps` use that
schema's exact enums and bounds. `SubjectStateRefModel` requires at least one non-null member.
`SafeDetails` is exactly the object-or-array union in `common/public-error-1.0.0`, including safe
integer, key, string, item, and property bounds. The privacy arrays use the exact common-schema
bounds and uniqueness rules.

Every request model inherits `protocol_version: Literal["0.1"]`,
`schema_version: Literal["1.0.0"]`, `request_id: RequestIdWire`,
`actor: ActorAssertionModel`, and `client: ClientInfoModel`. Its remaining exact fields are:

| Model | Additional fields (all required unless marked `?`) |
|---|---|
| `StartRequestModel` | `mode: Literal["attach","create","create_or_attach"]`; `task_title: String1To8192`; `requested_view: Literal["compact"]`; `session_id: SessionIdWire?`; `external_ref: String1To8192?`; `workspace_ref: String1To8192?` |
| `PublishWorkRequestModel` | `session_id: SessionIdWire`; `writer_id: WriterIdWire`; `expected_frontier: FrontierModel | None` (required and nullable); `event_drafts: tuple[Ref(event-draft-1.0.0), ...]` with `1..100` members, order preserved and duplicates permitted |
| `CheckRequestModel` | `session_id: SessionIdWire`; `writer_id: WriterIdWire`; `expected_frontier: FrontierModel`; `mode: Literal["deterministic_only","semantic_if_configured","semantic_required"]?` — omitted resolves through `VerificationPolicy.default_check_mode` in the application facade, so recorded check events always carry a concrete mode; `scope: CheckScopeModel?`; `max_findings: Literal["1",...,"10"]?`; `policy_packs: tuple[Literal["research-evidence/0.1.0","work-integrity/0.1.0"], ...]?` with `1..2` unique members |
| `RespondRequestModel` | `session_id: SessionIdWire`; `writer_id: WriterIdWire`; `expected_frontier: FrontierModel`; `finding_id: FindingIdWire`; `finding_frontier: FrontierModel`; `disposition: Literal["acknowledged","rejected","waived"]`; `reason: String1To4096?`; `waiver_scope: Literal["finding_only"]?`; `waiver_expiry: TimestampWire?`; `evidence_refs: tuple[EvidenceIdWire | ResultIdWire, ...]?` with `0..64` unique members |
| `StatusRequestModel` | `session_id: SessionIdWire`; `writer_id: WriterIdWire`; `view: Literal["assignment","candidate_findings","compact","evidence","findings","history","obligations","versions"]`; `limit: CanonicalPageLimitWire` (`"1".."100"`); `filter: StatusFilter?`; `at_frontier: CanonicalUInt64Wire | None = None`; `cursor: CursorWire | None = None` |
| `ReceiptRequestModel` | `task_id: TaskIdWire`; `session_id: SessionIdWire`; `writer_id: WriterIdWire`; `expected_frontier: FrontierModel`; `format: ReceiptFormat`; `include: ReceiptInclude`; `redaction_profile: ReceiptRedactionProfile` |

`CheckScopeModel` has required `claim_ids` and `obligation_ids`, each a `0..64` unique tuple
of its matching typed ID. `StatusFilter` is the union of the six exact closed `$defs` filter
models in `status-request-1.0.0`; each member's fields, enum values, integer bounds, and
requiredness come directly from that node. A status filter, when present, must match `view`;
`compact` and `versions` forbid it. Start enforces both-or-neither attachment refs and requires
`session_id` or both refs for `attach`. Respond enforces the three disposition branches exactly:
acknowledgement forbids waiver fields, rejection requires `reason` and forbids waiver fields, and
waiver requires `reason` plus `waiver_scope` (expiry remains optional).

Each success branch is a normal closed `BaseModel`; every field in the table is required and no
other field exists:

| Success model | Exact fields |
|---|---|
| `StartSuccessModel` | `protocol_version`, `schema_version`, `request_id`, `ok: Literal[True]`, `outcome: Literal["attached","created","replayed"]`, `task_id`, `session_id`, `writer_id`, `frontier: FrontierModel`, `compact: StartCompactViewModel`, `versions: StartVersionSliceModel`, `privacy_projection` |
| `PublishWorkSuccessModel` | `protocol_version`, `schema_version`, `request_id`, `ok: Literal[True]`, `outcome: Literal["accepted","replayed"]`, `task_id`, `session_id`, `writer_id`, `subject_frontier`, `result_frontier`, `accepted_events: tuple[PublishWorkAcceptedEventModel, ...]` (`1..100`, order preserved), `warning_codes`, `coverage`, `gaps`, `versions: PublishWorkVersionSliceModel`, `privacy_projection` |
| `CheckSuccessModel` | `protocol_version`, `schema_version`, `request_id`, `ok: Literal[True]`, `task_id`, `session_id`, `writer_id`, `subject_frontier`, `result_frontier`, `verdict`, `findings: tuple[CheckProjectedFindingModel, ...]` (`0..10`), `suppressed_count: CanonicalUInt64Wire`, `policy_executions: tuple[CheckPolicyExecutionModel, ...]` (`1..2`, unique), `semantic_status`, `semantic_reason`, `semantic_provenance: Ref(semantic-provenance) | None` (required and nullable), `coverage`, `versions: CheckVersionSliceModel`, `privacy_projection` |
| `RespondSuccessModel` | `protocol_version`, `schema_version`, `request_id`, `ok: Literal[True]`, `task_id`, `session_id`, `writer_id`, `subject_frontier`, `result_frontier`, `accepted_event: RespondAcceptedEventModel`, `response: RespondResponseModel`, `coverage`, `warning_codes`, `versions: RespondVersionSliceModel`, `privacy_projection` |
| `StatusSuccessModel` | `protocol_version`, `schema_version`, `request_id`, `ok: Literal[True]`, `task_id`, `session_id`, `writer_id`, `view`, `requested_frontier`, `head_frontier`, `subject_frontier`, `result_frontier`, `projection_lag: CanonicalUInt64Wire`, `projection_version`, `rebuild_state`, `page: StatusPage`, `coverage`, `gaps`, `import_status: StatusImportStatusModel`, `privacy_projection` |
| `ReceiptSuccessModel` | `protocol_version`, `schema_version`, `request_id`, `ok: Literal[True]`, `receipt_id`, `task_id`, `session_id`, `subject_frontier`, `result_frontier`, `receipt_object_id`, `receipt_digest`, `conclusion`, `redaction_profile`, `format`, `include`, `document: Ref(receipt-document) | None` (required and nullable), `human_text: String1To32768 | finding-summary omission | None` (required and nullable), `coverage`, `suppressed_finding_count: int` (`0..2**53-1`, bool forbidden), `versions: ReceiptVersionSliceModel` (exact 11-field slice, including `resource_manifest_digest`), `privacy_projection` |

The remaining result **data-object** support-model inventory is closed and exact: start has `compact_view` and
`version_slice`; publish-work has `accepted_event` and `version_slice`; check has
`policy_execution`, `projected_finding`, and `version_slice`; respond has `accepted_event`,
`evidence_summary`, `response`, and `version_slice`; receipt has
`ReceiptPolicyVersionEntryModel`, `ReceiptSchemaVersionEntryModel`, and the exact 11-field
`ReceiptVersionSliceModel` (including `resource_manifest_digest`); status has
`assignment_item/page`, `candidate_finding_item/candidate_findings_page`, `compact_finding/item/
obligation/page`, `evidence_item/page`, `finding_basis`, `finding_item/findings_page`,
`history_item/page`, `import_status`, `obligation_item/obligations_page`,
`structural_subject_state`, `version_slice`, and `versions_page`. Their Python fields are exactly
the properties at the same `$defs` node, with the same required list; the parity test compares
`model_fields` against those properties and therefore forbids an omitted, renamed, or invented
field.

Every named `$defs` entry outside that inventory has exactly one of three non-DTO roles and does not
produce a standalone `BaseModel`: primitive/string/ID definitions become constrained aliases;
`*_omission` definitions become literal-narrowed aliases of the common omission model and are
enforced by their owning outer field; and identity, `semantic_pair_*`, or `view_*` definitions are
schema predicates enforced by the corresponding outer model validator. In particular
`task_summary_event_identity`, `known_event_identity`, the specialized omission definitions,
`semantic_pair_*`, and `view_*` are not closed records: some intentionally admit surrounding
properties so they cannot inherit `extra="forbid"`. The schema-parity test maintains the complete
classification of every current named `$defs` key and fails if a new definition is neither in the
data-object inventory nor assigned one of these exact alias/predicate roles.

`StatusPage` is the union of exactly the eight page models. `StatusSuccessModel` validates the
outer `view`/`page` relation: each view admits only its corresponding page; compact and versions
pages require `next_cursor is None`. Check validates the semantic status/reason relation and the
provenance conditions in the frozen schema. Publish accepted-event summaries, projected finding
omissions, response disposition fields, finding provenance, and receipt `format` versus
`document`/`human_text` enforce their schema's `if`/`then` branches without repair or defaults.

Results are object-valued Pydantic `RootModel`s, never `BaseModel`s with a `root` wire property:

```text
StartResultBranch = Annotated[StartSuccessModel | OperationFailureModel,
                              Field(discriminator="ok")]
class StartResultModel(PublicResultModel[StartResultBranch]): ...
```

The identical construction applies to `PublishWorkResultModel`, `CheckResultModel`,
`RespondResultModel`, `StatusResultModel`, and `ReceiptResultModel`. A shared root
`model_validator(mode="before")` requires a mapping with `type(value.get("ok")) is bool` before
Pydantic's discriminator runs; this is required because strict Pydantic 2.13 still permits integer
`0` for `Literal[False]`. Thus `ok` must be the JSON boolean `true` or `false`; integers and strings
do not select a branch. Dumping a result root produces the branch object itself, with no
`{ "root": ... }` wrapper. The application-facing names are exact
aliases, not subclasses or parallel dataclasses:

```text
StartRequest = StartRequestModel                 StartResult = StartResultModel
PublishWorkRequest = PublishWorkRequestModel     PublishWorkResult = PublishWorkResultModel
CheckRequest = CheckRequestModel                 CheckResult = CheckResultModel
RespondRequest = RespondRequestModel             RespondResult = RespondResultModel
StatusRequest = StatusRequestModel               StatusResult = StatusResultModel
ReceiptRequest = ReceiptRequestModel             ReceiptResult = ReceiptResultModel
```

`public_model_to_wire(model)` is the only public wire-dump boundary. `type(model)` must be exactly
one of the twelve concrete request/result model classes; a support model, branch model, subclass, or
arbitrary object raises ordinary `TypeError("public_model_wrong_type")`. An immutable exact-type map
selects the matching `start|publish-work|check|respond|status|receipt` request/result schema name and
version `1.0.0`; no class-name parsing or nearest-schema lookup exists. The helper dumps with exactly
`mode="json", by_alias=True, exclude_unset=True, exclude_none=False`, requires the result to be an
ordinary dictionary, calls `protocol.schemas.validate_schema_instance(schema_name, "1.0.0",
dumped)`, and returns a newly allocated ordinary `dict[str, JsonValue]` only after that succeeds.
The helper preserves absent versus explicitly-null optional fields and arrays remain arrays.

Direct Pydantic `model_dump` remains an implementation primitive and is not an authorized CLI/MCP/
control serialization boundary. Canonical boundary bytes are
`canonical_encode(public_model_to_wire(model))`, not Pydantic's formatting. For every valid wire
value, parse then boundary-dump then parse is value-identical, and a result root round-trips as one
object. Schema resolution for this validation is local-only through the packaged catalog; no URL is
fetched. A model/schema drift failure is the bounded
`ProtocolValueError("schema_instance_invalid")` and no invalid dictionary is returned.

A post-validated `ReviewerChallenge` does not add a wire model. Its discrepancy is the semantic
finding `summary`; its bounded direct main-agent message, alternative interpretation, uncertainty,
and requested next step are deterministically formatted into `detail`. Ordinary agent-context
projection therefore uses the existing `/findings/*/summary|detail` `finding_summary` category and
either includes both authorized fields or emits the normal omission markers. No second advisory
message, provider reply, or human-approval field exists.

`ActorAssertionModel` captures the caller's assertion about who or what is acting. It is
validated as a shape, not accepted as truth. The implementation may preserve a display name and an
`asserted_by` marker, but the server assigns the durable assurance level.

`ClientInfoModel` records which client or harness surfaced the request. It is used for tracing,
compatibility, and receipt wording, not authorization. Its exact closed shape is
`{kind, version, integration}`; there is no `id` field, and extra keys are forbidden.

Each result model mirrors the request identity and exposes only the bounded fields needed by its
operation. Results may include coverage, versions, verdicts, warnings, and a payload object, but
they may not smuggle unbounded raw text or internal stack traces into the public boundary.

Every operation/support result has an internal validated form and an ordinary-client projected
form. The projected form keeps fields classified by the frozen result-field registry:

- structural leaves remain their original exact schema type;
- an approved content-bearing leaf remains its original exact schema type;
- a blocked content-bearing leaf is the closed `OmittedContentModel` with exactly
  `omitted: true`, its `DataCategory`, and
  `reason: "local_disclosure_not_authorized"`;
- a required top-level `privacy_projection: PrivacyProjectionModel` contains
  `sink: agent_context`, canonical `local_disclosure_receipt_id`, policy ID/version/digest,
  sorted unique included/blocked categories, and sorted unique omitted JSON Pointers.

The sole v0.1 replacement exception is `ReceiptSuccessModel.document` when `format="json"`.
That value is the exact canonical stored `ReceiptDocument` bound by `receipt_digest`; replacing an
in-document leaf would create a derivative that no longer matches the advertised digest. The local
disclosure gate still classifies every document content leaf, but returns the exact whole document
only when every present content category is authorized. If any present document content leaf is
blocked or unclassifiable, projection fails closed before success serialization with
`privacy_projection_unavailable`; it never emits a partly rewritten JSON receipt. Markdown/text
`human_text` remains an ordinary replaceable content leaf because it is a deterministic derivative
and is not what `receipt_digest` binds.

`PrivacyProjectionModel` is the ordinary operation/support projection and its frozen wire sink is
only `agent_context`. Authenticated `trusted_human_control` previews and policy diffs cross the
separate confidential foreground control boundary and use that boundary's own projection and audit
records; they never serialize this model. The registry enumerates every content-bearing leaf for
all six operations and every support result. Adding a result field without an explicit
`public_structural` or `DataCategory` classification is a release-blocking schema error and
prevents serialization at runtime.

### Frozen result-field registry and projection bounds

This module owns a private executable closed result-field registry. Its private representation is
an immutable, unsigned-UTF-8-sorted tuple of frozen, slotted rule records; there is no public
container, entry class, mapping/list view, or runtime registration/mutation API. A rule contains an
exact method, optional exact status view, JSON Pointer pattern, and classification. Its
classification is exactly `Literal["public_structural"] | DataCategory`. `*` matches one canonical
non-negative array-index segment and `**` is forbidden.

The only public access is
`classify_result_leaf(method: str, validated_result: Mapping[str, JsonValue], pointer: str) ->
Literal["public_structural"] | DataCategory`. The complete already-schema-validated result lets the
resolver derive the exact status `view` and publish-work event `schema_name` discriminants without
adding parallel selector arguments. The pointer must be NFC, RFC 6901 escaped, begin with `/`, be at
most 256 UTF-8 bytes, and identify a leaf in `validated_result`. Resolution precedence is exact
pointer, then one-index pattern. A malformed pointer, missing leaf, invalid rule, overlap at the
same precedence, or unmatched leaf raises `ProtocolValueError("invalid_json_pointer")`; the
service maps that fail-closed condition to `privacy_projection_unavailable` before any content
serialization. One leaf must match exactly one entry. There is no category guess or omission
marker for an unknown field.

The following typed leaves are structural for every method, wherever the exact result schema
places them: protocol/schema/version identifiers; typed Yoetz IDs; `Frontier`; enum/reason/policy/
gap codes; booleans; bounded integers; RFC3339 timestamps; canonical digest/commitment values;
coverage/authorship/immutability vectors; provider/profile/model identities; pagination cursors;
and the complete `privacy_projection` object. A bare string is never structural merely because it
is short. Exact string fields `schema_version`, `projection_version`, `engine_version`,
`policy_version`, `endpoint_profile_version`, and closed token-array members named by the schemas
are structural; relative/absolute paths and prose are not.

The complete content map is:

| Method/result | Exact pointer patterns | `DataCategory` |
|---|---|---|
| `start` | none; raw task/workspace/external text is forbidden by the result schema | — |
| `publish_work` | `/accepted_events/*/summary`; category is selected by the accepted event schema: plan/obligation → `task_description`, decision → `decision_excerpt`, action/result → `command_metadata`, evidence → `evidence_excerpt`, claim/response/finding → `finding_summary`, redaction/receipt/check/session structural summaries are fixed codes only | listed derivation |
| `check` | `/findings/*/summary`, `/findings/*/detail` | `finding_summary` |
| `respond` | `/response/reason` | `finding_summary` |
| `respond` | `/response/evidence/*/description` | `evidence_excerpt` |
| `status` compact view | `/page/items/*/task_title` | `task_description` |
| `status` compact view | `/page/items/*/open_obligations/*/description`, `/page/items/*/open_obligations/*/evidence_expectation`, `/page/items/*/open_obligations/*/acceptance_criteria` | `obligation_text` |
| `status` compact view | `/page/items/*/unresolved_findings/*/summary`, `/page/items/*/unresolved_findings/*/detail` | `finding_summary` |
| `status` obligations view | `/page/items/*/description`, `/page/items/*/evidence_expectation`, `/page/items/*/acceptance_criteria` | `obligation_text` |
| `status` evidence view | `/page/items/*/description`, `/page/items/*/reference` | `evidence_excerpt` |
| `status` findings view | `/page/items/*/summary`, `/page/items/*/detail`, `/page/items/*/reason` | `finding_summary` |
| `status` candidate_findings view | `/page/items/*/summary`, `/page/items/*/detail` | `finding_summary` |
| `status` assignment/history/versions views | none; their exact v0.1 schemas contain structural IDs, closed codes, and bounded metadata only | — |
| `receipt` | `/document/obligations/*/summary` | `obligation_text` |
| `receipt` | `/document/findings/*/summary`, `/document/findings/*/detail`, `/document/responses/*/reason`, `/document/gaps/*/detail`, `/human_text` | `finding_summary` |
| `receipt` | `/document/sections/*/title`, `/document/sections/*/body`, `/document/sections/*/coverage_note`, `/document/sections/*/items/*` | `finding_summary` |
| `review` | `/check_result/findings/*/summary`, `/check_result/findings/*/detail` | `finding_summary` |
| `integration_preview` | `/file_changes/*/relative_path`, `/file_states/*/relative_path` | `repository_excerpt` |
| `integration_execute` | `/changed_files/*` | `repository_excerpt` |
| all other current support results | none; their schemas contain structural IDs/codes/counts/digests only | — |

Each result schema and view discriminator makes these patterns unambiguous. For example,
`/page/items/*/description` is registered separately under the exact status view; it is not a
global field-name heuristic. Candidate-finding prose is content-bearing unless and until its schema
is narrowed to exact template tokens. `Coverage.known_gaps`, warning arrays, and failure classes
are closed codes and structural; any future free-form warning is a new content field and needs a
registry entry.

`MAX_PROJECTION_CONTENT_LEAVES=512`, `MAX_PROJECTION_POINTER_BYTES=256`,
`MAX_INTERNAL_PROJECTABLE_RESULT_BYTES=524_288`, and
`MAX_PROJECTED_RESULT_BYTES=1_048_576`. The internal result schema/page caps must satisfy the first
three before projection. There is no truncation after an operation commits: if a future result
cannot fit, it must paginate at its owning operation. Omission markers and
`privacy_projection.omitted_pointers` are sorted by unsigned UTF-8 pointer bytes, duplicate-free,
and capped at 512. Included/blocked category arrays are sorted unique and capped at the complete
`DataCategory` enum size. `privacy_projection` also carries
`projection_commitment=hmac-sha256:<64 lowercase hex>`, computed exactly as
`HMAC-SHA256(K_audit, b"yoetz/privacy/local-projection/v1\\x00" ||
JCS({method, canonical_internal_result, sink, policy_digest, field_decisions, receipt_id}))`.
Neither an unkeyed internal-result digest nor a plaintext-derived unkeyed projection digest is
public/catalog-visible. Replay validates the commitment before returning the same receipt/projection.

### Shared semantic outcome vocabulary

This module is the nominal owner of `SemanticStatus`, `SemanticReason`, and their closed relation.
It must define them before the domain and port layers, so `domain/findings.py`,
`domain/events.py`, `ports/semantic.py`, and the public check models all import the same enum
objects instead of defining lookalike values in opposite dependency directions.

`SemanticStatus` is exactly `not_requested`, `not_configured`, `blocked_by_policy`,
`blocked_forbidden_data`, `classification_uncertain`, `awaiting_human`, `human_denied`,
`approval_expired`, `succeeded`, `refused`, `timeout`, `invalid`, `unavailable`, `late`, `stale`,
or `failed`. `SemanticReason` is exactly the reason inventory registered in
`specs/INTERFACES.md` section 7. `VALID_SEMANTIC_REASONS` is a read-only mapping whose keys exhaust
`SemanticStatus` and whose frozen value sets are:

| Status | Allowed reasons |
|---|---|
| `not_requested` | `deterministic_mode`, `no_material_semantic_case` |
| `not_configured` | `provider_not_configured`, `local_model_not_configured` |
| `blocked_by_policy` | `network_egress_denied`, `channel_disabled`, `provider_binding_not_authorized`, `scope_not_authorized`, `content_category_not_authorized`, `policy_generation_revoked` |
| `blocked_forbidden_data` | `never_send_detected`, `secret_detected` |
| `classification_uncertain` | `classification_uncertain` |
| `awaiting_human` | `human_approval_required` |
| `human_denied` | `human_denied` |
| `approval_expired` | `human_approval_expired` |
| `succeeded` | `semantic_completed` |
| `refused` | `provider_refused` |
| `timeout` | `provider_timeout` |
| `invalid` | `response_schema_invalid`, `response_content_invalid`, `semantic_judgment_rejected` |
| `unavailable` | `credential_unavailable`, `endpoint_profile_unavailable`, `transport_unavailable`, `provider_rate_limited`, `provider_quota_exhausted`, `retry_budget_exhausted`, `audit_reservation_unavailable`, `receipt_persistence_unknown` |
| `late` | `deadline_authority_lost`, `lease_authority_lost` |
| `stale` | `frontier_changed`, `dependency_changed` |
| `failed` | `coordinator_failure` |

`validate_semantic_outcome(status, reason)` accepts enum instances only and returns `None` for an
allowed pair. A value of the wrong enum type raises
`ProtocolValueError("invalid_semantic_outcome_type")`; a disallowed pair raises
`ProtocolValueError("invalid_semantic_status_reason_pair")`. It never coerces strings. The
mapping and validator contain no provider dispatch logic; `ports/semantic.py` owns how a concrete
outcome reaches one of these already-registered pairs.

`validate_semantic_provenance_binding(status, reason, provenance_status, provenance_reason)` first
applies that pair validator, then requires the two provenance identity arguments to be either both
`None` or exact shared enum members. The provenance-presence partition is closed:

- all eight non-`unavailable` predispatch statuses require both absent;
- `unavailable/credential_unavailable`, `unavailable/endpoint_profile_unavailable`,
  `unavailable/retry_budget_exhausted`, `unavailable/audit_reservation_unavailable`, and
  `unavailable/receipt_persistence_unknown` require both absent;
- `succeeded`, `refused`, `timeout`, `invalid`, `late`, and `stale` require both present;
- `unavailable/transport_unavailable`, `unavailable/provider_rate_limited`, and
  `unavailable/provider_quota_exhausted` require both present; and
- `failed/coordinator_failure` permits both absent or both present because the coordinator can fail
  before or after a dispatch.

Whenever provenance is present, its nested status and reason must equal the top-level status and
reason exactly. A missing, partial, forbidden, wrong-type, or mismatched provenance identity raises
`ProtocolValueError("invalid_semantic_provenance")`. The helper validates identity only; the
semantic-provenance schema/domain codec remains responsible for the rest of that closed record.

`FrontierModel` additionally enforces the shared genesis cross-field invariant at construction:
sequence `"0"` requires `head_digest="genesis"`, and every positive sequence requires a SHA-256
digest. Direct model validation cannot defer either inversion to a later dump/schema gate.

`CheckSuccessModel` first validates its top-level pair. For a non-null provenance value it then
requires an exact built-in `dict` with exact built-in `"status"` and `"reason"` string values,
converts those two tokens through the shared enums, and calls
`validate_semantic_provenance_binding`; missing/non-object/wrong-token input fails with
`invalid_semantic_provenance` without invoking caller callbacks. The remaining closed provenance
shape is checked by the packaged schema gate. Its top-level pair therefore describes only the
selected/final attempt. Earlier late or non-selected attempts remain durable attempt-audit rows and
can never be substituted into the public result provenance.

`CheckResultModel` validates this locally owned matrix. A
`semantic_required` gap is an ordinary successful result envelope with
`verdict=incomplete_check`, preserved deterministic findings, no semantic findings, and the exact
reason code. The model never asks a client to infer semantic incompleteness from a generic warning
or coverage string.

Timestamp-shaped wire fields in this module use one private constrained alias that accepts only RFC
3339 UTC with exactly three fractional digits and a trailing `Z`. It rejects locale-specific
formats, offsets other than UTC, leap-second spellings, and any coercion. This module deliberately
does not expose a second timestamp parser/formatter: the public `Timestamp`,
`timestamp_from_string`, `timestamp_from_datetime`, `format_rfc3339_millis`,
`parse_rfc3339_millis`, and `add_utc_milliseconds` helpers are owned by B1
`domain/values.py`, as also stated by `INTERFACES.md` and `ports/clock.md`.

## Errors and edge cases

- Any field that accepts an ID must validate the registry prefix and canonical lowercase UUID form.
- `schema_version` mismatches are public protocol errors, not silent fallbacks.
- Caller-supplied actor data never becomes server-assigned assurance.
- Request and result models reject extra keys everywhere the contract requires strictness.
- Any timestamp outside the exact UTC format is invalid even if Python would otherwise parse it.
- Operation-specific models may require different IDs, but they all inherit the same bounded-string
  and strict-field discipline.
- An omission marker in a structural field, an unclassified result field, a projection without a
  durable receipt, or a receipt/sink/policy mismatch is invalid.
- A malformed, noncanonical, oversized, overlapping, or unmatched result-registry JSON Pointer
  raises `ProtocolValueError("invalid_json_pointer")`; this module owns that registered reason.
- An unknown semantic enum or disallowed semantic status/reason pair is invalid; no renderer or
  adapter may repair it.

## Invariants

1. Shared constants are the same everywhere the protocol is loaded.
2. Public boundary models stay strict and predictable.
3. Actor assertions never become security claims by themselves.
4. JSON compatibility does not mean arbitrary Python coercion is allowed.
5. The constants here are not duplicated ad hoc in other layers; other layers import them.
6. The service—not CLI or MCP—owns the one field-level projection contract.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py` — constant/version values, model/schema parity,
  and actor/client boundary validation.
- `tests/unit/domain/test_values.py` — timestamp round-trip formatting and strict rejection.
- `tests/conformance/surfaces/test_cli_contract_matrix.py` — JSON envelope shape checks at the CLI
  boundary.

## Open questions

None.
