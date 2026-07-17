# src/yoetz/resources/guidance/workflow.md — installed cooperative-workflow guidance

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-009, ADR-010 | **Imports (spec-tree):**
`specs/guidance/workflow.md`, `specs/src/yoetz/resources/manifest.json.md` |
**Imported by:** `mcp/resources.md`, every harness adapter, packaging and capability tests

## Purpose

Define the installed byte-identical copy of the harness-neutral ten-step cooperative workflow. The
file is a packaged resource, not a runtime-generated artifact.

It has exactly one packaged copy serving every consumer: the MCP resource registry serves it to any
host, and each harness adapter copies it into that harness's install layout. Per-harness packaged
copies do not exist (ADR-010).

## Public surface

- Logical resource: `guidance/workflow.md`.
- Installed package path: `src/yoetz/resources/guidance/workflow.md`.
- MCP resource URI: `yoetz://guidance/workflow.md`.

## Behavior

The installed copy matches the reviewed root guidance byte-for-byte. It teaches activation and
disclosure, the ten-step workflow, multi-agent attribution and handoff, resume and compaction
recovery and staying next to the record during the work, finding response and recheck,
receipt-bounded final wording, degraded behavior, and the safety and privacy rules, exactly as
reviewed at source. Its batching rule groups large inventories by coherent work package and keeps
leaf-file inventories in bounded manifest evidence rather than creating per-file obligations or
routine events.

The runtime does not synthesize, rewrite, template, or per-harness adapt the guidance. Manifest
verification checks byte size and SHA-256 before it is served or installed.

Serving this resource discloses no ledger, task, projection, provider, or user content, so it is not
a `LocalDisclosureSink` and creates no disclosure receipt.

## Errors and edge cases

- If the installed guidance diverges from source, the package or startup check fails.
- A missing guidance file or manifest mismatch blocks both MCP resource registration and skill
  activation; a partial guidance set is never served.
- A harness install whose copy differs from this resource fails byte parity.
- Exceeding the size bound fails packaging rather than shipping a document hosts will truncate.
- The file names no harness, install path, provider, or model.

## Invariants

1. The packaged copy is byte-identical to source and to every installed copy.
2. Exactly one packaged copy exists, regardless of how many harnesses ship.
3. The workflow stays offline and reviewable.
4. The installed resource never widens the public operation contract.
5. Serving it creates no disclosure receipt.

## Tests

- `specs/tests/packaging.md` — source/wheel parity, size bound, single-copy inventory, and identical
  bytes across every harness install.
- `specs/tests/capability.md` — an unprofiled MCP host retrieves it over `resources/read` and
  completes the ten-step workflow with no installed skill.
- `specs/tests/conformance.md`

## Open questions

None.
