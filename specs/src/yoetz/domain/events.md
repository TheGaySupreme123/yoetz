# src/yoetz/domain/events.py — the 16 event families, drafts, and accepted envelopes

**Wave:** B | **ADRs:** ADR-002, ADR-004 | **Imports (spec-tree):** `domain/values.md`,
`domain/findings.md` (payload mirror for `finding_recorded`),
`domain/receipts.md` (`ReceiptConclusion` only), `protocol/errors.md`,
`protocol/coverage.md` | **Imported by:** `kernel/reducers.md`, `kernel/projections.md`,
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
- Payload dataclasses, all `@dataclass(frozen=True, slots=True)`, named `<FamilyPascal>Payload`:
  `SessionOpenedPayload`, `SessionResumedPayload`, `PlanPublishedPayload`,
  `ObligationPublishedPayload`, `AssignmentRecordedPayload`, `DecisionRecordedPayload`,
  `ActionRecordedPayload`, `ResultRecordedPayload`, `EvidenceRecordedPayload`,
  `ClaimRecordedPayload`, `PlanRevisedPayload`, `FindingRecordedPayload`,
  `ResponseRecordedPayload`, `RedactionRecordedPayload`, `CheckRecordedPayload`,
  `ReceiptRecordedPayload`.
- Support types: `RequestedItem`, `RequestedItemKind`, `ObligationStatus`, `ObligationChange`,
  `ObligationChangeKind`, `ActionKind`, `ResultOutcome`, `ClaimKind`, `RedactionMethod`,
  `RedactionReasonCategory`, `CheckMode`, `EventSchema(name, version)`, `PayloadRef`,
  `RedactionState`.
- `EventPayload` — union alias of the 16 payload dataclasses.
- `PAYLOAD_TYPES: Mapping[str, type[EventPayload]]` — schema name → dataclass for version `1.0.0`.
- `decode_payload(schema: EventSchema, payload: JsonValue) -> EventPayload` — validating
  constructor from frozen JSON.
- `encode_payload(payload: EventPayload) -> JsonValue` — exact inverse; feeds the canonicalizer.
- `media_type_for(schema_name: str) -> str` — `"application/vnd.yoetz." + schema_name + "+json"`.
- `EventDraft` — frozen client-shaped pre-acceptance event.
- `AcceptedEvent` — frozen structural envelope + decoded payload handle.
- `UnknownEvent` — frozen opaque preserved event, `projection_status = "unknown_unprojected"`.
- `LedgerRecord` — union alias `AcceptedEvent | UnknownEvent` (what reducers consume).

Field-size constants (frozen protocol constants registered in `specs/INTERFACES.md`):
`MAX_TEXT_BYTES = 8_192` (descriptions/statements/summaries), `MAX_REASON_BYTES = 4_096`
(INTERFACES §3), `MAX_LABEL_BYTES = 256` (titles/short labels), `MAX_REF_LIST = 64`,
`MAX_CAUSAL_PARENTS = 32`, `MAX_REQUESTED_ITEMS = 64`, `MAX_ALTERNATIVES = 16`. All text bounds
are measured in UTF-8 bytes.

## Behavior

### Shared conventions

- Every dataclass validates in `__post_init__` via `domain/values` constructors and raises
  `ProtocolValueError` with a bounded reason code; no partial construction.
- All ID-list fields are tuples, sorted by unsigned ASCII byte order, duplicate-free
  (`ProtocolValueError("unsorted_set_field")` / `("duplicate_set_member")`) — matching the
  canonical set profile in `specs/src/yoetz/protocol/canonical.md` so
  `encode_payload ∘ decode_payload` is byte-stable.
- Optional fields are `None`-defaulted; `decode_payload` rejects unknown JSON keys
  (`ProtocolValueError("unknown_payload_field")`) and missing required keys
  (`("missing_payload_field")`).
- Free-text fields never carry semantics for deterministic checks; only IDs, enums, digests,
  and `SubjectStateRef` values do.

### 1. `SessionOpenedPayload`

| Field | Type | Required | Semantics |
|---|---|---:|---|
| `task_title` | str ≤ `MAX_LABEL_BYTES` | yes | User content; lives only in the encrypted payload object owned by `specs/src/yoetz/ports/objects.md` |
| `external_ref` | str ≤ `MAX_LABEL_BYTES` | no | Raw task identity as supplied to `start`; catalog keeps only its keyed commitment |
| `workspace_ref` | str ≤ `MAX_LABEL_BYTES` | no | Raw workspace identity; same commitment rule |
| `client_kind` | enum `codex_cli\|cooperative_agent\|yoetz_cli\|test_client\|importer` | yes | From `ClientInfoModel`; provenance only, never an assurance input |
| `client_version` | str ≤ `MAX_LABEL_BYTES` | yes | |
| `integration` | enum `cooperative_mcp\|local_cli\|codex_jsonl_import` | yes | |
| `profile` | enum `strict-local\|local-openai\|test-fake\|release-probe` | yes | Active config profile at open |

