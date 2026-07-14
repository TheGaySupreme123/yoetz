# tests/subprocess/helpers/child.py — isolated installed-executable process harness

**Wave:** C–F | **ADRs:** ADR-001, ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/tests/subprocess.md` | **Imported by:** every subprocess test and sibling frame/fault helpers

## Purpose

Own safe child creation, byte-exact stream capture, resource bounds, and process-group cleanup.
Tests use this helper so they cannot accidentally invoke a checkout import, inherit a maintainer's
HOME/secrets, deadlock on pipes, or kill an unrelated process.

## Public surface

- `ChildSpec`: immutable executable/argv/stdin/env-overlay/cwd/limits description.
- `ChildHandle`: process ID/group, stdin/stdout/stderr descriptors, start time, temp-root marker.
- `ChildResult`: exact stdout/stderr bytes and digests, exit/signal, duration, limit verdict.
- `spawn_installed(spec, artifact_env) -> ChildHandle`.
- `communicate_bounded(handle, input_bytes=b"") -> ChildResult`.
- `signal_child(handle, signal)`, `close_stdin(handle)`, `terminate_owned_group(handle)`.
- `assert_no_source_import(result, checkout_root)` and `assert_no_owned_children(temp_root)`.

## Behavior

Resolve `yoetz-core` or the selected Python from the isolated artifact environment and reject a path
inside the source checkout. Construct a minimal allowlisted environment: isolated HOME/XDG/Yoetz
data/temp, UTF-8/C locale, UTC, fixed hash seed, explicit test marker, and only named test variables.
Remove provider/package credentials, Python path/startup hooks, proxy variables, and ambient config.

Create a new process group/session. Open stdin/stdout/stderr as binary pipes and drain both outputs
concurrently into capped byte buffers while computing full stream digests. Exceeding an output,
memory, disk, descriptor, child-count, or wall-time cap terminates only the owned group and returns a
failing limit verdict; truncation never becomes a successful oracle. Record exact bytes before any
parser normalizes them.

On POSIX, verify group ownership through the unique temp-root token before signaling. Cleanup sends
the expected graceful signal, waits a bounded interval, escalates only within the group, closes
descriptors, and proves no marked descendant remains. Platform-specific process accounting is
enabled only on certified macOS arm64 and glibc Linux x86-64.

## Errors and edge cases

- Executable/path/source-import mismatch, ambient secret, unsupported platform, spawn failure, or
  incomplete cleanup fails the test before product assertions.
- Broken pipes and child exit during input are returned as observations, not helper exceptions.
- Hostile child bytes are never decoded for cleanup/error messages.
- A timeout is a harness failure with bounded metadata, never evidence of product timeout handling.

## Invariants

1. Every child is attributable to one isolated temp root and process group.
2. Exact streams are retained/digested before parsing.
3. No production/user environment or checkout module is consumed.
4. Cleanup never targets by broad process name or outside the owned group.

## Tests

Helper self-tests use children that exit, fork, hang, flood either stream, close pipes, ignore
SIGTERM, and reveal selected environment keys. They prove caps, concurrent draining, escalation,
source-import rejection, and no orphan. Synthetic canaries must not appear in helper diagnostics.

## Open questions

None.
