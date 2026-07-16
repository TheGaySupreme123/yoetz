# src/yoetz/resources/guidance/agent-instructions.md — installed always-delivered agent instructions

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-009, ADR-010 | **Imports (spec-tree):**
`specs/guidance/agent-instructions.md`, `specs/src/yoetz/resources/manifest.json.md` |
**Imported by:** `mcp/descriptors.md`, `mcp/resources.md`, every harness adapter, packaging and
capability tests

## Purpose

Define the installed byte-identical copy of the tier-0 agent instructions. The file is a packaged
resource, not a runtime-generated artifact.

This resource is unusual in that it is served two ways from one copy: `mcp/descriptors.md` reads its
bytes as the MCP initialize `instructions` string, and `mcp/resources.md` also exposes it as an
ordinary readable resource. Both paths serve these exact bytes; the `instructions` string is never
composed, summarized, truncated, or templated at runtime.

## Public surface

- Logical resource: `guidance/agent-instructions.md`.
- Installed package path: `src/yoetz/resources/guidance/agent-instructions.md`.
- MCP resource URI: `yoetz://guidance/agent-instructions.md`.
- Also served verbatim as the MCP initialize `instructions` string.

## Behavior

The installed copy matches the reviewed root guidance byte-for-byte and stays within its 2 KiB
bound, because every host injects it into model context on every session.

Manifest verification checks byte size and SHA-256 before the bytes are used as `instructions` or
served as a resource. Verification failure is fatal to MCP startup rather than degraded: a server
that cannot prove its instruction bytes must not hand an agent unverified text that shapes what that
agent publishes.

Serving this resource discloses no ledger, task, projection, provider, or user content. It is static
reviewed product text, so it is not a `LocalDisclosureSink` and creates no disclosure receipt.

## Errors and edge cases

- If the installed guidance diverges from source, the package or startup check fails.
- A missing or digest-mismatched file fails MCP startup; the server never falls back to a built-in
  literal, an empty `instructions`, or a summary.
- Exceeding the 2 KiB bound fails packaging.
- A harness install whose copy differs from this resource fails byte parity.
- The file names no harness, install path, provider, model, or version.

## Invariants

1. The packaged copy is byte-identical to source, to the served `instructions` string, and to every
   installed copy.
2. Exactly one packaged copy exists, regardless of how many harnesses ship.
3. The bytes are ≤2 KiB and verified before use.
4. There is no runtime composition, summarization, or fallback path for `instructions`.
5. Serving it creates no disclosure receipt.

## Tests

- `specs/tests/packaging.md` — source/wheel parity, size bound, single-copy inventory, and identical
  bytes across every harness install.
- `specs/tests/subprocess.md` — the served `instructions` bytes equal the packaged resource bytes;
  a corrupted resource fails startup rather than serving unverified text.
- `specs/tests/capability.md` — an unprofiled MCP host receives the instructions at initialize.

## Open questions

None.
