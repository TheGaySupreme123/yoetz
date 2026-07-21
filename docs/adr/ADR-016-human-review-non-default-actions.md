# ADR-016 — Human review for non-default actions (easy consent, hard secrets)

**Status:** Working decision — expands ADR-015 from bootstrap-only into a risk-class consent
framework for every *non-default* Yoetz action agents might request.
**Owning public specs:** `specs/src/yoetz/service/elevated_bootstrap.md` (consent registry),
`specs/src/yoetz/cli/elevated.md`, `guidance/agent-instructions.md`, ADR-008/009 amendments,
`OPEN_QUESTIONS.md`.
**Relates to:** ADR-015 (bootstrap path), ADR-008 (vault/TTY), ADR-009 (egress/privacy).

## Context

ADR-015 unblocked cloud-agent vault initialize and provider credential set. Users still need a
clear rule for everything else that is “not usual”: privacy widening, idle-relock weakening,
credential rotate, portable recovery, backup/restore/migrate, skill/MCP installs, and any path
that could move secrets into agent context.

Peer tools separate **containment** (sandbox/VM/egress) from **approval** (when to ask a human).
Yoetz must do the same without a standing `--yolo`, without putting secrets in chat/MCP, and
without making ordinary cooperative workflow painful.

## Goals

1. **Easy for humans.** One status surface, short danger text, one confirmation phrase, one exact
   approve command. Prefer phrase-only when no secret bytes are required.
2. **Safe for secrets.** Passphrases, API keys, recovery material, and reauth secrets never travel
   over MCP, chat, argv, environment, config, or transcripts. Inherited FDs only after phrase
   consent, and only for `secret_ingress` / `secret_reauth` lanes.
3. **Safe for durable harm.** Irreversible storage/route/config mutations require digest-bound
   phrase consent even when no secret is involved. Yoetz has no hardware-destructive surface; the
   analogous class is irreversible local state change (backup/restore/migrate, skill replace,
   harness MCP registration).
4. **Default stays fast.** Ordinary MCP workflow tools and privacy *tighten* remain ungated.
   Agents must still refuse to publish secrets (existing guidance).

## Risk classes

| Class | Examples | Human UX | Secret path |
|---|---|---|---|
| `default_safe` | MCP `start`/`publish_work`/`check`/`respond`/`status`/`receipt`; privacy tighten | No consent ceremony | None; publication policy still forbids secrets |
| `secret_ingress` | `vault_initialize`, `provider_credential_set`, `provider_credential_rotate`, portable recovery | Phrase + danger text | Inherited FDs after approve |
| `secret_reauth` | idle-relock disable/change, some privacy widen decisions | Phrase + danger text | Reauth FD after approve |
| `phrase_only` | backup/restore/migrate execute, skill install/replace/remove, harness MCP register | Phrase + plan/preview digest | None |
| `privacy_widen` | privacy policy widen, disclosure approve | Phrase + exact pending/policy digest | Reauth when the ceremony requires it |

## Decisions

1. **Consent catalog is authoritative.** `yoetz consent catalog` (alias
   `yoetz elevated-bootstrap catalog`) and `status` list every non-default operation, its risk
   class, whether FDs are required, the prepare template, and an `implemented` flag. Agents must
   consult the catalog before attempting a non-default action. Ops with `implemented=false` must
   not be prepared; they are reserved until durable grant consumption exists at the owning
   mutation boundary.

2. **One pending consent at a time.** Same singleton + TTL model as ADR-015. Approve consumes
   pending immediately on accept (single-shot); ceremony failure requires a new prepare.

3. **No standing danger mode.** Rejected again: a session-wide bypass is how secrets and
   irreversible actions leak past review.

4. **Easy path ≠ weak path.** “Easy” means fewer steps and clearer copy, not fewer checks. Phrase
   confirmation binds the exact `danger_digest` / `target_digest`. `approve_command` uses a
   `<confirmation_phrase>` placeholder; agents must not auto-fill the live phrase from status.

5. **Ordinary TTY remains preferred** when a controlling user-owned `/dev/tty` exists. Elevated
   consent is for cloud/no-TTY orchestration and for surfacing non-default risk to agents.

6. **Hardware safety.** Yoetz does not expose device firmware, disk wipe, or kernel privilege
   ops. Path-safety refusals (shared temp, sync folders, symlinks, broad perms) stay non-overridable
   without a separate reviewed exception — consent cannot waive path safety.

7. **Inherited FDs stay narrow.** Secret FD ingress applies only to catalogued `secret_ingress` /
   `secret_reauth` ops after digest-bound phrase consent (ADR-008 amendment). Phrase-only ops never
   take secret FDs and are not `implemented` until execute paths consume a durable grant.

## Consequences

Agents get a single, teachable rule: if it is not default-safe, consult the catalog; if
`implemented`, prepare consent, show the human the danger text, wait for the phrase, then approve
with FDs only when the catalog says so. Humans get short reviewable prompts without pasting
secrets. ADR-015 bootstrap ops (plus credential rotate) are the implemented secret-ingress lane;
phrase-only irreversible ops remain catalogued but `implemented=false` until grant consumption
lands.

## Alternatives considered

**MCP elicitation for secrets.** Rejected: models/clients must not observe secret entry.

**Agent-held bearer grant reusable across ops.** Rejected: becomes standing elevation.

**Ask once per session for “all elevated.”** Rejected: too coarse; irreversible and secret ops need
per-operation digests.
