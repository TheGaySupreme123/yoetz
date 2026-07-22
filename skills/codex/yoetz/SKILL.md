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

Use only the six registered Yoetz MCP tools and their current schemas. Every tool request's `client` is exactly `{kind, version, integration}`; do not send `client.id` or any other client field. Compatibility is exact and evidence-bound in the adjacent `manifest.json`; an empty profile set means this [REDACTED] skill advertises no tested Codex version or hook.

## Shared guidance

- Read [agent instructions](references/agent-instructions.md) for the non-negotiable safety floor.
- Follow the [cooperative workflow](references/workflow.md#the-ten-steps) for start, publication, re-grounding, check, response, and receipt.
- Consult [publication policy](references/publication-policy.md#materiality-checklist) before publishing source or a large inventory.
- Consult [coverage and receipts](references/coverage-and-receipts.md#receipt-fields-and-wording) before a completion claim.
