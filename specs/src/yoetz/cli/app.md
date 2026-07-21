# src/yoetz/cli/app.py — local-service CLI client and service command tree

**Wave:** D/F | **ADRs:** ADR-001, ADR-002, ADR-005, ADR-007, ADR-008, ADR-013 |
**Imports (spec-tree):** `cli/render.md`, `cli/exits.md`, `cli/unlock.md`, `cli/menu.md`,
`service/client.md`, `mcp/server.md`, `ports/subject_state.md`, `adapters/git_subject_state.md` |
**Imported by:** console/module entrypoints and CLI tests

## Purpose

Owns the human command surface. Normal commands are thin clients of the persistent local service;
they never construct `Application`, open storage/keyring, load provider credentials, decrypt
objects, or fall back to direct execution.

## Public surface

- Typer `app`, `main()`, one `run_async` bridge.
- `build_service_client(client_kind=cli) -> ServiceClient`.
- Shared workflow client commands `start`, `publish-work`, `check`, `respond`, `status`, and
  `receipt`.
- Complete command tree: those six workflows; `mcp serve`;
  `import`/`review`/`backup`/`restore`/`migrate`/`version`;
  local read-only `state capture --workspace PATH`;
  `integrate <harness> skill preview|install|status|remove`, where `<harness>` is an exact
  registered `HarnessId` (v0.1: exactly `codex`) that the user always names and the CLI never infers
  from cwd, environment, installed editors, or running processes;
  `integrate <harness> mcp status|preview|install` and `setup run|status` (ADR-012), thin wiring
  over `cli/setup.py` for preview-gated MCP registration and the first-run wizard — for
  registration the CLI may *discover* candidate binaries but a mutation still requires an explicit
  selection plus digest-bound confirmation;
  `menu` (ADR-013), thin wiring over `cli/menu.py` for the interactive control menu — a usage
  failure (exit 2) on a non-TTY, never a prompt;
  `service run|status|lock|stop|unlock|initialize-passphrase` plus trusted-foreground
  `service idle-relock <60..86400|disabled>`;
  `provider credential set|rotate` for foreground confidential provisioning; and
  `privacy setup|show|propose|tighten` and `privacy receipts list|get` for transparent setup and
  bounded structural local audit inspection, and the trusted-human-only
  `privacy decide-policy|decide-disclosure`.
- No `build_runtime`, `RuntimeFactory`, application constructor, password/key/token option, or
  secret environment reader.

## Behavior

Root bare-invocation (ADR-012 as amended by ADR-013): the app drops `no_args_is_help`; the root
callback reproduces the historical help output for every non-TTY bare invocation. When stdin and
stdout are both real TTYs, a bare invocation with the `setup_marker_path()` marker absent launches
the interactive `setup run` wizard once and then opens the `cli/menu.py` menu; with the marker
present it opens the menu directly. `--help`, named subcommands, and every non-TTY invocation are
unchanged.

Except for the explicitly client-local ADR-011 `state capture` support command, every normal
workflow/support command strictly parses its request, connects to the deterministic same-UID
service endpoint, invokes the matching `ServiceClient` method, validates the returned result, and
renders JSON/human output. Service absent reports bounded guidance; locked reports
`vault_locked` and directs a local human to `service unlock`. Neither state triggers hidden spawn,
direct runtime, prompt, or secret acceptance.

`mcp serve` delegates to the MCP bridge and owns only stdio framing/client connection. `service
run` is the foreground daemon entrypoint; it must not be nested inside another CLI event loop.
Native launchd/systemd-user install/start integration is deferred; an external user-selected
supervisor may run this foreground command, but normal workflows never start it.
`service lock` is CLI-only and drains/relocks; `service stop` drains/exits. Version may inspect
installed public package metadata locally but no user/vault state. `privacy setup|show|propose|tighten`
use least-authority ordinary control. `privacy decide-policy|decide-disclosure` delegate
to `cli/privacy_control.md`, never an ordinary decision method. `privacy receipts list|get` are
CLI/UI-only structural reads, never MCP tools; they render no excerpts/request bodies/object refs and
are local-projection/audit-exempt so inspection does not create a new receipt.

Privacy ordinary-control dispatch is exact and alias-free: `privacy setup`, `show`, `propose`, and
`tighten` call `privacy_get_setup`, `privacy_get_effective`, `privacy_propose_policy`, and
`privacy_tighten_policy`; `privacy receipts list|get` call `privacy_receipts_list` and
`privacy_receipts_get`, respectively. The trusted decision commands use YZH1 and are not
`ControlMethod` branches.

`service idle-relock TARGET` is a trusted foreground security ceremony, not an ordinary support
call. `TARGET` is exactly ASCII `disabled` or a canonical unsigned base-10 integer in `60..86400`;
signs, leading zeroes, whitespace, suffixes, floats, null/infinity aliases, and out-of-range values
exit `2` before any connection or prompt. The raw argument token is validated before integer
conversion. A valid target delegates only to
`cli/unlock.change_idle_relock_policy`, which opens YZH1 ceremony
`idle_relock_policy_change`; it never invokes `ServiceClient`, `ControlMethod`, MCP, config, or a
normal workflow envelope. The TTY preview shows the current and proposed tagged values, current
service generation, generation-only scope, restart reset to the 900-second default, and that
explicit/session-lock/suspend/monitor-loss locking remains active. Approval requires the
server-selected OS-presence or passphrase-mode `security_reauthentication` path. A typed denial is
a successful no-mutation outcome.

