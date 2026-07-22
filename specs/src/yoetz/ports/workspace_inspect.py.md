# src/yoetz/ports/workspace_inspect.py — descriptor-safe bounded artifact inspection

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):** `ports/subject_state.md`,
`protocol/errors.md` | **Imported by:** workspace inspect adapter, observation advice application

## Purpose

Own the port for reading bounded, relative-path artifacts under a consented workspace root. The
port returns structural digests and size-capped excerpts only. It never returns absolute paths,
follows escaping symlinks, or loads oversized content into advice/status surfaces.

## Public surface

- `WorkspaceInspectPort.inspect(WorkspaceInspectCommand) -> WorkspaceInspectResult`
- Closed types: `WorkspaceInspectStatus`, `WorkspaceInspectLimitation`, `InspectedArtifact`,
  `WorkspaceInspectCommand`, `WorkspaceInspectResult`
- Caps: `MAX_INSPECT_FILES`, `MAX_INSPECT_EXCERPT_BYTES`, `MAX_INSPECT_PATH_BYTES`

## Behavior

Commands require a validated `LocalWorkspaceHandle` and one or more relative paths (no `..`, no
absolute roots). Results are `inspected|partial|rejected` with sorted closed limitations. Public
status and advice must never embed absolute filesystem paths from this port.

## Errors and edge cases

Fail closed on consent, mapping, and validation errors; never leak secrets.

## Invariants

1. Relative paths only in public results.
2. Symlink escapes and out-of-scope paths fail closed.
3. Excerpts are size-capped; digests cover full read bytes.

## Tests

Unit coverage for relative success, path escape rejection, symlink escape, and absolute-path
absence in results.

## Open questions

None.
