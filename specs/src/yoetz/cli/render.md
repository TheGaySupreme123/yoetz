# src/yoetz/cli/render.py — human CLI rendering for status, check, and receipt

**Wave:** D | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):**
`domain/findings.md`, `domain/receipts.md`, `protocol/coverage.md`
**Imported by:** `cli/app.md` and render tests

## Purpose

This file renders the human-facing views for the CLI. It keeps the textual presentation faithful to
the structured result and avoids inventing a stronger conclusion than the underlying data supports.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `render_human_status(result)` | compact bounded status text |
| `render_human_check(result)` | compact check verdict and findings text |
| `render_human_receipt(result)` | compact receipt text |
| `render_human_findings(findings)` | top-three findings view |
| `render_human_error(error)` | safe text for a public error |

## Behavior

`render_human_status(result)` shows the current frontier, freshness, unresolved obligations,
unresolved findings, and any gaps. It does not dump the full ledger.

`render_human_check(result)` shows the verdict and at most the top three findings. It keeps the
priority order stable and explicitly states when semantic review was unavailable or not requested,
using the exact structured `semantic_status` and `semantic_reason` rather than guessing from prose.
Human wording is a fixed lookup table over valid pairs and cannot expose provider exception text.

`render_human_receipt(result)` prints the same conclusion and limitations that appear in the JSON
receipt. It must never strengthen the phrasing beyond the coverage or gap state.

`render_human_findings(findings)` enforces the top-three CLI rule and preserves the ordering
returned by the kernel. Suppressed findings may be mentioned as a count, not expanded.

`render_human_error(error)` produces a short safe message for the CLI without exposing a traceback
or hidden payloads.

## Errors and edge cases

- The human renderer must tolerate missing optional sections.
- If a finding set is empty, the renderer says so plainly instead of inventing a reassuring tone.
- If the user requested JSON, these functions are bypassed.
- An unknown or invalid semantic status/reason pair is a schema failure, never rendered as
  free-form fallback text.
- The receipt renderer must not claim "verified" when the coverage state is weaker.

## Invariants

1. Human rendering is always weaker than the structured result.
2. At most three findings are shown by default.
3. No raw secret or path leakage.
4. The receipt wording matches the structured conclusion.
5. `incomplete_check` always tells the local caller the exact bounded semantic reason.

## Tests

- `tests/subprocess/test_cli_invocations.py` — exact installed human snapshots for status gap
  wording, the three-finding cap/stable ordering, and honest receipt language.

## Open questions

None.
