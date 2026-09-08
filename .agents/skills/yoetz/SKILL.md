---
name: yoetz
description: Record and check material multi-step, delegated, resumable, or verification-heavy work with Yoetz. Skip trivial questions and edits.
metadata:
  short-description: Local work ledger and bounded completion checks
---

# Yoetz for Codex

Yoetz is a local work ledger and deterministic checker of participant-published facts. It is not an
enforcement system, observer, authorship proof, transcript recorder, or orchestrator. A clean
check does not prove the underlying work correct.

## Load guidance for the current operation

Initialize `instructions` already include `agent-instructions.md`; re-read only when absent from
context. Other guidance is fetched on demand from MCP server `yoetz`. The five URIs below are the
complete catalog. Do not call `resources/list` or `list_mcp_resources` to discover them. A list
failure is not a missing server and is not a reason to read product source.

Use `resources/read` with the exact URI. If a `resources/read` result has no text, call `read_guidance`
with the same URI. If that also has no text, open the matching installed `references/<name>.md`.
Do not call `start` on an empty guidance body. Retain already-read guidance while it is in context.

| When | Resource |
| --- | --- |
| Safety floor missing from context | `yoetz://guidance/agent-instructions.md` |
| Before the first `start`, or resume without workflow context | `yoetz://guidance/workflow.md` |
| Before the first `publish_work` | `yoetz://guidance/publication-policy.md` |
| Before the first `check`; pending approvals, findings, receipts; Recovery on errors/outages | `yoetz://guidance/coverage-and-receipts.md` |
| Missing/rejected schema metadata; Setup and consent before setup/settings, credentials, vault operations or import; Recommendations before recommendation decisions | `yoetz://guidance/request-templates.md` |

Coverage and setup details are not prerequisites for an ordinary configured `start`. Author calls
from their current schemas, not memory. Consumer recovery uses `status view=operation`, never live
SQLite databases/catalog or product source. Assigned Yoetz development/debugging work may inspect
source and isolated tests, without granting live-storage or egress authority.

## Workflow

Tell the user briefly when using Yoetz; claim activation only after `start` returns. Start or attach
once with stable task identity, publish the plan and material transitions, then publish the
completion claim and evidence. Read `status` before closing; `check`, disposition findings with
`respond`, and request `receipt` last. A completion claim is an assertion, not a conclusion.
`respond` records a disposition; it does not clear the finding. `unresolved_findings_remain` stays
until a later qualifying check of the repaired record resolves the finding. Recheck after material
changes, never unchanged state. Publication is per material transition, never per file/tool/message.

Use `semantic_if_configured` for ordinary material claims. Use `semantic_required` for explicit
user requirements, effective policy, or a named acceptance criterion requiring independent semantic
judgment. Qualitative work alone does not make optional review mandatory. Disclose deterministic-only
coverage and terminal optional review gaps; never silently downgrade required semantic review.

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

If optional Yoetz is unavailable, continue authorized work and disclose missing ledger or receipt
coverage. Required review remains an unmet requirement. Separate completed implementation/tests
from that requirement, and local ledger writes from product-file edits. Final wording must be no
stronger than the receipt's weakest material coverage.

## Compatibility

Use `start`, `publish_work`, `check`, `respond`, `status`, `receipt`, and read-only `read_guidance`
with current schemas. `client` is exactly `{kind, version, integration}`; canonical integers stay
JSON strings. The adjacent `manifest.json` binds compatibility evidence; an empty profile set
advertises no tested harness version or hook.
