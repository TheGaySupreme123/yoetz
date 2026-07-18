# src/yoetz/domain/events.py — the 16 event families, drafts, and accepted envelopes

**Wave:** B | **ADRs:** ADR-002, ADR-004 | **Imports (spec-tree):** `domain/values.md`,
`domain/findings.md` (payload mirror for `finding_recorded`),
`domain/receipts.md` (`ReceiptConclusion` only), `protocol/errors.md`,
`protocol/coverage.md`, `protocol/models.md` (`ClientKind`, `IntegrationKind`, `SemanticStatus`,
`SemanticReason`, `ReceiptRedactionProfile`, `CheckScopeModel`,
`CheckPolicyExecutionModel`) | **Imported by:** `kernel/reducers.md`, `kernel/projections.md`,
`kernel/deterministic_checks.md`, `application/*`, `adapters/memory/*`, `adapters/sqlite/*`,
`adapters/importers/codex_jsonl.md`

## Purpose

Defines the typed event vocabulary that makes the ledger more than a transcript: one frozen
payload dataclass per family (INTERFACES §7), the client-shaped `EventDraft`, the ledger-shaped
`AcceptedEvent` structural envelope registered in `specs/INTERFACES.md`, and the opaque
`UnknownEvent`. This file
is the single source of payload truth; JSON Schemas under `schemas/` and Pydantic boundary
models are generated from and tested against these shapes under the parity rules owned by
`specs/src/yoetz/protocol/schemas.md`, never hand-drifted.

Payload↔object separation is structural here: an `AcceptedEvent` never embeds plaintext payload
bytes. It carries a `PayloadRef` (encrypted-object pointer + commitment) **and** a decoded
in-memory payload handle supplied by the adapter after decrypt/verify. The kernel computes over
the handle; storage and wire surfaces see only the ref.

## Public surface

- `SCHEMA_VERSION = "1.0.0"` — every family starts at `1.0.0` (ADR-002 decision 6).
- `EVENT_FAMILIES` — frozen tuple of the 16 schema names (INTERFACES §7, verbatim):
  `session_opened`, `session_resumed`, `plan_published`, `obligation_published`,
  `assignment_recorded`, `decision_recorded`, `action_recorded`, `result_recorded`,
  `evidence_recorded`, `claim_recorded`, `plan_revised`, `finding_recorded`,
  `response_recorded`, `redaction_recorded`, `check_recorded`, `receipt_recorded`.
- Payload records, all `@dataclass(frozen=True, slots=True)` except the explicit finding alias,
  named `<FamilyPascal>Payload`:
  `SessionOpenedPayload`, `SessionResumedPayload`, `PlanPublishedPayload`,
  `ObligationPublishedPayload`, `AssignmentRecordedPayload`, `DecisionRecordedPayload`,
  `ActionRecordedPayload`, `ResultRecordedPayload`, `EvidenceRecordedPayload`,
  `ClaimRecordedPayload`, `PlanRevisedPayload`, `FindingRecordedPayload`,
  `ResponseRecordedPayload`, `RedactionRecordedPayload`, `CheckRecordedPayload`,
  `ReceiptRecordedPayload`. `FindingRecordedPayload = Finding` is an explicit type alias, not a
  second dataclass with duplicate validation.
- Support enums owned here: `RuntimeProfile`, `RequestedItemKind`, `ObligationStatus`,
  `WritePolicy`, `ActionKind`, `ResultOutcome`,
  `EvidenceKind`, `ClaimKind`, `ObligationChangeKind`, `RedactionMethod`,
  `RedactionReasonCategory`, `CheckMode`, and `RedactionState`.
- Support records owned here: `RequestedItem`, `ObligationChange`, `PolicyVersion`,
  `EventSchema`, `WriterChain`, `LedgerChain`, `PayloadRef`, and `ProjectionLocator`.
- `ClientKind` and `IntegrationKind` — imports/re-exports of the exact enums owned by
  `protocol/models.py`; this module never redeclares lookalikes.
- `EventPayload` — union alias of the 16 payload dataclasses.
- `PAYLOAD_TYPES: Mapping[EventSchema, type[EventPayload]]` — the exact 16
  `EventSchema(name, "1.0.0")` pairs → concrete payload type.
- `decode_payload(schema: EventSchema, payload: JsonValue) -> EventPayload` — validating
  constructor from frozen JSON.
- `encode_payload(payload: EventPayload) -> JsonValue` — emits the normalized closed object and
  feeds the canonicalizer.
- `normalize_payload_json(schema, value) -> JsonValue` — decode then encode into the one canonical
  optional-field representation.
- `media_type_for(schema_name: str) -> str` — `"application/vnd.yoetz." + schema_name + "+json"`.
- `EventDraft` — frozen client-shaped pre-acceptance event.
- `AcceptedEvent` — frozen structural envelope + decoded payload handle.
- `UnknownEvent` — frozen opaque preserved event, `projection_status = "unknown_unprojected"`.
- `LedgerRecord` — union alias `AcceptedEvent | UnknownEvent` (what reducers consume).
- `accepted_record_to_json(record)` — full schema-shaped accepted record including
  `entry_digest`, excluding decoded handles and unknown-only adjacent metadata.
- `accepted_record_digest_preimage(record)` — the exact accepted record with only
  `entry_digest` removed.

Field-size constants (frozen protocol constants registered in `specs/INTERFACES.md`):
`MAX_TEXT_BYTES = 8_192` (descriptions/statements/summaries), `MAX_REASON_BYTES = 4_096`
(INTERFACES §3), `MAX_LABEL_BYTES = 256` (titles/short labels), `MAX_REF_LIST = 64`,
`MAX_CAUSAL_PARENTS = 32`, `MAX_REQUESTED_ITEMS = 64`, `MAX_ALTERNATIVES = 16`. All text bounds
retain their registered historical `_BYTES` names, but payload-field limits follow JSON Schema
`maxLength` and therefore count Unicode code points. Canonical payload objects remain independently
bounded to `PayloadRef.plaintext_size <= 4_194_304` bytes at publication.

