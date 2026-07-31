# ADR-016 — Human review for non-default actions

**Status:** Working decision; amended 2026-07-31 to require action-bound OS user presence before
console review.
**Implemented by:** `src/yoetz/service/elevated_bootstrap.py`,
`src/yoetz/cli/elevated.py`, `src/yoetz/cli/trusted_console.py`,
`src/yoetz/protocol/consent.py`, and `guidance/agent-instructions.md`.
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
| `secret_ingress` | vault initialize; provider credential set/rotate | Trusted review of exact digests | Trusted confidential ceremony only |
| `secret_reauth` | idle-relock weakening; some privacy widening | Trusted review plus owning reauthentication | Trusted confidential ceremony only |
| `review_only` | backup/restore/migrate execute; skill or harness configuration | Trusted review of exact plan digest | None |
| `privacy_widen` | privacy policy widen; disclosure approve | Trusted review of exact policy/pending digest | Owning ceremony when required |

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

4. **Verified presence before review.** The fixed `yoetz consent review` command takes no
   authority-bearing arguments. An independently authenticated, action-bound, one-use
   `UserPresencePort` attestation must succeed before the helper opens a foreground console or
   claims pending state. The current runtime has no production adapter and therefore returns
   `human_authority_unavailable`. Redirected, headless, pseudo-terminal, and same-UID automation
   cannot authorize mutation.

5. **No standing danger mode.** There is no session-wide bypass or broad grant. Easy review means
   short bounded text and one console decision, not reduced checks.

6. **Path safety is independent.** Consent cannot waive shared-temp, sync-folder, symlink,
   ownership, or broad-permission refusals.

7. **Secrets remain confined.** Implemented secret-ingress operations enter the existing YZH1/YZS1
   confidential service ceremony from the trusted console. Agent-facing surfaces never carry the
   bytes or directions for transporting them.

8. **Future operations.** Backup, restore, migration, skill mutation, harness registration,
   idle-relock weakening, and privacy widening remain catalogued but unimplemented until the
   owning mutation boundary consumes this single-shot review safely.

## Consequences

Agents may prepare and inspect a request, then must ask the human to run the fixed review command
locally. Until an approved OS-presence adapter is installed, that command fails closed and the
explicit manual passphrase-initialization path remains the available setup route. The agent cannot
derive or submit anything that authorizes the operation. Approval of one
operation does not grant another operation, another digest, another installation, or a later
session.

Native biometrics, natural-language host approval, approved-machine profiles, monthly grants,
E2EE service authority, and authority-bearing MCP elicitation require separate ADRs and threat
reviews.

## Alternatives considered

**Agent-held bearer grant.** Rejected because it delegates approval authority to the requesting
channel.

**Approve once per session.** Rejected because secret and irreversible operations require distinct
targets and digests.

**MCP secret entry.** Rejected because clients and model contexts must not observe secret bytes.
