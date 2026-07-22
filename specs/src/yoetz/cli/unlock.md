# src/yoetz/cli/unlock.py — foreground no-echo confidential local-human helper

**Wave:** D | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):**
`service/confidential_client.md`, `service/confidential_protocol.md`, `cli/exits.md` |
**Imported by:** `cli/app.md` confidential commands only

## Purpose

Collects bounded first-install initialization, unlock, recovery, provider-credential, security-
policy authorization, or reauthentication input from a foreground local human and sends exactly
one accepted secret directly to the service's confidential ingress when the selected ceremony
requires one. It is the only CLI module that may handle these values and never exposes a normal
argument/stdin/environment API for a secret or decision. Nonsecret closed ceremony targets may be
supplied by their dedicated trusted-foreground command.

## Public surface

- `run_human_ceremony(kind, target) -> structural result`; it obtains the service-minted binding
  through the client-safe `HumanControlClient` before any prompt and never accepts a caller-
  supplied binding.
- Convenience `initialize_passphrase_vault()` and `unlock_vault()` plus purpose-specific wrappers
  owned by setup/maintenance/privacy flows.
- `retry_keyring()` — zero-secret YZH1 action; and `set_provider_credential(binding, credential=None)` /
  `rotate_provider_credential(binding)` — reauthenticate then collect one credential through exact
  service-minted bindings.
- `change_idle_relock_policy(target: int | Literal["disabled"]) -> IdleRelockCliResult` — open the
  exact `idle_relock_policy_change` human ceremony and return only the bounded structural outcome.
- `IdleRelockCliPolicyValue` is exactly `{mode:"finite", seconds:<60..86400>}` or
  `{mode:"disabled"}`. Frozen `IdleRelockCliResult` is exactly `outcome:applied|denied`,
  `previous:IdleRelockCliPolicyValue`, `effective:IdleRelockCliPolicyValue`,
  `scope:"service_generation"`, and positive canonical `service_generation`.
- Private terminal guard/no-echo reader; no raw secret return.

## Behavior

Require `/dev/tty` readable/writable, stdin and stderr attached to the same controlling TTY,
foreground process group, current user, and explicit structural purpose/target preview. Connect to
the third same-UID human-control endpoint, open exactly one ceremony, verify/freeze its binding and
render its service-owned preview. Only then open
`/dev/tty` directly; enter no-echo mode; read bounded bytes into `bytearray`; restore terminal in
`finally`; send exactly once via `ConfidentialSecretClient`; overwrite local buffer in `finally`.
Never read a secret or human decision from stdin, command args, environment, config, clipboard,
file, shell substitution, or MCP. Dedicated commands may pass only their documented closed,
nonsecret ceremony target; YZH1 still supplies and freezes the authoritative preview/binding.

`unlock_vault` opens YZH1 first. For locked OS-keyring mode it receives `next_phase=keyring_retry`
and sends one typed zero-secret retry action on that connection; it never calls YZS1. For locked
passphrase mode it receives a `vault_unlock` YZS1 binding and prompts once. Uninitialized mode
returns structural guidance for the distinct initialization command and never silently chooses it.
When the bounded reason is `human_authority_unavailable`, the helper explains that keyring storage
passed but this artifact/release cell lacks verified action-bound presence; it offers only a later
zero-secret retry or the separate explicit `service initialize-passphrase` command. It does not
open a secret prompt in the retry ceremony.

`initialize_passphrase_vault` is a separate explicit first-install command, never an automatic
response to keyring failure. It first obtains a `vault_initialize` challenge whose structural view
still says `uninitialized` and shows that this will select immutable passphrase mode. The helper
then reads the proposed passphrase twice in two separate bounded mutable no-echo buffers, compares
them locally without creating `str`/immutable copies, and sends exactly one confidential secret
frame only on equality. It overwrites the confirmation buffer before send and both buffers on every
exit. A mismatch sends nothing, consumes/cancels the challenge, and requires a fresh ceremony.
The service receives/captures one `SecretHandle(vault_initialize)`; confirmation bytes and a
match/mismatch detail never cross the confidential channel.

All passphrase prompts enforce the shared 16..1,024-byte strict-UTF-8 policy before send: U+0000,
U+000A, and U+000D are forbidden; nothing is trimmed, normalized, case-folded, replacement-decoded,
or NUL-terminated. The TTY delimiter is consumed separately. Vault initialization and portable-
recovery *creation* read two independently allocated mutable buffers, compare exact bytes in
constant-work bounded logic, overwrite the confirmation buffer, and send only the first buffer on
equality. Later vault unlock, portable *restore*, and established-passphrase reauthentication read
once. A mismatch or invalid encoding/length sends nothing, cancels the ceremony, and requires a
fresh binding. The trusted service repeats validation; helper validation is not authority.

