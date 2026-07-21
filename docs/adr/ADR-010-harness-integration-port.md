# ADR-010 — Harness integration is a port; Codex is the first adapter

**Status:** Working decision for spec drafting (2026-07-16; trigger-profile amendment 2026-07-17).
Ratification requires the packaging
byte-parity run plus a capability run proving an unprofiled MCP host completes the workflow from an
installed artifact.
**Owning public specs:** `specs/guidance/`, `specs/src/yoetz/ports/integrations.py.md`,
`specs/src/yoetz/application/integrations.py.md`,
`specs/src/yoetz/adapters/integrations/codex_skill.py.md`, `specs/src/yoetz/mcp/descriptors.md`,
`specs/src/yoetz/mcp/resources.md`, `specs/src/yoetz/mcp/server.md`,
`specs/schemas/common/client-info-1.0.0.schema.json.md`.
**Relates to:** ADR-002 (canonical protocol), ADR-005 (Codex capability identity), ADR-007
(packaging/release).

## Context

Yoetz is intended to work with any agent through MCP, with Codex as the first *officially supported*
harness. The drafted v0.1 tree did not express that split, and drifted toward Codex being the only
harness that could work well:

1. `IntegrationsPort` named its adapter in its own method set — `preview_codex_skill`,
   `install_codex_skill`, `status_codex_skill`, `remove_codex_skill` — with `CodexSkillSource` and
   `CodexSkill*Command` types. Every other port in the tree names a capability (`LedgerPort`,
   `ObjectStorePort`, `OutboundGatewayPort`) with adapters underneath. Adding a harness meant
   editing the shared registry, so a fork could not add one without touching the core.
2. All agent guidance — the ten-step workflow, the publication policy, the coverage rules — lived
   under `skills/codex/yoetz/`, physically inside the Codex-specific tree, in both the spec tree and
   the packaged resources. None of that content was Codex-specific. A second harness would have had
   to duplicate it and drift, or reach into another harness's directory.
3. `ClientInfoModel.kind` was the closed set `codex_cli|yoetz_cli|test_client|importer`. An
   arbitrary MCP host had no honest value to send, in a product whose thesis is honest provenance.
4. The MCP surface exposed six tools and nothing else: no `instructions`, no resources, and no owner
   for the tool description text. A non-Codex host therefore received the operations with none of
   the rules — including the forbidden-content rules, which are privacy-relevant, and the
   coverage-wording rules, which are the honesty product itself.

The through-line is that "Codex is first" had been encoded as "Codex is the only one that works."

## Decisions

1. **The guidance core is owned once, harness-neutrally, under `guidance/`.** It owns
   `agent-instructions.md`, `workflow.md`, `publication-policy.md`, and `coverage-and-receipts.md`,
   packaged byte-identically under `src/yoetz/resources/guidance/`. Exactly one packaged copy exists
   regardless of how many harnesses ship, which makes drift structurally impossible rather than
   merely tested. A harness adapter may choose layout, filename, and header; it may not vary a byte.

2. **`IntegrationsPort` is harness-parameterized.** Methods are `preview_skill`, `install_skill`,
   `status_skill`, and `remove_skill`, each taking an exact `HarnessId` (closed; v0.1 membership is
   exactly `codex`) plus a `HarnessProfile` carrying `skill_root`, `frontmatter_profile`,
   `capability_profile_ids`, `supported_versions`, and `hooks`. Adding a first-party harness is one
   `HarnessId` value plus one adapter under `adapters/integrations/`; it changes no method, no shared
   type, and no guidance. This is what makes the fork path real rather than aspirational, and it
   makes the shared registry smaller, not larger.

3. **Hook support is exact-profile capability data, not a release-wide boolean.**
   `HarnessProfile.hooks_by_capability_profile` maps each advertised capability-profile ID to
   `HarnessHookProfile | None`. A v0.1 cell may declare a trigger-only profile after E-013's
   installed-artifact evidence passes; an unproven cell remains `None`. Observation hooks are the
   only integration capability that would earn `hook_observed` coverage, and every v0.1 cell keeps
   that arm absent. This lets a safe ergonomic ship without implying observation or inferring
   support across neighboring host versions.

4. **Three delivery tiers, with tier 0 self-sufficient.** Tier 0 is
   `guidance/agent-instructions.md`, served verbatim as the MCP initialize `instructions` string, and
   reaches every host unconditionally. Tier 1 exposes the four documents as
   `yoetz://guidance/<name>` MCP resources for hosts that read them. Tier 2 installs them on disk
   for a first-party harness. Because tier 0 is the only tier guaranteed to arrive, it must carry
   every rule whose absence would cause harm, rather than summarizing and deferring.

5. **MCP declares tools and resources only.** No prompts, sampling, roots, completions, or resource
   subscribe/listChanged. Resources are static reviewed product documents: they reach no service,
   carry no user content, and are therefore served while the service is absent, locked, or draining —
   exactly when an agent most needs to know Yoetz is unavailable rather than invent a session. A
   resource URI is a key into a frozen table, never a path.