## Behavior

### Shared conventions

- Every dataclass validates in `__post_init__` via `domain/values` constructors and raises
  `ProtocolValueError` with a bounded reason code; no partial construction.
- All ID-list fields are tuples, sorted by unsigned ASCII byte order, duplicate-free
  (`ProtocolValueError("unsorted_set_field")` / `("duplicate_set_member")`) — matching the
  canonical set profile in `specs/src/yoetz/protocol/canonical.md` so
  `encode_payload ∘ decode_payload` is byte-stable.
- Optional scalar fields are `None`-defaulted and optional collection fields use empty tuples;
  `decode_payload` rejects unknown JSON keys
  (`ProtocolValueError("unknown_payload_field")`) and missing required keys
  (`("missing_payload_field")`).
- Free-text fields never carry semantics for deterministic checks; only IDs, enums, digests,
  and `SubjectStateRef` values do.
- `EventSchema.name` matches `^[a-z][a-z0-9_]{0,63}$`. `EventSchema.version` is stable SemVer with
  exactly three canonical decimal components, no leading zeros, prerelease, or build metadata,
  and at most 64 ASCII bytes. Known v0.1 families use exactly `1.0.0`; an otherwise bounded unknown
  event version remains preservable but is never coerced to a known payload type.

### Exact support values

All support enums are `str`-valued and all support records are
`@dataclass(frozen=True, slots=True)`. Their exact shapes are:

```text
RuntimeProfile = strict-local | local-openai | test-fake | release-probe
RequestedItemKind = url | file | command | change | source
ObligationStatus = open | resolved
WritePolicy = read_only | writes_allowed
ActionKind = command | edit | research | review | other
ResultOutcome = success | failure | partial | unknown
EvidenceKind = artifact | command_output | test_result | research_source | import_report | other
ClaimKind = completion | material
ObligationChangeKind = superseded | waived | carried
RedactionMethod = logical_redaction | object_deletion
RedactionReasonCategory = secret | privacy | retention | legal | other
CheckMode = deterministic_only | semantic_if_configured | semantic_required
RedactionState = present | logically_redacted | key_unavailable | erased_claimed
```

```text
RequestedItem(item_kind: RequestedItemKind, value: str)
ObligationChange(obligation_id: ObligationId,
                 change: ObligationChangeKind,
                 reason: str | None = None,
                 replacement_obligation_ids: tuple[ObligationId, ...] = ())
PolicyVersion(policy_id: str, policy_version: str)
EventSchema(name: str, version: str)
WriterChain(writer_id: WriterId, sequence: int, previous_entry_digest: str)
LedgerChain(ingestion_sequence: int, previous_entry_digest: str, accepted_at: Timestamp)
PayloadRef(object_id: ObjectId, media_type: str, plaintext_size: int,
           commitment: str, encryption_format: Literal["yoetz-object/1"] = "yoetz-object/1")
ProjectionLocator(schema: EventSchema, logical_key: str | None,
                  canonical_payload_digest: str,
                  redaction_target_event_ids: tuple[EventId, ...] = (),
                  redaction_target_object_ids: tuple[ObjectId, ...] = ())
```

`WriterChain.sequence` and `LedgerChain.ingestion_sequence` are domain integers in
`1..9_223_372_036_854_775_807`; their accepted-record wire representation is a canonical decimal
string. Sequence 1 requires predecessor `genesis`; later sequences require a SHA-256 digest.
`PayloadRef.plaintext_size` is deliberately different: it is an `int` but not `bool` in
`0..4_194_304`, and its JSON representation is a JSON integer, exactly as frozen in
`accepted-event-1.0.0.schema.json`. It is not passed through the sequence string helpers.

`ProjectionLocator` is non-plaintext replay metadata captured before payload-object publication
and committed atomically beside the accepted event. `schema` must equal the event schema and
`canonical_payload_digest` is the SHA-256 digest of the normalized encoded payload. Its exact
logical-key mapping is: plan version (canonical decimal) for both plan families; the payload's
obligation/action/result/evidence/claim/finding ID for their corresponding families; `finding_id`
for `response_recorded`; envelope `event_id` for assignment, decision, and check; and `None` for
session, receipt, and redaction families. For `redaction_recorded` only, the two target tuples are
the exact sorted target tuples from the payload and at least one is nonempty; they are empty for
every other family. This retains only typed IDs, schema identity, and a digest—never a title,
statement, reason, path, URL, or other payload text. An unknown event's locator has its exact
unknown schema, `logical_key=None`, empty target tuples, and the same digest exposed by the
variant's adjacent `canonical_payload_digest`; it is never interpreted into a family key.

The locator is deliberately not a second object-reference inventory. The immutable 19-field
accepted envelope already supplies the non-plaintext associations needed by object-only redaction
replay: `payload_ref.object_id` identifies that event's payload object; an exact-known
`evidence_recorded` envelope's `artifact_refs` identifies its optional captured-content object; and
an exact-known `receipt_recorded` envelope's singleton `artifact_refs` identifies its encrypted
receipt document. Reducers derive their reverse `ReplayIndex` only from those envelope fields plus
the locator's schema/logical key and redaction target tuples. No deleted payload body, object-store
lookup, path, URL, description, or `remaining_gap` text participates in that index.

`ClientKind` and `IntegrationKind` are imported from `protocol/models.py` and re-exported here;
`ResponseDisposition` and `WaiverScope` are imported from `domain/findings.py`;
`ReceiptConclusion` is imported from `domain/receipts.py`; `PublicationChannel` and the coverage
enums are imported from `protocol/coverage.py`; `SemanticStatus`, `SemanticReason`, and
`ReceiptRedactionProfile` are imported from `protocol/models.py`. This file does not create
lookalike enums for those values.

