# Codex-testing task: Grok / xAI easy linking

Work autonomously in `/Users/shayb/yoetz-core` on the already-created branch
`codex/grok-easy-linking-dogfood-20260727`.

## Explicit one-run authorization and process exemption

The repository owner explicitly authorizes implementation for this dogfood run without first
opening a GitHub issue, without waiting for maintainer acknowledgement, and without opening or
preparing a full pull request. This is a deliberate, narrow exemption from the AGENTS.md intake
steps for this run only, intended to avoid a process contradiction while testing Codex and Yoetz.
All substantive technical, security, privacy, authority-order, testing, documentation, and honesty
requirements in AGENTS.md and CONTRIBUTING.md remain in force. Do not create an issue or PR.

Operate in auto mode within the workspace. Do not push, merge, or publish externally. Do not expose,
print, commit, or copy credentials. Ask only if an action would cross those boundaries or require a
new security/privacy decision not already governed by repository authority.

## Job

On top of the existing OpenAI, Fireworks, and other reviewed easy-linking / provider-setup support
(OpenRouter and other presets included where present), add equivalent easy-linking support for
**Grok (xAI)**.

First inspect the current exact branch and authority chain (ADRs, `docs/INTERFACES.md`,
schemas/fixtures, owning docs, code, and tests). At launch time main has **no** Grok/xAI strings in
product code — verify that fact yourself, then identify the real remaining gap rather than
duplicating existing OpenAI-compatible plumbing incorrectly.

Prefer the smallest change that is authority-compatible:

- If a reviewed preset + factory path can be added in the same pattern as OpenRouter / other chat
  completions providers, do that.
- If only a partial surface is safe without a design decision, implement the safe structural path,
  document the live-unverified boundary honestly, and do not invent privacy/egress exceptions.
- If repository authority forbids adding a new provider without ADR/design gate beyond this
  dogfood exemption, implement everything safely possible, write the honest proposal, and stop
  without weakening honesty rules.

Target operator-facing parity similar to existing shorthands where justified, for example:

```text
yoetz --set --grok --model <model-id>
```

and/or:

```text
yoetz provider endpoint --provider grok --model <model-id>
# aliases like xai / x-ai only if consistent with existing alias patterns
```

Use the enabled Yoetz MCP integration materially throughout the work. Treat registration as
insufficient: start a Yoetz task, publish meaningful plans/claims/evidence as work advances, inspect
status/frontier after ambiguous writes, use `semantic_required` for qualitative correctness,
security/privacy, provider interoperability, and satisfaction of the ask, disposition findings,
and obtain a final receipt if the evidence honestly supports closure. Follow Yoetz guidance, but
independently verify it against repository authorities and record any incorrect, confusing, or
unhelpful guidance.

Requirements:

- Preserve explicit consent, encrypted credential storage, revocation/repair, independent network
  authorization, and fail-closed uncertainty.
- Do not call structural registration or catalog visibility "Grok support." Exercise the actual
  installed/runtime path where safely possible: package/CLI, service readiness, credential and
  privacy status, literal MCP use, provider selection, real Grok/xAI dispatch/provenance, and
  receipt replay. If a live credential or approval is unavailable, implement and test everything
  safely possible and label live interoperability unverified.
- Keep request-shape/canonical-byte identity and public provenance/receipt behavior intact.
- Do not change the active personal provider binding or privacy policy to Grok during this run
  unless the change is fully reversible local test isolation that does not leave the host bound
  to Grok without a credential. Prefer not mutating the live Fireworks personal binding.
- Add or update focused tests and documentation required by the actual behavior change. Do not
  hand-edit generated/frozen artifacts.
- Run the smallest relevant pytest slice, Ruff, and pinned Pyright as appropriate; expand only when
  evidence warrants it.
- Leave all changes on this branch. Do not commit unless necessary for the tooling; if you do,
  clearly report commits.
- Write a detailed final report to
  `docs/dogfood/2026-07-27-grok-easy-linking/codex-final-report.md`, including exact
  baseline/head, files changed, tests, Yoetz task/receipt/provenance identifiers, live proof versus
  structural proof, unresolved risks, and a chronological account of Yoetz guidance and how it
  affected the work.

Do not stop at a proposal: implement, verify, and document as far as the authorized local
environment safely permits.
