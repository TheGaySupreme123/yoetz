# schemas/operations/status-result-1.0.0.schema.json — status result schema

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/application/status.md`, `src/yoetz/protocol/errors.md`
**Imported by:** CLI, MCP, and parity tests

## Purpose

Describe the read-only page/result shape for status.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/operations/status-result-1.0.0.schema.json`.
- Owning model: `StatusResultModel`.

## Behavior

Union of success and public-error branches. The success branch carries:

- the requested/head/effective frontiers;
- projection lag and rebuild state;
- the bounded page payload for the selected view;
- next cursor when another page exists;
- coverage and gap metadata.

The schema must stay read-only in meaning and must not imply a mutation or result frontier.

Frontiers, lag/rebuild state, IDs, enum states, counts, digests, coverage vectors, and cursor are
structural. Task titles, obligation/claim/finding text, evidence descriptions, response reasons,
and other user/task-derived page leaves are content-bearing and admit only their exact original
type or the common omission marker. The success branch requires the common `agent_context` privacy
projection and durable local-disclosure receipt.

A `candidate_findings` page is the one exception to that split for finding prose, and the schema
must encode it: deterministic candidate `summary`/`detail` are rule-templated and name their
subjects by ID (`domain/findings.md`), so they are structural, not user/task-derived, and never
reduce to the omission marker. A candidate whose prose were withheld would carry no information at
all, since the rule and the IDs it fired on are the entire message. The same page carries no
`finding_id` and no `CheckVerdict`: no status result of any view may contain a verdict, because only
a recorded check produces one.

## Errors and edge cases

- A result that claims a newer frontier than the page represents fails.
- Missing fallback parity fails release.

## Invariants

1. Status is read-only.
2. Page shape is bounded.
3. Error fallback is shared.
4. No status page of any view carries a verdict or a finding ID.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/operations/test_status_contract.py`

## Open questions

None.
