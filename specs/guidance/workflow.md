# guidance/workflow.md — the harness-neutral ten-step cooperative workflow

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-009, ADR-010 | **Imports (spec-tree):**
`guidance/README.md`, `specs/INTERFACES.md`, operation schemas,
`guidance/publication-policy.md`, `guidance/coverage-and-receipts.md` | **Imported by:**
`mcp/resources.md`, `skills/codex/yoetz/SKILL.md`, every future harness skill, capability tests

## Purpose

Own the cooperative workflow an agent follows to use Yoetz well, independently of which harness it
is running in. Previously this state machine lived inside the Codex skill; it was never
Codex-specific, and owning it here is what lets every harness — and every unprofiled MCP host —
teach the same workflow without copying it (ADR-010).

It teaches cooperative publication. It does not turn Yoetz into an orchestrator, a transcript
recorder, or a mandatory gate: user and host policy own any hard gate, never Yoetz.

## Public surface

The future file is reviewed Markdown, bounded (target ≤12 KiB), with stable headings so an agent can
retrieve exactly one step. It is addressed by the logical resource name `guidance/workflow.md`.

Required sections:

1. What Yoetz does and does not do.
2. When to activate it.
3. Startup and availability disclosure.
4. The ten-step cooperative workflow.
5. Multi-agent attribution and handoff.
6. Resume, compaction recovery, and staying next to the record during the work.
7. Finding response and recheck.
8. Receipt-bounded final wording.
9. Degraded and unavailable behavior.
10. Safety and privacy rules.

It links only to `guidance/publication-policy.md` and `guidance/coverage-and-receipts.md`, each at
the narrow section relevant to the step at hand.

## Behavior

### Activation and disclosure

The agent applies Yoetz to non-trivial work with multiple requested outcomes, delegated work,
meaningful verification, long duration or resume risk, or a material completion claim. It skips
translation, explanation, one-line edits, and other work where the ledger ceremony would exceed the
integrity benefit. E-009 is the sole central materiality/activation gate.

At activation the agent tells the user in one short sentence that it is using Yoetz as a local work
ledger and verifier. It does not imply enforcement, complete observation, authenticated authorship,
or successful initialization before `start` returns.

If Yoetz is unavailable, the agent continues the user's work unless the user or host explicitly made
Yoetz required, and discloses that no live ledger or receipt will exist. It never invents session
IDs, publications, checks, findings, or receipts.

### Ten-step workflow

1. **Materiality decision.** Decide whether Yoetz is proportionate; record no ceremony for trivial
   work.
2. **Start or attach.** Call `start` with stable request identity and appropriate
   `create`/`attach`/`create_or_attach` semantics. Show active/degraded status.
3. **Publish plan and obligations.** Publish a bounded plan, explicit requested outcomes,
   acceptance/evidence expectations, and assignments. For a large inventory, migration, or
   generated set, group obligations by independently reviewable work package or user-visible
   outcome. Files are leaf evidence in a package manifest, not automatically one obligation each.
   Do not paste the user's whole prompt or manufacture one obligation per file.
4. **Delegate with context.** Give each subagent session/task/writer/assignment context and require
   concise typed material publications. Do not share or publish full transcripts.
5. **Publish material work.** Default to one bounded publication batch for each material work-package
   transition: assignment/start, a decision or blocked attempt, an independently useful result, or
   completion/revision. Routine reads, searches, formatting, generated-file writes, and other leaf
   mechanics do not each earn an event. Batch decisions, attempts, results, evidence references,
   claims, and plan revisions when they change what another participant or reviewer needs to know.
   When semantic
   review would otherwise be blind, publish only the bounded problem-local changed hunk, enclosing
   symbol, linked test/failure excerpt, or directly supporting/contradicting evidence needed to
   evaluate the claim; label its source, state, and coverage honestly.
6. **Stay next to the record.** Call `status` whenever memory and the record could have diverged —
   after resume, compaction, or handoff, and at any uncertainty about what is already done — rather
   than reconstructing from memory. This step is not only recovery. `status` with
   `view=candidate_findings` runs the deterministic packs against the current record, costs nothing
   durable, and records nothing, so an agent can ask what the record says about its own work at any
   point rather than only before a completion claim. An attempt the record left unresolved surfaces
   there while there is still time to finish it. Treat freshness, unknown, and redaction gaps as
   real. Candidates are advisory and carry no verdict: act on them freely, but reading them is not
   checking, and an empty list is not a clean result
   (`guidance/coverage-and-receipts.md`).
7. **Check before completion.** Publish the intended material completion claim and current evidence,
   then call `check` before telling the user the task is complete. The step carries the
   verification-mode decision rule: `semantic_if_configured` for most material implementation or
   review claims; `semantic_required` when the completion claim depends on qualitative correctness,
   design conformance, security or privacy reasoning, interoperability, or whether the code
   satisfies the ask; `deterministic_only` only for explicitly local or structural checks, a
   semantic-disabled policy, or a deliberate no-egress choice, and then the limitation is disclosed.
   Omitting `mode` resolves through the configured `VerificationPolicy` default. The step also
   states the semantic-case publication duty: publish the smallest state-bound diff or symbol and
   the directly relevant test or failure excerpt rather than self-asserted completion prose alone.
8. **Respond deliberately.** Read each reviewer challenge as advisory. Accept and act, provide
   evidence, revise the completion claim, dispute with evidence, or state that the limitation cannot
   currently be resolved. Encode those choices through existing `respond` plus material
   `publish_work`; reject or waive only with the existing bounded reason/authority rules. A response
   does not erase the finding.
