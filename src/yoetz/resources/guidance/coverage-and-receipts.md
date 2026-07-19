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

Deterministic evidence says what a reviewed rule computed from the accepted record. Semantic evidence retains provider, model, policy, request, response, and review provenance. Imported evidence never gains cooperative authorship merely because Yoetz stores it. A digest records identity, not content inspection.

## Freshness, redaction, and unknown input

Evidence bound to an older material state is stale. Hidden, redacted, or unknown-schema material remains a limitation rather than being treated as absent. An import gap is a gap, not an unchanged-state fact.

## Findings and responses

For a finding, choose one recorded response: accept and act; provide additional evidence; revise the claim; dispute with evidence; or state an unresolved limitation. Then recheck after material change. A response never deletes the original challenge.

## State examples

- Same state: evidence may remain current when its exact state binding still matches.
- Asserted change without observation: record the assertion and keep artifact observation limited.
- Observed change with hidden content: record observation without claiming content review.
- Reviewed targeted content: record only the bounded excerpt and its exact provenance.

## Candidate findings are not a check

`status` with `view=candidate_findings` is an advisory read of what deterministic packs currently say. Candidates have no verdict, IDs, or receipt and the read records nothing. An empty list means no rule fired at that frontier; it is not `no_issue_detected`.

Permitted: “I saw an unresolved attempt and went back to it.”

Forbidden after only a candidate read: “I checked and found nothing.”

## Receipt fields and wording

Read the receipt's frontier, verdict, coverage vector, finding disposition, evidence provenance, freshness, suppressed counts, and limitations together. Derived Markdown is a human view of the same structured record. Only a current recorded check can bound final wording.

Permitted: “Yoetz found no deterministic issue in the cooperatively published record at the stated frontier; artifact observation remained published-only.”

Forbidden: “Yoetz proved the implementation is complete and correct.”

Installing a harness integration or firing a trigger-only hook does not strengthen coverage. In v0.1 every observation arm is absent. A proven trigger may prompt a bounded status re-grounding; it observes nothing and changes no coverage.
