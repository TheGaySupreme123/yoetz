---
name: yoetz
description: Use Yoetz for material multi-step agent work that benefits from a durable obligation, evidence, and completion record; skip trivial edits and ordinary questions.
metadata:
  short-description: Local work ledger and deterministic checker
---

# Yoetz for Codex

## When to activate

Activate for non-trivial work with multiple outcomes, delegation, meaningful verification, long duration or resume risk, or a material completion claim. Do not activate for translation, explanation, ordinary questions, or one-line edits where the ledger ceremony exceeds the integrity benefit.

## Startup and availability

Tell the user briefly that you are using Yoetz as a local work ledger and verifier. Use the MCP server named `yoetz`; do not imply it started until `start` returns. If the optional server is unavailable, continue unless the user or host requires it, and say that no live Yoetz ledger or receipt will exist.

## Compatibility

Use only the six registered Yoetz MCP tools and their current schemas. Compatibility is exact and evidence-bound in the adjacent `manifest.json`; an empty profile set means this development skill advertises no tested Codex version or hook.

## Shared guidance

- Read [agent instructions](references/agent-instructions.md) for the non-negotiable safety floor.
- Follow the [cooperative workflow](references/workflow.md#the-ten-steps) for start, publication, re-grounding, check, response, and receipt.
- Consult [publication policy](references/publication-policy.md#materiality-checklist) before publishing source or a large inventory.
- Consult [coverage and receipts](references/coverage-and-receipts.md#receipt-fields-and-wording) before a completion claim.
