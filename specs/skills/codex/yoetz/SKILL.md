# skills/codex/yoetz/SKILL.md — Codex cooperative-workflow skill specification

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/INTERFACES.md`, operation schemas, `references.md` | **Imported by:** release
resources, `cli integrate codex skill install`, capability and packaging tests

## Purpose

Specify the canonical source skill that teaches Codex how to use Yoetz without turning Yoetz into
an orchestrator, transcript recorder, or mandatory gate. The installed skill is a product surface:
it determines whether cooperative publication is useful, current, honest, and low-ceremony.

This file is a specification of the future skill content. The implemented source will live at
`skills/codex/yoetz/SKILL.md` and be copied byte-for-byte into the wheel before explicit
installation into a trusted project.

## Public surface

### Required frontmatter

- `name: yoetz`.
- A concise description that triggers on material, multi-step agent work where the user benefits
  from a durable obligation/evidence/completion record; it explicitly avoids trivial edits and
  ordinary questions.
- Compatibility metadata: skill schema version, Yoetz protocol range, minimum/maximum-tested Codex
  versions, and required MCP server name `yoetz`.
- No secrets, installation-specific paths, provider/model names, or mutable network references.

### Required skill sections

1. What Yoetz does and does not do.
2. When to activate it.
3. Startup/availability disclosure.
4. The ten-step cooperative workflow.
5. Material-publication policy.
6. Multi-agent attribution and handoff.
7. Resume/compaction recovery.
8. Finding response and recheck.
9. Receipt-bounded final wording.
10. Degraded/unavailable behavior.
11. Safety/privacy rules.
12. Command/tool compatibility and reference links.

The skill links only to the two installed reference files specified by `references.md`.

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

### Ten-step workflow

The implemented skill teaches this exact state machine:

1. **Materiality decision.** Decide whether Yoetz is proportionate; record no ceremony for trivial
   work.
2. **Start or attach.** Call `start` with stable request identity and appropriate
   `create`/`attach`/`create_or_attach` semantics. Show active/degraded status.
3. **Publish plan and obligations.** Publish a bounded plan, explicit requested outcomes,
   acceptance/evidence expectations, and assignments. Do not paste the user's whole prompt.
4. **Delegate with context.** Give each subagent session/task/writer/assignment context and require
   concise typed material publications. Do not share or publish full transcripts.
5. **Publish material work.** Batch decisions, attempts, results, evidence references, claims, and
   plan revisions when they change what another participant/reviewer needs to know.
6. **Recover current state.** After resume, compaction, handoff, or uncertainty, call
   `status` before relying on memory. Treat freshness/unknown/redaction gaps as real.
7. **Check before completion.** Publish the intended material completion claim and current evidence,
   then call `check` before telling the user the task is complete.
8. **Respond deliberately.** Acknowledge and fix valid findings; reject or waive only with a bounded
   reason, authority, scope, and expiry where applicable. A response does not erase the finding.
9. **Recheck after change.** Material edits, new evidence, plan changes, or finding responses require
   a current check; do not reuse a stale verdict.
10. **Receipt and final answer.** Request a receipt and word the final response no more strongly than
    the receipt's weakest material coverage, freshness, unresolved findings, and limitations.

Every mutating call reuses its original request/operation IDs after timeout or reconnect. The skill
states that timeout means unknown outcome; Codex retries idempotently or inspects `status`.

### Publication policy

Publish:

- obligations created by explicit user requirements or accepted plans;
- decisions that change scope, approach, compatibility, security, or expected output;
- bounded actions/results whose success or failure materially supports completion;
- immutable evidence/digests or honest mutable references;
- plan revisions/supersessions and disclosed abandoned work;
- material claims and limitations;
- finding responses and waivers.

Do not publish:

- chain-of-thought, hidden reasoning, full prompts/transcripts, every file read, every search query,
  heartbeat/status chatter, duplicated terminal output, credentials, secrets, or raw source;
- an action result without binding it to relevant repository/artifact state where staleness matters;
- a success claim inferred solely from tool invocation or process start;
- a stronger identity/observation/immutability class than the channel proves.

Batch adjacent events up to public limits while preserving logical order and per-writer sequence.
When uncertain, prefer one concise event describing the material transition over noisy telemetry.

### Multi-agent behavior

The parent publishes assignments and gives each delegate a distinct logical writer identity.
Subagents may read compact status/projections and publish their own actions/results/claims. The
parent does not impersonate them and does not upgrade caller-asserted identity.

Before integration, the parent reads current assignments, decisions, contradictions, and open
obligations. A subagent summary is a claim, not proof; link accepted evidence/results separately.
Contradictory publications remain visible until a recorded decision resolves them.

### Findings and final language

The skill gives concrete wording examples:

- permitted: “Yoetz found no deterministic issue in the cooperatively published record at the
  current frontier; publication remained self-asserted and repository state was not independently
  reproduced.”
- forbidden: “Yoetz verified the work.”
- permitted when degraded: “The deterministic check completed; semantic review was not configured.”
- required when unavailable: “Yoetz was unavailable, so no live ledger check or receipt was
  produced.”

Codex retains judgment: it may reject a model-derived or inapplicable finding, but it records the
reason and does not describe rejection as proof the finding was false.

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

## Errors and edge cases

- Start succeeds but publication fails: disclose partial ledger/freshness; retry same IDs.
- Unknown event/server version: stop using incompatible operations and disclose, rather than
  guessing a downgraded payload.
- Context compaction loses local IDs: call `status`/reattach; never create a duplicate task
  merely to continue.
- Check provider timeout/refusal: preserve the deterministic result in every mode. When semantic
  was required, return it as `incomplete_check` with no semantic findings and state the exact
  missing-semantic reason; never turn the completed local result into an operation failure.
- Receipt generation fails: do not fabricate or summarize it as present; final answer can cite the
  last durable frontier and limitation.
- Skill/MCP version mismatch: capability check fails visibly; host work can continue only under the
  optional-server policy.

## Invariants

1. The skill teaches cooperative publication, not execution/orchestration.
2. It never asks Codex to expose hidden reasoning or full transcripts.
3. Every completion flow checks current state and bounds final wording by coverage.
4. Degradation is disclosed, never papered over with invented Yoetz state.
5. Installed bytes and advertised compatibility are testable release artifacts.
6. User/host policy—not Yoetz—owns any hard gate.

## Tests

- `specs/tests/capability.md`: explicit and implicit discovery, start/publish/check/respond/
  receipt workflow, parent/subagent attribution, resume/compaction, server unavailable optional vs
  required, cancellation/retry, and modified-skill install protection.
- `specs/tests/conformance.md`: scripted model follows all ten steps and never publishes
  forbidden transcript/secret/per-read data; final answer is no stronger than fixture receipt.
- `specs/tests/packaging.md`: source/wheel/installed byte equality and compatibility manifest.
- Adversarial fixtures: abandoned obligation, omitted failure, stale test, wrong semantic suggestion,
  and import-only missing event each produce the required skill response.

## Open questions

None.

E-009 is the sole central materiality/activation gate.
