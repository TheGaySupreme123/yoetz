# src/yoetz/protocol/errors.py — public error codes and bounded protocol errors

**Wave:** A/B | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):** none |
**Imported by:** `protocol/models.md`, `protocol/canonical.md`, `protocol/coverage.md`,
`cli/exits.md`, `mcp/errors.md`, `application/service.md`, `adapters/sqlite/*`,
`adapters/providers/*`, and nearly every public adapter and application module

## Purpose

This file defines the public error vocabulary that the rest of the system is allowed to surface.
It is the narrow boundary between internal failures and user-visible failures. If the code here is
too loose, every other layer starts leaking implementation details into CLI output, MCP errors,
logs, and receipts.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `PublicErrorCode` | `class PublicErrorCode(str, Enum)` with the 22 public error codes in `specs/INTERFACES.md` |
| `SafeDetailValue` | exact type alias `str | int`; `bool` is excluded despite being an `int` subclass |
| `PROTOCOL_REASON_CODES` | immutable closed set of the Wave B protocol/domain reason codes listed below |
| `SAFE_DETAIL_KEYS` | immutable closed tuple of safe-detail keys listed below |
| `PublicOperationError` | frozen, slotted exception/dataclass with exact constructor `(code, message, retryable, correlation_id=None, safe_details=None)` |
| `ProtocolValueError` | internal-only value error with a bounded `reason_code: str` |
| `normalize_safe_details(value: object) -> Mapping[str, SafeDetailValue]` | total fail-closed normalizer defined below |

## Behavior

`PublicErrorCode` is the only public code enum. It is declared exactly as
`class PublicErrorCode(str, Enum)`, with explicit string values equal to the member spellings. It
is not a `StrEnum` and does not use `auto()`. Its members and order are fixed by the shared interface
registry:

- `INVALID_REQUEST`
- `PROTOCOL_VERSION_UNSUPPORTED`
- `SESSION_NOT_FOUND`
- `SESSION_CONFLICT`
- `IDEMPOTENCY_CONFLICT`
- `OPERATION_PENDING`
- `FRONTIER_CONFLICT`
- `EVENT_INVALID`
- `LIMIT_EXCEEDED`
- `BUNDLE_BUSY`
- `STORAGE_UNSAFE`
- `STORAGE_CORRUPT`
- `MIGRATION_REQUIRED`
- `SERVICE_UNAVAILABLE`
- `VAULT_LOCKED`
- `PRIVACY_AUTHORITY_REQUIRED`
- `PROVIDER_UNAVAILABLE`
- `PROVIDER_REFUSED`
- `PROVIDER_TIMEOUT`
- `SEMANTIC_RESULT_INVALID`
- `CANCELLED`
- `INTERNAL_ERROR`

`SafeDetailValue` is the exact public type alias `str | int`. Because `bool` is an `int` subclass in
Python, every runtime integer check additionally requires `not isinstance(value, bool)`; no
`SafeDetailValue` position accepts `bool` in v0.1.

`PublicOperationError` is the structured failure object used across application, CLI, and MCP. It
is an `Exception` subclass declared as a frozen, slotted dataclass value. Its constructor signature
is exactly
`PublicOperationError(code: PublicErrorCode, message: str, retryable: bool,
correlation_id: str | None = None, safe_details: object | None = None)`. The stored field order is
`code`, `message`, `retryable`, `correlation_id`, `safe_details`; after construction the final field
is a `Mapping[str, SafeDetailValue]`, never the original input object. It must:

- keep the public code explicit;
- keep the message short, safe, and user-facing;
- record whether the caller may retry without changing the request;
- carry a generated correlation ID when the failure reaches a boundary that logs it;
- expose only bounded safe details, never raw payloads, SQL, filesystem paths outside the safe
  diagnostic view, provider output, or secrets.

