# src/yoetz/protocol/models.py — shared protocol constants, boundary models, and envelopes

**Wave:** A/B | **ADRs:** ADR-002, ADR-005, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
`protocol/errors.md`, `protocol/ids.md`, `protocol/canonical.md`, `protocol/coverage.md`,
`domain/values.md`
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
| `GENESIS_PREDECESSOR_DIGEST` | `str = "genesis"` |
| `ActorType` | enum of public actor kinds |
| `PublicationChannel` | enum of observed publication channels |
| `JsonValue` | recursive alias of strict JSON-compatible values |
| `PublicEnvelopeModel` | shared request/result envelope base with schema and request identity |
| `PublicRequestModel` | abstract base for operation requests |
| `PublicResultModel` | abstract base for operation results |
| `OmittedContentModel` | exact field-level marker for content blocked by local-disclosure policy |
| `PrivacyProjectionModel` | receipt-bound metadata attached to every ordinary projected result |
| `ActorAssertionModel` | caller-asserted actor boundary model |
| `ClientInfoModel` | caller-asserted client boundary model |
| `StartRequestModel` / `StartResultModel` | public models for the `start` operation |
| `PublishWorkRequestModel` / `PublishWorkResultModel` | public models for `publish_work` |
| `CheckRequestModel` / `CheckResultModel` | public models for `check` |
| `RespondRequestModel` / `RespondResultModel` | public models for `respond` |
| `StatusRequestModel` / `StatusResultModel` | public models for `status` |
| `ReceiptRequestModel` / `ReceiptResultModel` | public models for `receipt` |
| `Timestamp` helpers | parse/format helpers for RFC 3339 UTC with three fractional digits |

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

`ActorType` is the boundary-facing actor classification used in request envelopes and imported
observations. It must stay broad enough to represent humans, harnesses, subagents, imported runs,
and Yoetz-engine-authored internal events, but it must not pretend to establish authentication by
itself.

`PublicationChannel` names how an event or observation entered the ledger. The ordering and
specific members are owned by `protocol/coverage.md`; this file only exposes the boundary model
needed by request and event envelopes.

`JsonValue` is the strict recursive JSON alias used by boundary models and canonicalization. It
includes only JSON primitives, arrays, and objects with string keys. It excludes Python-specific
containers, datetimes, decimals, and any non-JSON coercion path.

`PublicEnvelopeModel` is the minimal strict envelope shared by all request/result models. It
requires:

- a `schema_version` string;
- a stable `request_id` when the operation uses one;
- strict extra-field rejection;
- bounded primitive content only;
- no hidden fallback to environment state.

`PublicRequestModel` and `PublicResultModel` are the abstract Pydantic boundary shapes that the
CLI and MCP adapters convert into and out of domain values. The operation-specific subclasses add
the fields for one public workflow operation at a time, but all of them must preserve the same
strict boundary rules. They enforce:

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
  closed `semantic_status`/`semantic_reason` pair, and optional receipt-finalized semantic
  provenance; a predispatch outcome always has null/absent provenance;
- `RespondRequestModel` / `RespondResultModel` carry one response to one finding;
- `StatusRequestModel` / `StatusResultModel` carry the read-only projection query and page result;
- `ReceiptRequestModel` / `ReceiptResultModel` carry the frozen-frontier receipt query and the
  canonical receipt payload.

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
compatibility, and receipt wording, not authorization.

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

`trusted_human_control` projections use the same model only on the confidential foreground control
protocol; ordinary operation schemas accept `agent_context` only. The registry enumerates every
content-bearing leaf for all six operations and every support result. Adding a result field without
an explicit `public_structural` or `DataCategory` classification is a release-blocking schema
error and prevents serialization at runtime.

### Frozen result-field registry and projection bounds

`RESULT_FIELD_REGISTRY` is an executable closed registry in this future file. It matches exact
method + JSON Pointer patterns; `*` matches one array index and `**` is forbidden. A pointer is NFC,
RFC 6901 escaped, begins with `/`, is at most 256 UTF-8 bytes, and is evaluated against the already
validated result tree. One leaf must match exactly one entry. Precedence is exact pointer, then one-
index pattern; overlap, absence, or a new schema path fails generation/tests and the whole
projection with bounded `privacy_projection_unavailable` before any content serialization. There
is no category guess or omission marker for an unknown field.

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
| `status` obligations view | `/page/items/*/description`, `/page/items/*/evidence_expectation`, `/page/items/*/acceptance_criteria` | `obligation_text` |
| `status` decisions view | `/page/items/*/statement`, `/page/items/*/rationale`, `/page/items/*/alternatives/*` | `decision_excerpt` |
| `status` actions/results view | `/page/items/*/description`, `/page/items/*/command`, `/page/items/*/summary` | `command_metadata` |
| `status` evidence view | `/page/items/*/description`, `/page/items/*/reference` | `evidence_excerpt` |
| `status` claims view | `/page/items/*/statement` | `claim_text` |
| `status` findings/responses view | `/page/items/*/summary`, `/page/items/*/detail`, `/page/items/*/reason` | `finding_summary` |
| `receipt` | `/document/task/title` | `task_description` |
| `receipt` | `/document/obligations/*/description`, `/document/obligations/*/evidence_expectation`, `/document/obligations/*/acceptance_criteria` | `obligation_text` |
| `receipt` | `/document/claims/*/statement` | `claim_text` |
| `receipt` | `/document/decisions/*/statement`, `/document/decisions/*/rationale`, `/document/decisions/*/alternatives/*` | `decision_excerpt` |
| `receipt` | `/document/evidence/*/description`, `/document/evidence/*/reference` | `evidence_excerpt` |
| `receipt` | `/document/findings/*/summary`, `/document/findings/*/detail`, `/document/responses/*/reason`, `/human_text` | `finding_summary` |
| `review` | `/check_result/findings/*/summary`, `/check_result/findings/*/detail` | `finding_summary` |
| `integration_preview` | `/file_changes/*/relative_path`, `/file_states/*/relative_path` | `repository_excerpt` |
| `integration_execute` | `/changed_files/*` | `repository_excerpt` |
| all other current support results | none; their schemas contain structural IDs/codes/counts/digests only | — |

Each result schema and view discriminator makes these patterns unambiguous. For example,
`/page/items/*/description` is registered separately under the exact status view; it is not a
global field-name heuristic. `Coverage.known_gaps`, warning arrays, and failure classes are closed
codes and structural; any future free-form warning is a new content field and needs a registry
entry.

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

`CheckResultModel` validates the status/reason matrix from `ports/semantic.md`. A
`semantic_required` gap is an ordinary successful result envelope with
`verdict=incomplete_check`, preserved deterministic findings, no semantic findings, and the exact
reason code. The model never asks a client to infer semantic incompleteness from a generic warning
or coverage string.

Timestamp helpers emit and parse RFC 3339 UTC with exactly three fractional digits and a trailing
`Z`. The implementation must reject locale-specific formats, offsets other than UTC, leap-second
spellings, and any lossy coercion that would alter the exact string round trip.

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