The payload dataclass constructor fields are exactly the fields in the sixteen numbered tables
below. Required fields precede optional fields in Python; an optional absent JSON field becomes its
documented `None` or empty-tuple default. A field marked required in a table remains required even
when an empty tuple is valid. No support record or payload accepts a mapping in place of its
nominal type after decoding.

For implementation, the exact constructor order/defaults are frozen here:

```text
SessionOpenedPayload(task_title: str, client_kind: ClientKind, client_version: str,
                     integration: IntegrationKind, profile: RuntimeProfile,
                     external_ref: str | None = None,
                     workspace_ref: str | None = None)
SessionResumedPayload(client_kind: ClientKind, client_version: str,
                      integration: IntegrationKind, profile: RuntimeProfile,
                      resumed_frontier: Frontier)
PlanPublishedPayload(plan_version: int, summary: str,
                     obligation_refs: tuple[ObligationId, ...],
                     scope_exclusions: tuple[str, ...] = ())
ObligationPublishedPayload(obligation_id: ObligationId, description: str,
                           evidence_expectation: str, status: ObligationStatus,
                           acceptance_criteria: str | None = None,
                           requested_items: tuple[RequestedItem, ...] = (),
                           source_refs: tuple[EventId, ...] = (),
                           resolution_evidence_refs:
                               tuple[EvidenceId | ResultId, ...] = ())
AssignmentRecordedPayload(assignee_actor_id: ActorId,
                          obligation_ids: tuple[ObligationId, ...],
                          scope_description: str,
                          write_policy: WritePolicy | None = None,
                          handoff_of: EventId | None = None)
DecisionRecordedPayload(statement: str, rationale: str, authority: ActorId,
                        alternatives: tuple[str, ...] = (),
                        affected_obligation_ids: tuple[ObligationId, ...] = (),
                        supersedes_event_id: EventId | None = None)
ActionRecordedPayload(action_id: ActionId, action_kind: ActionKind, description: str,
                      command: str | None = None,
                      subject_state: SubjectStateRef | None = None,
                      obligation_refs: tuple[ObligationId, ...] = (),
                      attempted_items: tuple[str, ...] = ())
ResultRecordedPayload(result_id: ResultId, action_id: ActionId, outcome: ResultOutcome,
                      exit_status: int | None = None, summary: str | None = None,
                      subject_state: SubjectStateRef | None = None,
                      evidence_refs: tuple[EvidenceId, ...] = ())
EvidenceRecordedPayload(evidence_id: EvidenceId, evidence_kind: EvidenceKind,
                        strength: EvidenceImmutability, observed_at: Timestamp,
                        reference: str | None = None,
                        captured_object_id: ObjectId | None = None,
                        content_digest: str | None = None,
                        description: str | None = None,
                        subject_state: SubjectStateRef | None = None)
ClaimRecordedPayload(claim_id: ClaimId, claim_kind: ClaimKind, statement: str,
                     supporting_refs:
                         tuple[EvidenceId | ResultId | ObligationId, ...],
                     subject_state: SubjectStateRef | None = None,
                     obligation_refs: tuple[ObligationId, ...] = (),
                     disputes_refs: tuple[ClaimId | EventId, ...] = ())
PlanRevisedPayload(plan_version: int, supersedes_plan_version: int,
                   reason: str, summary: str,
                   obligation_changes: tuple[ObligationChange, ...])
FindingRecordedPayload = Finding
ResponseRecordedPayload(finding_id: FindingId, finding_frontier: Frontier,
                        disposition: ResponseDisposition,
                        reason: str | None = None,
                        waiver_scope: WaiverScope | None = None,
                        waiver_expiry: Timestamp | None = None,
                        evidence_refs: tuple[EvidenceId | ResultId, ...] = ())
RedactionRecordedPayload(target_event_ids: tuple[EventId, ...],
                         target_object_ids: tuple[ObjectId, ...],
                         method: RedactionMethod,
                         reason_category: RedactionReasonCategory,
                         authority: ActorId, remaining_gap: str)
CheckRecordedPayload(mode: CheckMode, policies: tuple[PolicyVersion, ...],
                     scope: CheckScopeModel,
                     policy_executions: tuple[CheckPolicyExecutionModel, ...],
                     subject_frontier: Frontier, verdict: CheckVerdict,
                     returned_finding_ids: tuple[FindingId, ...],
                     suppressed_count: int, coverage: Coverage,
                     semantic_status: SemanticStatus,
                     semantic_reason: SemanticReason,
                     engine_version: str, projection_version: str,
                     semantic_provenance: SemanticProvenance | None = None)
ReceiptRecordedPayload(receipt_id: ReceiptId, subject_frontier: Frontier,
                       receipt_digest: str, receipt_object_id: ObjectId,
                       conclusion_code: ReceiptConclusion,
                       redaction_profile: ReceiptRedactionProfile)
```

The constructor order does not alter canonical JSON key order. Conditional rules in the family
tables remain mandatory in `__post_init__`; defaults are absence representations, not permission
to bypass those rules.

### 1. `SessionOpenedPayload`

| Field | Type | Required | Semantics |
|---|---|---:|---|
| `task_title` | nonempty str ≤ `MAX_TEXT_BYTES` | yes | User content copied losslessly from `StartRequestModel`; lives only in the encrypted payload object owned by `specs/src/yoetz/ports/objects.md` |
| `external_ref` | nonempty str ≤ `MAX_TEXT_BYTES` | no | Raw task identity as supplied to `start`; catalog keeps only its keyed commitment |
| `workspace_ref` | nonempty str ≤ `MAX_TEXT_BYTES` | no | Raw workspace identity; same commitment rule |
| `client_kind` | enum `codex_cli\|cooperative_agent\|yoetz_cli\|test_client\|importer` | yes | From `ClientInfoModel`; provenance only, never an assurance input |
| `client_version` | str ≤ `MAX_LABEL_BYTES` | yes | |
| `integration` | enum `cooperative_mcp\|local_cli\|codex_jsonl_import` | yes | |
| `profile` | enum `strict-local\|local-openai\|test-fake\|release-probe` | yes | Active config profile at open |