6. **`mcp/descriptors.py` owns every agent-read string** — the six tool names, descriptions, and
   annotations, plus `instructions` — loaded from verified packaged resources, never composed at
   runtime, and bound by the same honesty lint as the guidance ("verified", "proved",
   "authenticated", "complete" rejected unless the sentence states the exact sufficient coverage).
   `status` and `receipt` carry `readOnlyHint=true`; nothing carries `destructiveHint`, because no
   Yoetz operation deletes recorded evidence.

7. **`ClientInfoModel.kind` gains `cooperative_agent`**, the transport-neutral honest identity for
   any harness without a first-party integration, valid with `cooperative_mcp` or `local_cli`.
   `codex_cli` remains a capability claim bound to an installed Codex profile and is not a generic
   agent token. Assurance derives from the integration channel, so no `kind` value participates in
   coverage; recognising a harness first-party is additive and is never required for it to work.

8. **`HarnessHookProfile` distinguishes trigger hooks from observation hooks, and only the
   observation arm touches coverage.** A trigger hook fires on a harness lifecycle event — context
   compaction is the motivating case — and prompts the agent to re-ground by calling `status`. It
   earns no coverage: it observes nothing, and the `status` result it causes discloses only what
   `status` would already have returned under the ordinary provenance rules and the `agent_context`
   ceiling. It therefore touches no coverage lattice value, strengthens no claim, and changes no
   honesty wording. An observation hook reports what the harness saw and is the only arm that would
   earn `hook_observed`; it stays deferred to v0.2. Whether a specific harness exposes a usable
   compaction or lifecycle trigger point is capability evidence rather than a spec choice, so E-013
   must freeze the exact trigger points from an installed-artifact capability run before any trigger
   hook ships. v0.1 may declare the trigger arm in an exact passing cell; the observation arm is
   always absent.

   The current Codex `0.144.5` capability candidate exposes `PreToolUse`, `PermissionRequest`,
   `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `UserPromptSubmit`, `SubagentStart`,
   `SubagentStop`, and `Stop`. This inventory explains the deferral but does not freeze support:
   E-013 still requires installed-artifact payload, privacy, and behavior evidence before use.

## Consequences

**Amendment (ADR-012, 2026-07-21):** MCP server registration is added as a *sibling* port,
`HarnessMcpPort` (`ports/harness_mcp.py`), with its own Codex adapters
(`codex_discovery.py`, `codex_mcp.py`). It deliberately does not extend `IntegrationsPort`:
registration is global, file-free, and marker-free, so reusing the skill-install types would
misuse fields designed for on-disk trusted-project content. The guarantee below is unchanged —
adding a harness is still one `HarnessId` value plus adapters, with no shared-type edits.

A fork can make Yoetz first-party on another harness by writing one adapter and one profile. It
edits no port, no registry, no guidance, and no schema. That is the property this ADR exists to
guarantee, and it follows from the ports/adapters pattern the rest of the tree already uses.

Any MCP host works on day one with no integration, no skill, and no configuration: six tools, the
tier-0 instructions, and four fetchable guidance documents. It earns `cooperative_mcp` with
`self_asserted` authorship and `published_only` artifact observation — the weakest honest coverage,
which the coverage vector already expresses precisely. That is not a degraded mode needing a warning
label; it is an accurate one.

First-party integration therefore buys ergonomics, and — once observation hooks exist — stronger
coverage. It never buys a different public contract. "Codex first" now means "the first harness where
we can deliver the guidance natively, and the first where we will be able to earn `hook_observed`",
not "the only harness that works."

A trigger-only v0.1 profile is one such ergonomic: it improves recovery reliability but leaves the
coverage vector unchanged. Profile declaration is not installation authority; no adapter silently
edits host configuration. The exact support cell must prove the trigger is already available through
the reviewed host mechanism, or select `None` and retain manual re-grounding.

The cost is one extra indirection for the single harness that exists today, and four guidance files
whose ownership must be respected: a harness restating a shared rule instead of linking to it is a
drift failure, enforced at build.

## Alternatives considered

**Keep guidance under `skills/codex/` and let other harnesses copy it.** Rejected: duplication with
no single owner is exactly the drift the one-owner-per-file method exists to prevent, and it would
force every fork to fork the content.

**Grow `ClientInfoModel.kind` per harness (`claude_code`, `cursor`, …).** Rejected: every new host
would need a Yoetz release and a frozen-schema change, and an unrecognized host would still have
nothing honest to send. The generic value costs nothing because `kind` carries no authority.

**Ship `instructions` only, without resources.** Rejected: it leaves the deep material Codex-only and
gives an agent no follow-up channel, which was the original defect. The bounded, stable-heading
reference documents were already designed for on-demand retrieval.

**Leave the MCP surface bare and document the limitation.** Rejected: the omitted rules are not
polish. An unguided agent publishing transcripts is a privacy failure, and one claiming Yoetz
verified work is the exact dishonesty the product exists to prevent.
