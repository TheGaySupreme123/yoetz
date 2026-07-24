# guidance/publication-policy.md — harness-neutral publication policy reference

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-010 | **Imports (spec-tree):**
`specs/INTERFACES.md`, `guidance/README.md`, `guidance/workflow.md` | **Imported by:**
`mcp/resources.md`, every harness skill spec, capability/packaging tests

## Purpose

Define the concise reference document that teaches when material work should be published into the
Yoetz ledger and when it should stay out of the record. It is normative only insofar as it restates
the frozen public contract.

This document is harness-neutral and owned once (ADR-010). It reaches an unprofiled MCP host as the
`yoetz://guidance/publication-policy.md` resource and a first-party harness as an installed file;
both are the same bytes.

## Public surface

- `guidance/publication-policy.md` — public markdown reference, bounded (target ≤12 KiB), with
  stable headings so an agent can retrieve one section. Addressed by the logical resource name
  `guidance/publication-policy.md`.

## Behavior

The reference contains:

- a materiality checklist with positive and negative examples;
- the 16 event-family cheat sheet, including when each family is appropriate and what it does not
  prove;
- obligation/evidence/claim relationships;
- subject-state binding and stale-evidence examples;
- event batching, writer-sequence, expected-frontier, and retry examples;
- multi-agent assignment and attribution rules;
- forbidden-content guidance, including the semantic-case publication duty: for a material
  completion check, publish the smallest state-bound diff or symbol plus the directly relevant test
  or failure excerpt, because self-asserted completion prose alone leaves semantic review blind;
- four worked mini-flows: code change, research task, plan revision, and a large generated or
  migrated inventory. The inventory flow groups a 100-file result into independently reviewable
  work-package obligations, publishes material package transitions, and uses one bounded manifest
  evidence item for the leaf files in each completed package. It contrasts that with forbidden
  one-obligation-per-file and routine per-file event streams. The code-change flow shows
  a bounded state-bound changed hunk/enclosing symbol plus linked test/failure evidence, and
  contrasts it with forbidden repository-wide or unrelated source publication;

All examples use stable typed IDs, canonical integers, timestamp strings, and bounded payloads.
The document does not widen the public workflow contract and does not introduce new event families.

## Errors and edge cases

- If the event registry changes, the reference must be updated in lockstep or the build fails.
- Example payloads that contain secrets, real repository paths, or production identifiers are
  invalid.
- A reference that contradicts `INTERFACES.md` is a build failure, not a soft warning.
- Naming a harness, install path, provider, or model fails the public-boundary scan; harness-specific
  ergonomics belong in that harness's own skill spec.
- Exceeding the size bound fails packaging rather than shipping a document hosts will truncate.
- Treating enumerable files or tool calls as the publication unit fails conformance; agents must be
  able to name a coherent work package and its acceptance boundary.

## Invariants

1. The reference teaches publication discipline, not hidden reasoning or full transcripts.
2. Examples remain small, concrete, and offline-verifiable.
3. The 16-family cheat sheet matches the frozen registry exactly.
4. Problem-local excerpts remain ordinary evidence in existing event families; they never create a
   source-browsing operation or imply independent observation.
5. The document is harness-neutral and byte-identical wherever it is served.
6. Work-package transitions are the normative batching unit; leaf files belong in bounded manifest
   evidence and do not automatically become obligations or events.

## Tests

- `specs/tests/capability.md` — including retrieval by an unprofiled MCP host with no installed
  skill.
- `specs/tests/conformance.md`
- The large-inventory fixture rejects per-file obligation/event amplification and accepts grouped
  work packages with bounded manifests, partial-package status, and one final package transition.
- `specs/tests/packaging.md` — byte parity across the resource and every harness install, plus the
  size bound.

## Open questions

None.
