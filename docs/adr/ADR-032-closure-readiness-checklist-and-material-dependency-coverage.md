# ADR-032 — Closure readiness as a checklist, and material-dependency coverage

**Status:** Proposed for [issue #913](https://github.com/TheGaySupreme123/yoetz/issues/913). The
maintainer acknowledged the design-gated scope on 2026-09-30 and adopted option 2 of the issue (the
agent-actionable / standing / acknowledged split with `ready_with_limitations`); option 3 (a scoped
"clean local rules" outcome) stays a documented option only and is not decided here. Decisions 1–5
are implemented. Decisions 6 and 7 are recorded here as the direction the issue recommends; the
single status coverage definition of decision 7 is implemented, and the rest of 6 and 7 is tracked
on the issue.
**Implemented by:** `src/yoetz/kernel/closure_readiness.py`, `src/yoetz/application/status.py`,
`src/yoetz/adapters/memory/ledger.py`, `src/yoetz/protocol/models.py`,
`src/yoetz/protocol/readiness_text.py`, `src/yoetz/mcp/summaries.py`, `src/yoetz/cli/render.py`,
`src/yoetz/tui/`, the status-result 1.4.0 schema, and the shipped guidance and skills.
**Relates to:** ADR-019 (declared completion scope), ADR-020 (typed evidence digest provenance),
ADR-027 (task lineage), ADR-030 (typed recovery directives), and issues #904, #905, #910, #911,
#912, #917.

## Context

In all 58 Yoetz sessions of the DeepSWE v2 benchmark, `closure_readiness.blocking_conditions`
contained `coverage_gaps_declared`, and in 36 of them it was the only condition. The condition was
appended whenever the page carried any gap. Every Codex session carries standing gaps
(`host_outcome_unavailable`, `unpaired_event`), and a local-only check always adds
`semantic_review_not_requested`, so readiness could never say "you are done". Agents could not tell
work remaining from a limitation their host always has; they rechecked unchanged state, or stopped
without a checklist end state.

The verdict in that lane is honest and stays: a local-only check with gaps is
`insufficient_coverage`. What was missing is a stop signal that says the agent is finished and
names what the receipt will disclose.

## Decisions

1. **Readiness is a checklist.** Every `status` view carries, beside the unchanged
   `blocking_conditions`, a `state` and three groups:
   - `agent_actionable`: what the agent can still do — `obligations_open`, `findings_unanswered`,
     an unacknowledged `receipt_findings_unresolved`, `no_plan_published`,
     `no_obligations_declared`, `projection_stale`, `check_in_progress` (a check holds the session
     frontier; its result is still to come), `check_not_recorded` or `check_not_applicable` (the
     receipt's own applicability rule: run a check after the material change), and every gap code
     the table classifies as actionable;
   - `standing_limitations`: gap codes no available agent action removes under the current host,
     profile and privacy policy; they are disclosed on the receipt, never instructions;
   - `acknowledged_not_done`: obligations and findings the agent recorded as not done, with a
     reason (ids, bounded to 64, plus `acknowledged_not_done_count`).

   `state` is `action_required` while `agent_actionable` is non-empty, `ready_with_limitations`
   when it is empty and something is standing or acknowledged, `ready` when nothing remains at
   all, and `unknown` exactly when readiness itself is unknown (`readiness_unknown`).
   `blocking_conditions` keeps naming everything that bounds the conclusion, so
   `coverage_gaps_declared` remains, now as disclosure; nothing is filtered or suppressed.

2. **A closed, versioned classification table.** `yoetz.kernel.closure_readiness` assigns every gap
   code the product emits exactly once to `agent_actionable` or `standing_limitation`, and
   `gap_classification_version` (currently `"1"`) names the table that classified a response. The
   classification test: a code is standing when no agent action available under the current host,
   profile and privacy policy removes it; it is actionable when a documented agent action (publish,
   respond, revise a claim or plan, recheck after a material change, re-read status once the
   service catches up) removes it. Disclosure codes that describe the bounds of a review, a
   deliberate selection or a capture failure are standing. Of the 20 codes observed in the v2
   evidence only `completion_plan_not_claimed` is unconditionally actionable.

3. **Build-time completeness, runtime conservatism.** A conformance test enumerates every gap-code
   producer — `ObservationGapCode`, `CoordinationGapCode`, the `*_GAP` constants,
   `semantic_coverage_gap_code` outputs, the channel baselines, the lineage manifest template, and a
   syntax-tree scan of gap-sink literals across `src/yoetz` (deterministic-check, command-attempt,
   receipt, lineage, observation and import producers) — and fails when one emits an unclassified
   code or the table names a code nothing emits. Prefixed forms (`coverage:<code>`,
   `check_coverage:<code>`, `retained_finding_coverage:<code>`, `semantic_outcome:<code>`,
   `lineage:<code>:…`) classify by their base code. A code the running build does not know — for
   example one read from a ledger a newer build wrote — is reported as `unclassified_gap:<code>` in
   `agent_actionable`, so the conservative default is visible and never reads as done.

4. **One route-dependent code.** `semantic_review_not_requested` is standing on a route where
   AI-powered review is optional or disabled. It is actionable only when the effective verification
   policy requires AI-powered review and no check whose AI-powered review succeeded has been
   recorded without a later material change (remedy: run it).

5. **Derived per request; no verdict changes.** Readiness is derived from the compact projection and
   the recorded prefix at the requested frontier on every read, never cached across frontiers and
   never recorded. A restart, a reattach or an upgrade over a ledger written by an older build
   recomputes it without migrating any event, and old receipts keep their recorded wording.
   Readiness facts that an adapter cannot derive make readiness `unknown`, never a stop state.
   `standing_limitations` promises disclosure on the receipt, so it holds only recorded gaps: a live
   lineage token from status's catalog comparison that no recorded evaluation carries yet stays
   agent-actionable, because a receipt folds recorded lineage only.
   Classification never removes a code from `known_gaps`, never changes a check verdict or receipt
   conclusion, and never strengthens coverage. The frozen directive for each state lives in
   `yoetz.protocol.readiness_text`; the `ready_with_limitations` sentence is the owner-approved
   "Nothing further to do. N standing limitation(s) and M acknowledged item(s) will be disclosed on
   the receipt. Request the receipt." MCP text, the CLI and the terminal interface render it from
   the state token and service counts, in ADR-030's directive style. No readiness state is rendered
   as verified: the terminal interface never uses its verified glyph for any of them.

6. **Acknowledged, not done (direction).** Findings use the `acknowledged_not_done` `respond`
   disposition defined by #905, with its required reason; readiness reads a finding's latest
   response disposition by value, so no second finding mechanism exists. Obligations get the
   explicit form owned by #913: an obligation-level disposition published through `publish_work`
   with the same name and a required short reason. An acknowledged obligation leaves
   `obligations_open` and joins `acknowledged_not_done`; it is terminal — a later completion is a
   new linked item, and the receipt shows both. One receipt section, "Acknowledged, not done",
   lists each item with its reason; it is never folded into a pass or counted as resolved.

7. **Material-dependency coverage (direction).** A *material dependency* of a conclusion is the
   closure of the effective completion claims' `supporting_refs`, the resolved obligations'
   `resolution_evidence_refs`, and the results and evidence those references name. Receipt and
   completion coverage fold `weakest()` over that closure rather than over every projection record.
   Service-verified capture (an `observation_captured` digest with `immutable_snapshot` strength and
   a `captured_object_id`) may raise `evidence_immutability` and `artifact_observation` for the
   evidence it backs, never caller-declared strength. `ledger_freshness` derives from real
   freshness facts (lag, staleness after a material change, redaction, unknown events), not from
   the presence of a gap, and every gap stays in `known_gaps`. Page-level and task-level coverage
   are either unified or named distinctly. *Implemented part:* status reports one definition —
every projection view's envelope `coverage` and `gaps` are the compact task coverage, so a results
or history view can no longer read `service_authenticated / current / 0 gaps` beside a partial
task. The closure fold, capture-raised strength and fact-derived freshness remain open on the
issue.

## Consequences

- An agent whose remaining conditions are all standing reads `ready_with_limitations`, requests the
  receipt and stops; the receipt still says `insufficient_coverage` and lists every gap.
- A new gap code cannot ship unclassified, and an unknown code keeps the task actionable rather than
  hiding behind a stop state.
- The unreleased status-result 1.4.0 schema is edited in place: the checklist fields are optional
  on the wire, so a result shaped by an earlier 0.3 build still validates, but when present they
  are complete, and this build emits them on every status success. Guidance, every host skill and
  the status tool description name the stop signal.
- A standing set differs per host; each host runbook records its own.

## Alternatives rejected

- **Suppress `coverage_gaps_declared` when every gap is structural.** It hides limitations instead
  of classifying them.
- **Tolerate structural gaps in the check verdict.** It would let `no_issue_detected` and
  `no_unresolved_deterministic_findings` describe work whose record was only consistent.
- **Guidance that tells agents to ignore `coverage_gaps_declared`.** The product must classify, not
  the prompt.
- **An open-ended heuristic.** A closed table can be reviewed per host and tested for completeness.
