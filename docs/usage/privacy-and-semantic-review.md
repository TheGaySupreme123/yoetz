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
yoetz --privacy                 # recommended policy first; customize only when declined
yoetz privacy setup             # equivalent guided policy review
yoetz privacy show              # current effective policy
yoetz privacy tighten           # tighten (proceeds through gates)
yoetz privacy propose           # propose a change for decision
yoetz privacy export-desired    # effective nonsecret policy as desired-state TOML (never secrets)
yoetz privacy apply-desired     # apply desired-state TOML; tighten may proceed, widen never silent
yoetz privacy receipts          # inspect bounded structural egress receipts
```

`export-desired` / `apply-desired` are the reviewable path for version-controlling policy. The
asymmetry is the point: tightening can flow through gates, widening always requires a human.

The CLI recommends **Metadata only** as the privacy-first semantic starting point: public
structural metadata and declared file types, task scope, and a foreground approval before every
provider request. Its advantage is minimal disclosure; its tradeoff is less problem-specific
feedback. Accepting the displayed exact policy asks nothing further. Declining it opens Private,
Metadata only, Assisted review, Expanded review, and Custom; a named recipe goes straight to the
exact review, and only **Custom** opens the underlying settings, grouped into five sections. When
no external provider is configured, the recommendation is Private instead. The final trusted
widening decision is never skipped.

### `/privacy` in the terminal interface

`/privacy` shows the current posture and the one recommended policy — Private when no external
provider is configured, Metadata only when one is — with both what accepting it buys and what it
costs. It then offers exactly three choices: `Keep current`, `Review recommended change`, and
`Other privacy options`. `Other privacy options` lists the same five recipe names the CLI uses.

The interface takes **no approval of its own.** Choosing anything but `Keep current` suspends the
interface and hands the controlling terminal to the same `yoetz privacy setup` flow, ordinary
proposal, and separately reauthenticated trusted decision used by the CLI — and that trusted screen
is where the exact `before → after` policy diff is rendered and where the widening is authorized.
If terminal handoff is unavailable it prints `yoetz privacy setup` and changes nothing. Tightening
likewise stays on the existing policy gate. `local_only` remains the default and the interface
never moves off it on its own
([ADR-017](../adr/ADR-017-full-screen-terminal-interface.md) decision 5).

### What the trusted approval screen shows

Before a widening is authorized, the trusted terminal prints every security-relevant field the
proposal moves as a plain-English `before -> after` line, grouped by destination, information
disclosed, authorization, limits, and local visibility, with the ones that make privacy less
restrictive marked `(!)`. Simultaneous tightenings appear too, unmarked, so you see the whole
change rather than half of it. The service sends structured field/value records and never
explanatory prose, and the wording is fixed locally by Yoetz. The diff digest is printed underneath
and labelled for what it is: integrity evidence binding the decision to exact bytes, not a
description of the change.

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

When you are auditing a run rather than the installation, the
[semantic dogfood runbook](../runbooks/semantic-dogfood.md) gives the preflight and the provenance
gate: which route the agent actually got, and how to read `semantic_provenance` to tell "no provider
attempt was made" apart from "an attempt was made and produced nothing useful".

If you believe Yoetz disclosed, retained, or logged something these commitments forbid, treat it as
a security report: [`SECURITY.md`](../../SECURITY.md), not a public issue.