The payload schema intentionally permits either optional attachment reference independently.
`specs/src/yoetz/application/start.md` applies its stricter both-or-neither attachment-key rule
before creating a lifecycle draft; the domain codec does not reject otherwise schema-valid imported
history with a hidden cross-field rule.

### 2. `SessionResumedPayload`

`client_kind`, `client_version`, `integration`, `profile` as above, plus
`resumed_frontier: Frontier` — the frontier presented to the resuming client (audit of what
"current" meant at reattach). As required by `specs/src/yoetz/application/start.md`, a resume
never fabricates events from the resumed harness conversation.

### 3. `PlanPublishedPayload`

| Field | Type | Required | Semantics |
|---|---|---:|---|
| `plan_version` | int in `1..9_007_199_254_740_991` (not bool) | yes | Session-scoped plan identity; MUST be exactly `1 +` highest previously accepted plan version (validated pre-append against the projection); there is no `pln_` ID kind |
| `summary` | str ≤ `MAX_TEXT_BYTES` | yes | Human plan summary |
| `obligation_refs` | tuple[ObligationId] ≤ `MAX_REF_LIST` | yes (may be empty) | Obligations this plan declares/covers; usually published in the same atomic batch |
| `scope_exclusions` | tuple[str ≤ `MAX_LABEL_BYTES`] ≤ 16 | no | Explicitly out-of-scope items, so silence is distinguishable from exclusion |

### 4. `ObligationPublishedPayload`

Publish-then-resolve: the first event with a given `obligation_id` declares its immutable work
meaning. A later event with the same ID may repeat those fields byte-equivalently and move only
`status: open → resolved` plus add resolution evidence (there is no separate resolution family).
It may not silently rewrite description, expectation, criteria, requested items, or source refs,
and resolution never reopens. A material scope/acceptance change receives a new obligation ID and
is connected by a `plan_revised` event so history and check identity remain explicit.

| Field | Type | Required | Semantics |
|---|---|---:|---|
| `obligation_id` | ObligationId | yes | Client-generated, stable across retry |
| `description` | str ≤ `MAX_TEXT_BYTES` | yes | e.g. "Preserve `--dry-run` and JSON output exactly where promised" |
| `evidence_expectation` | str ≤ `MAX_TEXT_BYTES` | yes | What would count as completion evidence for this obligation |
| `acceptance_criteria` | str ≤ `MAX_TEXT_BYTES` | no | Sharper machine-facing criteria when available |
| `requested_items` | tuple[RequestedItem] ≤ `MAX_REQUESTED_ITEMS` | no | Explicit URLs/files/commands/changes whose non-attempt is deterministically checkable (finding K2) |
| `source_refs` | tuple[EventId] ≤ `MAX_REF_LIST` | no | Events that motivated the obligation |
| `status` | `ObligationStatus` | yes | `open` or `resolved` |
| `resolution_evidence_refs` | tuple[EvidenceId \| ResultId] ≤ `MAX_REF_LIST` | required iff `status == resolved`, else forbidden | The evidence claimed to resolve it |

`RequestedItem = (item_kind: RequestedItemKind, value: str ≤ 1_024 code points)` with
`RequestedItemKind ∈ {url, file, command, change, source}` (`source` exists for the
research-evidence pack). Values are compared by exact string equality only.

### 5. `AssignmentRecordedPayload`

| Field | Type | Required |
|---|---|---:|
| `assignee_actor_id` | ActorId | yes |
| `obligation_ids` | tuple[ObligationId] ≤ `MAX_REF_LIST` | yes, ≥ 1 |
| `scope_description` | str ≤ `MAX_TEXT_BYTES` | yes |
| `write_policy` | enum `read_only\|writes_allowed` | no |
| `handoff_of` | EventId | no — prior assignment event this supersedes |

The assignment's identity is its envelope `event_id`. Delegation is asserted, not authenticated;
it never upgrades the assignee's future `authorship_assurance`, whose trust boundary is owned by
`specs/INTERFACES.md` and ADR-005.

### 6. `DecisionRecordedPayload`

| Field | Type | Required |
|---|---|---:|
| `statement` | str ≤ `MAX_TEXT_BYTES` | yes — the accepted choice |
| `rationale` | str ≤ `MAX_TEXT_BYTES` | yes |
| `alternatives` | tuple[str ≤ `MAX_TEXT_BYTES`] ≤ `MAX_ALTERNATIVES` | no — rejected options |
| `authority` | ActorId | yes — who accepted (may be a human actor id) |
| `affected_obligation_ids` | tuple[ObligationId] ≤ `MAX_REF_LIST` | no |
| `supersedes_event_id` | EventId | no — earlier decision event this replaces |

### 7. `ActionRecordedPayload`

| Field | Type | Required | Semantics |
|---|---|---:|---|
| `action_id` | ActionId | yes | Client-generated, stable across retry |
| `action_kind` | `ActionKind ∈ {command, edit, research, review, other}` | yes | INTERFACES §7 |
| `description` | str ≤ `MAX_TEXT_BYTES` | yes | |
| `command` | str ≤ `MAX_TEXT_BYTES` | no | Exact command line when `action_kind == command` |
| `subject_state` | SubjectStateRef | no | Repository/artifact state the action started from or produced (an `edit` SHOULD publish the resulting state — this is what makes K6 freshness checkable). A first-party local workflow SHOULD use ADR-011 structural capture when available rather than inventing/describing a digest. |
| `obligation_refs` | tuple[ObligationId] ≤ `MAX_REF_LIST` | no | Obligations this action attempts |
| `attempted_items` | duplicate-free tuple[str ≤ 1_024] ≤ `MAX_REQUESTED_ITEMS` | no | Exact `RequestedItem.value` strings this action attempted (K2 matching is exact-string); input order is preserved because the schema requires uniqueness but does not declare a set sort |

