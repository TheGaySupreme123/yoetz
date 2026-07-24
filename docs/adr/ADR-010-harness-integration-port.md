# ADR-010 — Harness integration is a port; Codex is the first adapter

**Status:** Working decision for spec drafting (2026-07-16; trigger-profile amendment 2026-07-17;
first-party Codex observation amendment 2026-07-22). Ratification requires the packaging
byte-parity run plus a capability run proving an unprofiled MCP host completes the workflow from an
installed artifact, and — for first-party Codex cells that advertise observation — installed-artifact
evidence that observation ingest earns `hook_observed` only from real observation evidence.
**Owning public specs:** `specs/guidance/`, `specs/src/yoetz/ports/integrations.py.md`,
`specs/src/yoetz/ports/observation.py.md`, `specs/src/yoetz/domain/observation.py.md`,
`specs/src/yoetz/application/integrations.py.md`,
`specs/src/yoetz/adapters/integrations/codex_skill.py.md`, `specs/src/yoetz/mcp/descriptors.md`,
`specs/src/yoetz/mcp/resources.md`, `specs/src/yoetz/mcp/server.md`,
`specs/schemas/common/client-info-1.0.0.schema.json.md`.
**Relates to:** ADR-002 (canonical protocol), ADR-005 (Codex capability identity), ADR-007
(packaging/release), ADR-009 (egress/privacy).

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
   `HarnessHookProfile | None`. A v0.1 cell may declare a trigger arm, an observation arm, both, or
   neither after the corresponding installed-artifact evidence passes; an unproven cell remains
   `None`. Observation is a **required v0.1 capability for first-party Codex** once the exact cell
   is capability-proven: it is the only integration arm that earns `hook_observed`, and only when
   real observation evidence exists. Trigger-only cells remain valid ergonomics and still earn no
   coverage. Unprofiled or unsupported cells keep both arms absent. Support is never inferred
   across neighboring host versions.

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
   `status` carries `readOnlyHint=true`; `receipt` carries `readOnlyHint=false` because it stages a
   receipt object and appends a `receipt_recorded` event. Every tool carries an explicit
   `idempotentHint=true`. Nothing carries `destructiveHint`, because no Yoetz operation deletes
   recorded evidence. Observation does **not** add a seventh MCP tool: live observation is local
   CLI/service control through `ObservationPort` (ingest/status/pause/resume/revoke), and advice
   surfaces through existing nonblocking hooks plus ordinary `status` / findings / coverage
   machinery.

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
   honesty wording. An observation hook reports what the harness saw and is the only arm that earns
   `hook_observed`, and only when real observation evidence exists (never from a trigger alone, a
   consent marker, or an empty/degraded observation status).

   For first-party Codex in protocol `0.1`, observation is a required integration capability once
   the exact support cell is proven. Dual sources feed `ObservationPort`: **hooks are primary**
   (low-latency structural envelopes) and **selective session-stream reconciliation** fills gaps
   without replacing the hook path. Future unrecognized Codex events are accepted only as a stable
   envelope plus allowlisted structural facts; unknown semantics become opaque gaps and never infer
   success. Batch `ImporterPort` JSONL import remains a separate support surface that may reuse
   mapping vocabulary but is not live observation. App-server integration and additional harnesses
   remain deferred.

   Whether a specific harness exposes usable trigger or observation points is capability evidence
   rather than a spec choice. E-013 freezes exact trigger and observation event, payload/privacy,
   coalescing/loop, gap, and failure behavior from an installed-artifact run before a cell advertises
   either arm. Unproven cells remain `None`.

   The current Codex `0.144.5` capability candidate exposes `PreToolUse`, `PermissionRequest`,
   `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `UserPromptSubmit`, `SubagentStart`,
   `SubagentStop`, and `Stop`. This inventory nominates candidates but does not freeze support:
   E-013 still requires installed-artifact payload, privacy, and behavior evidence before use.

## Consequences

**Amendment (ADR-012, 2026-07-21):** MCP server registration is added as a *sibling* port,
`HarnessMcpPort` (`ports/harness_mcp.py`), with its own Codex adapters
(`codex_discovery.py`, `codex_mcp.py`). It deliberately does not extend `IntegrationsPort`:
registration is global, file-free, and marker-free, so reusing the skill-install types would
misuse fields designed for on-disk trusted-project content. The guarantee below is unchanged —
adding a harness is still one `HarnessId` value plus adapters, with no shared-type edits.

**Amendment (2026-07-22):** First-party Codex observation is required for v0.1 completion of the
Codex integration, still on protocol version `0.1`. Observation is a sibling local-control
capability (`ObservationPort`), not a seventh MCP tool and not an `IntegrationsPort` overload.
Existing v0.1 ledger/object data remains readable. Bundle migration `0002` owns consent, cursor,
dedup, structural envelopes, and the current advice snapshot. Migration `0003` owns authenticated
encrypted workspace-locator/content references, canonical logical identity claims, exact-digest
check-policy trust, generation-fenced verification jobs/results, and advice history/delivery state.
Migration `0004` owns inspection snapshots, workspace→Yoetz-session routing, and session-scoped
current advice without rewriting `0003`. Repositories own those tables; coordinators do not issue
private SQL.
Observation consent is project-level and separate from egress consent. The plaintext local boundary
records a private workspace commitment, structural outbox/quarantine evidence, and encrypted object
identities—never raw task content or a raw path in logs/status/SQLite.

Hook `PostToolUse` and stream `item.completed` for one host call normalize before materialization to
the same semantic kind, correlation identity, roles, operation digest, and ledger IDs. A durable
logical-identity claim merges source coverage and prevents duplicate action/result appends while
allowing later encrypted content references. Cursor advancement is coupled to durable outbox
insertion; overflow leaves later stream input replayable. Unsupported visible future events retain
an opaque envelope, encrypted visible payload when available, and an explicit gap. Session stop is
source-generation fenced, pending work drains fairly across mapped sessions, and bounded quarantine
eviction retains aggregate commitment/count/time range plus a loss gap.

After every completed tool action, ready-service composition captures a descriptor-safe structural
workspace digest. Real state change enqueues exact-policy verification; unchanged state does not.
Approved checks never execute inside the hook RPC budget. A generation-fenced
`ObservationVerificationSupervisor` owned by the ready application lifecycle drains pending jobs:
one job runs per workspace through the enforcing sandbox, newer pending state coalesces older
pending work, abandoned generation leases recover after restart, and a result becomes current only
if post-run state still matches. Sandbox absence is an explicit unavailable result, never a pass.
Bundle migration `0004` owns durable inspection snapshots, workspace→Yoetz-session routes, and
session-scoped current advice; migration `0003` is immutable. Deterministic advice consumes this
evidence offline and materializes through existing `finding_recorded`; ordinary
`status(view="advice")` loads only the advice for the routed workspace and Yoetz session, and safe
hook context surfaces the same bounded finding/evidence identities. The MCP registry remains exactly
six tools.

A fork can make Yoetz first-party on another harness by writing one adapter and one profile. It
edits no port, no registry, no guidance, and no schema. That is the property this ADR exists to
guarantee, and it follows from the ports/adapters pattern the rest of the tree already uses.

Any MCP host works on day one with no integration, no skill, and no configuration: six tools, the
tier-0 instructions, and four fetchable guidance documents. It earns `cooperative_mcp` with
`self_asserted` authorship and `published_only` artifact observation — the weakest honest coverage,
which the coverage vector already expresses precisely. That is not a degraded mode needing a warning
label; it is an accurate one.

First-party Codex integration therefore buys ergonomics and, when the observation arm is
capability-proven and consented, stronger coverage via `hook_observed`. It never buys a different
public contract. "Codex first" means "the first harness where we deliver guidance natively and the
first where observation can earn `hook_observed`", not "the only harness that works."

A trigger-only v0.1 profile remains a valid ergonomic: it improves recovery reliability but leaves
the coverage vector unchanged. An observation-capable cell must still prove real observation
evidence before any `hook_observed` claim. Profile declaration is not installation authority; no
adapter silently edits host configuration. The exact support cell must prove each advertised arm
through the reviewed host mechanism, or select `None` / empty `observation_events` and retain the
honest weaker coverage.

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