After all constructor validation and normalization succeeds, `Exception.args` is exactly
`(message,)` and `str(error) == message`. Its deterministic dataclass representation uses the stored
field order above and contains only the validated code, safe message, exact boolean, validated or
null correlation ID, and already-normalized immutable safe-details mapping. It never retains or
renders the original `safe_details` object, an omitted unknown key, or a rejected value.

`ProtocolValueError` is not a user-facing error. It is raised by strict protocol parsers,
canonicalizers, and validators when something is structurally invalid but should not yet be mapped
to a public operation code.

### Closed protocol reason registry

`PROTOCOL_REASON_CODES` is an immutable `frozenset[str]`. The B0 dependency-root module declares
the complete Wave B set below before its consumers import; spelling and membership are API
contracts:

```text
accepted_record_shape_invalid
actor_id_malformed
actor_id_not_generated
attachment_key_incomplete
byte_order_mark_forbidden
commitment_only_object_kind
dependency_changed
duplicate_object_key
duplicate_set_member
empty_check_types
empty_publication_channels
empty_subject_state
engine_family_wrong_author
entry_digest_mismatch
event_integer_out_of_range
event_text_out_of_bounds
evidence_strength_unsupported
finding_json_shape_invalid
finding_priority_mismatch
float_forbidden
frontier_changed
frontier_digest_mismatch
id_malformed_uuid
id_not_ascii
id_uuid_not_version_4
id_uuid_wrong_variant
id_wrong_length
id_wrong_prefix
id_wrong_type
import_report_invalid
input_not_bytes
integer_out_of_safe_range
integer_out_of_sqlite_range
invalid_chain
invalid_check_types
invalid_commitment
invalid_cost_fields
invalid_coverage_value
invalid_digest
invalid_duration
invalid_event_enum
invalid_event_schema
invalid_event_value_type
invalid_finding_kind
invalid_finding_origin
invalid_finding_policy_identity
invalid_finding_provenance
invalid_finding_subject_refs
invalid_frontier
invalid_json_pointer
invalid_known_gap
invalid_payload_ref
invalid_projection_locator
invalid_publication_channels
invalid_ranked_findings
invalid_receipt_conclusion
invalid_receipt_document
invalid_receipt_gap
invalid_receipt_obligation
invalid_receipt_redaction
invalid_receipt_response
invalid_receipt_section
invalid_receipt_section_order
invalid_receipt_version_slice
invalid_sampling_params
invalid_semantic_dispatch_kind
invalid_semantic_failure_class
invalid_semantic_outcome_type
invalid_semantic_provenance
invalid_semantic_status_reason_pair
invalid_timestamp
invalid_token_usage
invalid_utf8
ledger_assigned_field_in_request_identity
lone_surrogate
malformed_json
missing_payload_field
nesting_too_deep
noncanonical_integer_string
not_an_accepted_envelope
nul_byte_forbidden
object_key_not_string
obligation_change_invalid
obligation_resolution_invalid
payload_redaction_mismatch
plan_version_conflict
privacy_receipt_not_durable
provider_attempt_provenance_is_not_final
public_error_invalid_correlation_id
public_error_invalid_message
public_error_missing_correlation_id
receipt_coverage_mismatch
receipt_gap_not_in_coverage
receipt_json_shape_invalid
redaction_target_required
ref_mirror_mismatch
response_fields_invalid
schema_artifact_role_invalid
schema_artifact_role_mismatch
schema_bytes_invalid
schema_catalog_incomplete
schema_digest_mismatch
schema_draft_unsupported
schema_duplicate_identity
schema_id_mismatch
schema_instance_invalid
schema_kind_mismatch
schema_manifest_duplicate_path
schema_manifest_invalid
schema_manifest_member_mismatch
schema_manifest_missing
schema_name_invalid
schema_not_found
schema_path_unsafe
schema_reference_unresolved
schema_version_mismatch
semantic_provenance_json_shape_invalid
set_member_not_ascii
timestamp_not_utc
timestamp_out_of_range
timestamp_submillisecond_precision
timestamp_timezone_missing
unknown_event_schema
unknown_payload_field
unsorted_set_field
unsupported_json_type
unsupported_payload_type
```

