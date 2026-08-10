# Coverage and receipts

## Coverage is a vector

Coverage has six independent dimensions. Do not collapse them into a score or let strength in one compensate for weakness in another:

- participation: `cooperative_mcp`, `local_cli`, `codex_jsonl_import`, or the applicable recorded mode;
- authorship: `self_asserted`, `harness_observed`, or stronger only where the frozen contract permits it;
- artifact observation: `published_only`, `hook_observed`, or an exact stronger reviewed state;
- content visibility: `none`, `digest_only`, `targeted_excerpt`, or the applicable reviewed content class;
- provenance: deterministic, semantic-provider, imported, or participant-asserted as recorded;
- freshness: current, stale, unknown, or redacted according to the named frontier and subject state.

Use the exact enum values returned by the protocol; this reference does not create additional values. The weakest material dependency bounds the conclusion.

## Evidence and provenance

Deterministic evidence says what a reviewed rule computed from the accepted record. Semantic evidence retains provider, model, policy, request, response, and review provenance. Imported evidence never gains cooperative authorship merely because Yoetz stores it. A digest records identity, not content inspection. TOML, path, or metadata construction is not proof of SDK wire dispatch or semantic review (Yoetz cooperative/evidence boundary).

Digest-bearing evidence separates four facts: the evidence family, the exact byte subject, whether
the bytes were retained, and who established that binding. Ordinary publication remains
`caller_asserted` even when it supplies a valid SHA-256 digest. Only the approved-check service path
may record `approved_check`, and only the trusted importer may record `import_observed`.

Relevant limitations appear as exact coverage gaps:

- `evidence_digest_subject_legacy_unknown`: a historical digest record does not say what bytes were hashed;
- `evidence_content_digest_only`: the typed record retained identity but not the bytes;
- `evidence_content_withheld`: the publisher explicitly withheld the bytes.

These gaps make the conclusion coverage-incomplete. They do not establish that the evidence is
false. Unrelated historical evidence is not pulled into a current check merely because it remains
in the ledger.

## Freshness, redaction, and unknown input

Evidence bound to an older material state is stale. Hidden, redacted, or unknown-schema material remains a limitation rather than being treated as absent. An import gap is a gap, not an unchanged-state fact.

## Findings and responses

For a finding, choose one recorded response: accept and act; provide additional evidence; revise the claim; dispute with evidence; or state an unresolved limitation. Then recheck after material change. A response never deletes the original challenge.

No disposition resolves a finding. `acknowledged`, `rejected`, and `waived` each record what you decided and what evidence you attached; none of them clears the finding for receipt purposes. Every actionable finding recorded in a task keeps the receipt conclusion at `unresolved_findings_remain`, even when later checks return no findings at all. Word the final answer accordingly: the repair is real and the disposition is recorded, but the receipt is not a clean completion receipt and must not be described as one.

## Coverage attribution

A recorded check remains attributable to a later receipt when the only events between them are responses to findings that same check returned. Answering a check's own findings reports on the check; it does not publish untested work. The receipt then folds the check's coverage — including `semantic_model_derived` — and carries the gap `check_current_as_of_earlier_frontier`, naming the subject frontier that was actually tested.

That gap is a limitation, not a clean state: the verdict is current as of the tested frontier, not the receipt's, so the receipt is still coverage-incomplete and must not be described as a clean completion receipt.

Any other material event after the check — published work, a new finding, a response to a finding the check did not return, or a response whose payload is redacted or unreadable (it cannot prove which finding it answered) — requires a re-check before the receipt. The receipt reports `check_not_applicable` and the check contributes nothing until you re-run it at the current frontier.

`status` applies the same rule, so a compact status view and a receipt taken at the same frontier never disagree about what was checked.

## State examples

- Same state: evidence may remain current when its exact state binding still matches.
- Asserted change without observation: record the assertion and keep artifact observation limited.
- Observed change with hidden content: record observation without claiming content review.
- Reviewed targeted content: record only the bounded excerpt and its exact provenance.

## Candidate findings are not a check

`status` with `view=candidate_findings` is an advisory read of what deterministic packs currently say. Candidates have no verdict, IDs, or receipt and the read records nothing. An empty list means no rule fired at that frontier; it is not `no_issue_detected`.

Permitted: “I saw an unresolved attempt and went back to it.”

Forbidden after only a candidate read: “I checked and found nothing.”

## Check mode and semantic coverage

A clean deterministic-only check is not an implementation review. When `mode=deterministic_only` (or semantic status is `not_requested`), the receipt/check coverage includes `semantic_review_not_requested` and completeness is coverage-incomplete even if the verdict is `no_issue_detected`. Prefer `semantic_if_configured` for material claims; reserve `deterministic_only` for structural checks and disclose the limitation.

A non-succeeding `semantic_status` is a coverage gap, not a failure to retry away.

- `not_configured`, `blocked_by_policy`, and `human_denied` will not change without owner action: take the first answer.
- `unavailable` and `timeout` are the only statuses retried inside a job, and only for a transport-unavailable, provider-timeout, or rate-limited reason. By the time you see one, that job already spent its own attempt budget.
- `refused`, `invalid`, and `failed` are not retried inside the job at all, so a fresh request is a fresh gamble rather than a continuation.

When a second job in one session again returns no judgment, stop requesting semantic review: run `deterministic_only` and say in the final answer that semantic review was requested and did not run, naming the recorded `semantic_status` and `semantic_reason`. A terminal reason such as `retry_budget_exhausted` describes the retry outcome, not the initiating cause; do not present it as a diagnosis.

## Check scope

<a id="check-scope"></a>

`scope` is optional and has exactly two admitted shapes. Omit it to check the whole case, or send
both `claim_ids` and `obligation_ids` together as arrays of unique ids. Two empty arrays also mean
the whole case, so `{"claim_ids": [], "obligation_ids": []}` and an omitted `scope` are the same
request. Sending only one of the two keys is rejected: the other is reported as missing, and the
repair is to add it or to drop `scope` entirely.

## Receipt format

Default agent-context policy can project verification output (findings, obligations, receipt sections) so `json`, `markdown`, and `text` receipts work for the requesting agent. Under a deliberately stricter owner policy, digest-bound `json` may fail closed with `PRIVACY_AUTHORITY_REQUIRED` (`receipt_json_projection_blocked`); re-request `markdown` or `text`, or widen agent-context policy from a local terminal. The durable receipt is still recorded when projection is blocked.

## Receipt fields and wording

Read the receipt's frontier, verdict, coverage vector, finding disposition, evidence provenance, freshness, suppressed counts, and limitations together. Derived Markdown is a human view of the same structured record. Only a current recorded check can bound final wording. Receipts are frontier-bound: they do not upgrade caller-asserted event timestamps into service-checked event time.

Permitted: “Yoetz found no deterministic issue in the cooperatively published record at the stated frontier; artifact observation remained published-only.”

Forbidden: “Yoetz proved the implementation is complete and correct.”

Installing a harness integration or firing a trigger-only hook does not strengthen coverage. A proven trigger may prompt a bounded status re-grounding; it observes nothing and changes no coverage. Only a capability-proven, consented observation arm with real observation evidence may earn `hook_observed`; an absent, empty, paused, or degraded observation status does not.
