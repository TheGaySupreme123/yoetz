# Yoetz cooperative workflow

## What Yoetz does and does not do

Yoetz is a local ledger for bounded, participant-published work facts and a deterministic checker of that record. It does not observe the workspace, enforce a process, authenticate authorship, record hidden reasoning, or prove that work is correct.

## When to activate

Use Yoetz for material multi-step work, multiple requested outcomes, delegation, meaningful verification, long-running or resumable work, or a material completion claim. Skip it for translation, ordinary questions, explanations, and trivial edits where the ceremony would exceed the integrity benefit.

## Startup and availability disclosure

Tell the user briefly that Yoetz is being used as a local work ledger and verifier. Do not imply initialization succeeded before `start` returns. If the optional service is unavailable, continue unless the user or host requires it, disclose that no live ledger or receipt will exist, and invent no state.

## The ten steps

1. Decide whether the task is material enough for Yoetz.
2. Start or attach with stable request identity and the intended create or attach semantics.
3. Publish a bounded plan, requested outcomes, acceptance evidence, and assignments. Group large inventories into independently reviewable work packages; files are leaf evidence, not automatic obligations.
4. Delegate with the session, task, distinct logical writer, and bounded assignment context. Do not send or publish full transcripts.
5. Publish material work-package transitions: assignment, decision, blocked attempt, independently useful result, completion, or revision. Omit routine reads, searches, formatting, and per-file mechanics.
6. Stay next to the record. After resume, compaction, handoff, or uncertainty about what is already done or committed, call `status`. `view=candidate_findings` is an advisory read: it creates no verdict, IDs, receipt, or event.
7. Before completion, publish the intended material completion claim and current evidence, then call `check`. Read `closure_readiness` on any `status` result first: it names the open obligations, unresolved findings, and declared gaps that currently bound a conclusion. Spending a check or receipt while those stand returns a predictably insufficient result; resolve or explicitly record them instead. Choose mode deliberately: `semantic_if_configured` for most material implementation/review claims; `semantic_required` when completion depends on qualitative correctness, design conformance, security/privacy reasoning, interoperability, or whether the code satisfies the ask; `deterministic_only` only for explicitly local/structural checks, semantic-disabled policy, or a deliberate no-egress choice — and disclose that limitation. Publish the smallest state-bound diff/symbol and the directly relevant test or failure excerpt; never rely on self-asserted completion prose alone.
8. Respond to each challenge by accepting and acting, supplying evidence, revising the claim, disputing with evidence, or stating an unresolved limitation. A response does not erase a finding.
9. Recheck after any material edit, evidence change, plan change, or finding response.
10. Request a receipt and keep the final answer no stronger than its weakest material coverage, freshness, unresolved findings, and limitations. All receipt formats (`json`, `markdown`, `text`) project under default policy; if a stricter owner policy blocks `json`, re-request `markdown` or `text`.

## State the record you changed

Using Yoetz is itself a state change. A run that starts a task, advances the ledger, or obtains a check or receipt has changed durable local state even when it edited no product file. Separate the two in the final answer instead of collapsing them.

Permitted: “No product source, provider configuration, credential binding, or privacy authorization was changed. This run created a Yoetz task, published N events, and recorded one check and one receipt.”

Forbidden: “Nothing changed” or “no runtime state changed” after a real session, publication, check, or receipt.

Reuse the original request and operation IDs after timeout or reconnect. A timeout has unknown outcome; retry idempotently or inspect status. An operation that reports failure after its write may have committed: read `status` for the authoritative frontier before assuming it failed. Prefer `status view=operation` with the write's `request_id` as a state lookup without reconstructing the body — a complete `publish_work` surfaces stored frontiers and accepted event ids; pending, quarantined, absent, and non-publish states report only what is honest for that state. When replaying a write, reuse the same `request_id` rather than composing a new one — a matching body returns the stored result, and a different body returns `REQUEST_IDENTITY_CONFLICT` with the committed frontier rather than re-appending.

## Multi-agent attribution and handoff

The parent publishes assignments and gives each delegate a distinct logical writer identity. A delegate publishes its own bounded claims; the parent neither impersonates it nor upgrades self-asserted authorship. Before integration, read current assignments, decisions, contradictions, and obligations. A delegate summary is a claim, not proof, and contradictions remain visible until a recorded decision resolves them.

## Resume and compaction

On resume, attach to the existing task and read status before reconstructing work from memory. Preserve request and writer sequences and do not duplicate a prior publication. A trigger, when an exact capability profile proves one, may prompt the same bounded re-grounding; it observes nothing and changes no coverage.

## Findings and recheck

Candidate findings are what deterministic packs currently say about the record. They carry no verdict and cannot be cited as a check. An empty candidate list means only that no rule fired in that advisory read. Only a recorded check can support receipt-bounded completion wording.

## Degraded and unavailable behavior

Never invent success. State the unavailable or degraded boundary, continue ordinary work when allowed, and do not claim a live task, finding, verdict, or receipt. If the host requires Yoetz, stop at that host-owned requirement.

## Safety and privacy

Publish no hidden reasoning, transcript, secret, broad repository content, or unrelated source. Prefer typed facts, digests, bounded counts, and only the smallest material state-bound excerpt. See [publication policy](publication-policy.md) and [coverage and receipts](coverage-and-receipts.md).
