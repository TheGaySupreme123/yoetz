# Elevated bootstrap peer patterns

Sources: ADR-015, `guidance/agent-instructions.md`, Codex sandbox/approval docs,
Claude Code permission-mode docs, MCP elicitation spec, and Cursor Cloud environment notes.

## Peer pattern summary

- Codex CLI:
  - Separates `sandbox_mode` (technical containment: `read-only`, `workspace-write`,
    `danger-full-access`) from `approval_policy` (when the agent must stop).
  - Lower-risk automation uses workspace write plus on-request approvals; full access is an
    explicit danger posture, especially when paired with never-ask approvals.
  - Granular approval settings can keep sandbox escapes, MCP prompts, and permission requests
    interactive or auto-rejected instead of making one global trust decision.
- Claude Code:
  - Permission modes range from `plan`/manual read-only behavior through `acceptEdits`, `auto`,
    `dontAsk`, and `bypassPermissions`.
  - `bypassPermissions` is documented for isolated containers/VMs only, and still leaves some
    forced prompts/circuit breakers and MCP `requiresUserInteraction` cases.
  - `plan` keeps edits out of scope; `dontAsk` denies tools unless pre-approved, matching
    locked-down CI rather than human consent.
- MCP elicitation:
  - In-band forms may ask reviewable non-secret questions, but must not request passwords,
    API keys, access tokens, payment credentials, or equivalent secrets.
  - Sensitive flows use out-of-band URL-mode style interaction with explicit user consent and
    a displayed target, keeping the client and model from observing the secret entry.
  - Elicitation is not authorization for the MCP client itself and does not mint a reusable
    bearer capability for the agent.
- Cursor Cloud:
  - Agents run in isolated cloud VMs/environments; egress policy can be allow-all, defaults plus
    allowlist, or allowlist-only at user/team/environment scope.
  - This run reports unrestricted egress and owner-restricted environment JSON, which illustrates
    that host isolation/egress are containment facts, not Yoetz bootstrap consent.
  - Secrets and network policy are environment configuration; they do not authorize a specific
    vault initialization or provider-credential operation.

## Mapping to ADR-015/016 choices

- `prepare` mirrors peer approval prompts: the agent can request elevation and surface operation,
  `danger_text`, `danger_digest`, confirmation phrase (for display), expiry/state, and command
  template with a `<confirmation_phrase>` placeholder only.
- The human phrase binds reviewer intent to the exact digest for implemented secret-ingress ops
  (`vault_initialize`, `provider_credential_set`, `provider_credential_rotate`). Phrase-only
  irreversible ops are catalogued with `implemented=false` until durable grant consumption exists.
- `approve` consumes one pending record (single-shot) and supplies secret bytes only on inherited
  FDs, preserving ADR-008's service/vault boundary while allowing the no-TTY cloud case.
- There is no standing Yoetz `--yolo`, bypass mode, secret bearer token, or generic headless
  passphrase/unlock API; broader containment remains the host sandbox/VM/egress policy.

## MCP/status should tell agents

- Report `elevated_bootstrap.required` or pending state, operation, `danger_text`,
  `danger_digest`, confirmation phrase (display only), expiry/state, and `approve_command`
  template with `<confirmation_phrase>` placeholder — never pre-fill the live phrase into the
  command.
- Instruct the agent to stop ordinary work, show those fields to the human, wait for the repeated
  phrase, substitute only the human-typed phrase, then run approve with inherited secret FDs when
  listed. Refuse prepare when catalog `implemented` is false.
- Return a blocked reason when FD delivery, pending state, digest match, or phrase match is
  unavailable; do not suggest alternate secret channels. Locked vaults need local TTY unlock;
  elevated consent does not unlock.
- After completion, expose only sanitized structural success/failure/audit facts.

## NEVER over MCP/chat

- Vault passphrases, reauth passphrases, provider API keys, access tokens, OAuth/device codes, or
  recovery material.
- Secret FD contents, FD paths/proofs, local endpoint/socket paths, bearer consent tokens, or
  reusable vault capabilities.
- Owner-only pending-file paths, environment-variable secret values, argv secrets, or config files
  containing secrets.
- Full prompts/transcripts, hidden reasoning, or broad unrelated source/context.
