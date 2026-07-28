# Codex-testing task: provider model choices with custom entry

Work autonomously in `/Users/shayb/yoetz-core` on the already-created branch
`codex/provider-model-catalog-dogfood-20260728`, based on
`eda66239210584486528e7de60d0715b0d8cc285`.

## Explicit one-run authorization and process exemption

The repository owner explicitly authorizes implementation for this dogfood run without first
searching or opening a GitHub issue, without waiting for maintainer acknowledgement, and without
opening or preparing a full pull request. This is a deliberate, narrow exemption from the
AGENTS.md and CONTRIBUTING.md intake/PR steps for this run only, intended to avoid a process
contradiction while testing Codex and Yoetz. Do not create an issue or PR.

All substantive architecture authority, protocol, security, privacy/egress, storage/durability,
testing, documentation, generated-artifact, review-honesty, and public-boundary requirements remain
in force. Follow the authority order in AGENTS.md. Do not push, merge, publish externally, expose
credentials, copy state from the normal Codex installation, or weaken any honesty rule.

Operate in automatic mode within this workspace. Ask only if an action would cross those boundaries
or require a new security/privacy decision not already governed by repository authority.

## Job

Improve the operator-facing provider setup/model-selection experience. Today users may need to type
the model identifier directly for each provider option. For every applicable provider option or
setup path, present useful available model choices instead:

- Prefer a reliable provider-owned or repository-owned available-model source when the existing
  authority and network/privacy boundaries permit it.
- If the complete set is unsuitable, unstable, or too large, present at most 10 sensible current
  choices using a defensible "recent and/or popular" rule. Do not silently invent popularity data.
- Always preserve an explicit custom/manual model-ID entry so users can add a model that is not in
  the suggested list.
- Keep non-interactive/scripted explicit `--model` behavior working.
- Apply the experience consistently across the actual applicable provider options; do not merely
  hard-code one happy-path picker while leaving equivalent paths inconsistent.

First inspect the exact current branch, architecture authority, provider configuration/setup
surfaces, tests, and existing model aliases/catalog behavior. Resolve ambiguity from the repository
rather than assuming a particular UI or provider set. Prefer the smallest authority-compatible
design. If live remote model discovery would introduce an unauthorized network channel, unreliable
ordering, credential disclosure risk, or a new design gate, use a deterministic reviewed catalog
or another safe design and document the limitation honestly.

## Yoetz dogfood requirements

Use the enabled Yoetz MCP integration materially throughout the work:

1. Read and follow the relevant `yoetz://guidance/*.md` resources, while independently checking
   them against repository authorities.
2. Start a Yoetz task and publish a meaningful plan, obligations, decisions, claims, and evidence as
   the work advances.
3. After any ambiguous write, inspect authoritative status/frontier and use same-request recovery
   where applicable.
4. Use `semantic_required` for qualitative correctness, UX/API consistency, security/privacy
   conformance, and satisfaction of this assignment.
5. Record whether semantic findings were relevant, actionable, correct, and whether they changed
   the implementation. Disposition every finding; do not obey semantic advice that conflicts with
   repository authority.
6. Obtain a final receipt and replay/read it if the evidence honestly supports closure.

Treat MCP registration as insufficient. The report must distinguish activation, durable
publication, deterministic checks, semantic dispatch, semantic provenance, finding delivery,
disposition, receipt issuance, and receipt replay/recovery.

## Implementation and verification requirements

- Preserve explicit consent, encrypted credential storage, revocation/repair, independent network
  authorization, fail-closed uncertainty, and safe errors/logs.
- Do not place user-controlled or provider-controlled model strings in structural logs/errors or
  other forbidden persistence surfaces.
- Do not hand-edit generated or frozen artifacts; use owning scripts if regeneration is required.
- Add focused tests for selection, custom entry, non-interactive compatibility, empty/error cases,
  provider consistency, and any catalog ordering/cap rule introduced.
- Update owning docs or ADRs in the same change if public behavior changes.
- Run the smallest relevant pytest slice, Ruff, and pinned npm Pyright, expanding only when evidence
  warrants it. Run public-boundary and packaging checks if touched surfaces require them.
- Leave all changes on this branch. Do not commit unless necessary for the tooling; if you commit,
  report it clearly.

Write a detailed final report to
`docs/dogfood/2026-07-28-provider-model-catalog/codex-final-report.md` with:

- exact baseline/head and files changed;
- the design and the authority used;
- exact tests/checks and results;
- what is dynamically discovered versus deterministic/capped, including how "recent/popular" is
  justified;
- custom-entry and scripted compatibility evidence;
- Yoetz task, operation, finding, provenance, and receipt identifiers;
- a chronological account of Yoetz guidance and whether each item helped, confused, or hindered;
- semantic review status, actual provider/model provenance, findings and dispositions, and concrete
  implementation changes caused by semantics;
- live proof versus structural/test-only proof;
- unresolved risks and honest limitations.

Do not stop at a proposal: implement, verify, use Yoetz, and document as far as the authorized local
environment safely permits.
