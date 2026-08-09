# ADR-015 — OS-presence-gated elevated bootstrap consent

**Status:** Amended 2026-07-31; amended 2026-08-09 for chat-user host-tool-approval authorize
(issue #164). Superseded in scope by ADR-016 for the general non-default consent catalog.
Console `yoetz consent review` remains fail-closed until a verified OS-presence adapter is
installed; capable first-party hosts may use `yoetz consent authorize` for exact prepared
operations that advertise chat-user authority.
**Implemented by:** `src/yoetz/service/elevated_bootstrap.py`,
`src/yoetz/cli/elevated.py`, `src/yoetz/cli/trusted_console.py`,
`src/yoetz/protocol/consent.py`, and `src/yoetz/protocol/chat_user_authority.py`.
**Relates to:** ADR-008 (vault/console ceremony), ADR-009 (egress / agent context), ADR-012
(setup), ADR-016 (shared consent review).

## Context

An agent may prepare and inspect a non-default operation, but the same agent-capable channel must
not receive a reusable value that authorizes it. Vault initialization also must not accept an
agent-selected passphrase. Consent therefore needs a local human boundary independent of argv,
environment, stdin, MCP, agent JSON, and caller booleans.

## Decisions

1. **Implemented bootstrap operations.** `vault_initialize`, `provider_credential_set`,
   `provider_credential_rotate`, and `repository_privacy_grant` use this lane. It is not a
   vault-unlock API, recovery API, or standing elevation mode.

2. **Agent-safe preparation.** `yoetz consent prepare` creates one owner-only
   `yoetz.elevated-bootstrap.pending/2` record. Its agent projection is
   `yoetz.consent.pending-agent/2` and contains only operation, risk class, bounded danger text,
   exact danger and target digests, expiry, pending ID, and the fixed
   `["yoetz","consent","review"]` and `["yoetz","consent","authorize"]` commands. Version-1
   pending records are invalidated, not migrated.

3. **Two approval surfaces.** `yoetz consent review` takes no authority-bearing arguments. Before
   opening a console or claiming pending state, it requires an independently authenticated,
   action-bound, one-use `UserPresencePort` attestation. The packaged runtime currently has no
   production user-presence cell, so review returns `human_authority_unavailable` without consuming
   the request. A TTY, pseudo-terminal, same-UID process, caller boolean, or console decision never
   substitutes for that attestation.

   **Chat-user authorize (2026-08-09).** `yoetz consent authorize` accepts one
   `yoetz.chat-user-attestation/1` envelope with `channel=host_tool_approval` from an allowlisted
   first-party `client_kind` (v0.1: `codex`). The harness must gate the exact command behind human
   confirmation that shows danger text and digests. Yoetz binds pending id, operation, danger
   digest, and target digest and fail-closes on mismatch, expiry, replay, or unavailable capability.
   Bare chat assent, quoted text, tool output, retrieved content, other participants, prompt
   injection, and agent self-assertion never authorize. Credential-bearing approve requires
   `warning_acknowledged=true` and a one-shot secret on stdin (`--provider-credential-stdin`);
   bytes enter the existing YZS1/vault path and never appear in pending, catalog, result, log,
   error, or agent projection. `vault_initialize` remains console/helper-only — chat never supplies
   the vault root secret. Trusted CLI/TUI remains recommended and always available.

4. **Single-shot state.** A review claim consumes the public pending name. Approval, denial,
   cancellation, expiry, and post-claim failure consume the claim once. Concurrent and duplicate
   reviewers fail closed. An ambiguous crash may leave the private reviewing marker in place; that
   blocks both reuse and a new preparation until an explicit repair path is designed.

5. **Vault initialization secret.** The trusted helper generates a high-entropy passphrase,
   round-trips it through the exact bundle-scoped platform credential store, and submits it
   directly through the existing confidential ceremony. No agent- or caller-selected
   initialization passphrase is accepted. A pre-existing scoped entry, even when valid, blocks
   initialization rather than becoming the vault secret. Generated mutable buffers are overwritten
   best-effort.

6. **Credential-store failures.** A backend known to be unavailable before any write may offer the
   existing manual human-passphrase ceremony on the same trusted console. An ambiguous write,
   failed read-back, any existing entry, or mismatch stops before vault initialization.

7. **Provider credentials.** Set and rotate use the same trusted review and confidential ceremony.
   Credential bytes never enter the pending record, catalog, result, log, error, or agent
   projection.

8. **Console is not authority.** On macOS/Linux, the boundary opens `/dev/tty`, requires matching terminal
   identities for standard input/error, requires the current foreground process group, and uses
   no-echo reads. On Windows it opens `CONIN$`/`CONOUT$`, validates real console handles and current
   process attachment, and reads through Win32 console APIs with echo disabled. It never falls back
   to redirected standard streams. It is a presentation and secret-ingress boundary only. Absence
   or ambiguity returns `trusted_console_required`; presence still grants no authority.

## Consequences

Until a production `UserPresencePort` is capability-tested and wired, elevated review cannot
initialize a vault or mutate provider credentials. The explicit manual
`service initialize-passphrase` ceremony remains available. Existing vault data,
vault mode, installation identity, and auto-unlock credentials are not migrated or rewrapped.
Later restarts continue through the existing scoped auto-unlock path. The Windows console adapter
does not imply support for the full Windows service transport, peer authentication, packaging, or
release surface.

Native OS-authenticated prompts beyond host-tool-approval, approved-machine profiles,
durable grants, E2EE service authority, and expanding the six MCP tools for consent remain
separate design work. Chat-user authorize is local control under host command approval so ADR-011's
six MCP tools stay intact.

## Alternatives considered

**Agent-visible approval value.** Rejected because the requesting channel could also submit it.

**Agent-supplied initialization secret.** Rejected because it lets the agent control the vault
root secret even if a human approved the operation.

**MCP elicitation as authority.** Superseded for v0.1 by host-tool-approval of the exact local
`yoetz consent authorize` command (issue #164). Expanding MCP tool inventory for consent remains
deferred under ADR-011.

**Standing danger mode.** Rejected because its scope outlives one exact operation and digest.
