# src/yoetz/adapters/integrations/codex_mcp.py — Codex MCP registration adapter

**Wave:** D | **ADRs:** ADR-010, ADR-012 | **Imports (spec-tree):**
`specs/src/yoetz/ports/harness_mcp.py.md`, `specs/src/yoetz/ports/integrations.py.md`,
`specs/src/yoetz/protocol/canonical.md` | **Imported by:** `specs/src/yoetz/cli/setup.py.md`

## Purpose

Implements `HarnessMcpPort` for the Codex CLI by automating exactly the runbook's manual
sequence — `codex mcp get yoetz --json` first, `codex mcp add yoetz -- yoetz mcp serve` only when
no entry exists — with bounded subprocesses and no force path. Automation changes no runbook
rule; it runs the same two commands a human would, behind the same preview/consent gate.

## Public surface

- `CommandOutput` — frozen `(exit_code: int, stdout: bytes)` result of one bounded invocation.
- `CodexMcpAdapter` — constructor takes an optional `runner: (tuple[str, ...]) -> CommandOutput`
  injection seam (default: `subprocess.run` with `shell=False`, stdin devnull, captured output,
  10-second timeout, stdout truncated to 64 KiB); implements `status_registration`,
  `preview_registration`, `apply_registration`.

## Behavior

Classification of a `get` result: nonzero exit → `absent`; exit 0 with strict-UTF-8 JSON object
whose command tokens (a `command` string plus optional string `args`, or a `command` string
list) end with exactly `("yoetz", "mcp", "serve")` → `yoetz_owned`; any other readable object →
`foreign_present`; undecodable/non-object JSON → `McpRegistrationError(parse_failed)`. The
default runner maps timeout to `timeout` and OS/spawn failure to `harness_unavailable`.

`preview_registration` classifies state, selects `register` for `absent` and `noop` otherwise,
attaches the `foreign_entry_present` warning for a foreign state, and computes `preview_digest`
as the canonical digest of
`{action, executable_path, harness, schema: "yoetz.mcp-registration-preview/1", serve_command,
server_name, state_before}`.

`apply_registration` requires `explicitly_accepted` (else `confirmation_required`), recomputes
the preview and compares digests (mismatch → `preview_stale`), refuses a foreign entry
(`foreign_entry_present`), returns a no-op result for `yoetz_owned`, and otherwise runs the
`add` command; a nonzero add exit raises `registration_failed`. Success is then verified by
re-running `get` — only a verified `yoetz_owned` state yields the `register` result; anything
else raises `registration_failed` with the verified state token.

## Errors and edge cases

- Every failure is a typed `McpRegistrationError` whose `safe_details` carry at most a reason
  token and an exit-code class or verified-state token — never raw stdout/stderr, paths, or
  configuration content (runbook security rule).
- Non-Codex binaries are rejected with `harness_unavailable` before any subprocess runs.
- An entry whose command cannot be read is conservatively `foreign_present`: preserved, never
  replaced.

## Invariants

1. The only mutating invocation is `codex mcp add yoetz -- yoetz mcp serve`, and it runs at most
   once per apply, only from a verified `absent` state.
2. A foreign same-name entry is never replaced; there is no force flag.
3. The add exit code is never trusted as success; state is re-read.
4. All subprocess use is `shell=False` with bounded timeout and bounded output.

## Tests

- `tests/unit/adapters/test_codex_mcp_registration.py` — all three states, both entry shapes,
  parse failures, acceptance/digest gates, verify-by-reread success and failure, foreign
  refusal before any `add`, and the owned no-op, all through a scripted runner.

## Open questions

None.
