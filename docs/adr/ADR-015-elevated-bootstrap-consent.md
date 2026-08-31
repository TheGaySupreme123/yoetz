# ADR-015 — OS-presence-gated elevated bootstrap consent

**Status:** Amended 2026-07-31; amended 2026-08-09 for agent-attested chat authorize
(issue #164); amended 2026-08-18 for atomic concurrent review claims (issue #344); amended
2026-08-25 to name Codex marketplace/MCP removal as outside this OS-presence lane (issue #419);
amended 2026-08-30 for exact bounded Codex JSONL import publication (issue #301);
amended 2026-08-31 for helper-generated agent-authorized vault initialization and masked terminal
input (issues #490 and #491).
Superseded in scope by ADR-016 for the general non-default consent catalog. Console `yoetz consent
review` remains fail-closed until a verified OS-presence adapter is installed; allowlisted
first-party agents may use `yoetz consent authorize` for exact prepared operations that advertise
delegated chat authority.
**Implemented by:** `src/yoetz/service/elevated_bootstrap.py`,
`src/yoetz/cli/elevated.py`, `src/yoetz/cli/trusted_console.py`,
`src/yoetz/protocol/consent.py`, and `src/yoetz/protocol/chat_user_authority.py`.
**Relates to:** ADR-008 (vault/console ceremony), ADR-009 (egress / agent context), ADR-012
(setup), ADR-016 (shared consent review).

## Context

An agent may prepare and inspect a non-default operation. The trusted-console path must keep its
authority independent of argv, environment, stdin, MCP, agent JSON, and caller booleans. Issue
#164 also deliberately accepts a weaker opt-in path: an allowlisted agent may assert that the user
explicitly instructed the exact action in the current chat. That assertion is bounded and
single-use, but Yoetz cannot independently authenticate its chat provenance. Vault initialization
still must not accept an agent-selected passphrase; an authorized agent may ask the Yoetz helper to
generate and store one locally without any secret-bearing agent channel.

## Decisions

1. **Implemented bootstrap operations.** `vault_initialize`, `vault_passphrase_rotate`,
   `provider_credential_set`, `provider_credential_rotate`, `repository_privacy_grant`, and
   `import_publication` use this lane. It is not a
   vault-unlock API, recovery API, or standing elevation mode.

   **Amendment (2026-08-25, issue #419).** Codex marketplace/plugin removal
   (`yoetz integrate codex plugin remove`) and external MCP unregistration
   (`yoetz integrate codex mcp remove`) are not ADR-015 OS-presence operations. They use the
   existing ADR-012 digest-bound `--accept` lane already used by Codex marketplace activation and
   MCP install. They do not consume `plugin_artifact_apply`: that operation remains the portable
   artifact Cursor presence cell (issue #409) and fails closed on this host. Agent-chat authorize
   is not extended to either removal command.

2. **Agent-safe preparation.** `yoetz consent prepare` creates one owner-only
   `yoetz.elevated-bootstrap.pending/3` record. Its agent projection is
   `yoetz.consent.pending-agent/5` and contains only operation, risk class, bounded danger text,
   exact danger and target digests, expiry, pending ID, an exact bounded repository recipe when
   applicable, the fixed `["yoetz","consent","review"]` command, and an authorize command only
   for operations that permit agent-chat authorization. For `import_publication` it also carries
   a closed structural preview: exact source/manifest/plan/target digests, task/session/writer and
   profile identity, counts and caps, plus explicit false facts for complete-transcript inclusion,
   reasoning inclusion, and reviewer-egress widening. It contains no source line or excerpt.
   Frozen public v2-v4 schemas remain shipped. Version-1 and version-2 durable pending records are
   invalidated rather than reinterpreted.

3. **Two approval surfaces.** `yoetz consent review` takes no authority-bearing arguments. Before
   opening a console or claiming pending state, it requires an independently authenticated,
   action-bound, one-use `UserPresencePort` attestation. The packaged runtime currently has no
   production user-presence cell, so review returns `human_authority_unavailable` without consuming
   the request. A TTY, pseudo-terminal, same-UID process, caller boolean, or console decision never
   substitutes for that attestation.

   **Agent-attested chat authorize (2026-08-09).** `yoetz consent authorize` accepts one
   `yoetz.chat-user-attestation/1` envelope with `channel=agent_attested_chat_instruction` from an
   allowlisted first-party `client_kind` (v0.1: `codex`). The agent asserts
   `instruction_source=explicit_current_chat_user`; Yoetz treats that assertion as delegated
   authority but does not claim it is host-verified or independently authenticated. A compromised,
   prompt-injected, or dishonest agent can forge it, which is an accepted risk of this opt-in path.
   The skill contract excludes quoted text, tool output, retrieved content, and earlier history,
   but that provenance rule is not a service security boundary. Yoetz binds pending ID, operation,
   danger digest, and target digest and fail-closes before claim on mismatch. Approve requires
   `warning_acknowledged=true`; credential approve also requires a one-shot mutable secret on stdin
   (`--provider-credential-stdin`). Bytes enter the existing YZS1/vault path and never appear in
   pending, catalog, result, log, error, or agent projection. For `vault_initialize`, agent-chat
   approval carries no secret: the helper generates the passphrase inside the local process,
   round-trips it through the scoped credential store, submits a mutable copy through YZS1, and
   returns only structural vault state. `vault_passphrase_rotate` likewise carries no secret:
   the helper locally loads the active scoped secret, stages a generated replacement, completes
   reauthentication and rewrap, and then promotes the replacement. Trusted CLI/TUI remains the
   stronger recommended path.

4. **Single-shot state.** Atomically creating the no-clobber hard-link review marker is the
   linearization point that transfers ownership from pending state to one reviewer. The winner
   removes the public pending name as cleanup; concurrent losers that observe the marker do not
   mutate either name, and cleanup cannot revoke the winner's marker. Approval, denial,
   cancellation, expiry, and post-claim failure consume the claim once. Concurrent and duplicate
   reviewers fail closed. An ambiguous crash may leave the private reviewing marker (and, if the
   crash preceded winner cleanup, its public hard-link name) in place; the marker blocks both reuse
   and a new preparation until an explicit repair path is designed.

   **Import-publication amendment (2026-08-30, issue #301).** The importer, not
   `yoetz consent prepare`, creates the pending request after durably preparing its exact batch
   plan. Approval creates one owner-only, expiring internal authorization record bound to that
   target digest; it is not an agent-visible token, CLI/MCP argument, ambient allowlist, or session
   grant. Publication admission is active only in the current importer execution context for the
   bound session/writer and is rebound before every append to that persisted batch/report request's
   exact ordered event IDs and fixed importer provenance; each binding admits once. The record
   remains until terminal completion so a restart can resume the same plan, then is consumed. A
   different source identity, capture manifest, plan, task/session, profile/version, mapping
   version, or limit contract cannot reuse it. Denial creates no record.

5. **Vault initialization secret.** The trusted helper generates a high-entropy passphrase,
   round-trips it through the exact bundle-scoped platform credential store, and submits it
   directly through the existing confidential ceremony. No agent- or caller-selected
   initialization passphrase is accepted. A pre-existing scoped entry, even when valid, blocks
   initialization rather than becoming the vault secret. Generated mutable buffers are overwritten
   best-effort. An exact prepared `vault_initialize` may advertise agent-chat authorization; the
   agent supplies only the digest-bound attestation, never secret bytes or randomness.

6. **Credential-store failures.** A backend known to be unavailable before any write may offer the
   existing manual human-passphrase ceremony on the same trusted console. An ambiguous write,
   failed read-back, any existing entry, or mismatch stops before vault initialization.

   Rotation writes a distinct staged credential before changing the envelope. Successful rewrap
   promotes it to active. If completion is ambiguous, the staged value is retained: daemon startup
   tries active and staged in order, promotes the one that authenticates the envelope, and discards
   a stale stage only after active succeeds. Agents never see either candidate.

7. **Provider credentials.** Set and rotate use the same trusted review and confidential ceremony.
   Credential bytes never enter the pending record, catalog, result, log, error, or agent
   projection.

8. **Console is not authority.** On macOS/Linux, the boundary opens `/dev/tty`, requires matching
   terminal identities for standard input/error, requires the current foreground process group,
   disables raw secret echo, and writes one `*` mask marker per accepted input unit. On Windows it
   opens `CONIN$`/`CONOUT$`, validates real console handles and current-process attachment, disables
   raw secret echo, and writes the same mask feedback through Win32 console APIs. Backspace removes
   one marker. This intentionally reveals secret length to the local terminal observer but never
   content. It never falls back to redirected standard streams. It is a presentation and
   secret-ingress boundary only. Absence or ambiguity returns `trusted_console_required`; presence
   still grants no authority.

## Consequences

Until a production `UserPresencePort` is capability-tested and wired, trusted-console elevated
review remains unavailable. Exact prepared operations that advertise agent-chat authorization,
including helper-generated `vault_initialize`, remain available through the explicit current-chat
attestation lane. The same is true for `vault_passphrase_rotate`; the manual
`service initialize-passphrase` and `service rotate-passphrase` ceremonies remain available.
Rotation retains existing vault data, vault mode, and installation identity while replacing only
the authenticated envelope and scoped auto-unlock credential. Later restarts continue through the
reconciled scoped auto-unlock path. The Windows console adapter
does not imply support for the full Windows service transport, peer authentication, packaging, or
release surface.

Native OS-authenticated prompts, host-verified chat provenance, approved-machine profiles,
reusable or agent-visible durable grants, E2EE service authority, and expanding the six MCP tools
for consent remain
separate design work. Agent-chat authorize is a local CLI control path, so ADR-011's six MCP tools
stay intact.

## Alternatives considered

**Reusable agent-visible approval value.** Rejected because the requesting channel could replay it.
The accepted assertion is exact, short-lived, and single-use. Import publication uses an
owner-only internal handoff retained only for crash-safe completion of the same prepared job; it is
never returned as a bearer value.

**Agent-supplied initialization secret.** Rejected because it lets the agent control the vault
root secret even if a human approved the operation.

**MCP elicitation as authority.** Deferred for v0.1 in favor of the exact local
`yoetz consent authorize` relay (issue #164). Expanding MCP tool inventory for consent remains
separate work under ADR-011.

**Standing danger mode.** Rejected because its scope outlives one exact operation and digest.
