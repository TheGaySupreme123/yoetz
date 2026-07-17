# schemas/common/operation-result-1.0.0.schema.json — common operation-result wrapper

**Wave:** A/B/D | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/protocol/models.md`, `src/yoetz/protocol/errors.md`
**Imported by:** all public operation result schemas

## Purpose

Describe the closed reusable result definitions used by every public operation result schema.
JSON Schema has no parameterized payload type, so each operation artifact owns its exact success
union while this artifact owns the only operation-independent complete result and the shared
privacy-projection definitions.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/common/operation-result-1.0.0.schema.json`.
- Owning model: common result envelope helper.
- Stable definitions: `$defs/failure_result`, `$defs/privacy_projection`, and
  `$defs/omitted_content`.
- The document root is exactly `$ref: "#/$defs/failure_result"`; it contains no permissive
  generic-success instance shape.

## Behavior

The six public operation result schemas each define their own closed `ok: true` branch and pair it
with `$defs/failure_result`. This common document's root validates that complete `ok: false`
public-error result. It does not attempt to model an unknown operation success: closing such a base
would prevent composition, while leaving it open would violate the public boundary.

The wrapper also owns the common local-disclosure projection definitions. Every ordinary success
branch requires `privacy_projection` with sink `agent_context`, a canonical durable local-
disclosure receipt ID, policy ID/version/digest, sorted included/blocked `DataCategory` arrays, and
sorted omitted JSON Pointers. A content-bearing leaf is `oneOf` its original operation-specific
type or the exact closed marker
`{omitted:true, category:<DataCategory>, reason:
"local_disclosure_not_authorized"|"never_send_redacted"}`. Structural
leaves never admit the marker. The operation-specific schema enumerates which leaves are content;
there is no open recursive redactor.

The exact pointer registry and bounds are owned by `protocol/models.md`: at most 512 content leaves
and omission pointers, each RFC 6901 pointer at most 256 UTF-8 bytes, an internal projectable body
at most 524,288 canonical bytes, and a final body at most 1,048,576. `privacy_projection` additionally
requires `projection_commitment` in canonical `hmac-sha256:` form and forbids any unkeyed
internal-result/projection digest; arrays are sorted unique. A result that needs more content must use
its operation's pagination rather than truncate after commit.

`failure_result` requires `protocol_version: "0.1"`, `schema_version: "1.0.0"`, `ok: false`, and
the closed public-error object. Its `request_id` is optional and, when present, is either a
canonical request ID or `null`, which admits the request-independent last-resort `INTERNAL_ERROR`
fallback used by MCP/CLI recovery paths. It is closed and rejects extra properties. Every
operation-specific root references the exact definition, so all six still admit one
byte-compatible error contract.

## Errors and edge cases

- A result schema that rejects the fallback error shape fails release.
- A wrapper that widens the public error branch fails.
- Missing/mismatched projection receipt, an omission marker in a structural field, a free-form
  marker, or an unclassified success field fails.

## Invariants

1. All operation results share the same public failure branch.
2. Fallback error shape is admitted everywhere.
3. Extra keys are forbidden.
4. Ordinary result content has one service-owned, receipt-bound projection shape.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/protocol/test_frozen_schemas.py`

## Open questions

None.
