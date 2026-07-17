# scripts/verify_spec_manifest.py — one-to-one future-file specification and self-containment gate

**Wave:** A/F | **ADRs:** ADR-002, ADR-007 | **Imports (spec-tree):**
`specs/FILE_MANIFEST.md`, `specs/README.md` | **Imported by:** PR CI, tagged release and spec-tree
review/freeze

## Purpose

Make the natural-language-first build contract mechanically complete. The script proves that every
specification file is classified, every future repository file has exactly one owning spec, mirrored
extensions are intentional, family indexes do not masquerade as file owners, required headings are
present, referenced public specs resolve, and implementation does not depend on ignored/private
drafting inputs.

It validates `FILE_MANIFEST`; it never generates, rewrites, sorts, or “fixes” that document. The
manifest remains a reviewed design decision and is created only after the candidate file set is
known.

## Public surface

- `ManifestClass` — `future_file|index_only|coordination`.
- `PathMapping` — `exact_suffix|python_shorthand|markdown_shorthand|repository_projection|none`.
- `ManifestRow` — parsed values: `spec_path`, `classification`, `future_path`, `mapping`,
  `indexed_prefix`, `wave`, `status`, `owner_note`.
- `SpecManifest` — canonical ordered rows plus manifest schema/version.
- `SpecFinding` — bounded `code`, manifest row/spec path and line; no copied spec content.
- `parse_manifest(data: bytes) -> SpecManifest`.
- `inventory_specs(root) -> tuple[str, ...]`.
- `validate_one_to_one(manifest, inventory) -> tuple[SpecFinding, ...]`.
- `validate_extension_mapping(row)`, `validate_headings(row, bytes)`,
  `validate_index_coverage(row, bytes, manifest)`, and
  `validate_public_self_containment(row, bytes, repo_inventory)`.
- `validate_coordination_counts(manifest, manifest_bytes, readme_bytes) -> tuple[SpecFinding, ...]`
  — verifies that the manifest preamble and `specs/README.md` status board report the exact parsed
  owner/index/coordination/scope counts rather than independently maintained arithmetic.
- `main(argv: Sequence[str] | None = None) -> int`.

Command contract:

```text
uv run --locked python scripts/verify_spec_manifest.py --check
uv run --locked python scripts/verify_spec_manifest.py --check --json
```

Default manifest is `specs/FILE_MANIFEST.md`; test-only `--repo-root`/`--manifest` must remain inside
an explicitly supplied synthetic repository. `--check` is required and read-only. Exit `0` means
complete/exact; `1` means drift/invalid/self-containment failure; `2` invocation error.

## Behavior

### Manifest format

Require UTF-8/LF/final-LF Markdown with a single machine-readable section headed
`## File ownership registry`. The first table under it has exact columns:

```text
Spec path | Classification | Future path | Mapping | Indexed prefix | Wave | Status | Owner note
```

Paths and enum cells are backtick-wrapped; an inapplicable value is the Unicode em dash `—` without
backticks. Rows are ASCII-sorted by normalized `Spec path`. Duplicate header/table/row keys, multiline
cells, HTML, escaped pipe in path, unknown column/enum, empty owner note, or noncanonical ordering is
invalid. `Status` is `draft|reviewed|locked`; validation does not upgrade it.

`Spec path` is repository-relative, NFC UTF-8 POSIX, begins `specs/`, names a regular non-symlink
Markdown file, and contains no absolute/traversal/backslash/control/repeated/case-colliding segment.
`Future path` follows the same safety rules but must not begin `specs/`. Every recognized future file
extension is explicit (`.py`, `.sql`, `.json`, `.toml`, `.yml`, `.yaml`, `.lock`, `.md`, `.txt`, or
other extension allowlisted in repository metadata). Extensionless and leading-dot root repository
files are legal only through `repository_projection` and its exact root-path rule below.

### Classification rules

`future_file`:

- has one nonempty `Future path`, non-`none` mapping and `Indexed prefix=—`;
- is the sole owner of that exact future path;
- contains all seven standard headings in order: Purpose, Public surface, Behavior, Errors and edge
  cases, Invariants, Tests, Open questions;
- cannot own a directory, wildcard, family, generated range, or multiple files. Generated resources
  still receive one owner spec each; generation/parity is behavior, not ownership substitution.

`index_only`:

