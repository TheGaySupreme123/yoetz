# ADR-016 — Human review for non-default actions

**Status:** Working decision; amended 2026-07-31 to require action-bound OS user presence before
console review; amended 2026-08-09 for agent-attested current-chat authorize (issue #164); amended
2026-08-18 for atomic concurrent review claims (issue #344); amended 2026-08-21 to assign portable
plugin artifact and host-activation mutation to `review_only` (issue #149, ADR-023; implementation
owned by #150).
**Implemented by:** `src/yoetz/service/elevated_bootstrap.py`,
`src/yoetz/cli/elevated.py`, `src/yoetz/cli/trusted_console.py`,
`src/yoetz/protocol/consent.py`, `src/yoetz/protocol/chat_user_authority.py`, and
`guidance/agent-instructions.md`.
**Relates to:** ADR-015 (bootstrap path), ADR-008 (vault/console), ADR-009
(egress/privacy).

## Context

Yoetz needs one understandable rule for actions outside the ordinary cooperative workflow while
keeping the stronger trusted-console authority and secret entry boundaries clear. Human review is
per-operation and digest-bound; host sandboxing and egress policy remain separate containment
controls. The optional agent-chat lane deliberately delegates authority to an agent assertion and
documents that Yoetz cannot independently authenticate its chat provenance.

## Risk classes

| Class | Examples | Human UX | Secret path |
|---|---|---|---|
| `default_safe` | MCP `start`/`publish_work`/`check`/`respond`/`status`/`receipt`; privacy tighten | No consent ceremony | None |
| `secret_ingress` | vault initialize; provider credential set/rotate | Trusted console review, or agent-attested current-chat authorize for credential set/rotate only | Trusted confidential ceremony; chat authorize may supply one-shot credential after warning |
| `secret_reauth` | idle-relock weakening; some privacy widening | Trusted review plus owning reauthentication | Trusted confidential ceremony only |
| `review_only` | backup/restore/migrate execute; skill or harness configuration | Trusted review of exact plan digest | None |
| `privacy_widen` | repository privacy grant; privacy policy widen; disclosure approve | Trusted local privacy TUI, or agent-attested current-chat authorize for exact recipe grant | Owning ceremony when required |

## Decisions

1. **Catalog authority.** `yoetz consent catalog` and `status` list every non-default operation,
   risk class, prepare hint, binding requirements, and `implemented` flag. Operations marked
   `implemented=false` cannot be prepared.

2. **Agent-safe contracts.** Catalog, pending projection, prepare result, review result, and status
   publish current v3 contracts in `schemas/consent/`; the frozen v2 bytes remain shipped for
   compatibility. They contain no reusable approval value, generated passphrase, or credential.
   The v3 pending projection includes only a bounded recipe and an authorize command for operations
   that actually support agent-chat authorization.

3. **One pending request.** One owner-only request with a 15-minute TTL may exist. The trusted
   reviewer atomically claims it by creating a no-clobber hard-link review marker. Marker creation
   is the ownership transition; only the winner cleans up the public pending name, while concurrent
   losers are read-only. Every terminal decision and every post-claim failure is single-shot.

4. **Verified presence before console review.** The fixed `yoetz consent review` command takes no
   authority-bearing arguments. An independently authenticated, action-bound, one-use
   `UserPresencePort` attestation must succeed before the helper opens a foreground console or
   claims pending state. The current runtime has no production adapter and therefore returns
   `human_authority_unavailable`. Redirected, headless, pseudo-terminal, and same-UID automation
   cannot authorize mutation.

5. **Agent-attested current-chat authorize (issue #164).** When the user explicitly asks the agent
   in the current chat for help
   with an exact prepared setup action, `yoetz consent authorize` may complete
   `provider_credential_set`, `provider_credential_rotate`, or `repository_privacy_grant` under a
   `yoetz.chat-user-attestation/1` envelope. The envelope is an agent assertion, not independent
   proof; a compromised agent can forge it. Catalog rules expose that limitation directly through
   `agent_attestation_is_independent_proof=false` and
   `compromised_agent_can_forge_attestation=true`.

6. **No standing danger mode.** There is no session-wide bypass or broad grant. Easy review means
   short bounded text and one console decision or exact agent assertion, not reduced target checks.

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

   **Amendment (ADR-023, 2026-08-21, issue #149):** the skill/harness-configuration slice of
   `review_only` is the accepted owning boundary for standalone portable plugin artifact
   install/remove and host-activation apply. #150 must implement the operation-specific catalog,
   prepare, and consume path before any such mutation exists; this amendment does not mark the
   current generic skill/harness operations implemented. Once implemented, the path consumes this
   class's single-shot trusted review of the exact plan digest under all existing rules — one
   pending request, atomic claim, verified presence before console review. An ordinary TTY
   confirmation, `--accept`, a same-UID process, or a host marketplace prompt is not and cannot
   silently become `UserPresencePort` authority, so
   until a production presence adapter exists these standalone paths fail closed with
   `human_authority_unavailable` and remain render/preview/status-only in practice. The
   agent-attested current-chat authorize lane (decision 5) is deliberately **not** extended to
   them. The ADR-012 setup wizard's already-authorized digest-bound composition is a separate,
   unchanged authority and does not route through this class.

   Issue #150 implements the artifact half as the exact `plugin_artifact_apply` operation. Its
   target digest is the complete portable artifact preview digest, its risk class is
   `review_only`, and agent-chat authorization is disabled. Preparation and single-shot
   consumption exist, but the packaged runtime still has no production action-bound
   `UserPresencePort`, so the standalone lane fails closed before mutation with
   `human_authority_unavailable`. Generic `skill_install`, host activation apply, and harness MCP
   registration remain catalogued but unimplemented.

## Consequences

Agents may prepare and inspect a request, then either guide the human to `yoetz consent review` /
`yoetz --privacy`, or — when the user asks for help on a capable first-party host — warn once and
complete the exact pending action via agent-attested `yoetz consent authorize`. Until an approved
OS-presence adapter is installed, console review fails closed; chat-user authorize remains available
for the operations that advertise it. Approval of one operation does not grant another operation,
another digest, another installation, or a later session.

Native biometrics, host-verified chat provenance, approved-machine profiles, monthly grants,
E2EE service authority, and authority-bearing MCP tools (beyond the six ADR-011 tools) require
separate ADRs and threat reviews.

## Alternatives considered

**Agent-held bearer grant.** Rejected because it is reusable. The accepted agent assertion is
deliberately delegated but exact, expiring, and single-use.

**Approve once per session.** Rejected because secret and irreversible operations require distinct
targets and digests.

**Ordinary MCP/chat secret entry.** Rejected. Agent-attested one-shot stdin after warning
acknowledgement is the narrow #164 exception; ordinary MCP tool arguments still must not carry
secrets.
