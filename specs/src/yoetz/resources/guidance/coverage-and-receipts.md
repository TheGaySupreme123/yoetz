# src/yoetz/resources/guidance/coverage-and-receipts.md — installed coverage-and-receipts guidance

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-007, ADR-010 | **Imports (spec-tree):**
`specs/guidance/coverage-and-receipts.md`, `specs/src/yoetz/resources/manifest.json.md` |
**Imported by:** `mcp/resources.md`, every harness adapter, packaging and capability tests

## Purpose

Define the installed byte-identical copy of the harness-neutral coverage-and-receipts guidance. The
file is a packaged resource, not a runtime-generated artifact.

It has exactly one packaged copy serving every consumer: the MCP resource registry serves it to any
host, and each harness adapter copies it into that harness's install layout. Per-harness packaged
copies do not exist (ADR-010).

## Public surface

- Logical resource: `guidance/coverage-and-receipts.md`.
- Installed package path: `src/yoetz/resources/guidance/coverage-and-receipts.md`.
- MCP resource URI: `yoetz://guidance/coverage-and-receipts.md`.

## Behavior

The installed copy matches the reviewed root guidance byte-for-byte. It teaches the six coverage
dimensions and their orderings, why coverage is a vector rather than a score, weakest-material-
dependency reasoning, deterministic-versus-semantic provenance, freshness/redaction/unknown/import
gaps, finding disposition, reviewer-challenge response patterns, the receipt field map, the approved
and forbidden completion wording, and the rule that only a recorded check bounds final wording while
a candidate read never does, exactly as reviewed at source.

It also carries the honest statement of what a host does and does not earn: publishing over MCP
yields `cooperative_mcp` with `self_asserted` authorship and `published_only` artifact observation,
and no v0.1 harness integration changes that. An exact capability cell may carry a trigger-only hook
that prompts `status`, but it observes nothing and leaves coverage unchanged; every v0.1
observation arm is absent.

The runtime does not synthesize, rewrite, template, or per-harness adapt the guidance. Manifest
verification checks byte size and SHA-256 before it is served or installed.

Serving this resource discloses no ledger, task, projection, provider, or user content, so it is not
a `LocalDisclosureSink` and creates no disclosure receipt.

## Errors and edge cases

- If the installed guidance diverges from source, the package or startup check fails.
- A missing guidance file or manifest mismatch blocks both MCP resource registration and skill
  activation; a partial guidance set is never served.
- A harness install whose copy differs from this resource fails byte parity.
- Text implying that a first-party integration or trigger-only hook strengthens coverage is invalid
  while every v0.1 observation arm remains absent.
- The file names no harness, install path, provider, or model.

## Invariants

1. The packaged copy is byte-identical to source and to every installed copy.
2. Exactly one packaged copy exists, regardless of how many harnesses ship.
3. Coverage language never outruns the public contract.
4. Host ergonomics and coverage strength are described as independent.
5. Serving it creates no disclosure receipt.

## Tests

- `specs/tests/packaging.md` — source/wheel parity, single-copy inventory, and identical bytes across
  every harness install.
- `specs/tests/capability.md` — retrieval by an unprofiled MCP host over `resources/read`.
- `specs/tests/conformance.md`

## Open questions

None.