- has `Future path=—`, `Mapping=none`, one safe directory `Indexed prefix` ending `/`;
- contains the seven standard headings and a Public surface inventory of concrete future child files;
- owns no future file. Every recognized file path it lists must resolve to a separate `future_file`
  row/spec, and every manifest future file beneath its indexed prefix must be listed by that index
  unless the owner note names an exact nested index delegation;
- cannot satisfy completeness merely by saying “all tests/resources/files in this directory.”

`coordination`:

- has future/mapping/index values `—|none|—` and is restricted to the exact reviewed coordination
  allowlist: `specs/README.md`, `specs/INTERFACES.md`, `specs/FILE_MANIFEST.md`,
  `specs/OPEN_QUESTIONS.md` plus a future explicitly added by policy;
- owns no implementation/public repository file and cannot be cited as the sole behavioral contract
  for one. Coordination files have their own format; they are not forced into seven headings.

Every discovered regular `specs/**/*.md` file, including the manifest and this script's spec, appears
in exactly one row. No row names an absent spec. Ignore caches/editor/temp files only by rejecting
them as unexpected inventory; `.gitignore` is not a classification mechanism.

### Coordination-count consistency

After parsing and validating the registry, compute exact counts by classification and by the
reviewed repository-scope buckets rendered in `specs/README.md`. Parse the manifest preamble's
owner/index/coordination/total statement and the README status-board summary/table; require each
subtotal, each classification count, the future-owner total, and the final spec-file total to equal
the registry-derived values. The table total is recomputed from its rows rather than trusted as a
second input. A missing, duplicate, malformed, or mismatched summary emits
`SUMMARY_COUNT_DRIFT` with only the coordination-file path and count category, never copied prose.

This is validation, not generation: the script does not rewrite either Markdown file. A manifest
change and its reviewed summary update land together, and CI prevents an arithmetic or stale-copy
error from being described as an authoritative inventory.

### Extension mapping

Validate the declared mapping exactly:

- `exact_suffix`: `spec_path == "specs/" + future_path + ".md"`. This is the preferred unambiguous
  form: future `foo.py` → `specs/foo.py.md`; future `README.md` → `specs/README.md.md`.
- `python_shorthand`: historical Python-only form; future must end `.py` and spec equals
  `specs/` + future with `.py` replaced by `.md` (for example `src/x.py` → `specs/src/x.md`).
- `markdown_shorthand`: historical explicitly declared public-Markdown form; future ends `.md` and
  spec equals `specs/` + future. It is never inferred from filename alone because it collides with
  an index/coordination-looking name.
- `repository_projection`: root-repository file form; future path contains no `/`. For a future
  Markdown file, the spec is `specs/repository/` + the unchanged future path (for example future
  `README.md` → `specs/repository/README.md`); for every other root file, append `.md` (for example
  `LICENSE` → `specs/repository/LICENSE.md` and `package.json` →
  `specs/repository/package.json.md`). It covers explicit root files including extensionless
  `LICENSE`, dotfiles, lock files, and root Markdown. It is invalid for nested paths, directories,
  wildcards, or coordination files and is never inferred.
- `none`: legal only for non-owner classifications.

For one future path exactly one candidate/mapping is present. If both `foo.md` and `foo.py.md` claim
`foo.py`, or a shorthand row omits/misstates mapping, fail. Non-Python/non-Markdown files cannot drop
their extension. A root projection preserves the future filename byte-for-byte before appending the
spec's `.md`; directory `README.md` is not automatically an index and classification decides.

### Index coverage extraction

Within the Public surface section, extract future-looking paths from fenced trees, backtick code spans and
bullet/list literals. Resolve basenames relative to `Indexed prefix`; retain full safe paths already
under it. Reject ambiguous ellipsis/glob/dynamic path as coverage. Compare normalized set with
manifest future-file rows beneath the prefix, subtracting only exact nested index prefixes named by
manifest.

Each listed path must have one future owner row and existing spec; each owned path in scope must be
listed. A family index can describe common behavior but cannot replace child spec headings. This
check catches the original partial state where an index listed `.py` files that had no one-file spec.

### Standard heading and file validation

For every `future_file`/`index_only`, parse ATX level-2 headings outside code fences. Require exactly
one of each standard heading in template order; additional level-2 headings are allowed only after
Purpose and cannot duplicate/rename mandatory anchors. Purpose/Public surface/Behavior/Invariants/
Tests must contain non-placeholder prose. `TODO`, empty, “same as index,” or a sole link is not
content. Open questions may be exactly `None.`.

Reject unbalanced fences, NUL, BOM, CRLF, invalid UTF-8, trailing binary, symlink, oversized spec,
and duplicate/case-colliding spec path. Validate the first heading names the exact future path for
owners or indexed prefix for indexes.

