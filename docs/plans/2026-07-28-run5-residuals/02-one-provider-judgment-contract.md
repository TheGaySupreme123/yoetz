# 02 — Generation and consumption must share one provider judgment contract

**Severity:** critical  
**PR boundary:** provider judgment schema, normalization, and invalid-result classification

## The defect

The Responses adapter sends a strict JSON Schema, but the Python consumer enforces a materially
stronger contract.

Examples accepted by the provider schema and rejected by `normalize_judgment` include:

- an unknown `finding_kind`;
- empty, duplicate, prose, task, or noncanonical `cited_refs`;
- valid refs in narrative rather than ASCII order;
- empty review text;
- a challenge paired with `insufficient_packet`;
- `challenges_returned` with no challenges.

All failures collapse to `response_schema_invalid`, and raw output is discarded. In the dogfood,
the three short zero-challenge responses passed; all four longer responses failed. The strongest
evidence is that Yoetz rejects the branch where the reviewer has something to say.

The adapter also maps provider status `incomplete` to `provider_timeout`. The observed “timeout”
carried a provider request ID and completed in about 16 seconds against a 60-second deadline,
consistent with output truncation at the hard-coded 2,048-token cap rather than a transport
timeout.

## Design

### 1. One owning model

Define one provider-facing judgment model and generate the constrained-output schema from it. The
same model feeds normalization tests. Do not maintain an independent hand-written schema and a
stricter constructor contract.

The generated contract must express every machine-enforceable invariant:

- closed `FindingKind` and next-step enums;
- one to sixteen citable refs for a challenge;
- ref prefix/pattern and uniqueness;
- non-empty bounded text;
- zero to three challenges;
- conclusion/challenge coupling through explicit union branches;
- no additional properties.

### 2. Normalize representation; validate meaning

Reference order has no semantic meaning. Validate each ref and uniqueness, then canonicalize into
ASCII order before constructing the domain judgment. Do not reject an otherwise valid challenge
only because a provider emitted narrative order.

Do not normalize invalid IDs, invented enums, empty prose, duplicated refs, or conclusion
contradictions into acceptance.

### 3. Separate failure classes without retaining plaintext

Preserve ADR-006 decision 8. Retain no raw provider response.

Record bounded structural facts sufficient to distinguish:

- empty or non-JSON output;
- constrained-schema mismatch;
- valid provider shape rejected by case-bound post-validation;
- provider refusal/cancellation;
- output truncation/incomplete response;
- actual transport/deadline timeout.

Use the existing closed semantic reasons where possible:
`response_schema_invalid`, `response_content_invalid`,
`semantic_judgment_rejected`, and `provider_timeout`. Add a reason only if none states the truth.
`incomplete` caused by output limits must not be labeled as a transport timeout.

### 4. Keep provider-profile honesty

The generated schema proves what Yoetz requested, not that each compatible host enforces it.
Per-profile capability fixtures and E-007 live evidence remain required. A host that returns a
nonconforming response degrades to an invalid semantic result, never a fabricated pass.

## Files

- `src/yoetz/adapters/providers/openai_responses.py`
- the Chat Completions adapter sharing the same judgment contract
- `src/yoetz/ports/semantic.py`
- `src/yoetz/protocol/models.py`
- generated schemas/fixtures and `docs/INTERFACES.md`
- provider request/normalization tests

## Tests

- Generated provider schema and consumer model stay digest/shape equivalent.
- Every `FindingKind` is admitted; invented and near-miss values are rejected at generation shape.
- Challenge refs enforce pattern, count, and uniqueness; valid unsorted refs normalize and pass.
- Conclusion/challenge coupling is enforced by the provider schema and consumer.
- Empty, fenced, prefixed, duplicate-key, float-bearing, and oversized output retain exact bounded
  failure classifications.
- Provider `incomplete` caused by output limit maps to content/truncation invalidity, not timeout.
- Real deadline/transport timeout still maps to `provider_timeout`.
- No test or diagnostic sink retains provider plaintext.

## Done

Any output that satisfies the machine-enforced provider schema can enter case-bound
post-validation; useful challenge output is not discarded by a second hidden schema.

## Dogfood observable

A deliberately requested citable challenge is accepted on the first conforming provider response,
or fails with an exact structural reason that identifies the contract stage without retaining raw
text.

## Out of scope

Selecting the semantic case content (plan 03) and orchestrating retries (plan 04).

