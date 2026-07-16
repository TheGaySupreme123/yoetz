# guidance/coverage-and-receipts.md — harness-neutral coverage and receipt reference

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-007, ADR-010 | **Imports (spec-tree):**
`specs/INTERFACES.md`, `guidance/README.md`, `specs/src/yoetz/domain/receipts.md`,
`specs/src/yoetz/protocol/coverage.md` | **Imported by:** `mcp/resources.md`, every harness skill
spec, capability/packaging tests

## Purpose

Define the reference document that teaches how coverage, findings, freshness, and receipt wording
work together at completion time. It gives an agent the exact conservative language needed to avoid
overstating evidence.

This document is harness-neutral and owned once (ADR-010). It reaches an unprofiled MCP host as the
`yoetz://guidance/coverage-and-receipts.md` resource and a first-party harness as an installed file;
both are the same bytes.

It is also where the coverage difference between hosts is explained honestly: an agent publishing
over MCP earns `cooperative_mcp` with `self_asserted` authorship and `published_only` artifact
observation, and no harness integration in v0.1 changes that. A harness that later supplies
observation hooks can earn `hook_observed`; until then, a first-party integration buys ergonomics,
never a stronger claim.

## Public surface

- `guidance/coverage-and-receipts.md` — public markdown reference, bounded (target ≤12 KiB), with
  stable headings so an agent can retrieve one section. Addressed by the logical resource name
  `guidance/coverage-and-receipts.md`.

## Behavior

The reference contains:

- the six coverage dimensions, exact enum values, and default ordering;
- why coverage is a vector rather than a score;
- weakest-material-dependency examples;
- deterministic-vs-semantic origin and provenance rules;
- freshness, redaction, unknown-schema, and import-gap examples;
- finding disposition semantics;
- reviewer-challenge response patterns (`accept/act`, `provide evidence`, `revise claim`, `dispute
  with evidence`, `state unresolved limitation`) mapped to existing respond/publish/recheck calls;
- the difference between same state, asserted-but-unobserved change, observed change with hidden
  content, and reviewed targeted change content;
- JSON receipt field mapping and derived markdown rules;
- approved and forbidden completion-wording examples;
- the rule that only a recorded `check` bounds final wording, and that a `status`
  `view=candidate_findings` read never does.

That last rule needs its own short section, because the view is designed to be called often and
returns something that looks like a check result. It states: candidate findings are what the
deterministic packs say about the record right now; they carry no verdict, no IDs, and no receipt,
and reading them is not checking. An empty candidate list means no rule fired at that frontier — it
is not `no_issue_detected`, which only a recorded check produces. The agent may act on candidates
freely, and should; it may not report them, cite them, or let them stand in for the check before a
completion claim. The permitted use is "I saw an unresolved attempt and went back to it." The
forbidden sentence is "I checked and found nothing," said after a candidate read.

The reference stays no stronger than the owning protocol. It uses wording such as “recorded claim”
and “digest recorded” where the evidence is self-asserted or partial, and it treats stale or
redacted material as a real limitation.

## Errors and edge cases

- If the coverage registry or receipt schema changes, the reference must change with it.
- A wording example that claims proof, verification, or completeness beyond the frozen coverage is
  invalid.
- Text that presents a candidate read as a check, or an empty candidate list as a clean result, is
  invalid. The view exists so an agent can correct itself during the work, not so it can reach a
  conclusion without recording one.
- Missing or corrupt reference bytes must fail MCP resource registration and installed-skill
  validation.
- Text implying that installing a first-party integration strengthens coverage is invalid while every
  v0.1 `HarnessProfile.hooks` is `None`.
- Naming a harness, install path, provider, or model fails the public-boundary scan.

## Invariants

1. Coverage language never outruns the public contract.
2. Receipt wording is bounded by the weakest material dependency.
3. The reference remains usable offline.
4. Missing source visibility is always a coverage limitation, never an unchanged-state fact.
5. The document is harness-neutral and byte-identical wherever it is served.
6. Host ergonomics and coverage strength are described as independent.

## Tests

- `specs/tests/capability.md` — including retrieval by an unprofiled MCP host with no installed
  skill.
- `specs/tests/conformance.md`
- `specs/tests/packaging.md` — byte parity across the resource and every harness install, plus the
  size bound.

## Open questions

None.
