# ADR-026 — Exact-target isolated-root runtime identity

**Status:** Accepted (2026-09-01), maintainer-authorized in
[issue #518](https://github.com/TheGaySupreme123/yoetz/issues/518); amended 2026-09-03,
maintainer-authorized in [issue #534](https://github.com/TheGaySupreme123/yoetz/issues/534), to
exempt one explicitly passed dedicated Codex evaluator home from the "every artifact lives beneath
the root" reverse state; amended 2026-09-04, maintainer-authorized in
[issue #561](https://github.com/TheGaySupreme123/yoetz/issues/561), to bind the root into
Yoetz-owned external Codex MCP registrations.
**Implemented by:** `src/yoetz/config/paths.py` (`isolated_root()`, `runtime_dir()`,
`ISOLATED_ROOT_ENV`), `src/yoetz/adapters/control/unix_socket.py`, `src/yoetz/config/load.py`,
`src/yoetz/service/client.py`, `src/yoetz/cli/isolation_status.py`, the
`yoetz service isolation` CLI command, `scripts/check_codex_dogfood_parity.py`
(`yoetz.codex-dogfood-parity/3`), `tests/packaging/test_isolated_root_boundary.py`, and
`tests/packaging/test_codex_mcp_isolated_registration.py`.
**Relates to:** ADR-001 (single service/writer authority), ADR-003 (durable storage), and the
[Codex dogfood parity runbook](../runbooks/codex-dogfood.md).

## Context

Every Yoetz identity root previously resolved independently: `storage.data_dir` (and
`YOETZ_STORAGE_DATA_DIR`) relocated only the storage bundle, while the platform `state_dir()` kept
owning the service singleton lock, service generation, and setup markers, and the platform runtime
directory kept owning the control endpoints. A dogfood or test environment could therefore hold an
isolated Codex home, candidate wheel, plugin tree, and storage bundle and still transparently
connect to — and write through — the live user's Yoetz singleton. In a real run, a repository
privacy grant intended for a disposable dogfood environment committed into the normal Yoetz
state, and the parity preflight had no facet that could expose the shared service identity.

Internal test isolation existed only as unsupported mechanisms: monkeypatched path functions in
unit tests and whole-`HOME` overrides in packaging tests. A `HOME` override is not an exact-target
contract — it drags the host's Codex home, keychain-adjacent state, and every other `HOME`-derived
identity along with it, and nothing validates or proves it.

## Decisions

1. **One isolation contract, one variable.** `YOETZ_ISOLATED_ROOT` (owned by
   `yoetz.config.paths`) is the sole supported way to isolate a Yoetz runtime. When set, every
   identity root derives from the single root: config `<root>/config/config.toml`, default
   storage bundle `<root>/data`, state `<root>/state` (service lock, generation, setup markers),
   runtime endpoints `<root>/run` (control, secret-ingress, human-control sockets), cache
   `<root>/cache`, and logs `<root>/log`. There is deliberately no pile of per-subsystem
   overrides: partial mechanisms (`YOETZ_STORAGE_DATA_DIR`, `storage.data_dir`) remain storage
   relocation, never isolation.
2. **Fail closed, never fall back.** A set but unusable root raises
   `PathSafetyError("isolation_root_invalid")` (empty, relative, missing, or not a directory) or
   the precise existing path-safety reason (symlink component, shared temp, repository, sync
   folder, network filesystem, foreign owner, broad permissions) from the same
   `verify_private_local_bundle` gate that guards every private directory. No code path resolves
   to an ambient platform directory while the variable is set. At the endpoint layer this
   surfaces as the bounded `runtime_directory_unsafe` transport reason.
3. **The endpoint follows the root.** `runtime_dir()` in `config/paths.py` now owns the runtime
   endpoint directory and `adapters/control/unix_socket.py` consumes it, so an isolated runtime
   binds and connects only beneath its root. Not sharing an endpoint (or lock, which already
   derives from `state_dir()`) is what makes reaching the ambient singleton structurally
   impossible rather than merely discouraged.
4. **The variable is paths authority, not configuration.** `config/load.py` recognizes
   `YOETZ_ISOLATED_ROOT` as mapping to no configuration leaf (like `YOETZ_TUI`), and
   `service/client.py` forwards it to the detached service process it spawns, so an isolated
   client can never start a service whose state lands in the ambient install. An explicit
   `storage.data_dir` still wins over the isolated default `<root>/data`; the parity gate, not
   silent rejection, is what exposes an override that reaches shared storage.
5. **Isolation is proved from two exact target snapshots.** `yoetz service isolation [--json]`
   resolves — locally, without connecting to a service or opening a ledger — only the identity
   roots of the executable and environment that invoked it. Dogfood captures one report from the
   exact normal executable/config environment and one from the exact isolated candidate. The gate
   compares those reports; it never substitutes platform defaults for the normal target, because
   its config or storage may be relocated. Each report contains canonical path-identity digests,
   never raw paths. The command is CLI-only; MCP and hooks inherit isolation through environment.
6. **The host-launched external Codex MCP child keeps the same identity.** In ambient mode the
   Yoetz-owned registration has no environment block. In isolated mode preview and apply bind
   exactly one allowed entry, `YOETZ_ISOLATED_ROOT=<validated-root>`, into `codex mcp add`; the
   preview digest covers the exact root, and post-apply observation re-reads both argv and the
   environment block. Missing or different roots are Yoetz-owned but require re-registration;
   unknown keys, inherited-variable declarations, malformed roots, or any other environment
   shape are foreign and never overwritten. Status reports only the closed binding state
   (`ambient|isolated_exact|missing|different`), while preview shows the exact proposed root for
   local review. This exception is limited to external Codex registration; plugin-managed routes
   retain their own host-specific environment contracts.
7. **The dogfood parity gate fails closed on shared or unlaunched identity.** Report schema
   `yoetz.codex-dogfood-parity/3` retains the `service_isolation` preflight facet, the
   `identity.yoetz_isolation` digest block, and the `observed.yoetz_isolation_state` closed state
   (`isolated|shared|ambient|unknown`). The facet can pass only when the observed state is
   `isolated`, the candidate mode is `isolated`, the normal mode is `ambient`, and every resolved
   state/endpoint/storage/config/executable digest differs from its exact normal-target counterpart;
   any equality, wrong mode, or unknown
   state is rejected or fails preflight, and a non-pass row must carry the
   `provision_isolated_yoetz_root` continuation. It adds `mcp_child_isolation`, which can pass only
   when registration status is `yoetz_owned`, its binding is `isolated_exact`, and a pre-model
   Codex app-server inventory starts the registered child successfully. A non-pass row carries
   `reregister_isolated_mcp`. Version 1 and 2 reports are no longer accepted.

## Reverse states and rollback

Unsetting `YOETZ_ISOLATED_ROOT` restores ambient resolution with no residue: every artifact an
isolated runtime creates lives beneath the root, so deleting the root is complete rollback and
cannot touch unrelated user state. One explicit exemption (issue #534): a dedicated Codex
evaluator home that the operator passes by path to `yoetz provider codex-subscription setup
--codex-home` may live outside the root and be reused across runs. It holds Codex-owned OAuth
state plus the exact Codex `config.toml` Yoetz writes into it, and no Yoetz identity, state,
endpoint, or storage, so deleting the root still removes every Yoetz identity artifact and the
parity gate still fails on any shared Yoetz identity. That home is reverse-stated by
`yoetz provider codex-subscription disconnect` — Codex logout plus binding removal — and then by
the operator deleting the directory they provisioned; root deletion is not its rollback. The
default evaluator home (no explicit path) stays beneath the root. The packaged regressions lock
both boundaries: `test_isolated_root_boundary.py` proves an isolated service run leaves the ambient
home tree byte-identical before/after, an ambient client cannot reach the isolated singleton, and
removing the root removes every trace; `test_codex_mcp_isolated_registration.py` uses installed
Codex 0.150.1 to launch a registered child from an ambient parent and proves that only the reviewed
registration supplied the exact root.

## Consequences

- Dogfood and test environments get a natively separate temporary Yoetz whose service singleton,
  storage, config, and endpoints cannot reach the live install, with a preflight that proves it.
- The isolation root must be provisioned deliberately (existing, owner-only, symlink-free,
  outside shared temp and repositories) before launch; there is no auto-creation, so a typoed
  root is an error instead of a silently different target.
- Existing unsupported isolation mechanisms remain internal test details; new tooling must use
  this contract.