The schema requires `command` when `action_kind == command`; it does not forbid a command string
for another action kind. The codec follows that exact rule and applies no converse not-present
constraint.

### 8. `ResultRecordedPayload`

| Field | Type | Required | Semantics |
|---|---|---:|---|
| `result_id` | ResultId | yes | |
| `action_id` | ActionId | yes | The attempted action this is the outcome of; an unmatched value at check time is finding K5 `result_without_action` |
| `outcome` | `ResultOutcome ∈ {success, failure, partial, unknown}` | yes | |
| `exit_status` | int in −(2^31)..2^31−1 | no | Process exit code when applicable |
| `summary` | str ≤ `MAX_TEXT_BYTES` | no | |
| `subject_state` | SubjectStateRef | no | State the result tested — the freshness anchor used for later state comparison. Material verification SHOULD use the same capture format as adjacent edit/claim state. |
| `evidence_refs` | tuple[EvidenceId] ≤ `MAX_REF_LIST` | no | Captured evidence for this result |

### 9. `EvidenceRecordedPayload`

| Field | Type | Required | Semantics |
|---|---|---:|---|
| `evidence_id` | EvidenceId | yes | |
| `evidence_kind` | `EvidenceKind ∈ {artifact, command_output, test_result, research_source, import_report, other}` | yes | Stable semantic class; `import_report` is reserved for the importer's final ledger evidence |
| `strength` | `EvidenceImmutability` | yes | Self-declared, then clamped: the server MUST NOT accept a declared strength stronger than what the payload proves (see validation below) |
| `reference` | str ≤ 2_048 code points | conditional | Mutable reference (path/URL) |
| `captured_object_id` | ObjectId | conditional | Encrypted captured-content object |
| `content_digest` | str (sha256 form) | conditional | Digest of captured/observed content |
| `description` | str ≤ `MAX_TEXT_BYTES` | no | |
| `observed_at` | Timestamp | yes | When observed (metadata, not order) |
| `subject_state` | SubjectStateRef | no | State the evidence describes; captured structural state does not upgrade the event's actual provenance/coverage. |

Presence validation ties strength to substance: `mutable_reference` requires `reference`;
`metadata_only` requires `description` or `reference`; `content_digest` requires
`content_digest`; `immutable_snapshot` requires `captured_object_id` **and** `content_digest`;
`independently_reproduced` additionally requires `subject_state`. Any declared strength whose
required fields are absent is rejected pre-append (`EVENT_INVALID`,
reason `evidence_strength_unsupported`). Coverage never exceeds observation (binding).

For `evidence_kind=import_report`, strength is exactly `immutable_snapshot`, both
`captured_object_id` and `content_digest` are required, the event is importer-authored on the
`codex_jsonl_import` channel, and its coverage/gaps remain no stronger than the public source.

### 10. `ClaimRecordedPayload`

| Field | Type | Required | Semantics |
|---|---|---:|---|
| `claim_id` | ClaimId | yes | Re-publication with the same `claim_id` supersedes the prior claim body |
| `claim_kind` | `ClaimKind ∈ {completion, material}` | yes | `completion` proposes the final account; `material` is any other checkable assertion |
| `statement` | str ≤ `MAX_TEXT_BYTES` | yes | |
| `supporting_refs` | tuple[EvidenceId \| ResultId \| ObligationId] ≤ `MAX_REF_LIST` | yes (may be empty — emptiness is exactly what K4 catches) | Mixed typed IDs, distinguished by prefix |
| `subject_state` | SubjectStateRef | no | The exact state the claim is about; omission or incomparable formats remain an explicit freshness limitation. |
| `obligation_refs` | tuple[ObligationId] ≤ `MAX_REF_LIST` | no | Obligations the claim asserts satisfied |
| `disputes_refs` | tuple[ClaimId \| EventId] ≤ 16 | no | Explicit contradiction assertion against earlier claims/events (feeds K7) |

### 11. `PlanRevisedPayload`

| Field | Type | Required |
|---|---|---:|
| `plan_version` | int in `2..9_007_199_254_740_991` (not bool) | yes — new version, exactly prior + 1 |
| `supersedes_plan_version` | int in `1..9_007_199_254_740_991` (not bool) | yes — must equal the currently projected plan version |
| `reason` | str ≤ `MAX_REASON_BYTES` | yes |
| `summary` | str ≤ `MAX_TEXT_BYTES` | yes |
| `obligation_changes` | tuple[ObligationChange] ≤ `MAX_REF_LIST` | yes (may be empty) |

`ObligationChange = (obligation_id: ObligationId, change: ObligationChangeKind ∈ {superseded,
waived, carried}, reason: str ≤ MAX_REASON_BYTES | None, replacement_obligation_ids:
tuple[ObligationId] ≤ 8)` — `superseded`/`waived` require a non-empty `reason`;
`replacement_obligation_ids` allowed only with `superseded`. New obligations arrive as
`obligation_published` events in the same atomic batch. This is fixture 5 (legitimate disclosed
revision): a superseded obligation stops counting as open (K1 non-trigger) but stays in history.

### 12. `FindingRecordedPayload`

Engine-authored (`yoetz_engine`). `FindingRecordedPayload` is exactly the alias
`FindingRecordedPayload = Finding`, not a distinct wrapper or field-for-field copy. Its JSON object
therefore has the `Finding` fields directly: `finding_id`, `kind`, `origin`, `priority`, `summary`,
`detail`, `subject_refs`, `policy_id`, `policy_version`, `subject_frontier`, `coverage`, and the
conditional `provenance`. `decode_payload` delegates to `findings.finding_from_json`;
`encode_payload` delegates to `findings.finding_to_json`. The event module owns no second finding
validator or serializer.

