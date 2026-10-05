# When to use Yoetz

Use Yoetz for material multi-step, delegated, resumable, or verification-heavy work. New session: read guidance/discover schemas, then `start` before substantive work. If startup fails, follow recovery first; if still blocked, ask for intro and guidance. Skip trivial questions or edits; never invent a ledger task. Cadence: `start` once, `publish_work` per material transition; `receipt` last. Never claim Yoetz is active until `start` returns. `yoetz://guidance/workflow.md`.

This small core is required before `start`; retain one triggered topic through its operation. Schemas are wire authority. Yoetz is a local ledger/checker. Yoetz is not an enforcement system; a check is not correctness.

# Guidance catalog

Do not call `resources/list` or `list_mcp_resources` to find Yoetz guidance. The five index URIs are the complete legacy catalog; a list failure is not a missing server or reason to read product source. If a body is empty, carries a truncation marker, or is nonempty but clipped, call `read_guidance` with the same URI; verify `structuredContent.text`, UTF-8 byte counts, offsets, markers, revision, continuation, and final digest. `page_size` is a UTF-8 byte string from 4 through 16,384 inclusive; default 4,096. Only if that result is also empty, use installed `references/<name>.md` or the focused topic's parent index. Do not call `start` on incomplete guidance.

Legacy triggers: startup `yoetz://guidance/agent-instructions.md`; resume/recovery/delegation/capacity `yoetz://guidance/workflow.md`; publication `yoetz://guidance/publication-policy.md`; check/receipt `yoetz://guidance/coverage-and-receipts.md`; schema/setup/consent/import `yoetz://guidance/request-templates.md`.

Focused topics: `startup.md`, `recovery.md`, `publication.md`, `review.md`, `receipt.md`, `delegation.md`, `consent.md`, `page-delivery.md`. Heading topics such as `yoetz://guidance/workflow.md#start-and-resume` are a closed catalog: use only anchors guidance names verbatim, never derived ones; an unknown anchor's rejection lists the valid ones.

# Start contract

Before `start`, discover its schema, use a fresh `req_` UUIDv4, and provide `mode`, `task_title`, `task_statement`, `requested_view`, `actor`, `client`, plus a held `session_id` or `workspace_ref` + `external_ref`. A child needs a handle or selector; otherwise it does no Yoetz work. If recovery still blocks startup, ask for intro and guidance; never invent a task.

Use two stages: `start` records intent, delivery, and constraints; after bounded exploration and before the first material edit, publish one `plan_revised` refinement mapping every instruction requirement to a testable obligation. Carry, supersede, or waive each earlier obligation with a visible reason. Example: `start` → explore → `plan_revised` → edit. Do not edit material files before refinement.

# Essential boundaries

Never publish hidden reasoning or chain-of-thought, full prompts, transcripts, conversation history, credentials, secrets, whole files/repositories, or broad unrelated source. A digest identifies bytes. Recover through schemas/guidance, never SQLite or source. On `retryable: false`, follow only the typed `continuation`; keep an unknown write's request id. If Yoetz is unavailable, say no live record or receipt exists.

# Review and closure

Select `semantic_required` when the user, effective policy, or named acceptance criterion requires independent review; use `semantic_if_configured` only when optional; reserve `deterministic_only` for local/structural or no-egress work and disclose the limit. Host authorization and Yoetz disclosure are separate. Read review/coverage before `check`.

Before material evidence or a completion claim, read `status`; cite IDs you published. Find native captures with `view=evidence` filter `strength=immutable_snapshot`, reusing only IDs a structural link ties to the claim. Omitted descriptions are a privacy setting, not missing evidence. Publish claim/evidence before the final check; answer findings, check, and inspect resolved state. Recheck only after repair/material record. After `insufficient_packet`, go to the receipt; never use `deterministic_only` merely to shorten closure.

Final prose and receipts are scope-first: lead with evidence/checks covered, what was not verified, frontier, review status/reason, and coverage limits; then give actionable-unresolved, unanswered, and resolved-history counts. Never headline “no findings”, “zero findings”, “clean”, or “verified”. Keep the final answer no stronger than the receipt's weakest coverage. Read delegation guidance before delegating.

# Multi-agent work

Before delegating, read the multi-agent sections of `yoetz://guidance/workflow.md` and `delegation.md`; a helper without a selector does no Yoetz work.

# Read more

Topics: `yoetz://guidance/agent-instructions.md`, `yoetz://guidance/workflow.md`, `yoetz://guidance/publication-policy.md`, `yoetz://guidance/coverage-and-receipts.md`, `yoetz://guidance/request-templates.md`; read with `read_guidance`, never memory.
