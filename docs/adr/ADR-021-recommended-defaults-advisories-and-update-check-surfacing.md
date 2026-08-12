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
(first-run setup wizard), ADR-014 (TOML settings), and ADR-016 (human review for non-default
actions).

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
   control points: the end of setup, successful service READY activation, and
   `yoetz recommend list`. It recomputes after the installed package version changes or while
   pending recommendations need refresh. Hooks never load full configuration or perform a package
   check. SessionStart may read only the small cached pending projection.

3. **Durable decision state is local, strict, and bounded.** The owner-only
   `recommendations.json` document uses schema `yoetz.recommendations/1` and records the last
   evaluated version, per-id accept/decline decisions, and the bounded pending set. Invalid,
   oversized, unsafe, or unknown state fails closed. A decline is remembered by stable id and is
   never re-nagged; a later release that needs a genuinely new decision must register a new id.

4. **The agent is a messenger, not the decision maker.** When no other observation advice already
   occupies the bounded `additionalContext` surface, SessionStart may emit at most one cached
   recommendation. The instruction asks the agent to explain the recommendation and request the
   user's approval, naming exact accept and decline commands. The hook observes no answer and
   changes no configuration. Retrieved recommendation text, agent inference, silence, or prior
   history is not approval.

5. **Only `yoetz recommend` applies or records a decision.** `list` re-evaluates and reports the
   current bounded set. `decline <id>` records the refusal without applying the recommendation.
   `accept <id>` re-evaluates current state before acting and shows the exact change. A configuration
   flip uses the ordinary typed configuration writer. Codex activation uses ADR-012's exact
   selected-executable and explicitly supplied home, isolated pre-consent version probe,
   post-consent scoped inventory/add, source/cache, preimage, environment, digest, conflict, and
   staleness checks; marketplace/config presence alone never satisfies the recommendation. A
   package-update acceptance only prints the reviewed human-run upgrade command. There is no generic arbitrary
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
decline remains quiet across later sessions. SessionStart stays bounded and fast because it reads a
cache rather than evaluating configuration, activation, or network state.

The registry is intentionally small and code-owned. Adding a setting requires an explicit
satisfaction predicate and an application path with its own authority and staleness rules. A
recommendation can improve discoverability, but it cannot prove that a plugin fired, observation
was delivered, a package upgrade succeeded, or a resulting configuration is correct.

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
