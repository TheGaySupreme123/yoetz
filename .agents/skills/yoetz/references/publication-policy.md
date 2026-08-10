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

### Digest subjects and provenance

Every newly published `evidence_recorded/1.1.0` payload that includes `content_digest` must also
include a closed `digest_binding` object:

- `subject` names the exact byte class that was hashed;
- `content_availability` is `captured`, `digest_only`, or `withheld`;
- `byte_count` is the size of the exact hashed byte sequence;
- `provenance` is `caller_asserted` for ordinary cooperative publication.

The closed subjects are `approved_check_receipt`, `artifact_bytes`, `bounded_excerpt`,
`command_stdout`, `import_report`, `source_diff`, `static_analysis_report`, `test_report`, and
`test_stdout`. The subject must be compatible with `evidence_kind`. In particular,
`evidence_kind=test_result` accepts test output/report, static-analysis report, or an approved-check
receipt. It does not accept `source_diff`. Publish a source-diff digest as `evidence_kind=artifact`
with `subject=source_diff` instead.

`captured` requires the mirrored `captured_object_id`; `digest_only` and `withheld` forbid one. A
digest proves byte identity only. It does not prove that a command ran, that its exit status was
successful, that Yoetz inspected the content, or that the evidence supports a claim. `description`
is caller-authored narrative and is never treated as the bytes identified by `content_digest`.

### Making a change reviewable

When semantic review is expected, one evidence record can carry both legibility and identity: put
the smallest problem-local changed hunk or test slice (at most roughly 3,500 bytes) in
`description`, and publish the matching `content_digest` with its `digest_binding`. The review
excerpt shown to the reviewer is the `description`; the digest identity facts travel alongside it
as excerpt provenance. A digest-bound record without a `description` contributes only its bounded
provenance facts, so the reviewer sees identity but no content.

Cite the evidence id in the claim's `supporting_refs`. Evidence referenced only from
`result_recorded.evidence_refs` does not qualify for excerpt selection under linked-subject
relevance.

Do not submit `approved_check` or `import_observed` provenance through ordinary publication. Those
values are reserved for the capability-proven service path and trusted importer. Historical
`evidence_recorded/1.0.0` records remain readable byte-for-byte; a historical digest without a
binding is reported as legacy/unknown and cannot silently satisfy a new completion claim.

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

### Event draft skeleton

<a id="event-draft-skeleton"></a>

A host may drop the tool schema `examples` entry, so the envelope shape is restated here. Every
draft carries exactly these seven keys, and nothing else:

```json
{
  "event_id": "evt_<uuid4>",
  "schema": { "name": "<family>", "version": "1.0.0" },
  "occurred_at": "<RFC 3339 UTC, millisecond precision>",
  "causal_parents": [],
  "payload": {},
  "artifact_refs": [],
  "evidence_refs": []
}
```

`schema.name` is the family discriminator and selects the payload shape. There is no top-level
`event_type`, `family`, or `type` key. The ordinary publishable families are `plan_published`,
`plan_revised`, `obligation_published`, `assignment_recorded`, `decision_recorded`,
`action_recorded`, `result_recorded`, `evidence_recorded`, and `claim_recorded`.

The illustrative `event_id` and `occurred_at` above are shape only: mint a real UUIDv4 and use the
best real time available, exactly as for the schema examples.

### Reference mirrors

<a id="reference-mirrors"></a>

Some families mirror a payload reference into the draft envelope, and the two must match exactly —
the same ids in the same ascending ASCII order, with no extra and no omitted member. A mismatch is
rejected with reason `ref_mirror_mismatch`, and the public error names the envelope field:

- `result_recorded` and `response_recorded`: envelope `evidence_refs` equals the payload's own
  `evidence_refs`, or both are empty.
- `evidence_recorded`: envelope `artifact_refs` is exactly `[captured_object_id]`, or empty when
  the payload declares no captured object.
- `receipt_recorded`: envelope `artifact_refs` is exactly the payload's `receipt_object_id`.
- `redaction_recorded`: envelope `artifact_refs` equals the payload's own `target_object_ids`.

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
