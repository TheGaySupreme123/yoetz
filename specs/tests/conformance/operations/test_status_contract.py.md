# tests/conformance/operations/test_status_contract.py — status public contract

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/status.md`, `src/yoetz/kernel/projections.md`
**Imported by:** conformance operations tests

## Purpose

Prove status is read-only and returns the same canonical projection page across surfaces.

## Public surface

- `test_status_request_result_parity` — structured pages match.
- `test_status_is_task_state_read_only_and_projection_receipted` — no task state mutation occurs;
  ordinary client output has one replayable local-disclosure receipt and `privacy_projection`.
- `test_status_frontier_and_pagination_parity` — lag, page size, and frontier are exact.
- `test_future_frontier_is_invalid_request` — every surface maps a future read-only frontier to
  `INVALID_REQUEST`, never `FRONTIER_CONFLICT`.
- `test_candidate_findings_uses_only_whole_case_path` — no `ProjectionQuery` is issued for the
  candidate view and exactly one is issued for every other view.
- `test_candidate_parity_excludes_semantic_findings` — deterministic candidate identity matches a
  same-frontier check independently of capping, while semantic findings have no candidate row.
- `test_finding_and_candidate_tie_breaks_are_distinct` — recorded ties end in finding ID and
  candidate ties end in canonical emission ordinal.
- `test_status_raw_item_derivation_and_filter_defaults` — every field in all seven repository
  views follows the registered projection source; absent/false include flags and AND composition
  are exact.
- `test_finding_resolution_requires_recorded_applicability` — responses/expiry and arbitrary
  scoped/skipped checks do not resolve; a complete applicable recheck or same-issue successor does.
- `test_status_tombstone_and_unreadable_page_progress` — opaque rows are omitted with gaps,
  compact counts remain conservative, and cursor progress never loops or triggers unbounded
  backfill.
- `test_status_filters_before_payload_hydration` — filter/order/lookahead are structural and only
  the selected bounded rows are opened.

## Behavior

The test asserts:

- status does not write task events/objects/operations or mutate the projection;
- service projection reserves/completes one privacy-audit catalog receipt, adds the required
  `privacy_projection`, and replays it for the identical internal result/policy binding;
- requested/head/effective frontiers and page contents match;
- latest/current vs lagged cache disclosure is surfaced honestly;
- future-frontier input fails identically as `INVALID_REQUEST` without a write/conflict path;
- CLI and MCP wrappers do not alter the page shape.
- candidate paging uses the rank prefix plus emission ordinal; row-query paging uses the exact
  port-owned typed positions and never accepts candidate view.
- assignment scope/resolution, obligation effective status/actor/revision edges, finding current
  response/resolution/rank, evidence availability/freshness, history summary codes, compact
  counters, and version identity match the exact port mapping field by field;
- include flags exercise absent, false, and true. False/absent add their predicate and never
  override another filter, including the intentionally empty `status=resolved` without
  `include_resolved=true` case;
- waiver expiry on both sides of the injected clock leaves recorded disposition and ordering
  unchanged, and acknowledgement/rejection/waiver never flips `resolved`;
- clean whole-case and direct-scope-overlap checks resolve only with matching `run/completed`, zero
  suppression, current gap-free coverage, and (for semantic findings)
  `succeeded/semantic_completed`. Scoped non-overlap, skipped/failed, suppressed, stale, and weak
  checks do nothing and never reopen a resolved row; redacting its proof may reopen it only with
  the explicit redaction gap;
- redacted assignment/obligation/finding/evidence rows expose no payload-derived item fields.
  Obligation/finding tombstones still contribute conservative compact counts; unreadable selected
  payload/response rows consume one structural cursor slot, add the exact omission gap, and are not
  backfilled;
- spies fail if an adapter opens any payload while applying filters, sorting, or reading lookahead;
  hydration opens at most the selected limit (plus one response per selected finding), while
  history/versions open none and compact obeys its fixed 1+10+10 limit.

## Errors and edge cases

- A status call that mutates task state fails; an ordinary result without its privacy projection
  receipt also fails.

## Invariants

1. Status is task-state read-only and client disclosure is durably receipted outside that ledger.
2. Frontier disclosure is exact.
3. Page content is canonical.
4. Resolution is proven by visible durable applicability facts, never response state or time.
5. Structural filtering precedes bounded hydration on every backend.

## Tests

- `tests/conformance/operations/test_status_contract.py`

## Open questions

None.