Provider set/rotate accepts only nonsecret installed profile/scope/purpose identifiers as its
target. The YZH1 preview freezes the exact `ProviderCredentialBinding` and action. The helper first
completes service-requested OS presence or one `provider_reauthentication` no-echo frame, then
receives a separate `provider_credential` binding and prompts for the credential once. It returns
only stored/rotated plus activation status. Any failure sends no credential or preserves the old
record; no provider secret appears in normal CLI parsing/output.

`change_idle_relock_policy` accepts only an already parsed canonical integer `60..86400` or the
literal `disabled`, then opens the closed YZH1 target `{kind:"idle_relock_policy", operation:"set",
seconds:N}` or `{kind:"idle_relock_policy", operation:"disable"}`. It verifies that the server
preview repeats the exact proposed target and supplies a tagged current value, service generation,
generation-only scope, and canonical target digest before rendering it on `/dev/tty`. It explicitly
states that restart restores 900 seconds and that explicit, session-lock, suspend, and monitor-loss
relock remain active even when idle relock is disabled.

The helper accepts only the TTY choices `approve|deny`. Denial completes without YZS1; after the
matching terminal server result it returns `IdleRelockCliResult(outcome="denied", ...)` using that
result's exact previous/effective tagged values, scope, and generation. Approval follows the
server's closed authorization phase: challenge-bound OS user presence, or
`security_reauthentication` in established passphrase mode. Only the latter reads and sends one
secret through YZS1 under the shared passphrase byte policy. After a matching terminal server
result, the helper returns `outcome="applied"` with the server's exact previous/effective tagged
values, scope, and generation. The CLI adds the `outcome` tag from the exact action plus matching
terminal result; it never infers application from a connection close. A target race, relock,
generation change, expiry, rejection, cancellation, or authorization failure returns no applied
result and does not apply this request's proposed target.

The target is nonsecret but grants no authority. There is no ordinary-control request, MCP method,
config write, environment variable, reusable proof, or `--yes`/stdin decision path. The effective
change is current-generation-only and is never reported as durable across service restart.

A yes/boolean confirmation is not proof of human presence and cannot loosen privacy. That purpose
requires the human to reauthenticate through the vault/OS user-presence ceremony and binds the
result to an exact policy digest. TTY presence is an explicit ceremony but not cryptographic proof
against malicious same-UID automation; documentation states the limit.

## Errors and edge cases

No controlling TTY/foreground mismatch/redirection/pipeline/noninteractive mode fails before a
prompt. Signals/EOF/timeout restore echo and overwrite. Secret never appears in traceback/rendering.
No headless/password-FD fallback ships in v0.1.
If the service no longer proves pristine `uninitialized` state, initialization fails before either
prompt. An existing keyring/passphrase vault is never offered this command as unlock/reset fallback.
Presence-capability failure is never rendered as a keyring corruption or as permission to auto-
select passphrase mode.

- Idle-relock target parsing occurs in `cli/app.md` before YZH1: anything other than canonical
  `disabled|60..86400` exits `2` with no ceremony. A service not ready, unavailable presence/
  passphrase authorization, stale target/generation, or expired binding fails closed without
  changing the current policy. Ctrl-C exits `130`; an explicit `deny` exits `0` with the structural
  denied outcome, and a confirmed applied result exits `0` with the structural applied outcome.

## Invariants

1. Secret bytes travel helper-memory to confidential socket to service-memory only.
2. Normal CLI parser/MCP/application never receives or returns them.
3. Terminal mode is restored and mutable buffer overwritten on every path, best effort.
4. Purpose/target binding is shown before collection and immutable during send.
5. Passphrase initialization confirms twice locally but transmits/captures only one
   `vault_initialize` secret.
6. Every prompt consumes a service-minted live YZH1 binding; keyring retry remains zero-secret.
7. This module's import graph contains client/protocol/terminal/render code only, never
   `human_control`, `secret_ingress`, `unlock`, `vault`, application, or provider server authority.
8. Portable-recovery create is double-entry/one-send; restore is single-entry/one-send, and the
   operation is frozen in the server-minted binding before collection.
9. Setup-required keyring authority failure remains zero-secret until a human explicitly starts a
   new passphrase-initialization ceremony.
10. Idle-relock change uses only `idle_relock_policy_change` plus, when required,
    `security_reauthentication`; privacy/provider purposes, ordinary control, and TTY presence
    alone cannot authorize it.

## Tests

- `tests/subprocess/test_service_unlock_boundary.py` uses PTYs/signals/pipes/redirection/process-
  group, exact passphrase byte vectors, initialize/recovery-create two-prompt match/mismatch,
  unlock/recovery-restore single-prompt cases, and scans argv/env/output/history/logs.
- `tests/integration/service/test_secret_ingress.py` checks exact purpose/binding transfer.
- Provider credential tests cover set/existing refusal, rotate/old-record preservation,
  reauthentication separation, and post-store adapter reconciliation.
- `tests/subprocess/test_service_unlock_boundary.py` covers finite/disabled idle-relock target
  grammar, exact preview, deny/no-YZS1, both authorization sources, structural applied outcome,
  stale/cancel paths, restart disclaimer, and absence from ordinary/MCP control.

## Open questions

None.
