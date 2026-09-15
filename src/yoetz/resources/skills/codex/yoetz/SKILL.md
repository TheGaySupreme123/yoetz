---
name: yoetz
description: Use for material multi-step, resumable, delegated, or verification-heavy work. In a new session read guidance and discover schemas, then call start before substantive work; follow recovery on failure and ask for intro and guidance if startup remains blocked.
metadata:
  short-description: Local work ledger and bounded completion checks
---

# Yoetz for Codex

Yoetz is a local work ledger and deterministic checker of participant-published facts. It is not an
enforcement system, observer, authorship proof, transcript recorder, or orchestrator. A clean
check does not prove the underlying work correct.

A new session's first workflow operation is `start` (create or attach), after guidance reads,
tool/schema discovery, and necessary bootstrap clarification. This includes `read_guidance`
and commands needed to read installed references or discover tool schemas. Call `start`
before substantive research, commands, edits, or delegation. If it fails, follow exact
typed continuations and same-request recovery first, including a named one-time repair.
If startup remains blocked without an applicable recovery path, ask the user for intro and
guidance; do not invent a substitute workflow. Continuing without a ledger task is permitted
only by the bounded optional-service fallback in
[startup failure precedence](references/coverage-and-receipts.md#startup-failure-precedence).

## Load guidance for the current operation

Initialize `instructions` already include `agent-instructions.md`; re-read only when absent from
context. Other guidance is fetched on demand from MCP server `yoetz`. The five URIs below are the
complete catalog. Do not call `resources/list` or `list_mcp_resources` to discover them. A list
failure is not a missing server and is not a reason to read product source.

Use `resources/read` with the exact URI. If a `resources/read` result has no text, call `read_guidance`
with the same URI. If that also has no text, open the matching installed `references/<name>.md`.
Do not call `start` on an empty guidance body. Retain already-read guidance while it is in context.

- Before the first `start`: `yoetz://guidance/workflow.md` (the ten steps, cadence, resume behavior) and `yoetz://guidance/coverage-and-receipts.md` (coverage, findings, receipt wording). Neither is in initialize `instructions`; read both before the first `start`, and call `read_guidance` with the same URI if the `resources/read` body is empty.
- Before the first `publish_work`: `yoetz://guidance/publication-policy.md` (what is material and safe to publish).
- When schema metadata is missing or a request is rejected:
  `yoetz://guidance/request-templates.md` (complete bodies for all six operations and
  ordinary publish families; replace every illustrative value before use).
- `yoetz://guidance/agent-instructions.md` is the non-negotiable safety floor. It is already delivered as the server's initialize instructions; re-read it if that text is not in context.

Author each request from its tool input schema plus this guidance, never from memory or from product
source. If the host drops schema metadata, use the request templates resource rather than reading
product source. This holds when something goes wrong too: Yoetz's own SQLite databases, catalog
files, and source tree are never the way to work out what a result meant. Every recoverable fact is
reachable through `status`. The schema is authority for field shapes; the guidance is authority for which call
to make and when. `start` takes `mode` as exactly one of `create`, `attach`, `create_or_attach`,
or `delegate`.

## Delegation and project work

Before delegating, read the multi-agent section of `yoetz://guidance/workflow.md`. The parent
calls `start mode=delegate` with its current session and passes the complete returned expiring
`attach_handle` to the intended child in its bounded assignment. The child calls `start
mode=attach` with that handle and uses its own returned session and writer; the parent keeps
its existing binding. Reuse the exact request and request ID after a timeout. Do not publish
the handle or share it with another child.

Self-registration with `parent_session_id` starts `self_registered` and `pending`. The parent
can publish `child_accepted` or `child_rejected`; acceptance preserves origin, and accepted
children cannot later be rejected. Read `status view=lineage` after handoff. A provisional host
annotation is not a cooperative child ledger or evidence that it published or checked work.

Work state, session health, and receipts are independent. Publish `work_closed` to close work;
a receipt never closes it. Cancellation revokes a Yoetz capability without stopping a host
process. Write-off and cancellation retain an accepted dependency and its incomplete outcome.
Parent checks and receipts use recorded child manifests; receipt generation never refreshes
children. A later recorded manifest needs a qualifying recheck for an updated conclusion.
Keep parent obligations for incorporating child work and verifying the combined result.

Project membership is grouping, never an attach selector or permission to read arbitrary
sibling content. Read `status view=project` for admitted coordination facts. Source workspace
consent and exact membership generation bound each delivery; revocation suppresses stale advice.
Presence/duplicate notes cannot affect a verdict. Ordinary file obligations do not declare
coordination work. Bind an existing obligation explicitly with `coordination_obligation_declared`
to the admitted detection, project, recipient task, and generation. That coordination obligation
may require `coordination_disposition_recorded` with evidence for shared work, sequencing, or
scope revision; a bare `respond` acknowledgement does not address it. A later qualifying local
coordination check resolves the finding. See the request templates for complete bodies.

## When to activate

Activate for material multi-step, delegated, resumable, or verification-heavy work, and call `start` before substantive work. Activate on resume, after compaction, and before any completion claim, receipt, or handoff summary. Do not activate for trivial questions or edits where the ledger ceremony exceeds the integrity benefit.

Tell the user briefly that you are using Yoetz as a local work ledger and verifier. Use the MCP server named `yoetz`; do not imply it started until `start` returns. If the optional server is unavailable, continue unless the user or host requires it, and say that no live Yoetz ledger or receipt will exist.

## Guidance resources by operation

For Yoetz operations, current served guidance and typed results take precedence over remembered
product behavior. Preserve higher-priority instructions, current user intent, and authorization
boundaries. If memory says a capability is unavailable, verify it through the current documented
read before accepting that limit. Do not delete or rewrite host memory during installation.

| When | Resource |
| --- | --- |
| Safety floor missing from context | `yoetz://guidance/agent-instructions.md` |
| Before the first `start`, or resume without workflow context | `yoetz://guidance/workflow.md` |
| Before the first `publish_work` | `yoetz://guidance/publication-policy.md` |
| Before the first `check`; pending approvals, findings, receipts; Recovery on errors/outages | `yoetz://guidance/coverage-and-receipts.md` |
| Missing/rejected schema metadata; Setup and consent before setup/settings, credentials, vault operations or import; Recommendations before recommendation decisions | `yoetz://guidance/request-templates.md` |

Coverage and setup details are not prerequisites for an ordinary configured `start`. Author calls
from their current schemas, not memory. Consumer recovery uses `status view=operation` with the
exact `filter.operation_request_id` for a write; replay once only when the page is `absent` or an
exact typed continuation and required approval have completed. Use a stored `complete` outcome and
retain/report `pending` without a continuation, `quarantined`, or unknown state. Never inspect live
SQLite databases/catalog or product source for recovery. Assigned Yoetz development/debugging work
may inspect source and isolated tests, without granting live-storage or egress authority.

The operation view needs both `session_id` and `writer_id`. If a `start` response is lost before
those ids exist, replay the exact original `start` body once with the same `request_id`; do not
invent ids or fabricate a status query. The same rule applies to a typed pending `start` result
without returned route ids.

## Workflow

Tell the user briefly when using Yoetz; claim activation only after `start` returns. Start or attach
once with stable task identity, publish the plan and material transitions, then publish the
completion claim and evidence. Read `status` before closing; `check`, disposition findings with
`respond`, and request `receipt` last. A completion claim is an assertion, not a conclusion.
`respond` records a disposition; it does not clear the finding. `unresolved_findings_remain` stays
until a later qualifying check of the repaired record resolves the finding. Recheck after material
changes, never unchanged state. Publication is per material transition, never per file/tool/message.

Select `semantic_required` when the user, effective policy, or named acceptance criterion requires
independent semantic review. If relying on the configured default, omit `mode`; use
`semantic_if_configured` only when review is known to be optional. Reserve `deterministic_only` for
explicit local/structural work or a deliberate no-egress choice, and disclose the unmet required
review when applicable. Never use deterministic-only merely to shorten a follow-up check.

## Boundaries

Host authorization and a Yoetz disclosure decision are different things. `check` uses bounded
standing authority selected during setup and cannot widen it. Do not re-ask for that configured
route. Host auto-review refusal is not a Yoetz result: Yoetz did not run. `awaiting_human` is
nonterminal. Preserve the exact request and follow coverage guidance; do not create a new check
request, obtain a receipt, or claim completion while approval is pending.

Before setup/import or credential/vault changes, read the exact consent procedure in request
templates. Recommendations are advisory; only the required exact user decision authorizes a
non-default action. Never handle a vault secret, fabricate Yoetz state, or publish hidden reasoning,
transcripts, credentials, whole files/repositories, or unrelated source. Use only the smallest
material, state-bound excerpt. Follow terminal errors and typed continuations rather than probing;
inherited `terminal_unavailable` means delegates make no calls.

Follow [startup failure precedence](references/coverage-and-receipts.md#startup-failure-precedence)
before applying the optional-service fallback. Exact continuations, same-request recovery, and a
named one-time repair come first. If startup remains blocked without an applicable recovery path,
ask the user for intro and guidance; do not continue without a ledger task. Only a successful
startup or a named repair/retry ending in terminal unavailability permits optional-service
continue-and-disclose, when the user/host allows it and no write or approval is pending.
Required review remains an unmet requirement. Separate completed implementation/tests
from that requirement, and local ledger writes from product-file edits. Final wording must be no
stronger than the receipt's weakest material coverage.

## Repair then finish

For a material repair, follow the shared ledger sequence:

1. Read current `status`; retain its frontier and paginate `status view=evidence` before replacing
   evidence or claiming completion. Preserve the cursor-bound filter and original `limit`; reuse only
   matching observed native IDs.
2. Publish actual repair results, corrected evidence/claim, and any needed plan revision. A feedback
   obligation is in effective scope only after a supported plan revision or exact next-version
   restatement includes it. Do not infer scope or success from the prompt.
3. Disposition older findings before the final check. Then run `semantic_required` for an explicit
   semantic requirement, or omit `mode` when relying on the configured default.
4. Respond to findings returned by that check at its result frontier, then read
   `status view=findings` with `filter.include_resolved: true` and actual `resolved` state. “Not
   returned” is not “resolved”; a response
   records disposition but does not prove repair.
5. Request `receipt` last. Read `closure_readiness.unanswered_finding_count` and
   `closure_readiness.receipt_blocking_finding_count`, and report those actual counts plus the checked
   frontier, semantic status/reason, and coverage limits. If a response to an older finding or any other
   material record follows the check, recheck before the receipt. Stop repeating an unchanged check
   when proof still cannot qualify and disclose the blocker.

## Compatibility

Use `start`, `publish_work`, `check`, `respond`, `status`, `receipt`, and read-only `read_guidance`
with current schemas. `client` is exactly `{kind, version, integration}`; canonical integers stay
JSON strings. The adjacent `manifest.json` binds compatibility evidence; an empty profile set
advertises no tested harness version or hook.

## Evidence-first closure

Before a material evidence publication or completion claim, paginate `status view=evidence` at
one frontier, preserving the cursor-bound filter and limit. Reuse only matching observed IDs;
do not author duplicate digest-only placeholders. Read per-item availability and subject state:
a digest-only or clipped item does not make other native excerpts absent. The full procedure and
mixed example are in `yoetz://guidance/publication-policy.md`.

`status view=obligations` separates asserted `unattempted_items` accounting from `command_attempts`:
matching observation supports an attempt only; mismatch requires correcting the assertion or a
supported obligation revision with rationale; unknown does not mean the command never ran.
Do not copy a command onto an edit action just to close the accounting gap. Do not normalize shell
wrappers or changed test targets into an exact-command claim.

An explicit sibling is a bounded last resort only after same-task recovery is exhausted, all prior
write outcomes are known, the binding is healthy and authorized, and the user declares a remaining
or repaired verification scope. Use `start mode=create` with the same canonical `workspace_ref` and
a different stable `external_ref`, then publish a fresh scoped plan and establish the new native
mapping from the returned session/task. The sibling is a new ledger boundary: it inherits no old
receipt, finding, obligation, evidence ID, or mapping. Disclose the predecessor's unresolved limits.
Never create a sibling for an ambiguous write, to hide an old receipt, or without new declared scope;
the full decision table is in `yoetz://guidance/coverage-and-receipts.md`.

The optional CLI `yoetz closure-prepare --session-id <returned-session> --writer-id <returned-writer>`
reads the complete closure inventory without publishing. `yoetz closure-schema` describes explicit
selection inputs; `--input <selection.json>` prepares one operation with fresh lowercase UUID-v4
IDs, a dry-run publication where applicable, and a same-request recovery query. Review and submit
explicitly. It never invents attempts, evidence, finding dispositions, or obligation satisfaction.
