---
name: yoetz
description: Use for material multi-step, delegated, resumable, or verification-heavy work; call start before substantive work. Skip trivial questions or edits where the ceremony exceeds the integrity benefit.
metadata:
  short-description: Durable task ledger and completion checks for material multi-step or delegated work — call start before substantive work
---

# Yoetz for Codex

## When to activate

Activate for material multi-step, delegated, resumable, or verification-heavy work, and call `start` before substantive work. Do not activate for trivial questions or edits where the ledger ceremony exceeds the integrity benefit.

## Startup and availability

Tell the user briefly that you are using Yoetz as a local work ledger and verifier. Use the MCP server named `yoetz`; do not imply it started until `start` returns. If the optional server is unavailable, continue unless the user or host requires it, and say that no live Yoetz ledger or receipt will exist.

## Compatibility

Use only the six registered Yoetz MCP tools and their current schemas. Every tool request's `client` is exactly `{kind, version, integration}`; do not send `client.id` or any other client field. Compatibility is exact and evidence-bound in the adjacent `manifest.json`; an empty profile set means this Yoetz skill advertises no tested Codex version or hook.

## Shared guidance

Read these with the MCP `resources/read` request for the exact URI. They are served by the `yoetz`
server itself, so they resolve without any repository checkout.

- Read `yoetz://guidance/agent-instructions.md` for the non-negotiable safety floor.
- Follow `yoetz://guidance/workflow.md` (section `the-ten-steps`) for start, publication, re-grounding, check, response, and receipt.
- Consult `yoetz://guidance/publication-policy.md` (section `materiality-checklist`) before publishing source or a large inventory.
- Consult `yoetz://guidance/coverage-and-receipts.md` (section `receipt-fields-and-wording`) before a completion claim.

## Check mode and receipts

- Prefer `semantic_if_configured` for material implementation/review claims; use `semantic_required` when qualitative correctness is part of completion; use `deterministic_only` only for structural/no-egress checks and disclose that limitation. Omitting `mode` resolves via policy.
- Prefer receipt format `markdown` or `text`. Default policy can return usable `json` receipts; if `json` is blocked under a strict policy, switch format rather than retrying forever.
