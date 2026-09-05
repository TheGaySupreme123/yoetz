# Privacy and semantic review

The full user-facing promise is [`PRIVACY.md`](../../PRIVACY.md). The enforceable technical contract
is [`docs/protocol/data-egress-and-privacy.md`](../protocol/data-egress-and-privacy.md) and
[ADR-009](../adr/ADR-009-data-egress-privacy.md). This page is the operator's view: what the
defaults are, and what changes when you turn something on.

## Two defaults, deliberately separate

An unconfigured installation is **external-LLM-egress-free and deterministic**. No task content
leaves for a provider. The independent structural package update channel may be on unless disabled;
deterministic checks, findings, and receipts all work in this state.

Turning on external semantic review is a separate, explicit decision. It is not implied by
installing, by binding a provider, or by having a credential.

An MCP host can impose a stronger process-local ceiling with
`yoetz mcp serve --semantic off`. That strict route cannot request external semantic review even if
durable policy is widened later. It is separate from the policy profiles below; see
[Auto-approving an MCP route](auto-approving-agents.md).

## Native work evidence is a separate choice

The default host integration records structural observation. When you explicitly select a host's
versioned ordinary-work artifact, it can also prepare bounded native content for that host. Selecting
the artifact does not enable content capture. Content capture is a second, reversible choice for the
same repository:

```text
yoetz observe content-enable --workspace /exact/project \
  --profile claude-code-ordinary-observation-v1
yoetz observe content-status --workspace /exact/project --json
yoetz observe content-disable --workspace /exact/project \
  --profile claude-code-ordinary-observation-v1
```

Status keeps the configured profiles separate from effective capture. A configured profile is the
profile you selected; it is effective only while observation consent is active and the local runtime
gate is enabled. Pausing observation or disabling the runtime gate preserves the configured choice
but makes effective capture empty. Disabling a profile removes that configured choice.

Native content capture and semantic review have separate authority. Enabling a host profile does not
authorize external review, choose a provider, or permit any content to leave the computer. Semantic
review still requires its own provider readiness and repository privacy decision described below.

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

Effective policy loosens only through a reauthenticated decision you make: the trusted local
ceremony, or your explicit current-chat approval of one exact prepared, previewed, expiring consent
target that a capable agent relays for you. That relay is the agent's assertion, which Yoetz cannot
independently authenticate, so the local ceremony remains the stronger path. Loosening is never
silent: every path shows the complete change before you decide. The machine row is an installation
ceiling; external LLM work also needs an exact granted row for the current repository beneath it.

## Commands

```text
yoetz --privacy                 # recommended policy first; customize only when declined
yoetz privacy setup             # equivalent guided policy review
yoetz privacy show              # current effective policy
yoetz privacy tighten           # tighten (proceeds through gates)
yoetz privacy propose           # propose a change for decision
yoetz privacy decide-policy     # approve or deny one exact pending policy proposal on the terminal
yoetz privacy export-desired    # effective nonsecret policy as desired-state TOML (never secrets)
yoetz privacy apply-desired     # apply desired-state TOML; tighten may proceed, widen never silent
yoetz privacy pending           # list disclosure decisions waiting for you
yoetz privacy decide-disclosure # decide one exact pending disclosure by id (see below)
yoetz privacy receipts          # inspect bounded structural egress receipts
```

You can also ask a capable Yoetz agent to install, configure, or change Yoetz in ordinary
conversation. The agent should explain each consequential choice, recommend an option and its
trade-off, perform the mechanical steps, and leave the decision with you. If you explicitly ask
for semantic review, it recommends **Expanded review** first for the deepest useful in-scope
review and explains that it permits broader problem-relevant context; **Assisted review** is the
lower-disclosure semantic alternative. Metadata only and Private remain available. Choosing a
recipe is not yet approval: the agent prepares one exact combined change and shows it before asking
you to approve or deny it.

On an authority-capable Codex route, that prepared view includes every substantive `before ->
after` policy row, the provider/model/endpoint binding, repository commitment, current and
candidate policy digests, diff and authority digests, expiry, and one-use target. Your explicit
current-chat decision can complete exactly that action. Any repository, policy-generation, route,
recipe, target, expiry, or replay drift stops with no policy or provider mutation. A host without
the required chat-authority capability gives the shortest trusted-local continuation instead of
silently choosing a different policy.

`privacy pending` exists for one situation: `privacy decide-disclosure` needs an exact pending id,
which normally arrives in the check result that is waiting on it, and that id can be lost — a
closed terminal, an agent that did not relay it. The listing names the decisions you can still
make and their expiry, and nothing about what any of them would disclose. Destination, categories,
and prepared bytes belong to the decision preview, which is bound to the exact prepared case and
is the only surface that renders them.

`export-desired` / `apply-desired` are the reviewable path for version-controlling policy. The
asymmetry is the point: tightening can flow through gates, widening always requires a human.

Privacy commands bind to the repository derived from their actual working directory. The service
resolves symlinks and Git's common root, commits it under the installation key, and discards the raw
path. Branches and linked worktrees share authority; independent clones and unrelated repositories
do not. A task's public `workspace_ref` is not consulted.

The CLI recommends **Assisted review** when the exact configured provider route has a current
reviewed data-use record stating no default training and retention no longer than 30 days, and
**Private** otherwise. Assisted review is exact-repository-scoped and problem-local, and does not prompt
before each request once its policy is committed; its tradeoff is that selected ordinary user
content may be sent. **Metadata only** remains available as the minimal-disclosure option —
public structural metadata and declared file types, task scope, and a foreground approval before
every provider request — and is the profile that makes `privacy pending` matter. Accepting the
displayed exact policy asks nothing further. Declining it opens Private, Metadata only, Assisted
review, Expanded review, and Custom; a named recipe goes straight to the exact review, and only
**Custom** opens the underlying settings, grouped into five sections. The final trusted widening
decision is never skipped.

