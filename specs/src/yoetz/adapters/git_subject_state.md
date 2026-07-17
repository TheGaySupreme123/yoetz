# src/yoetz/adapters/git_subject_state.py — bounded local Git structural-state capture

**Wave:** D | **ADRs:** ADR-005, ADR-009, ADR-011 | **Imports (spec-tree):**
`ports/subject_state.md`, `domain/values.md`, `protocol/canonical.md` | **Imported by:** CLI
state-capture support and installed-artifact capability/subprocess tests

## Purpose

Implement `SubjectStateCapturePort` for one explicit local Git worktree while withholding all
repository content from its result. This adapter supplies trustworthy comparable digest mechanics;
it is not a repository browser, evidence fetcher, or observation hook.

## Public surface

- `class GitSubjectStateAdapter(SubjectStateCapturePort)` with `capture(command)`.
- `GIT_SUBJECT_STATE_FORMAT = "yoetz.git-subject-state/1"`.
- Adapter-local `GitStateComponents(object_format, head_state, index_digest,
  worktree_digest, untracked_digest)` used only as canonical hash input and never serialized to the
  caller.
- `open_local_workspace(path) -> LocalWorkspaceHandle` — CLI-only descriptor-safe validation for
  one explicit trusted root; the handle cannot be logged, serialized, or persisted.

## Behavior

Open the explicit root with no-follow descriptor checks, require that Git resolves the same root,
and reject root/home ambiguity, traversal, nested-root substitution, unsafe ownership, submodules,
or unsupported symlink cases. Invoke Git through fixed argv with `shell=False`, a minimal sanitized
environment, external diff/textconv/fsmonitor disabled, optional locks disabled, and bounded stdout/
stderr pipes. Never honor aliases, hooks, pager, editor, credential helper, or network transport.

Capture a stable pre-snapshot identity, then stream these components into SHA-256 hashers without
retaining or returning their bytes:

1. repository object format and HEAD object ID, or the fixed `unborn` marker;
2. canonical binary staged delta relative to HEAD;
3. canonical binary tracked-worktree delta relative to the index;
4. the NUL-sorted Git-ignored-aware untracked inventory, hashing each safe regular file's relative
   path, mode, length, and bytes under the global file/byte caps.

Tracked Git diff bytes may contain source content; they exist only in bounded in-memory streaming
buffers feeding the hasher and are overwritten/released immediately after use. No component,
filename, diff, path, Git output, or file digest is returned, logged, persisted, or published. The
adapter recomputes the pre-snapshot identity after hashing. Any change, Git failure, unsafe entry,
submodule, unsupported symlink, special file, or cap breach discards all candidate digests and
returns a closed no-state result.

`diff_digest` is SHA-256 over restricted canonical JSON containing the format token plus the three
delta component digests. `tree_digest` is SHA-256 over restricted canonical JSON containing the
format token, object-format token, HEAD state, and `diff_digest`. The resulting
`SubjectStateRef` therefore compares complete supported state while disclosing none of its
components. It is a Yoetz digest, not a Git tree OID.

The adapter performs no Git mutation: no add, update-index, write-tree, checkout, clean, refresh,
commit, lock acquisition, or config write. It opens no network and reads no repository outside the
validated root. A complete capture can support `content_digest` evidence immutability, but the
event's publication/authorship/artifact-observation coverage remains whatever the actual channel
earned.

## Errors and edge cases

- Non-Git, bare, linked/ambiguous root, unsupported object format, submodule, symlink/special file,
  unsafe owner/mode, oversized output/file set, permission error, process timeout, cancellation, or
  state change returns no digest and one or more closed limitations.
- Git stderr is discarded after mapping to a closed reason and never appears in output.
- A malicious repository cannot select an executable, helper, filter, hook, pager, or external
  diff through config.

## Invariants

1. Same supported stable state and format produces the same two digests.
2. A material supported tracked, staged, HEAD, or included-untracked change changes `tree_digest`.
3. No repository content, path, filename, component digest, or Git output leaves the adapter.
4. Capture is read-only, bounded, local, and network-free.
5. Any ambiguity weakens to no observed state; it never fabricates equality.

## Tests

- Unit/property fixtures cover canonical component ordering, same/different states, unborn HEAD,
  untracked inclusion, caps, and deterministic environment controls.
- Subprocess fixtures cover installed CLI capture, dirty/index/untracked changes, process timeout,
  changing input, malicious Git config/helpers, symlink/special-file/submodule rejection, stdout/
  stderr canaries, and zero mutation.
- Capability evidence binds exact Git/OS/package identities and proves path/source canaries never
  appear in JSON, human output, logs, receipts, or ledger state.

## Open questions

None.
