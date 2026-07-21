# guidance/agent-instructions.md — the always-delivered agent instructions

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-009, ADR-010, ADR-015, ADR-016 | **Imports (spec-tree):**
`guidance/README.md`, `guidance/workflow.md`, `guidance/publication-policy.md`,
`guidance/coverage-and-receipts.md` | **Imported by:** `mcp/descriptors.md`, `mcp/resources.md`,
every harness skill spec, packaging and capability tests

## Purpose

Own the one guidance document that reaches every agent unconditionally. `mcp/descriptors.md` serves
its bytes as the MCP initialize `instructions` string, so any host — profiled or not, resource-aware
or not — receives it before its first tool call.

This is the tier-0 floor from `guidance/README.md`. It exists because the six tools are not
self-explanatory in the ways that matter: an unguided agent will publish its transcript, treat the
ledger as enforcement, and tell the user Yoetz verified work it merely recorded. Those failures are
privacy and honesty failures, not polish, so their prevention cannot depend on a tier the host may
never read.

It is not a tutorial, an API reference, or a restatement of the tool schemas. It is the shortest
text that prevents harm and points to the rest.

## Public surface

The future file is reviewed Markdown with stable headings, no frontmatter, and a hard bound of
2 KiB. It is addressed by the logical resource name `guidance/agent-instructions.md` and is also
served as the `instructions` string; both are the same bytes.

Required sections, in this order:

1. **What Yoetz is** — a local work ledger and deterministic checker. One or two sentences.
2. **What Yoetz is not** — not enforcement, not observation, not proof of authorship, not a
   transcript recorder, not an orchestrator.
3. **Never publish** — the shortlist, stated as absolutes.
4. **Before you claim done** — publish the completion claim and current evidence, then `check`.
5. **Word conclusions honestly** — never stronger than the receipt's weakest coverage; one
   permitted and one forbidden example.
6. **Never invent Yoetz state** — no fabricated session IDs, findings, or receipts; if a call fails,
   say Yoetz was unavailable.
7. **Non-default actions need consent** — ordinary MCP tools and privacy tighten are default-safe;
   otherwise use `yoetz consent catalog` / `status`, prepare only when catalog `implemented=true`,
   show `danger_text`, wait for the repeated `confirmation_phrase`, substitute the human-typed
   phrase into `approve_command` (never auto-fill), and never take secrets via chat/MCP/argv/env/
   config (inherited FDs only when the catalog lists them; no `--yolo`; elevated consent does not
   unlock an already-locked vault) (ADR-015/016).
8. **Read more** — the three `yoetz://guidance/<name>` resource URIs and one line each on when to
   read them.

## Behavior

The never-publish shortlist is absolute and stated without hedging: chain-of-thought or hidden
reasoning; full prompts, transcripts, or conversation history; credentials or secrets of any kind;
whole files, whole repositories, or broad unrelated source. It states that a small problem-local
excerpt is permitted only when material, in scope, and state-bound, and points to
`guidance/publication-policy.md` for the boundary rather than trying to draw it here.

The honesty rule gives exactly one permitted and one forbidden sentence, because a concrete pair
teaches the distinction faster than a rule and survives truncation better than a paragraph. The
forbidden example is "Yoetz verified the work."

It states that Yoetz records what agents publish and checks it deterministically, so a check result
describes the recorded evidence at a frontier and never the work itself. It states that a `check`
returning no issue is not a statement that the work is correct.

The text names no harness, no provider, no model, no install path, and no version. It is identical
for Codex and for a host Yoetz has never seen.

Deliberate non-goals, each because tier 0 must survive being the only tier read: it does not
enumerate the six tools, whose schemas the host already has; it does not teach the ten steps, which
`guidance/workflow.md` owns; it does not explain coverage dimensions, which
`guidance/coverage-and-receipts.md` owns. It states the rule and the pointer, never the derivation.

## Errors and edge cases

- Exceeding 2 KiB fails packaging. Hosts inject this text every session, and a document long enough
  to be truncated or skipped is worse than a shorter one that is read.
- A rule that appears only here and not in the owning tier-1 document is a drift failure; tier 0
  restates, and never originates, a rule.
- A rule that appears only in tier 1 while its absence would cause harm is also a failure: it
  belongs here too.
- Wording-lint applies exactly as it does to the references: "verified", "proved", "authenticated",
  and "complete" are rejected unless the sentence states the exact sufficient coverage. The
  forbidden example is exempt by construction, since it is labeled forbidden.
- Naming a harness, provider, model, path, or version fails the public-boundary scan.

## Invariants

1. Every rule whose absence would cause harm is stated here, not deferred.
2. The text is ≤2 KiB and self-sufficient.
3. It is byte-identical wherever it is served: `instructions`, resource, and every installed copy.
4. It never originates a rule that its owning document does not already state.
5. It never claims Yoetz observes, enforces, or verifies.
6. It names no harness, provider, path, or version.

## Tests

- `specs/tests/packaging.md`: size bound, exact section inventory, byte parity across the
  `instructions` string, the resource, and every harness install.
- `specs/tests/conformance.md`: wording-lint; every rule here is traceable to an owning tier-1
  document; every harm-class rule in the never-publish and honesty sets appears here.
- `specs/tests/capability.md`: a host that reads only `instructions` and never fetches a resource
  still declines to publish a transcript and still bounds its final wording.
- `specs/tests/subprocess.md`: the served `instructions` bytes equal the packaged resource bytes.

## Open questions

None.

Localization is deferred to v0.2; v0.1 is English-only.
