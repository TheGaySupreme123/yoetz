# ADR-021 — Recommended-defaults advisories and update-check surfacing

**Status:** Accepted (2026-08-12), acknowledged in
[issue #204](https://github.com/TheGaySupreme123/yoetz/issues/204) and
[issue #205](https://github.com/TheGaySupreme123/yoetz/issues/205).
**Implemented by:** `src/yoetz/application/recommendations.py`,
`src/yoetz/cli/recommend.py`, `src/yoetz/application/package_update.py`,
`src/yoetz/adapters/privacy/update_checks.py`,
`src/yoetz/adapters/integrations/codex_marketplace.py`,
`src/yoetz/cli/observe_hooks.py`, `src/yoetz/cli/setup.py`,
`src/yoetz/service/ready_composition.py`, `src/yoetz/config/models.py`, and
`src/yoetz/config/write.py`.
**Relates to:** ADR-009 (data egress and privacy), ADR-010 (harness integration port), ADR-012
(first-run setup wizard), ADR-014 (TOML settings), ADR-016 (human review for non-default actions),
and ADR-022 (observation writer identity and observation-tolerant concurrency).

## Context

Releases sometimes add a safer or more useful default that an existing installation cannot adopt
silently. A new installation can receive that default when it makes its initial choices, but an
upgrade already has durable configuration and trust decisions that the package must preserve.
Scattering upgrade prompts across setup, hooks, and UI code would make declines hard to remember,
create inconsistent ceremonies, and tempt individual consumers to mutate configuration when they
notice drift.

The first consumers make the boundary concrete. Observation is configured on by default, while
per-workspace observation consent remains an independent gate. Installed Codex hook sources still
need explicit activation through the selected executable: exact marketplace/config state alone is
insufficient without canonical installed inventory and a byte-exact versioned plugin cache. Package
update checks already have a bounded, policy-gated PyPI transport and a human-run upgrade command.
These three cases need one advisory mechanism, but they do not share one kind of authority and must
not become a bundled consent switch.

## Decisions

1. **Recommendations come from a reviewed, versioned registry.** Each `RecommendedDefault` has a
   stable id, introduction version, title, bounded summary, closed kind (`config_flip`,
   `activation`, or `package_update`), and an explicit satisfaction predicate. Adding behavior to
   the registry is a code and review change; configuration, network data, hook payloads, and an
   agent cannot inject a new recommendation. A materially different recommendation uses a new id
   rather than recycling a declined one.

2. **Evaluation is cached and deliberately infrequent.** Yoetz evaluates recommendations at heavy
   control points: the end of setup, successful service READY activation, hourly while that READY generation remains active, and
   `yoetz recommend list`. It recomputes after the installed package version changes or while
   pending recommendations need refresh. An already-resolved context is always reconciled, including
   a newer release while the installed version is unchanged and the pending set is empty. The
   hourly maintenance is bounded, cancellation follows READY retirement, and the transport retains
   its 24-hour cache; offline failures wait until the next interval. Hooks never load full configuration or perform a package
   check. SessionStart may read only the small cached pending projection.

3. **Durable decision state is local, strict, and bounded.** The owner-only
   `recommendations.json` document writes schema `yoetz.recommendations/3` and backward-reads
   schemas `yoetz.recommendations/1` and `/2`. It records the last evaluated version, bounded decisions, the
   pending set, and any pending exact-target identity. Invalid, oversized, unsafe, or unknown state
   fails closed. Config decisions remain global by stable id. New package decisions bind the advertised
   release in `release_version`; the pending projection carries `pending_package_version`. Accept
   and decline suppress only that release, so a later release can prompt without first upgrading.
   Legacy unscoped declines retain their promised permanent suppression. The durable `update_checks`
   policy remains the global network opt-out. A supplied `--release-version` must still match the
   pending release under the decision lock; stale hook commands fail without recording a decision.
   Codex activation is the
   exception: its accept/decline identity contains only digests and binds the resolved executable
   path, executable bytes and version, canonical Codex home, activation preview, and intended
   host-rendered cache. A legacy unscoped activation decision is retained as history but suppresses
   no exact target; a legacy unscoped pending activation is withheld until an exact-target
   evaluation rebuilds actionable advice. Exact-target activation history is compacted to bounded
   accepted and declined windows; forgetting an old row can only cause advice to be shown again and
   never grants authority.

4. **The agent is a messenger, not the decision maker.** When no other observation advice already
   occupies the bounded `additionalContext` surface, SessionStart may emit at most one cached
   recommendation. The instruction asks the agent to explain the recommendation and request the
   user's approval, naming exact accept and decline commands. The hook observes no answer and
   changes no configuration. Cached advice is available even when observation is disabled or
   workspace observation consent is missing, paused, or revoked; these paths do not ingest or spool
   an observation. Retrieved recommendation text, agent inference, silence, or prior
   history is not approval. ADR-022 separately governs the stable identity and authorship of the
   observation advice that shares this delivery channel; a recommendation never becomes an
   observation-authored claim.

5. **Only `yoetz recommend` applies or records a decision.** `list` re-evaluates and reports the
   current bounded set. `decline <id>` records the refusal without applying the recommendation.
   `accept <id>` re-evaluates current state before acting and shows the exact change. Exact Codex
   evaluations run even when a same-version historical decision left no global pending item: a
   observed `installed_not_activated` target therefore gets fresh advice even if an earlier accepted
   row has the same digest, while a currently active exact target stays quiet. A target-bound decline
   suppresses only that unchanged target; it never grants activation and never applies to another
   home. Foreign, modified, ambiguous, or otherwise non-previewable state clears stale actionable
   advice and requires manual review. A configuration flip uses the ordinary typed configuration
   writer. Codex activation uses ADR-012's exact
   selected-executable and explicitly supplied home, isolated pre-consent version probe,
   post-consent scoped inventory/add, source/cache, preimage, environment, digest, conflict, and
   staleness checks; marketplace/config presence alone never satisfies the recommendation. After
   the final confirmation, activation acceptance is durably recorded before host mutation. A store
   failure therefore performs no activation, while a later apply failure remains an accepted
   decision but cannot suppress recovery if reinspection is still inactive. A package-update
   acceptance only prints the reviewed human-run upgrade command. There is no generic arbitrary
   setting setter, no force path, and no silent apply-on-upgrade behavior.

6. **Consumers keep their independent authority gates.** `[observation].enabled = true` permits the
   observation subsystem to operate but does not grant per-workspace observation consent. Codex
   plugin activation remains a separate standing trust/configuration/inventory/cache transition
   under ADR-012. The
   `update_checks` egress channel remains the sole update-check flag under ADR-009; a recommendation
   cannot enable it, widen the global ceiling, or bypass policy.

7. **The update advisory remains PyPI-only and non-upgrading.** Yoetz is distributed on PyPI; the
   repository's `package.json` is development tooling, not a shipped Yoetz package, so no npm
   update check exists. When the durable `update_checks` policy permits it, the existing bounded
   resolver may use its 24-hour cache or the allowlisted PyPI package-identity request. A fresh,
   unconfigured installation has no continuing policy before first-run setup commits one, and its
   pre-policy advisory paths explicitly disable networking. A setup rerun does not suspend or revoke
   existing authority; activity during the rerun remains governed by the current standing policy
   until a replacement commits. First-run setup's explicit yes/no may overlay only `update_checks`
   on its recommended or named privacy recipe before the resulting candidate is rendered and sent
   through the unchanged privacy proposal/decision ceremony; Custom retains its own section-5
   question. A newer version feeds the same recommendation channel; neither evaluation nor
   acceptance runs an upgrade.

8. **This is a support surface, not a seventh protocol operation.** Recommendations add no MCP
   operation, work event, receipt claim, semantic-review permission, or general observation
   payload. They are local advisory/configuration ergonomics. Guidance instructs agents to preserve
   the current-chat approval boundary and to word any result as a recommendation decision, not as
   verification of the recommended behavior.

## Consequences

Existing installations can learn about reviewed defaults without an upgrade rewriting durable
preferences or trust surfaces. A user can accept, decline, or defer each recommendation, and a
config decline remains quiet across later sessions. A new package decline stays quiet for that
release; legacy permanent package declines remain quiet. An activation decline stays quiet only for its
unchanged exact target. SessionStart stays bounded and fast because it reads a cache rather than
evaluating configuration, activation, or network state.

The registry is intentionally small and code-owned. Adding a setting requires an explicit
satisfaction predicate and an application path with its own authority and staleness rules. A
recommendation can improve discoverability, but it cannot prove that a plugin fired, observation
was delivered, a package upgrade succeeded, or a resulting configuration is correct.

The v2 target identity deliberately stores no executable or home path. Re-evaluation may replace
the one cached pending target, while the bounded decision map retains only recent independent
decisions for previous exact targets. Any inactive target requires current actionable advice unless
that exact target was declined; generic setup `--accept` remains unable to authorize an unshown
digest.

## Alternatives considered

**Rewrite configuration during package upgrade.** Rejected: installation is not authorization to
change an existing user's settings or Codex trust configuration, and package-manager hooks are the
wrong authority surface.

**Prompt on every SessionStart until the user accepts.** Rejected: it creates nagging, makes silence
ambiguous, and performs too much work in a latency-sensitive hook.

**Let the agent edit the recommended setting directly.** Rejected: that bypasses typed writers,
exact activation previews, staleness checks, and durable decline memory.

**Add a second update-check configuration flag.** Rejected: the independently authorized durable
`update_checks` channel already owns that decision. A duplicate leaf could disagree with policy and
make actual network authority unclear.

**Check npm as well as PyPI.** Rejected: Yoetz ships as a Python distribution only. Repository npm
metadata belongs to development tooling and is not a user-install update source.

## Release-discovery correction (issue #699)

The maintainer requested this scoped repair and upgrade workflow before 0.2. Advice is delivered on
a later eligible host SessionStart after discovery, not an OS notification or a guarantee that the
very first session after publication sees it. A cached up-to-date PyPI result may take up to its
24-hour TTL plus the hourly READY interval to refresh. A service that is not READY, a policy refusal, an offline transport, or an occupied task-advice
context can delay delivery. These conditions do not authorize broader networking; refresh remains
subject to the existing `update_checks` policy and bounded cache.
Older recommendation writers must be retired before the new schema is written; unknown future
schemas still fail closed.

## Guided upgrade entrypoint (issue #699)

`yoetz upgrade` is a connection-free human-readable plan for the package, existing host targets,
data migration, activation and verification. It emits only inspection/preview commands for host
steps, requiring explicit existing roots and configuration rather than inferring ambient defaults.
`--accept --writers-stopped` invokes only the fixed `uv tool upgrade yoetz` command after checking
that this is the ambient uv tool installation. Source checkouts and isolated/pinned runtimes refuse.
Package-manager output is not copied into structural diagnostics. A timeout leaves the package
outcome unknown. A successful command still reports host refresh/migration/activation as unverified;
a fresh invocation is required to continue from the new package. It never automatically approves
host trust, changes privacy settings, migrates ledgers, or claims a complete upgrade from exit zero.
See [Upgrading](../usage/upgrading.md) for the user workflow.

Concurrent refresh results retain the newer validated release from the pending projection or
most recent package decision under the store lock. An older response, including one that called
the older installation up to date, cannot regress that known release or resurrect dismissed older
advice. Once the installed version catches up, the pending update clears. The READY refresh
deadline includes both gate acquisition and evaluation, preserving gate order and cancellation.
