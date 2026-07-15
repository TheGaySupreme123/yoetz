# src/yoetz/kernel/ranking.py — stable ordering and verdict selection for findings

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`domain/findings.md`, `protocol/coverage.md`, `protocol/errors.md`, `kernel/projections.md`,
`kernel/policies/work_integrity.md`, `kernel/policies/research_evidence.md` | **Imported by:**
`application/check.md`, `application/respond.md`, `domain/receipts.md`, `cli/render.md`

## Purpose

This file turns a pile of deterministic and semantic findings into the bounded, stable result that
the rest of Yoetz shows to users. It is the policy-aware ordering layer between raw findings
and the public check/receipt surface.

Ranking is not scoring. It does not average findings into a single number. It keeps the strongest
and most actionable findings first, preserves how many were suppressed, and carries the verdict that
the caller should present.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `RankedFindings` | frozen wrapper with ordered findings, suppressed count, verdict, and coverage |
| `rank_findings(deterministic, semantic, policy, max_findings)` | stable merge, order, and verdict selection |

## Behavior

`rank_findings` takes the deterministic findings produced by the kernel, the semantic findings
returned by the model path, the active policy pack set, and the caller’s maximum finding count.
It returns a `RankedFindings` value with:

- the findings ordered by materiality, actionability, evidence strength, and stable ID tie-break;
- the number of findings suppressed by the `max_findings` limit;
- the final verdict implied by the ordered set;
- the weakest material coverage represented by the set.

The ordering rules are fixed:

1. higher `priority` comes first;
2. findings that demand direct action come before explanatory findings;
3. stronger evidence comes before weaker evidence;
4. if everything else ties, smaller `finding_id` bytes win so the order is deterministic.

The sort key is therefore lexicographic and stable: priority bucket, actionability bucket,
coverage/evidence bucket, origin preference, then `finding_id`. The canonical direct-to-agent merge
adds one bounded diversity rule after sorting: when `max_findings>=2` and at least one
post-validated semantic reviewer challenge has material priority 1 or 2, the selected set must
contain the highest-ranked such challenge. If the ordinary prefix omitted it, replace the prefix's
lowest-ranked item, then sort the selected set by the same key. The displaced item becomes
suppressed while the challenge leaves the suppressed tail, so total suppression remains
`total_input_findings - returned_findings`. This reserves at most one slot; with
`max_findings=1`, or only priority-3 semantic
explanation, the ordinary strongest prefix wins.

Deterministic findings outrank semantic findings when all else ties, because they are already
proven against the frozen projection state. Semantic findings can still appear earlier when they are
materially stronger by the same ordering rules. The one material-challenge slot does not relabel,
rewrite, or resolve a deterministic finding; every displaced deterministic finding remains in the
suppressed tail and the exact suppressed count is unchanged by the swap.

The verdict logic is conservative:

- `action_required` when any selected finding is materially blocking or the top findings are a
  direct work failure;
- `no_issue_detected` when the selected set is empty and the coverage state is current enough to
  support that conclusion;
- `insufficient_coverage` when the evidence is too weak, redacted, stale, or incomplete to support a
  stronger conclusion;
- `incomplete_check` when the selected set shows the check was not fully resolved or the available
  findings are only a partial view of the requested policy surface.

The verdict is chosen from the selected findings, not from the suppressed tail. A saturated cap can
therefore still return `action_required` if the visible prefix contains a blocker, or
`insufficient_coverage` if the visible prefix is too weak to support a stronger judgment.

`max_findings` is a hard cap, not a hint. The ranker must never return more than the caller
requested and must never silently drop the suppressed-count information.

The returned coverage is the weakest material coverage of the findings that made it into the
ordered set. If the selected set is empty, the ranker uses the caller-supplied coverage context and
the policy pack context to select the most honest verdict.

`RankedFindings` therefore carries four facts that must remain in sync: the ordered prefix, the
suppressed-count tally, the verdict, and the weakest coverage of the visible prefix. If any one of
those four is stale, the entire value is stale.

## Errors and edge cases

- `max_findings` must be positive and no larger than the registry limit.
- Duplicate `finding_id` values in the input are a broken invariant and must not be hidden.
- A finding with a malformed coverage vector is invalid upstream.
- The ranker never rewrites finding text, provenance, or policy identity.
- The reviewer-voice rule applies only to post-validated semantic challenge findings and can reserve
  at most one slot.

## Invariants

1. Stable input order produces stable output order.
2. The ranker never fabricates or mutates findings.
3. Suppressed-count accounting is exact.
4. Verdicts never outrun the evidence or coverage.
5. Ranking is pure and side-effect free.
6. At any cap of at least two (including the default of three), one material accepted reviewer challenge reaches the main-agent finding
   surface without mutating deterministic truth.

## Tests

- `specs/tests/unit.md` — ordering, tie-break, and suppressed-count coverage.
- `specs/tests/conformance.md` — deterministic and semantic findings merge the same way in memory
  and SQLite runs.
- `fixtures/findings/` — sort and verdict vectors.

## Open questions

None.
