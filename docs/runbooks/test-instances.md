# Independent test instances

This runbook is for contributors who want to run one Yoetz for real coding while developing and
testing Yoetz on the same machine, and for CI jobs that need throwaway instances. It builds on the
isolated-root contract (ADR-026) and the instance identity contract (ADR-028, issue #604).

Vocabulary:

| Lifecycle | What it is | How it resolves its roots |
|---|---|---|
| `permanent` | The everyday installation (`uv tool install yoetz` or the documented install path). | Ambient platform directories; no marker, no pin. |
| `persistent` | A development instance you keep across runs. | Its own isolated root, sealed by `instance-identity.json`; usually pinned into its runtime. |
| `disposable` | A test snapshot built from one exact revision with a bounded lifetime. | Same as persistent, plus `expires_at`; disposed after the run. |

A *snapshot* is the tested code and runtime. It never clones the permanent vault or user data;
every instance has a fresh vault, config, ledger catalog, locks, endpoints, cache, and logs.

## Prerequisites and constraints

- macOS arm64 or Linux x86-64 (the certified cells), `uv`, `git`, and a Python the runtime can
  use (the script defaults to the interpreter running it).
- A short, owner-private base directory outside every repository and outside shared temp, for
  example `~/.yz-instances`. Unix control sockets bind beneath each instance root, so the whole
  path `<base>/<tag>/state/run/secret-ingress.sock` must stay under 100 bytes;
  `yoetz instance create` refuses a longer root (`instance_root_too_long`). The root must not be a
  symlink, must be owned by you, and must not sit under `/tmp` (macOS resolves it through a
  symlink and the runtime refuses it as `path_contains_symlink`).
- Nothing here needs, reads, or writes the maintainer's workstation paths, credentials, host
  configuration, or vault. Do not point an instance at the everyday install's directories.

## Create a snapshot from the code under test

From a checkout (a linked worktree is fine):

```text
uv run python scripts/provision_test_instance.py create --base ~/.yz-instances --tag pr604 \
    --lifecycle disposable --expires-in 8
```

What happens, in order:

1. The exact revision is resolved (`--revision` defaults to `HEAD`). A modified working tree is
   refused unless you pass `--allow-dirty`, which records `source_state: modified`; a non-HEAD
   revision is exported with `git archive` and built from that clean tree.
2. `uv build --no-sources` produces the wheel into `<base>/<tag>/dist/`; its SHA-256 becomes the
   instance's `package_digest`.
3. `uv venv` plus `uv pip install <wheel>` creates `<base>/<tag>/runtime/`, a runtime holding
   only that wheel.
4. The snapshot's own launcher runs `yoetz instance create --root <base>/<tag>/state --lifecycle
   ... --source-ref <sha> --package-digest <sha256> --bind-runtime`, which creates the root
   (owner-only), seals `state/instance-identity.json`, and writes `yoetz-instance-pin.json` into
   the runtime's virtual environment.
5. `<base>/<tag>/provenance.json` records the tag, lifecycle, installation id, source revision and
   state, package version and digest, and digest-only identities of the runtime and root.

The command prints the launcher path and the environment export. Because the runtime is pinned,
`<base>/<tag>/runtime/bin/yoetz` resolves that root even when `YOETZ_ISOLATED_ROOT` is unset or
dropped by a host, hook, or shell; exporting the variable to the same root is fine, and exporting
it to a different root is refused (`isolation_root_conflict`) rather than obeyed.

Verify before use:

```text
<base>/<tag>/runtime/bin/yoetz instance status --json
<base>/<tag>/runtime/bin/yoetz service isolation --json
```

Expect `mode: isolated`, `binding: runtime_pin` (or `environment_and_pin`), the requested
`lifecycle`, the exact `source_ref`, `runtime_provenance: matched`, and identity digests that
differ from the everyday install's own `yoetz service isolation --json`. The report is
digest-only; only `create` echoes the exact root, once, for local review.

## Run the snapshot and tests against it

Start its service in the foreground for a test, or let the first control command start it on
demand:

```text
<base>/<tag>/runtime/bin/yoetz service run
<base>/<tag>/runtime/bin/yoetz service status --json
```

The service refuses to start, with a bounded token and exit 20, when the instance is past its
expiry (`instance_expired`), when the pin and marker disagree (`installation_identity_mismatch`),
or when a labeled marker sits in ambient state (`instance_lifecycle_requires_isolated_root`).

Host integration against a snapshot follows the host runbooks; register the snapshot's absolute
launcher (never a bare `yoetz`) and, for Codex external registration and Cursor native artifacts,
the root binding those hosts already carry. The everyday install keeps its own registrations
untouched.

Ordinary source tests do not need an instance: `uv run pytest <path>` from the checkout uses the
checkout's `.venv`, which is unpinned. Provision an instance when a test or dogfood must exercise
an installed launcher, a real service, a host registration, or the upgrade path.

Advancing the checkout does not change a running snapshot: the runtime holds the wheel that was
built, and `instance status` reports `runtime_provenance: drifted` if the runtime prefix or
package version the marker recorded no longer matches. Create a new snapshot for newer code; do
not reinstall into an existing one.

## Concurrency

- One tag per worker or job. Two workers must never share a `<base>/<tag>`; the second `create`
  is refused (`instance_exists`) and the second service start is refused
  (`service_already_running`) because the root's singleton is already held.
- Snapshots can run at the same time as each other and as the everyday install; their locks,
  endpoints, storage, config, cache, and logs are disjoint by construction. The packaged
  regression `tests/packaging/test_instance_lifecycle.py` proves one everyday-equivalent install
  plus two snapshots from two revisions concurrently.
- `tests/packaging` itself still shares install roots under `~/.yz-*` and must not run
  concurrently with another pytest run; that restriction is unchanged.

## Dispose

```text
uv run python scripts/provision_test_instance.py dispose --base ~/.yz-instances --tag pr604 \
    [--retain-logs ~/.yz-instances/retained]
```

The snapshot's own launcher runs `yoetz instance dispose --root <base>/<tag>/state`: it probes
the root's singleton lock, sends the stamped holder (and nothing else) a bounded stop, waits up to
the drain window, optionally copies the log files to `<retain>/<installation_id>/`, removes the
root, and removes the pin that named it. The script then removes the runtime and wheel. Repeating
`dispose` reports `absent` and exits 0. `dispose` refuses roots without a persistent or
disposable marker (`instance_not_disposable`): it cannot remove the everyday install or an
unlabeled ADR-026 root. If a holder does not release within the window
(`instance_service_running`), the root is left in place; stop that runtime's service and retry.

Expiry is a serving limit, not a sweeper: an expired snapshot refuses to start until disposed.
Disposal deletes files; it is not secure erasure.

## Recovering a workstation whose everyday service was replaced

Symptom: every everyday `yoetz` command answers `service_incompatible` and points at
`yoetz service restart`, after a test runtime reached the ambient endpoint and superseded the
everyday service (the diagnostics ring shows `control_handshake`/`service_incompatible` followed
by `service_supersede`).

Recovery, on a local terminal, using the everyday install by absolute path:

```text
~/.local/bin/yoetz service restart
~/.local/bin/yoetz service status --json
```

Then make sure host registrations name the everyday launcher by absolute path, and provision test
runtimes with `--bind-runtime` (the script always does) so they can never resolve ambient state
again.