### 13. `ResponseRecordedPayload`

| Field | Type | Required |
|---|---|---:|
| `finding_id` | FindingId | yes |
| `finding_frontier` | Frontier | yes — the full frontier at which the finding was shown, as enforced by `specs/src/yoetz/application/respond.md` |
| `disposition` | `ResponseDisposition ∈ {acknowledged, rejected, waived}` | yes |
| `reason` | str, 1..`MAX_REASON_BYTES` | required for `rejected`/`waived`; optional for `acknowledged` |
| `waiver_scope` | `WaiverScope` (`domain/findings.md`) | required iff `waived`, else forbidden |
| `waiver_expiry` | Timestamp | no; only with `waived` |
| `evidence_refs` | tuple[EvidenceId \| ResultId] ≤ `MAX_REF_LIST` | no |

### 14. `RedactionRecordedPayload`

| Field | Type | Required |
|---|---|---:|
| `target_event_ids` | tuple[EventId] ≤ `MAX_REF_LIST` | at least one of the two target lists non-empty |
| `target_object_ids` | tuple[ObjectId] ≤ `MAX_REF_LIST` | " |
| `method` | `RedactionMethod ∈ {logical_redaction, object_deletion}` | yes — v0.1 promises only these (ADR-004 “Recovery and passphrase separation”) |
| `reason_category` | `RedactionReasonCategory ∈ {secret, privacy, retention, legal, other}` | yes |
| `authority` | ActorId | yes |
| `remaining_gap` | str ≤ `MAX_REASON_BYTES` | yes — human statement of what is now unreadable |

Appended only after the target is actually unavailable through normal reads; attempting to record
redaction while the target remains readable is invalid.

### 15. `CheckRecordedPayload`

| Field | Type | Required |
|---|---|---:|
| `mode` | `CheckMode ∈ {deterministic_only, semantic_if_configured, semantic_required}` | yes |
| `policies` | exact nonempty schema tuple: one built-in policy, or `(research-evidence@0.1.0, work-integrity@0.1.0)` | yes |
| `scope` | exact `CheckScopeModel(claim_ids, obligation_ids)` | yes — each tuple is sorted unique; both empty means the whole case |
| `policy_executions` | tuple[`CheckPolicyExecutionModel`] in `1..2` | yes — one exact legal execution per `policies` member, with identical identity/order |
| `subject_frontier` | Frontier | yes — the frozen frontier checked |
| `verdict` | `CheckVerdict` | yes |
| `returned_finding_ids` | tuple[FindingId] ≤ `MAX_FINDINGS_LIMIT` | yes (may be empty) |
| `suppressed_count` | int in `0..9_007_199_254_740_991` (not bool) | yes |
| `coverage` | `Coverage` | yes — exact weakest material coverage carried by the recorded `RankedFindings` |
| `semantic_status` | shared enum `not_requested\|not_configured\|blocked_by_policy\|blocked_forbidden_data\|classification_uncertain\|awaiting_human\|human_denied\|approval_expired\|succeeded\|refused\|timeout\|invalid\|unavailable\|late\|stale\|failed` | yes |
| `semantic_reason` | shared closed `SemanticReason` | yes — must be allowed for `semantic_status` by `protocol/models.md` |
| `semantic_provenance` | `SemanticProvenance | None` | conditional — final receipt-bound attempt provenance only; forbidden for predispatch outcomes |
| `engine_version` | literal `0.1.0` | yes |
| `projection_version` | literal `yoetz/0.1.0` | yes |

`scope` is the normalized request scope durably bound to the check. Each tuple contains at most
`MAX_REF_LIST` matching typed IDs, is unsigned-ASCII sorted and duplicate-free, and is emitted
even when empty. The empty/empty value means all policy-relevant material at
`subject_frontier`; it never means that nothing was checked. A nonempty scope names direct
claim/obligation roots. Dependency material used to evaluate those roots never expands the
recorded direct scope.

`policy_executions` uses the exact closed support-model shape already exposed by the check result:
`(policy_id, policy_version, outcome, reason)`, with legal pairs only
`run/completed`, `skipped/material_unavailable|not_applicable|scope_excluded`, or
`failed/policy_failure`. It has the same length, built-in identity, version, and canonical pack-ID
order as `policies`; an execution cannot be inserted, omitted, reordered, or attributed to another
pack. Policy finding-emission order remains a separate kernel rule and does not alter this
canonical accounting order.

`semantic_provenance` is required for `succeeded`, `refused`, `timeout`, `invalid`, `late`, `stale`,
and unavailable reasons `transport_unavailable`, `provider_rate_limited`, and
`provider_quota_exhausted`. It is forbidden for `not_requested`, `not_configured`, policy/data
blocks, classification uncertainty, waiting/denial/expiry, and unavailable reasons
`credential_unavailable`, `endpoint_profile_unavailable`, `retry_budget_exhausted`,
`audit_reservation_unavailable`, and `receipt_persistence_unknown`; `failed/coordinator_failure`
permits it only when a finalized receipt-bound attempt exists. `semantic_reason` remains sufficient
to explain the no-provenance cases.
When provenance is present, its nested status/reason equals this payload's top-level selected/final
pair. Earlier late/non-selected attempts remain attempt-audit rows only. The complete
status/reason/provenance identity validator is the one owned by `protocol/models.py`; this payload
calls it and adds no broader branch. A one-member policy tuple is exactly either
`PolicyVersion("research-evidence", "0.1.0")` or
`PolicyVersion("work-integrity", "0.1.0")`; the two-member tuple has those entries in that exact
order. No empty selection, other policy identity/version, or other order is schema-valid in the
current v0.1 write contract. Released backward-read artifacts retain their original historical
shape and are translated only by the compatibility reader; they are not rewritten as current
events.

### 16. `ReceiptRecordedPayload`

