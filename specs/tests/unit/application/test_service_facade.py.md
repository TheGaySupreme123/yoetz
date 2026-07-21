# tests/unit/application/test_service_facade.py — ready-only application facade unit suite

**Wave:** D | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** `src/yoetz/application/service.md` | **Imported by:** test runner

## Purpose

Verify only a current ready service context constructs/uses/closes Application.

## Public surface

Frozen `VerificationPolicy`, `ClientProjectionContext`, disclosure-sink resolution, projected-body
boundary, delegation, generation, cancellation, incomplete-semantic, error fencing,
privacy-token identity, and close tests.

## Behavior

Assert six methods delegate once, semantic failure returns deterministic incomplete_check,
relock/stale context denies, and close orders resources. Assert the six privacy facade names equal
the six registered control tokens exactly; receipt list/get delegate only to the internal
`PrivacyAuditPort.list_receipts`/`get_receipt` methods and expose no aliases. The page fields map
one-to-one; get maps `PrivacyReceiptView` to `found` and `None` to `not_found` without nullable wire
output.

The contract slice proves `disabled|optional|required` maps exactly to the three check modes and
that a non-bool `max_findings` is limited to `1..10`. It proves only
`cli + human_readable + output_is_controlling_tty` resolves to `local_human_view`; machine mode,
missing/non-TTY output, MCP, UI, wrong runtime types, and the explicit fail-safe default resolve to
`agent_context` or reject before projection. It also proves JSON `receipt` projection fails closed
with `privacy_projection_unavailable` when any `/document` content leaf is blocked, rather than
emitting a partly rewritten digest-bound document. The daemon integration suite separately proves the
exact context reaches `project_result_for_client` and cannot cross-pair client kinds.

## Errors and edge cases

Partial startup, result validation defect, disconnect, cancellation around shielded commit.

## Invariants

1. No per-client RuntimeFactory exists.
2. Application never escapes daemon composition.
3. Projection context never carries a caller-selected sink.

## Tests

This file is the executable owner.

## Open questions

None.
