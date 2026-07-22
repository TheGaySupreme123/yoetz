# src/yoetz/adapters/workspace_inspect.py — local bounded workspace inspection

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):** `ports/workspace_inspect.py.md`,
`ports/subject_state.md` | **Imported by:** observation advice wiring and unit tests

## Purpose

Implement `WorkspaceInspectPort` for one explicit local workspace root opened via
`open_inspect_workspace`. Reads only relative regular files under the validated root.

## Public surface

- `LocalWorkspaceInspectAdapter`
- `open_inspect_workspace(path) -> LocalWorkspaceHandle`
- `WORKSPACE_INSPECT_FORMAT`

## Behavior

Open the root with no-follow directory checks. Reject symlink escapes, out-of-scope joins,
oversized files, and non-files. Return relative-path digests plus capped excerpts. Selection
digest is canonical over relative path + content digest + length metadata.

## Invariants

1. No absolute paths in results.
2. No network.
3. Fail closed on unsafe roots.

## Tests

`tests/unit/adapters/test_workspace_inspect.py`
