# ADR-015 — Founder-authorized elevated bootstrap consent for cloud agents

**Status:** Superseded in scope by ADR-016 for the general non-default consent catalog; the
bootstrap secret-ingress lane and FD rules below remain binding.
**Owning public specs:** `specs/src/yoetz/service/elevated_bootstrap.md`,
`specs/src/yoetz/cli/elevated.md`, amendments to ADR-008, ADR-009 agent-context wording for
structural consent projection only, `guidance/agent-instructions.md`, `OPEN_QUESTIONS.md`.
**Relates to:** ADR-008 (vault/TTY ceremony), ADR-009 (egress / agent context), ADR-012 (setup).

## Context

Cloud coding agents (Cursor Cloud, Codex remote, similar) run without a user-owned controlling
`/dev/tty`. ADR-008 therefore blocks `vault_initialize` and `provider_credential_set` in those
environments even when the human is present in the host UI and can grant consent.

Leading agent hosts separate **capability containment** from **human consent** (Codex approval
policies, Claude permission modes, Cursor cloud isolation + allowlists, MCP elicitation). Yoetz
needs an analogous, narrower path: the agent may *request* elevated bootstrap; the human must
*grant* exact digest-bound consent; secrets still never travel over MCP, argv, env, config, or
chat paste.

## Decisions

1. **Ordinary path unchanged.** Interactive local users keep the ADR-008 `/dev/tty` ceremony.

2. **Scoped elevated bootstrap only (ADR-015 lane).** Exact *implemented* secret-ingress
   operations: `vault_initialize`, `provider_credential_set`, and (per ADR-016 catalog)
   `provider_credential_rotate`. Not vault unlock loops, privacy widening, idle-relock weakening,
   portable recovery, phrase-only irreversible ops, or a general `--yolo`. ADR-016 catalogues
   additional ops with `implemented=false` until durable grant consumption exists.

3. **Consent challenge, then FD secrets.** Flow:
   - `yoetz elevated-bootstrap prepare …` creates one owner-only pending record with danger text,
     `danger_digest`, and a random confirmation phrase (no secrets).
   - MCP / agent instructions surface structural pending facts and an approve-command template
     with a `<confirmation_phrase>` placeholder — never tokens, secrets, paths, proofs, or a
     pre-filled live phrase in the command template (phrase is shown separately for human display).
   - Human reviews danger text in the host UI/chat and supplies the confirmation phrase.
   - `yoetz consent approve … --confirm "…" --passphrase-fd N` (and for credentials,
     `--reauth-fd` / `--credential-fd`) verifies pending digests/phrase (single-shot consume), then
     drives the existing YZH1/YZS1 path with secret bytes read only from inherited FDs (not 0/1/2).

4. **ADR-008 amendment (narrow).** The rejected “generic inherited password FD” alternative is
   opened **only** for this founder-authorized elevated-bootstrap approve path after exact
   pending consent. MCP and ordinary `ServiceClient` still cannot carry secret bytes.

5. **Storage.** Pending consent is ephemeral owner-only file state under platformdirs (not a
   catalog `0002`), because bootstrap must work while the vault is uninitialized. Structural
   audit events append to an owner-only JSONL file without secret material.

6. **Same-UID honesty.** This does not claim cryptographic exclusion of malicious same-UID
   code. It records human intent for cloud-agent orchestration; TTY remains the stronger local
   human ceremony.

## Consequences

Cloud agents can complete first-run vault init and provider credential set after explicit human
consent, without putting secrets on MCP. Local interactive installs remain on the TTY path.
OPEN_QUESTIONS “generic headless passphrase input” stays deferred; this ADR is a scoped
exception, not a general headless vault API.

## Alternatives considered

**Agent-held bearer consent token + later complete.** Rejected: token is vault-adjacent
capability until burn; races widen the same-UID window.

**Secrets via MCP elicitation form.** Rejected: ADR-009 / MCP guidance forbid secrets in agent
context; use out-of-band FDs after consent.

**Standing danger mode / `--yolo`.** Rejected: too broad; contradicts ADR-008 threat model.
