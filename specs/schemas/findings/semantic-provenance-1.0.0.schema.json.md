# schemas/findings/semantic-provenance-1.0.0.schema.json — semantic provenance schema

**Wave:** B/E | **ADRs:** ADR-002, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/findings.md`
**Imported by:** semantic check results and conformance fixtures

## Purpose

Describe the bounded audit trail for a semantic finding.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/findings/semantic-provenance-1.0.0.schema.json`.
- Owning model: `SemanticProvenance`.

## Behavior

Closed receipt-finalized object with fields for:

- provider profile;
- model identity;
- request identity;
- attempt identity;
- dispatch kind and exactly one external-authorization or local-disclosure-reservation identity;
- durable privacy receipt identity and external request commitment when applicable;
- exact status and `SemanticReason`;
- usage;
- failure class when present.

The schema keeps provenance auditable but bounded and does not allow free-form trace dumps.
`dispatch_kind=external` requires `egress_authorization_id` and `request_commitment`, forbids
`local_disclosure_reservation_id`; `dispatch_kind=local_model` requires the local reservation,
forbids the external fields. `privacy_receipt_id` is always required. Adapter-returned
`ProviderAttemptProvenance` is a private in-memory type and never validates against this schema.

## Errors and edge cases

- Missing required identifiers fail.
- A receipt that is not yet durable, both/neither authority fields, or invalid status/reason fails.
- Oversized raw logs fail.

## Invariants

1. Semantic provenance is auditable.
2. Raw traces are not admitted.
3. The schema stays bounded.
4. Published semantic provenance is never provisional.

## Tests

- `tests/unit/domain/test_findings.py`
- `tests/conformance/operations/test_check_contract.py`

## Open questions

None.
