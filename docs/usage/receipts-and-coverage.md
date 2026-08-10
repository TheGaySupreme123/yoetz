# Receipts and coverage

A receipt says what was checked and how well. It is deliberately narrow, and reading it correctly
matters more than any other single thing in Yoetz.

The agent-facing rules are [`guidance/coverage-and-receipts.md`](../../guidance/coverage-and-receipts.md),
which ships with the product. This page is the human's version.

## Coverage is a vector, not a score

Six independent dimensions. Strength in one never compensates for weakness in another, and they
never collapse into a number:

| Dimension | Asks |
|---|---|
| participation | How did the work reach the ledger? (`cooperative_mcp`, `local_cli`, `codex_jsonl_import`, …) |
| authorship | How well is the author established? (`self_asserted`, `harness_observed`, …) |
| artifact observation | Did anything observe the artifacts, or only the published claims? (`published_only`, `hook_observed`, …) |
| content visibility | How much content was actually looked at? (`none`, `digest_only`, `targeted_excerpt`, …) |
| provenance | Deterministic, semantic-provider, imported, or participant-asserted? |
| freshness | Is the evidence bound to the current state? (current, stale, unknown, redacted) |

**The weakest material dependency bounds the conclusion.** Use the exact enum values the protocol
returns.

## Getting one

```text
yoetz receipt --request '{"request_id":"req_...","format":"markdown", ...}' --json
```

Formats are `json`, `markdown`, and `text`. All three project under the default agent-context
policy. Under a deliberately stricter owner policy, digest-bound `json` can fail closed with
`PRIVACY_AUTHORITY_REQUIRED` (`receipt_json_projection_blocked`) — re-request `markdown` or `text`,
or widen agent-context policy from a local terminal.

**The durable receipt is recorded even when projection is blocked.** A projection failure is a
disclosure decision, not a lost receipt.

## Reading one

Read these together, never in isolation: the frontier, the verdict, the coverage vector, the finding
disposition, evidence provenance, freshness, suppressed counts, and limitations. Derived Markdown is
a human view of the same structured record.

Honest:

> Yoetz found no deterministic issue in the cooperatively published record at the stated frontier;
> artifact observation remained published-only.

Not honest:

> Yoetz proved the implementation is complete and correct.

## The trap: a clean deterministic-only check

A clean `deterministic_only` check is **not** an implementation review. When mode is
`deterministic_only`, or semantic status is `not_requested`, the coverage includes
`semantic_review_not_requested` and completeness is coverage-incomplete — even when the verdict
reads `no_issue_detected`.

Prefer `semantic_if_configured` for material claims. Reserve `deterministic_only` for genuinely
structural checks, and disclose the limitation when you use it.

## Completion scope is declared, not inferred

Yoetz reads completion scope only from the effective plan chain. It never invents obligations from
the prompt, source tree, workspace, or completion prose.

When a completion claim exists and the effective plan has zero declared obligations, the check is
coverage-incomplete in both cases:

- no typed reason: `completion_scope_undeclared` — the receipt says scope was never declared;
- a typed reason: `completion_scope_declared_none` — the receipt says the plan declared none and
  names only the closed reason value.

The typed declaration clears the status readiness blocker; it does not buy a clean verdict. A
positive declared count whose obligations are all resolved is the distinct resolved-scope state.
Redacted or unreadable scope remains unknown and never becomes zero.

## Candidate findings are not a check

`status` with `view=candidate_findings` is an advisory read. No verdict, no IDs, no receipt, and the
read records nothing. An empty list means no rule fired at that frontier — it is not
`no_issue_detected`.

Permitted after a candidate read: "I saw an unresolved attempt and went back to it."
Forbidden after a candidate read: "I checked and found nothing."

## Things that do not strengthen coverage

- Installing a harness integration.
- Firing a trigger-only hook. A proven trigger may prompt a bounded `status` re-grounding; it
  observes nothing and changes no coverage.
- Storing imported evidence. Imported evidence never gains cooperative authorship because Yoetz
  stored it.
- A digest. It records identity, not content inspection.
- Constructing TOML, a path, or metadata. That is not proof of wire dispatch or semantic review.

Only a capability-proven, consented observation arm with real observation evidence earns
`hook_observed`. Absent, empty, paused, or degraded observation status does not.

## Findings and responses

Choose one recorded response per finding: accept and act, provide additional evidence, revise the
claim, dispute with evidence, or state an unresolved limitation. Then recheck after material change.

A response never deletes the original challenge. That is the point — the record keeps the
disagreement visible.

No disposition resolves a finding. `acknowledged`, `rejected`, and `waived` each record what was
decided and what evidence was attached; none of them clears the finding for receipt purposes. Every
actionable finding recorded in a task keeps the receipt conclusion at `unresolved_findings_remain`,
even after later checks return no findings at all.

Repairing the record is still worth doing — it stops the next check from firing the same rule, and
it shows a reader what was done — but a task that fired an actionable finding does not go on to
produce a clean completion receipt, and the final answer should not describe one.

Responding does not throw the check away. A recorded check still counts toward a later receipt when
the only events between the two are responses to findings that check itself returned — the receipt
folds the check's coverage, including `semantic_model_derived`, and carries the gap
`check_current_as_of_earlier_frontier` naming the subject frontier that was tested. The verdict is
current as of that earlier frontier, not the receipt's, so the receipt is still coverage-incomplete.

Any other material event after the check — published work, a new finding, a response to a finding
the check did not return, or a response whose payload is redacted or unreadable (it cannot prove
which finding it answered) — requires a re-check first. The receipt reports `check_not_applicable`
and the check contributes nothing until it is re-run at the current frontier. `status` applies the same
rule, so status and a receipt at the same frontier never disagree about what was checked.

The cheapest finding is the one that never fires. Before the first `check`, confirm that every
requested item has an exact `attempted_items` entry, that every claim has linked evidence, and that
every open obligation is either resolved or deliberately left open with a stated reason.