The both-or-neither rule for `external_ref`/`workspace_ref` is shared with
`specs/src/yoetz/ports/start_catalog.md`;
violation → `ProtocolValueError("attachment_key_incomplete")`. Authored by `yoetz_engine` as part
of the lifecycle-event phase in `specs/src/yoetz/application/start.md`.

### 2. `SessionResumedPayload`

`client_kind`, `client_version`, `integration`, `profile` as above, plus
`resumed_frontier: Frontier` — the frontier presented to the resuming client (audit of what
"current" meant at reattach). As required by `specs/src/yoetz/application/start.md`, a resume
never fabricates events from the resumed harness conversation.

### 3. `PlanPublishedPayload`

| Field | Type | Required | Semantics |
|---|---|---:|---|
| `plan_version` | int ≥ 1 | yes | Session-scoped plan identity; MUST be exactly `1 +` highest previously accepted plan version (validated pre-append against the projection); there is no `pln_` ID kind |
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

`RequestedItem = (item_kind: RequestedItemKind, value: str ≤ 1_024 bytes)` with
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
| `attempted_items` | tuple[str ≤ 1_024] ≤ `MAX_REQUESTED_ITEMS` | no | Exact `RequestedItem.value` strings this action attempted (K2 matching is exact-string) |

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
| `reference` | str ≤ 2_048 bytes | conditional | Mutable reference (path/URL) |
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
| `plan_version` | int ≥ 2 | yes — new version, exactly prior + 1 |
| `supersedes_plan_version` | int ≥ 1 | yes — must equal the currently projected plan version |
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

Engine-authored (`yoetz_engine`). Mirrors the `Finding` dataclass field-for-field
(`domain/findings.md`): `finding_id`, `kind`, `origin`, `priority`, `summary`, `detail`,
`subject_refs`, `policy_id`, `policy_version`, `subject_frontier` (Frontier), `coverage`
(Coverage), `provenance` (SemanticProvenance | None — required iff
`origin == semantic_model_derived`, forbidden otherwise). `decode_payload` delegates to
`findings.finding_from_json`; `encode_payload` to `findings.finding_to_json` so there is exactly
one serialization of a finding.

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
| `policies` | tuple[(policy_id: str, policy_version: str)] ≤ 8, sorted by policy_id | yes |
| `subject_frontier` | Frontier | yes — the frozen frontier checked |
| `verdict` | `CheckVerdict` | yes |
| `returned_finding_ids` | tuple[FindingId] ≤ `MAX_FINDINGS_LIMIT` | yes (may be empty) |
| `suppressed_count` | int ≥ 0 | yes |
| `coverage` | `Coverage` | yes — exact weakest material coverage carried by the recorded `RankedFindings` |
| `semantic_status` | shared enum `not_requested\|not_configured\|blocked_by_policy\|blocked_forbidden_data\|classification_uncertain\|awaiting_human\|human_denied\|approval_expired\|succeeded\|refused\|timeout\|invalid\|unavailable\|late\|stale\|failed` | yes |
| `semantic_reason` | shared closed `SemanticReason` | yes — must be one of the reasons allowed for `semantic_status` by `ports/semantic.md` |
| `semantic_provenance` | `SemanticProvenance | None` | conditional — final receipt-bound attempt provenance only; forbidden for predispatch outcomes |
| `engine_version` | str | yes |
| `projection_version` | str (`yoetz/0.1.0`) | yes |

`semantic_provenance` is required for `succeeded` and for any provider/local-model attempt whose
terminal receipt is available; it is forbidden for `not_requested`, `not_configured`, policy/data
blocks, classification uncertainty, waiting/denial/expiry, audit-reservation failure, and any
other predispatch outcome. `semantic_reason` remains sufficient to explain those no-attempt cases.

### 16. `ReceiptRecordedPayload`

| Field | Type | Required |
|---|---|---:|
| `receipt_id` | ReceiptId | yes |
| `subject_frontier` | Frontier | yes — the pre-receipt frontier, excluding this event itself, as owned by `specs/src/yoetz/application/receipt.md` |
| `receipt_digest` | str (sha256 form) | yes — canonical digest of the ReceiptDocument |
| `receipt_object_id` | ObjectId | yes — encrypted stored document |
| `conclusion_code` | `ReceiptConclusion` (`domain/receipts.md`) | yes |
| `redaction_profile` | enum `full_local\|default_local_export\|redacted_share` | yes |

### `EventDraft`

Client-shaped pre-acceptance value produced from a validated `publish_work` item:

`EventDraft(event_id: EventId, schema: EventSchema, occurred_at: Timestamp,
causal_parents: tuple[EventId] ≤ MAX_CAUSAL_PARENTS (sorted unique),
payload: EventPayload | JsonValue, artifact_refs: tuple[ObjectId] ≤ MAX_REF_LIST,
evidence_refs: tuple[EvidenceId] ≤ MAX_REF_LIST)`.

For a known schema, `payload` is the decoded dataclass; for an unknown bounded schema it remains
frozen `JsonValue` and the draft routes to the unknown path. There are no `author_seq` or
`object_refs` draft fields: the writer sequence is ledger-assigned and object refs ride
`artifact_refs`. Note: envelope-level `evidence_refs` are the indexable
reference copy (ledger `event_refs` table); the payload keeps its own typed refs — both must
agree at validation (`EVENT_INVALID`, reason `ref_mirror_mismatch`) for families whose payload
declares `evidence_refs`.

### `AcceptedEvent`

Frozen structural envelope defined by the public interface registry and this owning file, plus the
decoded handle:

`protocol = "yoetz.event"`, `protocol_version = "0.1"`, `event_id`, `task_id`, `session_id`,
`schema: EventSchema`, `author: Actor` (assurance server-constrained), `writer: WriterChain
(writer_id, sequence: int, previous_entry_digest)`, `ledger: LedgerChain (ingestion_sequence:
int, previous_entry_digest, accepted_at: Timestamp)`, `operation_id`, `occurred_at`,
`causal_parents`, `publication_channel: PublicationChannel`, `coverage: Coverage`,
`payload_ref: PayloadRef (object_id, media_type, plaintext_size: int, commitment,
encryption_format = "yoetz-object/1")`, `redaction: RedactionState ∈ {present,
logically_redacted, key_unavailable, erased_claimed}`, `artifact_refs`, `evidence_refs`,
`entry_digest: str` (stored beside, never inside, the hashed bytes), and
`payload: EventPayload | None` — the decoded handle; `None` exactly when
`redaction != present` or the object is unavailable, in which case reducers must treat the event
as a payload gap, never guess content.

`AcceptedEvent.canonical_envelope() -> JsonValue` renders the structural object registered in
`specs/INTERFACES.md` (without
`entry_digest`, without `payload`) whose JCS bytes are what `entry_digest` commits to. First
event predecessor digests are the literal `"genesis"`.

### `UnknownEvent`

`UnknownEvent(event_id, task_id, session_id, schema: EventSchema (the declared unknown
name/version), author, writer, ledger, operation_id, occurred_at, publication_channel, coverage,
payload_ref, canonical_payload_digest: str, projection_status: Literal["unknown_unprojected"],
entry_digest)`. Constructed for (a) a structurally valid, bounded draft whose schema name is not
in `EVENT_FAMILIES`, or (b) a known name with an unknown (higher-major) version. It is stored,
replayed, counted, and surfaces as a coverage gap; it is never coerced into the nearest known
type and never strengthens any projection. `specs/src/yoetz/protocol/schemas.md` owns the
schema/version routing boundary.

### `decode_payload` / `encode_payload`

`decode_payload` looks up `PAYLOAD_TYPES[schema.name]`; unknown name or version-major mismatch
raises `ProtocolValueError("unknown_event_schema")` (the caller then takes the UnknownEvent
path); a known name with same-major/higher-minor version decodes with unknown-field tolerance
disabled in v0.1 (only `1.0.0` exists — forward-minor policy is an ADR-002 evolution rule).
Field decoding converts via `domain/values` constructors; every failure is `ProtocolValueError`
with the offending bounded reason code and JSON-pointer-style field path built from schema
constants only (never input text). `encode_payload(decode_payload(x)) == x` byte-for-byte after
canonicalization for every valid input — a frozen property test.

## Errors and edge cases

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
- A `FindingRecordedPayload` or `CheckRecordedPayload` authored by anyone other than
  `yoetz_engine` via cooperative channels is rejected (`EVENT_INVALID`,
  `engine_family_wrong_author`); import channels may carry them as observations with importer
  authorship and `import_observed` channel.
- Redacted payloads (`payload is None`) are legal `AcceptedEvent`s; anything that needs the
  payload must degrade coverage instead of failing.

## Invariants

1. Accepted events are immutable; corrections append (binding invariant 1).
2. No plaintext payload field ever appears in the structural envelope or `canonical_envelope()`
   output — only `PayloadRef` metadata.
3. `event_id` names a logical event; `payload_ref.commitment` names accepted payload bytes;
   `entry_digest` names the accepted envelope; the three are never conflated.
4. Unknown events are preserved opaque, never interpreted, and always visible as a coverage gap.
5. Set-valued fields have exactly one canonical byte rendering.
6. Timestamps in payloads/envelopes are metadata; nothing in this file orders by them.

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