9. **Recheck after change.** Material edits, new evidence, plan changes, or finding responses require
   a current check; do not reuse a stale verdict.
10. **Receipt and final answer.** Request a receipt and word the final response no more strongly than
    the receipt's weakest material coverage, freshness, unresolved findings, and limitations. The
    step states that every receipt format projects under the default agent-context policy, and that
    a stricter owner policy blocking digest-bound `json` is answered by re-requesting `markdown` or
    `text`, not by retrying the same shape.

Every mutating call reuses its original request/operation IDs after timeout or reconnect. Timeout
means unknown outcome; the agent retries idempotently or inspects `status`.

### Multi-agent behavior

The parent publishes assignments and gives each delegate a distinct logical writer identity.
Subagents may read compact status/projections and publish their own actions, results, and claims. The
parent does not impersonate them and does not upgrade caller-asserted identity.

Before integration, the parent reads current assignments, decisions, contradictions, and open
obligations. A subagent summary is a claim, not proof; link accepted evidence and results separately.
Contradictory publications remain visible until a recorded decision resolves them.

A parent reading a subagent's material is reading content it did not author, so that material stays
under the ordinary agent-context ceiling even when the parent's own material projects freely.

### Findings and final language

The document gives concrete wording examples:

- permitted: "Yoetz found no deterministic issue in the cooperatively published record at the
  current frontier; publication remained self-asserted and repository state was not independently
  reproduced."
- forbidden: "Yoetz verified the work."
- permitted when degraded: "The deterministic check completed; semantic review was not configured."
- required when unavailable: "Yoetz was unavailable, so no live ledger check or receipt was
  produced."

The agent retains judgment: it may reject a model-derived or inapplicable finding, but it records the
reason and does not describe rejection as proof the finding was false.

When a reviewer says code did not change, the agent first checks the structured change observation.
If content was `not_recorded`, `not_selected`, `withheld_by_policy`, or `redacted_never_send`, it
treats the statement as unsupported and may publish better evidence or reject it with evidence. Only
an observed equal subject-state relation supports unchanged-state language. Any response, new
evidence, or revised claim is followed by a current check before completion.

### Harness neutrality

The document names no harness, install path, provider, or model. It describes calling the six
operations; whether they arrive as MCP tools or as CLI invocations is the host's business and does
not change a step. Where a harness offers a native ergonomic — a skill trigger, a hook — that
harness's own skill spec describes it, and never by editing this file.

## Errors and edge cases

- Start succeeds but publication fails: disclose partial ledger/freshness; retry same IDs.
- Unknown event or server version: stop using incompatible operations and disclose, rather than
  guessing a downgraded payload.
- Context compaction loses local IDs: call `status` or reattach; never create a duplicate task merely
  to continue.
- Check provider timeout or refusal: preserve the deterministic result in every mode. When semantic
  was required, return it as `incomplete_check` with no semantic findings and state the exact
  missing-semantic reason; never turn the completed local result into an operation failure.
- Receipt generation fails: do not fabricate or summarize it as present; the final answer can cite the
  last durable frontier and limitation.
- Version mismatch: the capability check fails visibly; host work can continue only under the
  optional-server policy.
- A finding whose prose is withheld by the agent-context ceiling is still a real finding: the agent
  reports the structural finding honestly and does not treat missing prose as absence of a problem.
- A large file inventory has no coherent work-package boundary: pause before publication and define
  bounded outcomes; do not fall back to one obligation/event per file merely because filenames are
  enumerable.

## Invariants

1. The workflow teaches cooperative publication, not execution or orchestration.
2. It never asks an agent to expose hidden reasoning or full transcripts.
3. Every completion flow checks current state and bounds final wording by coverage.
4. Degradation is disclosed, never papered over with invented Yoetz state.
5. User and host policy — not Yoetz — own any hard gate.
6. The reviewer/agent loop remains `check → respond/publish_work → check`; it never asks a human for
   routine assisted-review findings and never treats the reviewer as waiver authority.
7. The document is harness-neutral and names no harness, path, provider, or model.
8. A candidate read is never presented as a check and never substitutes for one before a completion
   claim; it exists so the agent can correct itself during the work.
9. Work-package transitions, not file count or tool-call count, are the publication unit; a manifest
   may prove many leaf files without creating matching obligations or events.

## Tests

- `specs/tests/capability.md`: the ten steps drive a real workflow from an unprofiled MCP host with
  no installed skill, proving no step depends on a harness integration.
- `specs/tests/conformance.md`: a scripted model follows all ten steps and never publishes forbidden
  transcript/secret/per-read data; the final answer is no stronger than the fixture receipt. A
  scripted model that reads `candidate_findings`, sees an empty list, and reports that it checked
  and found nothing fails; one that reads candidates, acts on an unresolved attempt, and then runs a
  recorded check before claiming completion passes.
- A generated 100-file fixture is represented by coherent work-package obligations and one bounded
  manifest evidence item per completed package. A model that emits 100 file-shaped obligations or
  routine per-file events fails even if every event is otherwise schema-valid.
- `specs/tests/packaging.md`: source/wheel/installed byte parity, size bound, and stable headings.
- Adversarial fixtures: abandoned obligation, omitted failure, stale test, wrong semantic suggestion,
  and import-only missing event each produce the required response.

## Open questions

None.
