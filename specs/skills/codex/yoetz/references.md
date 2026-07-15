# skills/codex/yoetz/references/ — installed skill reference specifications

**Wave:** D | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):** `INTERFACES.md`, operation
and event schemas | **Imported by:** the Codex Yoetz skill and packaging manifest

## Purpose

Define the two concise reference documents shipped beside the skill. They carry lookup material
that should not crowd the main workflow instructions. They are normative only where they restate
the frozen public schemas and coverage rules; conflicts are build failures.

## Public surface

### `references/publication-policy.md`

Contains:

- a materiality decision checklist with positive/negative examples;
- the 16 event-family cheat sheet: when to use each, minimum fields, and what it does not prove;
- obligation/evidence/claim relationships;
- subject-state binding and stale-evidence examples;
- event batching, writer-sequence, expected-frontier, and retry examples;
- multi-agent assignment/attribution rules;
- forbidden-content checklist;
- problem-local excerpt guidance and three worked mini-flows: code change, research task, plan revision.

### `references/coverage-and-receipts.md`

Contains:

- all six coverage dimensions, exact enum values/orderings, and conservative channel defaults;
- why coverage is a vector rather than a score;
- weakest-material-dependency examples;
- deterministic vs semantic origin/provenance;
- freshness, redaction, unknown-schema, and import gaps;
- finding disposition semantics;
- reviewer-challenge response/recheck patterns and honest change/content-visibility distinctions;
- JSON receipt field map and derived Markdown rules;
- approved/forbidden completion wording examples.

Both begin with skill/schema version compatibility and link to no unpinned remote content required
for correct operation.

### Exact future-file inventory

This index covers exactly these separately owned future files:

```text
skills/codex/yoetz/references/coverage-and-receipts.md
skills/codex/yoetz/references/publication-policy.md
```

## Behavior

The publication reference uses tables generated/checked from the registry but reviewed as stable
Markdown. Every event example contains valid typed IDs, canonical integer strings, timestamps, enum
spelling, sorted set arrays, and bounded payloads. Examples explicitly label illustrative IDs and
never become golden digest vectors unless copied into `fixtures/canonical`.

The coverage reference derives ordering tables from `protocol/coverage`. Unordered dimensions
are described as kinds, never ranked. It includes at least these wording transformations:

- self-asserted + published-only → “recorded claim,” not “observed fact”;
- content digest without captured bytes → “digest recorded,” not “artifact inspected”;
- stale-after-material-change → prior evidence cannot support current completion;
- imported observation → observed in bounded Codex JSONL, not complete internal trace;
- semantic model-derived → advisory judgment with provider/profile provenance;
- content not recorded/selected/authorized → explicit visibility gap, never “no code changed”;
- redacted/unknown event → explicit gap, never silently omitted.

Reference size is bounded (target ≤12 KiB each), headings are stable for agent retrieval, and the
main skill links to the narrow section relevant to each step.

## Errors and edge cases

- Generated registry/schema drift blocks build; references are never auto-updated without a review
  diff.
- An event added in a future schema requires a new compatible reference version; an old skill must
  not invent instructions for it.
- Examples containing user-like secrets, real repositories, or production session identifiers fail
  publication scans.
- If a reference file is missing/corrupt, skill installation and capability validation fail rather
  than installing a partial workflow.

## Invariants

1. References never widen the public contract.
2. Coverage language is no stronger than the owning protocol.
3. Examples teach material records, not transcript telemetry.
4. Source, packaged, and installed reference bytes are identical.
5. Correct use is possible offline.

## Tests

- Event/enum/field tables match `INTERFACES.md` and frozen schemas.
- Every JSON example passes strict parser/model/schema validation.
- Wording-lint rejects “verified,” “proved,” “authenticated,” and “complete” unless the surrounding
  example states the exact sufficient coverage.
- Source/wheel/installed byte parity and size bounds.
- Skill evaluation retrieves the right reference section for publication, resume, finding response,
  and final receipt tasks.

## Open questions

None.

Combined handbook rendering is deferred to v0.2.
