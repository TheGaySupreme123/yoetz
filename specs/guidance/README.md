# guidance/ — harness-neutral agent guidance specifications

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-010 | **Imports (spec-tree):** `specs/INTERFACES.md`,
operation and event schemas | **Imported by:** `mcp/descriptors.md`, `mcp/resources.md`,
`skills/codex/yoetz/SKILL.md`, every future harness adapter, packaging and capability tests

## Purpose

Own the agent-facing guidance content exactly once, harness-neutrally, so that no harness owns it
and every harness ships the same bytes.

Yoetz works with any agent through MCP. Codex is the first harness with a first-party integration
because its skill/hook surfaces let Yoetz deliver that guidance ergonomically — not because the
guidance is about Codex. Splitting the two is what lets a fork add a first-party harness by writing
an adapter and a `HarnessProfile`, never by editing or copying this content (ADR-010).

Nothing here is Codex-specific. Harness-specific material — frontmatter shape, activation and
discovery semantics, install layout, capability profiles — lives with that harness's adapter and
skill spec.

## Public surface

### Exact future-file inventory

This index covers exactly these separately owned future files:

```text
guidance/agent-instructions.md
guidance/workflow.md
guidance/publication-policy.md
guidance/coverage-and-receipts.md
```

Each is packaged byte-identically under `src/yoetz/resources/guidance/` and addressed by the
logical resource name `guidance/<basename>`.

### Delivery tiers

The same four documents reach an agent through exactly three tiers, weakest host requirement first:

| Tier | Reaches | Carries | Host requirement |
|---|---|---|---|
| 0 | every MCP host, always | `agent-instructions.md` as the initialize `instructions` string | none |
| 1 | any MCP host that reads resources | all four, as `yoetz://guidance/<name>` resources | MCP resource support |
| 2 | a first-party harness | all four, installed on disk in the harness's layout | an installed integration |

Tier 0 must stand alone: it is the only tier guaranteed to arrive, so it carries every rule whose
absence would cause harm — not a summary that depends on tier 1 being read. Tiers 1 and 2 enrich;
they never correct or contradict tier 0.

## Behavior

`agent-instructions.md` is bounded hard, because every host injects it into model context on every
session. It states what Yoetz is and is not, the never-publish list, check-before-claiming-
completion, never inventing Yoetz state, and where to read more. Target ≤2 KiB.

`workflow.md` owns the ten-step cooperative workflow. `publication-policy.md` and
`coverage-and-receipts.md` own the material previously specified under `skills/codex/yoetz/
references/`; their content contract is unchanged by the move. Each is bounded (target ≤12 KiB),
uses stable headings so agents can retrieve one section, and links to no unpinned remote content
required for correct operation.

These documents are normative only where they restate the frozen public schemas, coverage rules,
and `INTERFACES.md` registry. Conflicts are build failures, never per-document interpretation.

A harness adapter may choose layout, filename, and header; it may not vary a byte of these members.
Two harnesses installing `publication-policy.md` install identical bytes.

Guidance is static reviewed product text. It contains no ledger, task, projection, provider, or user
content, so serving it is not a `LocalDisclosureSink` and creates no disclosure receipt.

## Errors and edge cases

- Generated registry/schema drift blocks build; guidance is never auto-updated without a review diff.
- A member that exceeds its size bound fails packaging rather than shipping a document hosts will
  truncate.
- A harness adapter that rewrites, reflows, templates, or per-harness edits a member fails byte
  parity.
- Guidance that names a harness, an install path, a provider, or a mutable network reference fails
  the public-boundary scan.
- If a member is missing or corrupt, MCP resource registration and skill installation fail closed
  rather than serving partial guidance.
- Tier 0 text that defers a harm-avoiding rule to tier 1 is a review failure, because tier 1 is not
  guaranteed to be read.

## Invariants

1. Guidance is owned once and is harness-neutral.
2. Source, packaged, and every installed copy are byte-identical.
3. Guidance never widens the public contract.
4. Coverage language is no stronger than the owning protocol.
5. Tier 0 is self-sufficient for every rule whose absence would cause harm.
6. Adding a harness adds no guidance file and edits none.
7. Correct use is possible offline.

## Tests

- `specs/tests/packaging.md`: source/wheel/installed byte parity, size bounds, exact member
  inventory, and identical bytes across every harness install.
- `specs/tests/capability.md`: an MCP host with no integration reaches tier 0 and tier 1 and can
  complete the workflow.
- `specs/tests/conformance.md`: event/enum/field tables match `INTERFACES.md` and frozen schemas;
  every JSON example passes strict parser/model/schema validation; wording-lint rejects "verified",
  "proved", "authenticated", and "complete" unless the surrounding example states the exact
  sufficient coverage.
- The public-boundary scan proves no member names a harness, path, provider, or remote reference.

## Open questions

None.

Localization beyond English is deferred to v0.2. Combined handbook rendering is deferred to v0.2.