### One-to-one future ownership

Build maps `spec_path → row` and `future_path → owner row`; both are injective and cover their
inventories. Also scan every file-looking item in owner Public surfaces/imported-by lists: when a spec
claims another future file exists, that path must have one manifest owner or be clearly identified as
runtime-generated/user data rather than repository file. The script does not invent missing future
paths from source code because implementation does not yet exist; reviewed manifest + index inventories
are the declared universe.

Generated copies remain distinct future paths with their own specs and may point to a canonical
source owner. A source/package parity relation cannot give two specs ownership of one future path.

### Public self-containment

The validator never opens or inventories private/ignored drafting directories. For every owner/index
spec it checks:

1. all `Imports (spec-tree)` and repository-relative normative links resolve to a manifest-listed
   public spec/ADR/schema/fixture/doc or named future file;
2. no path/link/code reference names any configured private-drafting, assistant-session, transcript,
   absolute-home, external-local-repository, editor/debug/cache root or private-root canary;
3. no normative phrase delegates behavior to an unavailable source (“as defined only in private
   notes,” “see local architecture,” etc.); allowlisted external standards may support facts but
   cannot replace a required local behavior contract;
4. every imported shared name is owned by `INTERFACES.md` or a named public file spec; unresolved
   file references fail rather than becoming prose assumptions;
5. a simulated public-tree inventory containing only manifest-listed public files resolves all
   required references.

Coordination README may document that private drafting inputs existed, but no future-file/index row
may cite them. The check is complemented by `scan_public_boundary.py`; it is a dependency graph gate,
not a general secret detector.

### Output and CI discipline

Sort findings by code/spec/line. Human output gives bounded relative spec/future path and reason.
`--json` emits canonical structural counts/findings and a digest of manifest+spec inventory, never
spec excerpts or private match text. Passing output reports counts by classification/status/mapping,
future owner count, index coverage count and self-containment status.

The script reads no network, Git history, ignored directory, clock, environment beyond explicit test
root, or implementation source. It performs no writes. Same tree yields identical findings under
locale/hash seed/platform.

## Errors and edge cases

- Missing/unreadable/invalid manifest, absent/extra spec, unknown extension/classification/mapping,
  unresolved index path or normative dependency, or coordination summary/count drift is blocking.
- FILE_MANIFEST self-row is coordination and does not create recursion; parser reads its current bytes
  once, then validates its own row/path.
- A manifest row cannot hide a symlink/case collision/ignored private spec.
- Markdown examples inside fenced blocks are ignored for heading parsing but file-looking paths in an
  index Public surface are intentionally parsed by its restricted extractor.
- False positive private dependency is fixed by self-contained wording/public source or narrow
  reviewed detector change, never per-row `ignore=true`.
- Dirty working tree is irrelevant; the checked file inventory/bytes are the input.

## Invariants

1. Every spec file is classified exactly once.
2. Every declared future repository file has exactly one owning file spec.
3. Indexes enumerate but never own child files.
4. Future-path extension mapping is explicit and unambiguous.
5. Every owner/index has the complete natural-language code template.
6. Public implementation can proceed with private drafting inputs absent.
7. Verification is deterministic and read-only.
8. Every published inventory count is derived from and equal to the parsed registry.

## Tests

- Unit: strict table parsing, path normalization/collision, every classification/mapping combination,
  headings/fences/first-title validation and deterministic report.
- Property: arbitrary row/path/order/Unicode/Markdown tables cannot escape traversal, duplicate or
  case-collision checks.
- Integration synthetic trees: missing/extra/duplicate owner/spec; legacy/exact mapping conflicts;
  `.md.md`; valid `repository_projection` for `LICENSE`, dotfiles, lock files, and root Markdown;
  rejected nested/root-projection collision; index listed/unlisted child/nested delegation;
  coordination allowlist; symlink/NUL/CRLF.
- Self-containment fixtures: ignored private-drafting link, absolute home, transcript/private canary,
  unresolved import/normative link, external standard as sole behavior, and clean public-only tree.
- Coordination-count fixtures: exact owner/index/coordination totals; wrong scope subtotal; correct
  subtotals with wrong grand total; manifest change without README update; malformed or duplicate
  status board. Every mismatch returns `SUMMARY_COUNT_DRIFT` deterministically.
- PR/release workflow tests assert the exact `--check` command runs before build/publication.

## Open questions

None.