`state capture` is the sole client-local repository-read exception under ADR-011. It requires one
explicit trusted workspace argument, lazily constructs only `GitSubjectStateAdapter`, performs no
service connection or ledger write, and renders only the bounded structural capture result. JSON
and human output contain format/status, the two final digests when complete, closed limitations,
and bounded counts—never workspace path, filename, source/diff bytes, Git identity/output, or
component digests. Supported untracked regular files are always included; exceeding the bound
returns no comparable state rather than a stronger but partial tree claim.

`privacy setup` is preset-first for convenience but answer-first for authority. It offers
`private`, `metadata-only`, `assisted-review` (recommended only for an eligible exact endpoint),
`expanded-review`, and `custom`. Selecting a preset expands it into the thirteen setup questions;
the CLI prints the exact privacy profile, review-context profile and compiled selector,
provider/model/endpoint, editable current-data-use runtime guard and versioned evidence posture,
scope, categories/classes, source-selection behavior, exact agent-context categories/classes,
byte/token ceilings, preview policy, and never-send exclusions before proposing a
change. The user may edit any value. A preset choice alone commits nothing and never bypasses the
trusted-local widening decision.

`assisted-review` creates a standing workspace policy, so normal checks, automatic retries,
reviewer findings, agent responses, and rechecks do not prompt a human. `--preset` is accepted only
on interactive setup and never on ordinary workflow commands; noninteractive use must provide and
review the complete typed draft through the trusted control contract. `confirm_every_request`
remains separately selectable when the user wants one foreground decision per physical attempt.
The CLI labels current provider data-use records as declared/evidence-bound posture, never as a
technical guarantee, and removes the recommendation when that record is known-broad, stale, or
unknown.
The standard verification default is `semantic_if_configured`; after the selected provider and
standing policy are active, ordinary checks invoke review without another hidden mode switch.
Users can still choose deterministic-only or semantic-required behavior explicitly.

`service unlock` and the distinct first-install-only `service initialize-passphrase` delegate to
`cli/unlock.md`. The latter is available only for a pristine uninitialized vault, confirms the
passphrase twice locally, and sends one `vault_initialize` confidential value; it is never offered
as fallback/reset for an existing keyring/passphrase vault. There is no passphrase/password/secret flag,
environment/config/stdin/file option. Provider credential/recovery/reauthorization ceremonies use
the YZH1 human-control plus YZS1 one-secret helper family, not normal request parsing. Provider
set/rotate arguments contain only exact nonsecret profile/scope/purpose identifiers; credential and
reauth bytes never enter Typer values. Boolean confirmation never
authorizes privacy-policy loosening; fresh vault reauth/OS user presence does.

JSON stdin/flags remain available for ordinary non-secret requests. Diagnostics go to stderr;
normal JSON preserves exact envelopes. Connection loss never implies cancel/commit; retry preserves
the identical operation request ID.

## Errors and edge cases

- Usage/input shape → exit 2; conflicts 10; pending 11; service/storage/vault unavailable 20;
  provider 30; corruption 40; sanitized defect 70; interrupt 130, using the exhaustive per-code
  table in `cli/exits.md`.
- Non-TTY commands never prompt or hang. Unlock specifically opens the controlling TTY and rejects
  absence; it never falls back to stdin. Idle-relock policy change has the same foreground/TTY
  requirement and accepts no `--yes`, piped decision, ordinary JSON request, or MCP route.
- Findings/incomplete semantic checks still exit 0 when deterministic operation completed.
- `mcp serve` emits protocol frames only on stdout.
- `state capture` rejects non-Git/unsafe/ambiguous/changed/over-limit input with no digest and never
  falls back to a described state or a previous result.

## Invariants

1. CLI normal path imports only client/protocol/render code, not application/storage/key/provider
   composition.
2. Service absent/locked never causes direct execution or secret prompt in an ordinary operation.
3. No secret appears in argv/env/config/stdin/history/output/logs.
4. CLI and MCP preserve identical public operation semantics through one service.
5. No built-in service-manager install/start command or hidden spawn exists in v0.1.
6. CLI presets are editable draft macros, not policy authority, and the fail-safe installation seed
   remains zero-egress until a user commits a different policy.
7. The only ordinary client-local repository read is ADR-011 `state capture`; it is read-only,
   network-free, content-withholding, and imports no trusted service/application composition.
8. Idle-relock mutation is reachable only as the exact `idle_relock_policy_change` YZH1/YZS1
   ceremony; its nonsecret target argument grants no authority and is never an ordinary-control or
   MCP method.

## Tests

- `tests/subprocess/test_cli_invocations.py` covers command tree and service client behavior.
- Privacy CLI snapshots cover all five recipe expansions, exact thirteen-answer review, eligible/
  stale provider data-use posture, no-prompt assisted checks, and high-ceremony confirmation mode.
- `tests/subprocess/test_service_unlock_boundary.py` covers TTY-only confidential input.
- That suite also covers `service idle-relock` target grammar, preview, approve/deny,
  OS-presence/passphrase authorization, generation scope, and forbidden ordinary/MCP routes.
- `tests/conformance/surfaces/test_cli_mcp_parity.py` covers exact operation parity.
- `tests/subprocess/test_setup_wizard_cli.py` covers the `setup`/`integrate mcp` wiring and the
  non-TTY bare-invocation help fallback; `tests/conformance/surfaces/test_cli_contract_matrix.py`
  freezes `setup` in the support-command matrix.
- `tests/subprocess/test_cli_menu.py` covers the `menu` command TTY gate and the bare-TTY
  menu dispatch (ADR-013).
- `tests/packaging/test_service_boundary_imports.py` covers import trust boundary.
- `tests/subprocess/test_cli_invocations.py` covers state capture, dirty/staged/untracked changes,
  no-content/path output, caps, changing input, and zero Git/ledger mutation.

## Open questions

None.
