# tests/subprocess/test_cli_invocations.py — installed CLI command and input matrix

**Wave:** D/F | **ADRs:** ADR-002, ADR-007 | **Imports (spec-tree):**
CLI/application specs, `specs/tests/subprocess/helpers/child.py.md` | **Imported by:** PR and release
subprocess gates

## Purpose

Prove the installed executable exposes the intended command tree and accepts inputs through the
documented channels without a source checkout, TTY assumption, interactive prompt, or ambient config.

## Public surface

Pytest cases cover root/per-command help; `version`; six public operations; support commands
`import`, `review`, `backup`, `restore`, `migrate`, `integrate`; input file/stdin/inline modes; and
invalid invocation. Human-mode cases explicitly cover status gap wording, the three-finding display
cap with stable ordering/suppressed count, and receipt wording no stronger than its canonical
document. Parameter IDs are stable command/scenario labels.

## Behavior

Build a clean private bundle through the CLI. Invoke each command from an unrelated cwd using the
installed console script, isolated HOME/app data, non-TTY pipes, fixed IDs/clock fixtures, and strict
local profile. Exercise canonical valid request, omitted required input, unknown/duplicate flag,
unknown command, `--input PATH`, `--input -`, empty stdin, oversized input, invalid UTF-8/JSON,
duplicate JSON key, and forbidden extra field.

For six operations, compare JSON mode to application golden results: IDs, frontier/digest, coverage,
findings, limitations, and idempotent retry. Human mode compares exact structural snapshot with only
installed executable path normalized. The status snapshot preserves every declared gap, finding
snapshots show exactly the top three in canonical order plus the suppressed count, and receipt text
cannot strengthen coverage/conclusion or hide redaction/limitations from the JSON document. Support
commands assert consent/dry-run/overwrite boundaries and that no hidden public
`doctor`/release-probe command appears.

The oracle includes exit, exact stdout/stderr bytes, bundle frontier before/after, created files,
and network-denial observation. Findings do not make a successful operation fail. Invalid input
cannot create/advance a catalog/bundle.

## Errors and edge cases

- No invocation may prompt when stdin is not a TTY or echo input/path/exception/traceback.
- Missing key/provider/storage states map to bounded documented errors.
- Input path symlink/traversal/owner-permission cases fail before read or mutation.
- A human render that reorders findings, exceeds the cap, drops the suppressed count/gap, or uses
  stronger receipt language fails even when the structured result is valid.
- Unsupported platform cells are explicit; advertised platforms cannot skip.

## Invariants

1. Tests execute only the installed entry point.
2. Every mutating command is identity-bound and retry-safe.
3. Invalid invocation has no durable side effect.
4. Strict-local command tests make no network call.
5. Human rendering is a bounded projection of structured truth and never strengthens it.

## Tests

The file is run directly in installed-wheel PR/release jobs. Fixtures seed one complete workflow,
one finding, one import gap, one backup, and invalid byte/path tables; snapshots are public and
canary-scanned.

## Open questions

None.
