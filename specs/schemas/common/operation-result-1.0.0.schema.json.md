# schemas/common/operation-result-1.0.0.schema.json — common operation-result wrapper

**Wave:** A/B/D | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/protocol/models.md`, `src/yoetz_core/protocol/errors.md`
**Imported by:** all public operation result schemas

## Purpose

Describe the common result wrapper used by every public operation result schema.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/common/operation-result/1.0.0`.
- Owning model: common result envelope helper.

## Behavior

This wrapper is the shared union shape used by the six public operation results:

- `ok: true` branch containing the operation-specific success payload.
- `ok: false` branch containing the common public-error object.

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

The wrapper requires canonical request identity fields where the operation contract does so and must
accept the last-resort `INTERNAL_ERROR` fallback used by MCP/CLI recovery paths. It is closed and
rejects extra properties.

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
