# skills/codex/yoetz/SKILL.md — Codex skill header and activation adapter

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-007, ADR-010 | **Imports (spec-tree):**
`specs/INTERFACES.md`, operation schemas, `guidance/README.md`, `guidance/agent-instructions.md`,
`guidance/workflow.md`, `guidance/publication-policy.md`, `guidance/coverage-and-receipts.md` |
**Imported by:** release resources, `cli integrate codex skill install`, capability and packaging
tests

## Purpose

Specify the canonical Codex skill: the frontmatter, activation semantics, and install layout that
let Codex discover and apply Yoetz's harness-neutral guidance ergonomically.

This is the first harness adapter for guidance, not the owner of it (ADR-010). The workflow,
publication policy, and coverage rules live under `guidance/` and are shared byte-for-byte with
every other harness and with the MCP baseline. This file owns only what is genuinely Codex-shaped:

- the required Codex skill frontmatter and the separate Yoetz compatibility manifest;
- when and how Codex activates the skill;
- the `.agents/skills/yoetz/` install layout and how the shared members are laid out inside it;
- the Codex capability profiles this skill is tested against.

It owns no workflow step, no publication rule, no coverage wording, and no forbidden-content rule.
Restating one here instead of linking to its owner is a drift failure. Codex is first because its
skill surface makes the guidance land well — not because the guidance is about Codex.

This file is a specification of the future skill content. The implemented source will live at
`skills/codex/yoetz/SKILL.md` and be copied byte-for-byte into the wheel before explicit
installation into a trusted project.

## Public surface

### Required frontmatter

- `name: yoetz`.
- A concise description that triggers on material, multi-step agent work where the user benefits
  from a durable obligation/evidence/completion record; it explicitly avoids trivial edits and
  ordinary questions.
- Optional `metadata.short-description` contains only a concise UI description.
- No other frontmatter fields. Codex ignores Yoetz-private compatibility fields; skill schema
  version, Yoetz protocol range, minimum/maximum-tested Codex versions, and required MCP server
  name `yoetz` live in the adjacent compatibility manifest.
- No secrets, installation-specific paths, provider/model names, or mutable network references.

The Codex-readable frontmatter shape is the Codex-specific part of this file and must match
`HarnessProfile(codex).frontmatter_profile`. Another harness's skill spec owns its own header shape
and shares none of these fields.

### Required skill sections

The skill file itself is deliberately thin. It contains exactly:

1. When Codex should activate the skill, and when it should not.
2. Startup/availability disclosure.
3. Codex-specific tool/command compatibility and the required MCP server name.
4. Links into the installed shared guidance, at the narrow section relevant to each step.

Everything else is owned by `guidance/` and installed alongside, not restated:
`guidance/workflow.md` owns the ten steps, multi-agent attribution, resume/compaction recovery,
finding response, receipt-bounded wording, and degraded behavior;
`guidance/publication-policy.md` owns material-publication and forbidden-content rules;
`guidance/coverage-and-receipts.md` owns coverage and receipt language;
`guidance/agent-instructions.md` owns the non-negotiable floor.

### Installed layout

`install_skill(codex, …)` writes `HarnessProfile(codex).skill_root` — `.agents/skills/yoetz/` —
containing this `SKILL.md`, the compatibility manifest, and the four shared guidance members under
`references/`, each byte-identical to `resources/guidance/`. The `references/` name is a Codex
layout convention owned here; the bytes inside are not.

## Behavior

### Activation and disclosure

Codex applies the skill to non-trivial work with multiple requested outcomes, delegated work,
meaningful verification, long duration/resume risk, or a material completion claim. It skips
translation, explanation, one-line edits, and other work where the ledger ceremony would exceed
the integrity benefit.

At activation, Codex tells the user in one short sentence that it is using Yoetz as a local work
ledger/verifier. It does not imply enforcement, complete observation, authenticated authorship, or
successful initialization before `start` returns.

If the optional MCP server is unavailable, Codex continues the user's work unless the user/host
explicitly made Yoetz required. It discloses that no live Yoetz ledger or receipt will exist. It
never invents session IDs, publications, checks, findings, or receipts.

### Shared workflow, publication, and wording

These are owned by `guidance/` and installed beside this skill; the skill links to the relevant
section and restates none of them:

| Concern | Owner |
|---|---|
| Ten-step cooperative workflow, multi-agent attribution, resume/compaction, finding response, receipt-bounded final wording, degraded behavior | `guidance/workflow.md` |
| Material-publication and forbidden-content rules, event-family cheat sheet, problem-local excerpt boundary | `guidance/publication-policy.md` |
| Coverage dimensions, freshness/redaction gaps, receipt field map, approved and forbidden completion wording | `guidance/coverage-and-receipts.md` |
| The non-negotiable floor every host receives regardless of this skill | `guidance/agent-instructions.md` |

Codex reaches these as installed files under `references/`. An unprofiled MCP host reaches the same
bytes as `yoetz://guidance/<name>` resources. Neither path is authoritative over the other, because
both serve one owner's bytes. If this file and a guidance document ever disagree, the guidance
document wins and the build fails.

### Installation/versioning

The canonical source, packaged resource, and installed copy are byte-identical. Installation:

1. locate the target trusted repository and refuse symlink/traversal destinations;
2. show source/destination, version compatibility, and exact diff;
3. require explicit consent before create/overwrite;
4. preserve a modified installed file unless the user explicitly approves replacement;
5. write atomically with owner-safe permissions;
6. `status` reports source/installed digests and compatibility;
7. `remove` deletes only the exact Yoetz-managed copy after confirmation.

The skill never silently edits Codex global/project configuration or registers MCP as a side effect.
Those are separate previewed integration steps.

Its compatibility manifest records the exact capability-profile ID selected by the installed
artifact. That profile may advertise a trigger-only compaction recovery hook after E-013 passes, or
an explicit absent value. This skill does not install/configure the hook and does not treat its
presence as observation or stronger coverage; unsupported profiles continue through the shared
manual resume/compaction guidance.

## Errors and edge cases

Workflow-level degradation — publication failure, compaction, provider timeout, receipt failure — is
owned by `guidance/workflow.md` and behaves identically on every harness. Only the Codex-specific
cases live here:

- Skill/MCP version mismatch: capability check fails visibly; host work can continue only under the
  optional-server policy.
- Another loaded skill also named `yoetz`: discovery is ambiguous and the capability/install flow
  refuses to advertise `$yoetz` until the loaded skill set resolves uniquely to this managed path.
- Codex discovers the skill but the `yoetz` MCP server is absent: the skill still loads and discloses
  that no live ledger or receipt will exist. Skill presence is never evidence of server availability.
- The installed copy is modified: installation preserves it and refuses silent replacement; a
  modified skill is not silently trusted as the reviewed one.
- The installed guidance members drift from `resources/guidance/`: byte parity fails and the skill is
  reported `modified`, never repaired in place.
- The selected capability profile lacks passing trigger evidence or the trigger fails: use the
  manual shared recovery path; do not infer support, block optional work, or claim observation.

## Invariants

1. This file owns Codex frontmatter, activation, and layout only; it restates no shared rule.
2. Installed bytes and advertised compatibility are testable release artifacts.
3. Installed guidance members are byte-identical to `resources/guidance/` and to every other
   harness's copy.
4. The skill never claims Yoetz observes, enforces, or verifies, and never implies that installing it
   strengthens coverage.
5. User/host policy — not Yoetz — owns any hard gate.
6. Removing this skill removes no shared guidance owner and breaks no other harness.
7. Trigger-hook presence is exact-profile capability evidence, never a version-range inference or
   a coverage upgrade.

## Tests

- `specs/tests/capability.md`: explicit and implicit Codex discovery, start/publish/check/respond/
  receipt workflow, parent/subagent attribution, resume/compaction, server unavailable optional vs
  required, trigger-present and trigger-absent recovery with equal coverage, cancellation/retry,
  and modified-skill install protection.
- `specs/tests/conformance.md`: a scripted model follows all ten steps and never publishes
  forbidden transcript/secret/per-read data; the final answer is no stronger than the fixture
  receipt. The same fixtures run against an unprofiled MCP host with no installed skill, proving the
  guidance — not this file — carries the behavior.
- `specs/tests/packaging.md`: source/wheel/installed byte equality, compatibility manifest, and
  guidance-member parity with `resources/guidance/`.
- A drift check fails the build if this file restates a workflow, publication, or coverage rule owned
  by `guidance/`.

## Open questions

None.

E-009 is the sole central materiality/activation gate.