| Field | Type | Required |
|---|---|---:|
| `receipt_id` | ReceiptId | yes |
| `subject_frontier` | Frontier | yes — the pre-receipt frontier, excluding this event itself, as owned by `specs/src/yoetz/application/receipt.md` |
| `receipt_digest` | str (sha256 form) | yes — canonical digest of the ReceiptDocument |
| `receipt_object_id` | ObjectId | yes — encrypted stored document |
| `conclusion_code` | `ReceiptConclusion` (`domain/receipts.md`) | yes |
| `redaction_profile` | `ReceiptRedactionProfile` | yes |

### `EventDraft`

Client-shaped pre-acceptance value produced from a validated `publish_work` item:

`EventDraft(event_id: EventId, schema: EventSchema, occurred_at: Timestamp,
causal_parents: tuple[EventId] ≤ MAX_CAUSAL_PARENTS (sorted unique),
payload: EventPayload | JsonValue, artifact_refs: tuple[ObjectId] ≤ MAX_REF_LIST,
evidence_refs: tuple[EvidenceId | ResultId] ≤ MAX_REF_LIST)`.

For a known schema, `payload` is the decoded dataclass; for an unknown bounded schema it remains
frozen `JsonValue` and the draft routes to the unknown path. There are no `author_seq` or
`object_refs` draft fields: the writer sequence is ledger-assigned and object refs ride
`artifact_refs`. Note: envelope-level `evidence_refs` are the indexable
reference copy (ledger `event_refs` table); the payload keeps its own typed refs — both must
agree at validation (`EVENT_INVALID`, reason `ref_mirror_mismatch`) for families whose payload
declares `evidence_refs`, including preserving each `evd_` versus `res_` kind.
`EvidenceRecordedPayload.captured_object_id` has an exact mirror:
`artifact_refs == ()` when it is absent and `artifact_refs == (captured_object_id,)` when it is
present. For `redaction_recorded`, `artifact_refs` is exactly `target_object_ids`. These closed
mirrors make object-only redaction replay possible after either object is deleted and prevent an
unrelated artifact ref from being mistaken for captured evidence. For `receipt_recorded`,
`artifact_refs` is exactly `(receipt_object_id,)`; the encrypted receipt document cannot be omitted,
reordered, or accompanied by an unrelated object.

### `AcceptedEvent`

Frozen accepted envelope plus a nonserializable decoded handle. Its exact fields are:

```text
protocol: Literal["yoetz.event"]                 # init=False
protocol_version: Literal["0.1"]                 # init=False
event_id: EventId
task_id: TaskId
session_id: SessionId
schema: EventSchema
author: Actor
writer: WriterChain
ledger: LedgerChain
operation_id: RequestId
occurred_at: Timestamp
causal_parents: tuple[EventId, ...]
publication_channel: PublicationChannel
coverage: Coverage
payload_ref: PayloadRef
redaction: RedactionState
artifact_refs: tuple[ObjectId, ...]
evidence_refs: tuple[EvidenceId | ResultId, ...]
entry_digest: str
payload: EventPayload | None
projection_locator: ProjectionLocator
```

`payload` is `None` exactly when `redaction != present` or the object is unavailable; reducers then
use the locator to materialize the fixture-owned structural tombstone and never guess content.
`projection_locator` is required even while the payload is readable; when both are present its
schema, logical key, redaction targets, and payload digest must recompute exactly. Missing or
mismatched durable metadata is corruption, not a coverage gap. The full accepted-record JSON
excludes both runtime-only fields but includes `entry_digest`, matching
`schemas/events/accepted-event-1.0.0.schema.json` exactly.

The replay association is therefore exact without widening that wire shape:
`payload_ref.object_id -> event_id` for every accepted record; only for an exact-known
`evidence_recorded` record, `artifact_refs[0] -> (logical evidence_id, source event_id)`; and only
for an exact-known `receipt_recorded` record, `artifact_refs[0] -> source receipt event`. Payload
object IDs are unique within a bundle; reusing one for two accepted payload envelopes is
corruption. A captured object may support multiple evidence events, so that reverse association is
a sorted tuple rather than a guessed singleton; a receipt document mirror is the exact singleton
owned by its one receipt event.

There are two deliberately distinct views:

- `accepted_record_to_json(record) -> JsonObject` emits all 19 schema fields, including
  `entry_digest`. It emits writer and ledger sequences as decimal strings and
  `payload_ref.plaintext_size` as a JSON integer.
- `accepted_record_digest_preimage(record) -> JsonObject` returns that exact object with only the
  top-level `entry_digest` member removed. It also never contains a decoded payload handle. Those
  bytes are the sole input to `protocol.canonical.entry_digest`.

The full record is therefore not itself the digest preimage. `entry_digest` is stored in the full
record and beside the preimage identity; it is never recursively hashed into itself. The old
ambiguous `canonical_envelope()` name must not be implemented as a third view.

### `UnknownEvent`

`UnknownEvent` repeats every accepted-envelope and runtime-only field above with the same types,
including `projection_locator`, replaces the typed handle with `payload: JsonValue | None`, and
adds exactly
`canonical_payload_digest: str` plus
`projection_status: Literal["unknown_unprojected"]` (`init=False`). Thus it also retains
`causal_parents`, `redaction`, `artifact_refs`, and `evidence_refs`; unknown events cannot lose
structural ancestry or references merely because their payload is opaque.

The canonical payload digest commits to the canonical plaintext payload bytes and remains present
even when a later redaction makes the handle unavailable. It equals the locator digest; the
unknown locator's logical key is `None` and both target tuples are empty. The raw frozen payload
handle, locator, and two unknown-only fields are adjacent runtime/storage metadata, not members of
the accepted-event record or its digest preimage. `accepted_record_to_json` accepts either
`LedgerRecord` variant and emits the same closed schema shape.

An unknown record is stored, replayed, counted, and surfaces as a coverage gap. Reducers may retain
its schema identity, digest, and structural refs, but never inspect its payload or strengthen any
projection from it.

