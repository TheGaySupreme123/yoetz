# tests/conformance/privacy/test_never_send_scope_and_channels.py — non-overridable privacy fences

**Wave:** C/E/F | **ADRs:** ADR-004, ADR-006, ADR-009 | **Imports (spec-tree):**
privacy protocol/schemas, PRIV-005..008, logging/diagnostics/MCP render specs | **Imported by:**
conformance, packaging canary and public-claim gates

## Purpose

Prove never-send, local-disclosure, scope, policy-widening and independent-channel boundaries are
non-bypassable under the most permissive public configuration.

## Public surface

Exact tests consume `PRIV-005`, `PRIV-006`, `PRIV-007`, and `PRIV-008` across memory/durable
backends, CLI/MCP views, trusted control, the approved external LLM fake, non-LLM unsupported-
capability sentinels, and canary/network-attempt collectors.

## Behavior

For every `ForbiddenDataKind`, assert absence from outbound/local-model/agent-context bytes, MCP
structured/text output, previews except bounded trusted-human selected display, receipts, logs,
errors, traces, stderr, crash and telemetry. Test tightening immediate, widening human-only, and
forged agent/MCP/provider/import authority denied. Test scope ancestry/intersection and every
cross-scope reuse. Under a true global ceiling, the LLM-only row makes exactly its approved attempt
while the other four make none. For each non-LLM row, use `local_only` and prove v0.1 rejects the
enabling transition as `channel_unavailable` with prior policy unchanged. A forced/imported enabled
row remains fenced at use time and emits only a taskless pre-dispatch decision receipt with
outcome/reason `channel_unavailable/channel_unavailable`; it has no authorization/dispatch/commitment/attempt-body
fields, uses structural `PreDispatchAuditDecision` with no content object, and makes zero DNS/socket
attempts. Never-send/classification blocks use the same structural branch and retain no denied
bytes. A false ceiling with any proposed enabled channel is
rejected before construction; a true ceiling with all channels disabled makes zero attempts.

Network instrumentation allows only exact release-cell service/confidential, optional local-model,
and OS credential/user-presence/session-lifecycle security IPC, denies arbitrary AF_UNIX/D-Bus/
proxies plus AF_INET/AF_INET6,
DNS and redirects, and attributes every attempted call to one exact channel. Evidence names the
Yoetz-owned process/startup-through-ready boundary and excludes external OS/model agents. Agent-context canaries
are checked separately because it is a local disclosure sink before MCP rendering, not an egress
channel.

Repeat the never-send and scope matrix for `structural`, `goal_aware`, `assisted`, `expanded`, and
`custom`. A broader context selector may choose more already recorded candidates but cannot make a
forbidden, unrelated, out-of-scope, or unclassified excerpt representable as approved content. An
omission retains only its typed subject/category/reason; no withheld plaintext appears in the
manifest or model prompt.

## Errors and edge cases

Include encoding splits, nested values, misleading file types, wildcard policy, ambiguous scope,
stale generation, redirects, shared SDK pools, crash-after-write and scanner uncertainty. Any
inconclusive fence is a failure, not skip or sanitized pass.
Initial audit-reservation failure is asserted separately as bounded no-receipt `audit_failed` with
zero prompt/authorization/network activity; tests never fabricate a receipt for it.

## Invariants

1. Never-send has zero override paths.
2. Agent/MCP output cannot become an unclassified side channel.
3. Authorization is exact-scope and non-transferable.
4. The global ceiling grants nothing, and consent for one egress channel implies nothing about
   another; privacy profiles govern only LLM inference.
5. Receipts remain structural and canary-free.
6. v0.1 non-LLM channel rows have no adapters or attempt receipts; capability installation later
   requires a fresh exact local-human transition and cannot consume dormant intent.
7. Terminal receipts never use pending `awaiting_human|approved|receipt_pending` or removed
   `dispatched` outcomes.
8. No review-context profile or recipe weakens never-send, scope, classification, or byte caps.

## Tests

Run `uv run --locked pytest tests/conformance/privacy/test_never_send_scope_and_channels.py -q` and
retain only normalized structural test evidence.

## Open questions

None.