The tuple used to construct the frozenset is written in the ASCII order above and import-time
assertions require no duplicate and `^[a-z][a-z0-9_]{0,63}$` for every member. A later module may
raise a new `ProtocolValueError` only after its reason is added here in the same reviewed change.
There is no runtime registration function, plugin extension, or import-order-dependent mutation.
Constructing `ProtocolValueError` with a nonmember is a programmer defect and raises ordinary
`ValueError("unregistered_protocol_reason_code")`; it never creates a public client-blame error.
`privacy_receipt_not_durable` and `provider_attempt_provenance_is_not_final` are registered here so
the dependency-root exception can carry them, but their first-raiser and recovery semantics are
owned exclusively by `application/check.py`; this module does not import privacy, provider, or
coordinator types.

### Correlation lifecycle

Deep application/port code may construct `PublicOperationError(..., correlation_id=None)` because
those layers do not own an ID source. The frozen method
`bind_correlation_id(value: str) -> PublicOperationError` accepts exactly a validated canonical
`err_` UUIDv4 spelling. Binding an unbound error returns a new frozen value. Binding an already
bound error to that same ID is an identity-preserving no-op and returns `self`; binding it to a
different ID raises `ProtocolValueError("public_error_invalid_correlation_id")`.
`as_public_dict() -> dict[str, object]` requires a bound ID and otherwise raises
`ProtocolValueError("public_error_missing_correlation_id")`.
Consequently every object crossing the CLI/MCP/control result boundary satisfies the Wave A public
error schema, which always requires `correlation_id`, while internal exception flow need not invent
an ID source or mutate a frozen exception.

`as_public_dict()` returns an ordinary newly allocated dictionary with exactly `code` as
`code.value`, `message`, `retryable`, and the bound `correlation_id`, inserted in that order. It
adds `safe_details` as the fifth and final key only when the normalized mapping is nonempty. That
value is a newly allocated ordinary `dict[str, SafeDetailValue]` whose insertion order is the same
ASCII order as the stored mapping; it is never the internal immutable mapping. No exception args,
dataclass metadata, empty `safe_details`, or other field is serialized.

`message` is boundary-authored safe text, 1..4,096 UTF-8 bytes, contains no NUL or C0/DEL control,
and is never derived from `str(exception)` or caller/provider content. Failure raises
`public_error_invalid_message`. `code` must be a `PublicErrorCode` instance and `retryable` must be
exactly `bool`; those are programmer-owned constructor contracts, so wrong types raise ordinary
`TypeError("public_error_code_wrong_type")` and
`TypeError("public_error_retryable_wrong_type")` respectively rather than adding client-facing
protocol reasons. A supplied non-null `correlation_id` is validated by the same direct canonical
`err_` UUIDv4 checker used by `bind_correlation_id` and otherwise raises
`public_error_invalid_correlation_id`. This dependency-root module performs the prefix/version/
variant/lowercase check directly and does not import `protocol/ids.py`, preserving the import DAG.
`safe_details=None` and every non-`Mapping` input normalize to the same empty immutable mapping.
Within a mapping, each rejected value is omitted independently; if no entry survives, the result is
that same empty shape. Every nonempty accepted subset is defensively copied and stored as a deeply
immutable, ASCII-key-sorted `Mapping[str, SafeDetailValue]`.

Constructor validation is left-to-right in stored-field order and is frozen: validate `code`
type, then `message`, then exact-`bool` `retryable`, then a non-null `correlation_id`, and only then
normalize `safe_details`. Mixed-invalid input raises the first failure in that order. The direct
correlation checker accepts an `object`; every non-string value is invalid and both construction
and `bind_correlation_id` raise
`ProtocolValueError("public_error_invalid_correlation_id")` rather than a separate `TypeError`.

### Safe-detail normalization

`SAFE_DETAIL_KEYS`, in exact ASCII order, is:

```text
actual_version
component
count
expected_version
field
limit
method
operation
phase
quarantine_code
reason_code
retry_after_ms
schema_name
state
status
view
```

`normalize_safe_details(value)` is total and non-raising for every input object:

1. A non-`Mapping` returns the empty immutable mapping. Unknown keys are omitted without reading or
   converting their values. Keys are never stringified.
2. `count`, `limit`, and `retry_after_ms` accept only `int` but not `bool`, in
   `0..9_007_199_254_740_991`.
3. `reason_code` accepts only a member of `PROTOCOL_REASON_CODES`.
4. `quarantine_code` accepts only `operation_kind_state_contradiction`,
   `operation_result_digest_mismatch`, `operation_event_range_mismatch`,
   `operation_resume_object_invalid`, or `operation_lease_shape_invalid`.
5. `component`, `method`, `operation`, `phase`, `state`, `status`, and `view` accept an `Enum`
   instance whose `.value` is a lower-snake token matching `^[a-z][a-z0-9_]{0,63}$`; a raw string
   is rejected so arbitrary input cannot masquerade as a trusted structural enum.
6. `actual_version` and `expected_version` accept only an ASCII string matching
   `^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$`. `schema_name` accepts only the artifact-name grammar
   `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`, at most 128 bytes. `field` accepts only an RFC 6901 pointer
   assembled by the caller from schema constants, at most 256 ASCII bytes; raw input names are not
   passed here. Its exact accepted form is the empty root pointer or a string beginning with `/`
   whose tokens contain only printable ASCII (`0x20..0x7e`), with every `~` occurring only as
   `~0` or `~1`. Empty tokens and repeated `/` are valid RFC 6901 tokens; a missing leading slash,
   dangling/other tilde escape, control/DEL byte, non-ASCII code point, or 257th byte is rejected.
7. A known key with a value outside its rule is omitted in full. Values are never truncated,
   coerced, recursively walked, or replaced with input-derived text. Output contains at most the 16
   keys above in ASCII order.

The Wave A JSON Schema remains a structural superset (it also describes bounded arrays for future
reviewed keys), but v0.1 runtime emission is the exact mapping-only subset above. A new key or value
domain requires this owner spec, implementation, and tests to change together.

Exception-family classification does **not** live in this dependency-root module. The application,
CLI, MCP, and service boundary owners map their own typed closed failure unions to
`PublicErrorCode`; they must not classify by Python class-name strings or by unbounded exception
messages. Unknown internal exceptions map at the last boundary to `INTERNAL_ERROR`, after which the
boundary binds a fresh correlation ID.

## Errors and edge cases

- `PublicOperationError.message` must satisfy the exact byte/control rule above and must not embed
  arbitrary payload text.
- `safe_details` is optional and normalizes to the empty immutable mapping when absent or invalid.
- `ProtocolValueError.reason_code` is always an exact `PROTOCOL_REASON_CODES` member and never
  contains raw user content.
- `PublicErrorCode` is never inferred from Python exception class names alone.
- Unknown internal exceptions degrade to `INTERNAL_ERROR` at the public boundary, but still get a
  correlation ID for local diagnostics.

## Invariants

1. Public errors are deterministic and bounded.
2. Public error handling never requires a traceback to explain the user-visible outcome.
3. No public boundary may invent a new code outside `PublicErrorCode`.
4. `ProtocolValueError` stays internal and low-level.
5. `retryable` is a claim about the operation state, not about whether the process crashed.
6. No runtime reason-code registration or arbitrary safe-detail string channel exists.

## Tests

- `tests/unit/protocol/test_errors.py` — enum membership, formatting, bounded details, reason-code
  validation, and no-raw-input guarantees.
- `tests/subprocess/test_cli_streams_and_exits.py` — exit-code mapping from public codes.
- `tests/conformance/surfaces/test_mcp_contract_matrix.py` — structured MCP error results and
  sanitized summaries.

## Open questions

None.
