# Publication policy

## Materiality checklist

Publish a fact when it changes an independently reviewable work package, a requested outcome, an obligation, a decision, a material attempt, a result, evidence, a claim, a plan, or a finding response. Keep routine navigation, searches, formatting, generated-file writes, repeated status reads, tool chatter, and per-file bookkeeping out of the ledger.

For a large inventory, create obligations per coherent work package. Publish one bounded manifest evidence item for its leaf files and one material package transition. Do not create one obligation or routine event per file.

## Cadence

Publish one batch per material transition, roughly one to eight events. A normal session is a handful of batches: the plan and its obligations, each independently useful result with its evidence, the completion claim, and any finding response. It is not one batch per file, per tool call, or per message.

These are not publishable transitions: reading or searching, running a command whose result you already expect, formatting, regenerating a derived file, repeating a status read, or republishing state that has not changed since the last accepted event. When in doubt, ask whether an independent reader of the ledger alone would conclude something different without the fact.

## The sixteen event families

- `session_opened` — establishes the recorded session; it does not prove workspace access.
- `session_resumed` — records a resume assertion; it does not prove continuity outside the ledger.
- `plan_published` — records the current plan; it is not completion.
- `plan_revised` — records a material plan change; it does not erase the prior plan.
- `obligation_published` — creates a requested or derived obligation; it is not evidence.
- `assignment_recorded` — attributes a bounded assignment assertion; it is not authenticated authorship.
- `decision_recorded` — records a decision and rationale summary; it is not hidden reasoning.
- `action_recorded` — records a material action assertion; it does not prove the action occurred.
- `evidence_recorded` — links bounded evidence identity; it does not upgrade its provenance.
- `claim_recorded` — records a claim to be checked; it is not a verdict.
- `result_recorded` — records an independently useful result assertion; it is not automatically accepted.
- `finding_recorded` — records a deterministic or semantic challenge; it is not self-resolving.
- `response_recorded` — records a response to a finding; it does not erase the finding.
- `check_recorded` — records a check at one frontier; it becomes stale after material change.
- `redaction_recorded` — records a bounded redaction fact; it does not prove forensic erasure.
- `receipt_recorded` — records derived completion wording and limitations; it is not proof of the work.

## Obligations, evidence, and claims

An obligation names what must be satisfied. Evidence is a bounded, provenance-labeled reason to believe something about it. A claim states a conclusion. Link them explicitly; do not substitute a file list for an obligation or a claim for evidence.

## Declare completion scope in the plan

The effective current plan must distinguish obligations from an intentional empty scope. Normally,
publish sorted-unique `obligation_refs`. If none apply, set `no_obligations_reason` to exactly one
closed value:

- `no_material_change` — the work makes no material change;
- `single_atomic_change` — one atomic change has no independently useful obligation split;
- `exploratory_scope_unknown` — exploration cannot yet declare material obligations.

Do not send a reason beside effective obligation refs. A `plan_revised` event restates the current
declaration: include the current reason when the revised effective ref set is empty, or omit it to
clear an earlier reason. Yoetz never infers obligations from prompts, source code, workspace state,
or plan prose.

An empty-scope reason clears the `no_obligations_declared` readiness blocker, but it does not buy a
clean completion check. A completion claim over zero declared obligations remains
coverage-incomplete: `completion_scope_undeclared` without a reason or
`completion_scope_declared_none` with one.

## Obligation resolution

<a id="obligation-resolution"></a>

Resolving an obligation is a one-way `open → resolved` state transition, not an edit. Publish a second
`obligation_published` event for the **same** `obligation_id` that:

1. repeats every meaning field from the open obligation **byte-for-byte**: `description`,
   `acceptance_criteria`, `evidence_expectation`, `requested_items`, and `source_refs` (omit a field
   only when the open event also omitted it);
2. sets `status` to `resolved`;
3. supplies the final bounded `resolution_evidence_refs` (one or more evidence or result ids).

Only `status` and `resolution_evidence_refs` may differ. Changing meaning fields, republishing
`status: open` for an existing id, mutating an already-resolved obligation, or reopening
`resolved → open` is rejected with reason `obligation_resolution_mismatch` and invariant
`meaning_fields_must_repeat` (or `open_to_resolved_only` for an invalid status transition). The
public error names the mismatched schema field names only — never their values. A worked open +
resolution pair lives in the `publish_work` tool input schema `examples` entry. Complete request
bodies remain available at `yoetz://guidance/request-templates.md` if a host drops schema examples.

## Subject state and freshness

Bind change-sensitive evidence to the exact subject state or frontier it concerns. If a material dependency changed or its state is unknown, mark the evidence stale or limited. Absence of visible source is not evidence that nothing changed.

## Batching, sequencing, and retry

Batch facts that belong to one material transition. Preserve writer sequence and expected frontier. On timeout, reuse the same request and operation IDs; never manufacture a replacement event merely because the response was lost.

Before a material publish over MCP, set `dry_run: true` to validate the batch and preview accepted event ids and coverage without appending. The dry-run result is not evidential and must not be cited as a check, publication, or coverage source. When the preview is acceptable, publish with the same `request_id` and `dry_run` omitted or false.

Worked examples for each ordinary publishable family — and a cross-linked
action/result/evidence/claim batch — live in the `publish_work` tool input schema `examples` entry
and as complete fallback bodies at `yoetz://guidance/request-templates.md`. Example `occurred_at`
values are illustrative shape only; do not copy them into live drafts.

## Event time claims

`occurred_at` is a caller assertion of when the event happened. Use the best real RFC 3339 millisecond UTC time available. If the exact time is unknown, use an honest bounded approximation and understand that it remains a claim — the service does not check outside clocks and does not reject far-past, future, or out-of-order caller times.

The service independently stamps `accepted_at` on acceptance. Both values are durable and bound into the entry digest. Ledger order, causality, supersession, optimistic concurrency, and receipt freshness use ingestion sequence and frontier, not caller time. `status` with `view=history` returns both clocks on each item so a reader never sees a caller claim alone as if it were service time.

## Multi-agent work

Publish bounded assignments and preserve each delegate's logical writer identity. Treat delegate summaries as claims. Link their accepted evidence separately and record a decision when resolving a contradiction.

## Forbidden content

Never publish chain-of-thought or hidden reasoning; full prompts, transcripts, or conversation history; credentials or secrets; whole files, repositories, or broad unrelated source. When semantic review would otherwise be blind, publish only the smallest problem-local changed hunk or enclosing symbol needed, with source, state, and coverage labels. For material completion checks, publish the smallest state-bound diff/symbol and the directly relevant test or failure excerpt; never rely on self-asserted completion prose alone.

## Mini-flows

### Code change

Publish one obligation for the behavior, one material implementation result, a bounded changed-symbol digest or excerpt, and focused test evidence. Do not publish the repository or every edit.

### Research

Publish the question, source identities, bounded supported and contradicted claims, limitations, and the conclusion. Do not paste articles or browsing transcripts.

### Plan revision

Publish the original obligation, the material new fact, and one plan revision explaining which outcome or dependency changed. Restate the current empty-scope reason or omit it to clear the declaration; omission never inherits the prior reason. Routine schedule adjustment needs no event.

### Large generated inventory

For 100 files, group them into independently reviewable packages, record partial package status when useful, attach one bounded member manifest per completed package, and publish one final package transition. One obligation or routine event per file is invalid.
