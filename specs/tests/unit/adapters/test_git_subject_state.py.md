# tests/unit/adapters/test_git_subject_state.py — bounded Git structural-state capture

**Wave:** D | **ADRs:** ADR-005, ADR-009, ADR-011 | **Imports (spec-tree):**
`src/yoetz/adapters/git_subject_state.md`, `src/yoetz/ports/subject_state.md` |
**Imported by:** unit suite

## Purpose

Freeze the client-local Git adapter's comparable digest behavior, path and process safety, bounded
resource use, closed failure mapping, and content-withholding result boundary. The executable tests
use disposable local repositories only; the adapter commands under test remain fixed, read-only,
non-network Git operations.

## Public surface

Tests construct `LocalWorkspaceHandle` values only through `open_local_workspace`, invoke
`GitSubjectStateAdapter.capture(SubjectStateCaptureCommand)`, and inspect only the closed
`SubjectStateCaptureResult`. No private component, repository path, filename, diff, Git output, or
source bytes are asserted through a public return value.

## Behavior

Cover clean, tracked-worktree dirty, staged, ignored-aware untracked, and unborn-HEAD states. The
same stable state repeats both public digests, while each material HEAD/index/worktree/included-
untracked change changes `tree_digest`. Tests freeze the two-pass stable-snapshot check, exact file
and byte caps, malicious external-diff/config suppression, read-only index/ref preservation, and
the fact that result rendering contains none of the path/content canaries used by the fixture.

## Errors and edge cases

Submodule gitlinks, tracked or ignored symlinks, special files, nested or linked worktree roots,
unsafe root aliases, file/byte cap breaches, changed input, cancellation, and process timeout all
return or raise only their bounded contract outcome. Every no-state result discards candidate
digests and reports zero hashed counts. The tests make no network request and never use the adapter
to mutate Git state.

## Invariants

1. Complete stable supported state produces both `tree_digest` and `diff_digest`.
2. Partial, unsafe, racing, unsupported, and over-limit state produces no `SubjectStateRef`.
3. Repository content, names, paths, Git stdout/stderr, and component digests never cross the port.
4. The executed adapter argv disables aliases, hooks, external diff/textconv, fsmonitor, optional
   locks, pagers, credential helpers, prompts, and ambient global/system configuration.
5. Test repository setup is disposable; capture itself remains read-only and local.

## Tests

This file is the executable owner for deterministic adapter coverage. Installed-artifact,
subprocess-output, and release capability claims remain in their separately owned suites.

## Open questions

None.
