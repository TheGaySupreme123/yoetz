# ADR-015 — Trusted-console elevated bootstrap consent

**Status:** Amended 2026-07-30; superseded in scope by ADR-016 for the general non-default
consent catalog. The trusted-console bootstrap lane below remains binding.
**Implemented by:** `src/yoetz/service/elevated_bootstrap.py`,
`src/yoetz/cli/elevated.py`, `src/yoetz/cli/trusted_console.py`, and
`src/yoetz/protocol/consent.py`.
**Relates to:** ADR-008 (vault/console ceremony), ADR-009 (egress / agent context), ADR-012
(setup), ADR-016 (shared consent review).

## Context

An agent may prepare and inspect a non-default operation, but the same agent-capable channel must
not receive a reusable value that authorizes it. Vault initialization also must not accept an
agent-selected passphrase. Consent therefore needs a local human boundary independent of argv,
environment, stdin, MCP, agent JSON, and caller booleans.

## Decisions

1. **Implemented bootstrap operations.** `vault_initialize`, `provider_credential_set`, and
   `provider_credential_rotate` use this lane. It is not a vault-unlock API, recovery API, or
   standing elevation mode.

2. **Agent-safe preparation.** `yoetz consent prepare` creates one owner-only
   `yoetz.elevated-bootstrap.pending/2` record. Its agent projection is
   `yoetz.consent.pending-agent/2` and contains only operation, risk class, bounded danger text,
   exact danger and target digests, expiry, pending ID, and the fixed
   `["yoetz","consent","review"]` command. Version-1 pending records are invalidated, not migrated.

3. **One approval surface.** `yoetz consent review` is the only approval path. It first opens a
   `TrustedForegroundConsole`, then atomically claims and reloads the pending request, validates
   operation, digests, expiry, and binding, displays the exact action, and accepts an explicit
   `approve` or `deny` decision. Approval is never accepted through arguments, environment, stdin,
   MCP, JSON, or a caller-supplied boolean.

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

8. **Console boundary.** On macOS/Linux, the boundary opens `/dev/tty`, requires matching terminal
   identities for standard input/error, requires the current foreground process group, and uses
   no-echo reads. On Windows it opens `CONIN$`/`CONOUT$`, validates real console handles and current
   process attachment, and reads through Win32 console APIs with echo disabled. It never falls back
   to redirected standard streams. Absence or ambiguity returns `trusted_console_required` before
   pending consumption or vault mutation.

## Consequences

The normal initialization path requires one human review per installation. Existing vault data,
vault mode, installation identity, and auto-unlock credentials are not migrated or rewrapped.
Later restarts continue through the existing scoped auto-unlock path. The Windows console adapter
does not imply support for the full Windows service transport, peer authentication, packaging, or
release surface.

Native biometric prompts, natural-language host approval, approved-machine profiles, durable
grants, E2EE service authority, and MCP presentation UX remain separate design work.

## Alternatives considered

**Agent-visible approval value.** Rejected because the requesting channel could also submit it.

**Agent-supplied initialization secret.** Rejected because it lets the agent control the vault
root secret even if a human approved the operation.

**MCP elicitation as authority.** Deferred. It may later present the request but cannot authorize
without a verified client/user identity channel.

**Standing danger mode.** Rejected because its scope outlives one exact operation and digest.
