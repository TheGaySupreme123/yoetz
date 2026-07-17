# src/yoetz/resources/guidance/publication-policy.md — installed publication-policy guidance

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-010 | **Imports (spec-tree):**
`specs/guidance/publication-policy.md`, `specs/src/yoetz/resources/manifest.json.md` |
**Imported by:** `mcp/resources.md`, every harness adapter, packaging and capability tests

## Purpose

Define the installed byte-identical copy of the harness-neutral publication-policy guidance. The
file is a packaged resource, not a runtime-generated artifact.

It has exactly one packaged copy serving every consumer: the MCP resource registry serves it to any
host, and each harness adapter copies it into that harness's install layout. Per-harness packaged
copies do not exist, which is what makes drift structurally impossible rather than merely tested
(ADR-010).

## Public surface

- Logical resource: `guidance/publication-policy.md`.
- Installed package path: `src/yoetz/resources/guidance/publication-policy.md`.
- MCP resource URI: `yoetz://guidance/publication-policy.md`.

## Behavior

The installed copy matches the reviewed root guidance byte-for-byte. It teaches publication
materiality, the 16 event-family cheat sheet, obligation/evidence/claim relationships,
subject-state binding, batching/retry, multi-agent attribution, forbidden content, and the four
mini-flows exactly as reviewed at source, including
bounded problem-local excerpt guidance. The large-inventory example makes work-package transitions
the publication unit and treats leaf files as bounded manifest evidence.

The runtime does not synthesize, rewrite, template, or per-harness adapt the guidance. Manifest
verification checks byte size and SHA-256 before it is served or installed.

Serving this resource discloses no ledger, task, projection, provider, or user content. It is static
reviewed product text, so it is not a `LocalDisclosureSink` and creates no disclosure receipt.

## Errors and edge cases

- If the installed guidance diverges from source, the package or startup check fails.
- A missing guidance file or manifest mismatch blocks both MCP resource registration and skill
  activation; a partial guidance set is never served.
- A harness install whose copy differs from this resource fails byte parity.
- The file is not allowed to pull live network examples or private transcript snippets.
- The file names no harness, install path, provider, or model.

## Invariants

1. The packaged copy is byte-identical to source and to every installed copy.
2. Exactly one packaged copy exists, regardless of how many harnesses ship.
3. The guidance stays offline and reviewable.
4. The installed resource never expands the event registry.
5. Serving it creates no disclosure receipt.

## Tests

- `specs/tests/packaging.md` — source/wheel parity, single-copy inventory, and identical bytes across
  every harness install.
- `specs/tests/capability.md` — retrieval by an unprofiled MCP host over `resources/read`.
- `specs/tests/conformance.md`

## Open questions

None.
