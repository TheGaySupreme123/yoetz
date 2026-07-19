# Publication policy

## Materiality checklist

Publish a fact when it changes an independently reviewable work package, a requested outcome, an obligation, a decision, a material attempt, a result, evidence, a claim, a plan, or a finding response. Keep routine navigation, searches, formatting, generated-file writes, repeated status reads, tool chatter, and per-file bookkeeping out of the ledger.

For a large inventory, create obligations per coherent work package. Publish one bounded manifest evidence item for its leaf files and one material package transition. Do not create one obligation or routine event per file.

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

## Subject state and freshness

Bind change-sensitive evidence to the exact subject state or frontier it concerns. If a material dependency changed or its state is unknown, mark the evidence stale or limited. Absence of visible source is not evidence that nothing changed.

## Batching, sequencing, and retry

Batch facts that belong to one material transition. Preserve writer sequence and expected frontier. On timeout, reuse the same request and operation IDs; never manufacture a replacement event merely because the response was lost.

## Multi-agent work

Publish bounded assignments and preserve each delegate's logical writer identity. Treat delegate summaries as claims. Link their accepted evidence separately and record a decision when resolving a contradiction.

## Forbidden content

Never publish chain-of-thought or hidden reasoning; full prompts, transcripts, or conversation history; credentials or secrets; whole files, repositories, or broad unrelated source. When semantic review would otherwise be blind, publish only the smallest problem-local changed hunk or enclosing symbol needed, with source, state, and coverage labels.

## Mini-flows

### Code change

Publish one obligation for the behavior, one material implementation result, a bounded changed-symbol digest or excerpt, and focused test evidence. Do not publish the repository or every edit.

### Research

Publish the question, source identities, bounded supported and contradicted claims, limitations, and the conclusion. Do not paste articles or browsing transcripts.

### Plan revision

Publish the original obligation, the material new fact, and one plan revision explaining which outcome or dependency changed. Routine schedule adjustment needs no event.

### Large generated inventory

For 100 files, group them into independently reviewable packages, record partial package status when useful, attach one bounded member manifest per completed package, and publish one final package transition. One obligation or routine event per file is invalid.