### `decode_payload` / `encode_payload`

`decode_payload` looks up the complete `EventSchema` key in `PAYLOAD_TYPES`. Only the sixteen exact
`(registered_name, "1.0.0")` pairs decode in v0.1. Every other syntactically valid pair raises
`ProtocolValueError("unknown_event_schema")`, and the caller takes the opaque path. This includes
an unregistered name and a registered name at `0.9.0`, `1.0.1`, `1.1.0`, or `2.0.0`; there is no
same-major tolerance or nearest-version coercion. This is exactly the split frozen by the known
branches of `event-draft-1.0.0` and the complement in
`opaque-unknown-event-draft-1.0.0`.

Once the pair is recognized, any missing, extra, or invalid payload field is a known-payload
validation failure and must not be converted into an unknown event. `encode_payload` dispatches on
the exact concrete payload type (with the explicit `Finding` alias) and emits that family's closed
object. Required collection fields are always emitted, even when empty. Optional scalar members
whose value is `None` and optional collection members whose value is the empty tuple are omitted;
present nondefault optional members are emitted. Thus schema-valid `{optional_list: []}` and an
otherwise identical object with that optional member absent decode to the same domain value and
normalize to the absent form.
Field decoding converts via `domain/values` constructors; every failure is the shared
`ProtocolValueError(reason_code)` with the offending bounded reason code only. The exception never
retains an input-derived field path or payload fragment; any later boundary-safe location detail is
owned by the operation boundary rather than this domain codec.
`normalize_payload_json(schema, x)` is defined exactly as
`encode_payload(decode_payload(schema, x))`; it is idempotent, and canonical encoding of the result
is the codec identity used for payload digests. For every domain-valid JSON value `x`, decoding its
normalized form and encoding again is byte-identical. Arbitrary schema-valid input need not be
byte-identical before normalization because JSON Schema deliberately treats absent and empty
optional arrays as equivalent inputs.

## Errors and edge cases

The exact `ProtocolValueError` reasons first raised by this module are:
`invalid_event_value_type`, `event_text_out_of_bounds`, `event_integer_out_of_range`,
`invalid_event_enum`, `invalid_event_schema`, `invalid_chain`, `invalid_payload_ref`,
`unknown_payload_field`, `missing_payload_field`, `unsorted_set_field`,
`duplicate_set_member`, `obligation_resolution_invalid`,
`evidence_strength_unsupported`, `import_report_invalid`, `obligation_change_invalid`,
`response_fields_invalid`, `redaction_target_required`, `unknown_event_schema`,
`unsupported_payload_type`, `payload_redaction_mismatch`, `entry_digest_mismatch`,
`accepted_record_shape_invalid`, `invalid_projection_locator`, and `ref_mirror_mismatch`. This inventory is immutable and must
be present in `protocol.errors.PROTOCOL_REASON_CODES`; adding a new local reason lands in that
registry and this list in the same change. ID/digest/timestamp/frontier/coverage/finding/provenance
validation propagates the reason owned by its imported module and is not rewrapped.

- All validation failures map to public `EVENT_INVALID` at the operation boundary; one invalid
  known event rejects the whole `publish_work` batch atomically, as owned by
  `specs/src/yoetz/application/publish_work.md`.
- Duplicate `event_id` within a batch or against the ledger: rejected pre-append.
- `causal_parents` referencing events not yet accepted in the same task: rejected pre-append;
  parents must precede children, so reducers never see dangling parents.
- Payload-level cross-event references (`action_id`, `obligation_id`, claim `supporting_refs`)
  are **not** required to resolve at append time (different writers may interleave); dangling
  references are deterministic check material (K5), not append errors.
- `plan_version` continuity violations (`plan_published` not `+1`, `plan_revised` superseding a
  non-current version): rejected pre-append with `EVENT_INVALID` reason `plan_version_conflict`.
- Operation/channel family admission is owned by `application/publish_work.md`, not this payload
  codec. A known payload may be valid domain history while still being forbidden through the public
  operation that presented it; no caller-selected author or integration widens that authority.
- Redacted payloads (`payload is None`) are legal in either ledger-record variant; anything that
  needs the payload must degrade coverage instead of failing.
- A supported exact schema pair with an invalid payload is `EVENT_INVALID`, never an opaque
  fallback. Only an unsupported but syntactically valid pair takes the unknown path.
- Hashing a full accepted record that still contains `entry_digest` is rejected; callers must pass
  the explicit digest-preimage view.

## Invariants

1. Accepted events are immutable; corrections append (binding invariant 1).
2. No plaintext payload field ever appears in either accepted-record JSON view — only
   `PayloadRef` metadata.
3. `event_id` names a logical event; `payload_ref.commitment` names accepted payload bytes;
   `entry_digest` names the accepted envelope; the three are never conflated.
4. Unknown events are preserved opaque, never interpreted, and always visible as a coverage gap.
5. Set-valued fields have exactly one canonical byte rendering.
6. Timestamps in payloads/envelopes are metadata; nothing in this file orders by them.
7. Full-record serialization includes `entry_digest`; digest-preimage serialization excludes that
   one field. No other structural field differs between the two.

## Tests

- `tests/unit/domain/test_event_payloads.py` — per-family accept/reject tables (every field,
  bound, conditional-presence rule; the evidence strength↔substance matrix).
- `tests/unit/domain/test_event_payloads.py` — family validation plus Hypothesis encode/decode
  byte-stability under varied hash seeds.
- `fixtures/canonical/` — accepted-envelope and entry-digest golden vectors (ADR-002 §7).
- `fixtures/replay/` — one fixture stream exercising all 16 families plus unknown events.
- `tests/conformance/protocol/test_unknown_events.py` — unknown preserved/quarantined behavior and the
  warning surfaced by `publish_work`.

## Open questions

None.