### `/privacy` in the terminal interface

`/privacy` shows the current posture and the one recommended policy — Private when no exact provider
route has current reviewed no-training evidence with retention no longer than 30 days, Assisted review
when one does — with both what accepting it buys and what it
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

The agent-chat decision surface renders the same structured substantive diff. It is not allowed to
show only a digest and call that informed approval; the digest binds the bytes while the readable
rows explain what changes.

When a fallback endpoint is paired (see [Providers](providers.md#pairing-a-fallback-endpoint)),
the destination group carries a second row, `Fallback provider and model`, directly under
`Provider and model`. Adding or changing it is a widening and is marked `(!)`; removing it is a
tightening and appears unmarked. The approval covers exactly the two named destinations —
nothing is sent to the fallback before that decision, and nothing else ever becomes a fallback.
`yoetz privacy setup` shows the same pair before asking, as `Destination` and
`Fallback destination (after the primary cannot serve)`.

Custom privacy setup asks separately whether to authorize the configured fallback, naming its
provider and model. Declining keeps the policy bound to the primary alone. Named review recipes
include the configured pair in the review screen before approval.

## What semantic review actually sends

When you accept the CLI's recommended `assisted-review` recipe, it shows and confirms a standing
exact-repository policy that sends the reviewer a structured packet: the goal, obligations, claims, the
material timeline, deterministic findings and their exact bases, coverage gaps, and bounded
problem-local excerpts of evidence, tests, diffs, or source **already recorded in the case**.

Sensitive and confidential content is off. The never-send set remains absolute. The reviewer gets a
packet built from the ledger, not a handle on your repository — composition passes bundled provider
adapters no repository, storage, environment, or transcript handles.

The Codex subscription evaluator follows the same policy and receives the same approved packet.
The difference is credential and transport authority: Codex owns ChatGPT login and internally
constructs the upstream OpenAI body, while Yoetz passes the packet to one exact app-server over
stdio. The receipt commits to the packet and exact runtime cell and says
`upstream_body_observability=unavailable`. It does not claim to know Codex's upstream body. Login,
plan, or model availability never substitutes for repository privacy approval.

For the first grant, the trusted screen may show both a machine-ceiling change and insertion of the
repository row. One authority digest binds the complete preview and one atomic CAS commits both or
neither. An eligible upgrade carry-forward is labeled separately: it preserves the machine bytes,
consumes one bounded pre-upgrade entitlement, and grants no new repository, so it needs no repeated
approval. Older clients and missing repository locators fail closed.

The recipe is only recommended for an exact endpoint profile with a data-use record satisfying
`reviewed_at <= now < expires_at`, training `prohibited`, retention `none|bounded`, and a bounded
ceiling no greater than 30 days. Provider human-access facts remain prominent disclosure
information, but are not a separate recommendation threshold. Known-broad, unknown, or stale
posture removes the recommendation.

## How review comes back

Inside that confirmed policy, review is direct-to-agent. The reviewer returns a bounded challenge to
the main agent, which can act, supply evidence, revise its claim, dispute with evidence, or state an
unresolved limitation — then recheck. Routine checks and retries need no human prompt.

Semantic output is advisory, provenance-labeled, and deterministically fenced. It never silently
becomes deterministic truth, and it never upgrades a coverage claim.

If a fallback endpoint is approved and the primary could not serve — repeated timeouts,
connection failures, or rate limits, a quota exhaustion, or a missing credential — the same
approved packet goes to the fallback as a fresh attempt with its own authorization and privacy
receipt; under Metadata only that is its own foreground approval. An answer the primary actually
gave is never re-asked elsewhere. The result and receipt then name the fallback as the provider,
model, and endpoint that served and carry `fallback_from`: the primary, how many attempts it was
given, and the exact reason it was abandoned. The markdown and text receipts say which endpoint
served and why the primary could not, so a review that came from the fallback is never mistaken
for one from the primary.

## Auditing it

Every terminal outbound decision and every physical attempt leaves a local structural egress
receipt. Policy answers are plain reviewed configuration you can read. The installation privacy
catalog keeps encrypted proposal objects and their structural sidecars enumerable.

No control surface is required to trust a summary — the receipts, catalog, and policy file are the
evidence, and they are on your machine.

Installed-wheel proof for issue #139 remains outstanding until two consecutive real semantic checks
in one approved repository have distinct authorizations, credential handles, dispatch identities,
semantic provenance, and terminal privacy receipts, and a second repository is shown blocked.
Router downstream/fallback grants and issue #141's foreground disclosure continuation remain
separate work.

For an `external_runtime_oauth` profile, the equivalent attempt identity is the secret-free
runtime authority plus exact runtime evidence, not a vault credential handle. Post-acknowledgement
ambiguity is terminal `outcome_unknown`; it does not mint a replacement attempt.

When you are auditing a run rather than the installation, the
[semantic dogfood runbook](../runbooks/semantic-dogfood.md) gives the preflight and the provenance
gate: which route the agent actually got, and how to read `semantic_provenance`.

Read `semantic_provenance` together with `semantic_status` and `semantic_reason`, never on its own.
The outcome is three-way, not two-way: on the statuses where the protocol forbids provenance, null
means **no provider attempt was made**; where it requires provenance, an attempt happened (which is
not the same as it being useful); and `failed`/`coordinator_failure` is unconstrained, so it is
**indeterminate** — never read it as "not attempted".

If you believe Yoetz disclosed, retained, or logged something these commitments forbid, treat it as
a security report: [`SECURITY.md`](../../SECURITY.md), not a public issue.
