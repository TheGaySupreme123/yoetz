# ADR-016 — Human review for non-default actions

**Status:** Working decision; amended 2026-07-31 to require action-bound OS user presence before
console review; amended 2026-08-09 for chat-user host-tool-approval authorize (issue #164).
**Implemented by:** `src/yoetz/service/elevated_bootstrap.py`,
`src/yoetz/cli/elevated.py`, `src/yoetz/cli/trusted_console.py`,
`src/yoetz/protocol/consent.py`, `src/yoetz/protocol/chat_user_authority.py`, and
`guidance/agent-instructions.md`.
**Relates to:** ADR-015 (bootstrap path), ADR-008 (vault/console), ADR-009
(egress/privacy).

## Context

Yoetz needs one understandable rule for actions outside the ordinary cooperative workflow while
keeping approval authority and secret entry out of agent-visible channels. Human review is
per-operation and digest-bound; host sandboxing and egress policy remain separate containment
controls.

## Risk classes

| Class | Examples | Human UX | Secret path |
|---|---|---|---|
| `default_safe` | MCP `start`/`publish_work`/`check`/`respond`/`status`/`receipt`; privacy tighten | No consent ceremony | None |
| `secret_ingress` | vault initialize; provider credential set/rotate | Trusted console review, or host-approved chat-user authorize for credential set/rotate only | Trusted confidential ceremony; chat authorize may supply one-shot credential after warning |
| `secret_reauth` | idle-relock weakening; some privacy widening | Trusted review plus owning reauthentication | Trusted confidential ceremony only |
| `review_only` | backup/restore/migrate execute; skill or harness configuration | Trusted review of exact plan digest | None |
| `privacy_widen` | repository privacy grant; privacy policy widen; disclosure approve | Trusted local privacy TUI, or host-approved chat-user authorize for exact recipe grant | Owning ceremony when required |

## Decisions

1. **Catalog authority.** `yoetz consent catalog` and `status` list every non-default operation,
   risk class, prepare hint, binding requirements, and `implemented` flag. Operations marked
   `implemented=false` cannot be prepared.

2. **Agent-safe contracts.** Catalog, pending projection, prepare result, review result, and status
   are frozen v2 contracts in `schemas/consent/`. They contain no authorization credential,
   reusable approval value, secret transport instruction, generated passphrase, or credential.

3. **One pending request.** One owner-only request with a 15-minute TTL may exist. The trusted
   reviewer atomically claims it. Every terminal decision and every post-claim failure is
   single-shot.

4. **Verified presence before console review.** The fixed `yoetz consent review` command takes no
   authority-bearing arguments. An independently authenticated, action-bound, one-use
   `UserPresencePort` attestation must succeed before the helper opens a foreground console or
   claims pending state. The current runtime has no production adapter and therefore returns
   `human_authority_unavailable`. Redirected, headless, pseudo-terminal, and same-UID automation
   cannot authorize mutation.

5. **Chat-user host-tool-approval authorize (issue #164).** When the user asks the agent for help
   with an exact prepared setup action, `yoetz consent authorize` may complete
   `provider_credential_set`, `provider_credential_rotate`, or `repository_privacy_grant` under a
   `yoetz.chat-user-attestation/1` envelope. Unattested chat assent remains forbidden. Catalog rules
   keep `never_over_chat_or_mcp` for unattested channels and add
   `chat_user_host_tool_approval_permitted` / `unattested_chat_assent_forbidden`.

6. **No standing danger mode.** There is no session-wide bypass or broad grant. Easy review means
   short bounded text and one console or host-approved authorize decision, not reduced checks.

7. **Path safety is independent.** Consent cannot waive shared-temp, sync-folder, symlink,
   ownership, or broad-permission refusals.

8. **Secrets remain confined.** Implemented secret-ingress operations enter the existing YZH1/YZS1
   confidential service ceremony. Chat-user authorize may deliver one-shot provider credential bytes
   through stdin into that ceremony after warning acknowledgement; results stay presence-only.
   Agent-facing projections never echo secret bytes. Vault initialization secrets remain
   helper-generated and console-only.

9. **Future operations.** Backup, restore, migration, skill mutation, harness registration,
   idle-relock weakening, and generic `privacy_policy_widen` remain catalogued but unimplemented
   until the owning mutation boundary consumes this single-shot review safely.
   `repository_privacy_grant` is the implemented exact-recipe privacy path for chat-user authorize.

## Consequences

Agents may prepare and inspect a request, then either guide the human to `yoetz consent review` /
`yoetz --privacy`, or — when the user asks for help on a capable first-party host — warn once and
complete the exact pending action via host-approved `yoetz consent authorize`. Until an approved
OS-presence adapter is installed, console review fails closed; chat-user authorize remains available
for the operations that advertise it. Approval of one operation does not grant another operation,
another digest, another installation, or a later session.

Native biometrics beyond host-tool-approval, approved-machine profiles, monthly grants,
E2EE service authority, and authority-bearing MCP tools (beyond the six ADR-011 tools) require
separate ADRs and threat reviews.

## Alternatives considered

**Agent-held bearer grant.** Rejected because it delegates approval authority to the requesting
channel.

**Approve once per session.** Rejected because secret and irreversible operations require distinct
targets and digests.

**Unattested MCP/chat secret entry.** Rejected. Attested one-shot stdin after host tool approval and
warning acknowledgement is the narrow #164 exception; ordinary MCP tool arguments still must not
carry secrets.
