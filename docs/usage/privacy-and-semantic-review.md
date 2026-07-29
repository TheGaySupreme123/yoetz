# Privacy and semantic review

The full user-facing promise is [`PRIVACY.md`](../../PRIVACY.md). The enforceable technical contract
is [`docs/protocol/data-egress-and-privacy.md`](../protocol/data-egress-and-privacy.md) and
[ADR-009](../adr/ADR-009-data-egress-privacy.md). This page is the operator's view: what the
defaults are, and what changes when you turn something on.

## Two defaults, deliberately separate

An unconfigured installation is **zero-egress and deterministic**. Nothing leaves your machine.
Deterministic checks, findings, and receipts all work in this state.

Turning on external semantic review is a separate, explicit decision. It is not implied by
installing, by binding a provider, or by having a credential.

An MCP host can impose a stronger process-local ceiling with
`yoetz mcp serve --semantic off`. That strict route cannot request external semantic review even if
durable policy is widened later. It is separate from the policy profiles below; see
[Auto-approving an MCP route](auto-approving-agents.md).

## The four LLM privacy profiles

`local_only`, `confirm_every_request`, `minimal_external`, `trusted_provider`.

The terminal interface names these in ordinary words — **Local only**, **Ask every time**,
**Minimal external review**, **Trusted provider** — under `/privacy`. The words are a label for
the same durable profile; nothing about the policy changes because the wording is friendlier.

Trusted-provider permission always binds named categories, a purpose, a scope, a provider, a model,
and an endpoint profile. It never means "send everything available."

Enabling one channel authorizes no other. Telemetry, crash diagnostics, update checks, and
capability testing have their own separate policies. `local_only` governs LLM disclosure and can
coexist with a separately consented bounded structural non-LLM channel.

**True zero-network mode** is the composite of `local_only`, the global ceiling off, and all five
network channels disabled. Even then, exact release-profiled local IPC is permitted for the Yoetz
service, the confidential helper, an approved local model, the OS credential service, and session
paths.

## The never-send set is absolute

Some content classes can never become model input or reach another sink — see the enumerated list in
[`PRIVACY.md`](../../PRIVACY.md). No profile overrides it. No approval unlocks it. Authorship does
not unlock it: sensitive and confidential content stays sensitive no matter who wrote it.

Only a reauthenticated local human can loosen effective policy, and loosening is never silent.

## Commands

```text
yoetz privacy setup             # guided policy review
yoetz privacy show              # current effective policy
yoetz privacy tighten           # tighten (proceeds through gates)
yoetz privacy propose           # propose a change for decision
yoetz privacy export-desired    # effective nonsecret policy as desired-state TOML (never secrets)
yoetz privacy apply-desired     # apply desired-state TOML; tighten may proceed, widen never silent
yoetz privacy receipts          # inspect bounded structural egress receipts
```

`export-desired` / `apply-desired` are the reviewable path for version-controlling policy. The
asymmetry is the point: tightening can flow through gates, widening always requires a human.

### `/privacy` in the terminal interface

`/privacy` reads the current posture and, before any widening, renders the exact disclosure: which
data categories become eligible, the provider, model, endpoint profile, purpose, and scope, plus
the never-send set and any unavailable or untested provider posture. The cursor starts on the
declining option.

Approving there does **not itself** widen policy. The interface suspends and hands the controlling
terminal to the same thirteen-answer `yoetz privacy setup` questionnaire, ordinary proposal, and
separately reauthenticated trusted decision used by the CLI. If terminal handoff is unavailable it
prints `yoetz privacy setup` and changes nothing. Tightening likewise stays on the existing policy
gate. `local_only` remains the default and the interface never moves off it on its own
([ADR-017](../adr/ADR-017-full-screen-terminal-interface.md) decision 5).

## What semantic review actually sends

When you accept the CLI's recommended `assisted-review` recipe, it shows and confirms a standing
workspace policy that sends the reviewer a structured packet: the goal, obligations, claims, the
material timeline, deterministic findings and their exact bases, coverage gaps, and bounded
problem-local excerpts of evidence, tests, diffs, or source **already recorded in the case**.

Sensitive and confidential content is off. The never-send set remains absolute. The reviewer gets a
packet built from the ledger, not a handle on your repository — composition passes bundled provider
adapters no repository, storage, environment, or transcript handles.

The recipe is only recommended for an exact endpoint profile with a current data-use record stating
training `prohibited`, retention `none|bounded`, and provider human access `prohibited|restricted`.
Known-broad, unknown, or stale posture removes the recommendation.

## How review comes back

Inside that confirmed policy, review is direct-to-agent. The reviewer returns a bounded challenge to
the main agent, which can act, supply evidence, revise its claim, dispute with evidence, or state an
unresolved limitation — then recheck. Routine checks and retries need no human prompt.

Semantic output is advisory, provenance-labeled, and deterministically fenced. It never silently
becomes deterministic truth, and it never upgrades a coverage claim.

## Auditing it

Every terminal outbound decision and every physical attempt leaves a local structural egress
receipt. Policy answers are plain reviewed configuration you can read. The installation privacy
catalog keeps encrypted proposal objects and their structural sidecars enumerable.

No control surface is required to trust a summary — the receipts, catalog, and policy file are the
evidence, and they are on your machine.

If you believe Yoetz disclosed, retained, or logged something these commitments forbid, treat it as
a security report: [`SECURITY.md`](../../SECURITY.md), not a public issue.
