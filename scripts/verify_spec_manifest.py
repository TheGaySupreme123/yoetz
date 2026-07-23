"""Validate the spec-tree file-ownership manifest and its self-containment guarantees.

This script proves the natural-language-first build contract is mechanically complete: every
specification file is classified exactly once, every declared future repository file has exactly
one owning spec, mirrored extensions are unambiguous, family indexes enumerate but never own
child files, every owner/index has the complete seven-heading template, and no owner/index spec
depends on a private/ignored drafting input. It validates ``specs/FILE_MANIFEST.md``; it never
generates, rewrites, sorts, or "fixes" that document.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

__all__ = [
    "ManifestClass",
    "ManifestFormatError",
    "ManifestRow",
    "PathMapping",
    "SpecFinding",
    "SpecManifest",
    "inventory_specs",
    "main",
    "parse_manifest",
    "validate_coordination_counts",
    "validate_extension_mapping",
    "validate_headings",
    "validate_index_coverage",
    "validate_one_to_one",
    "validate_public_self_containment",
]


# --------------------------------------------------------------------------
# Enums and data model
# --------------------------------------------------------------------------


class ManifestClass(str, Enum):  # noqa: UP042 - exact manifest wire values
    FUTURE_FILE = "future_file"
    INDEX_ONLY = "index_only"
    COORDINATION = "coordination"


class PathMapping(str, Enum):  # noqa: UP042 - exact manifest wire values
    EXACT_SUFFIX = "exact_suffix"
    PYTHON_SHORTHAND = "python_shorthand"
    MARKDOWN_SHORTHAND = "markdown_shorthand"
    REPOSITORY_PROJECTION = "repository_projection"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ManifestRow:
    spec_path: str
    classification: ManifestClass
    future_path: str | None
    mapping: PathMapping
    indexed_prefix: str | None
    wave: str
    status: str
    owner_note: str
    line: int


@dataclass(frozen=True, slots=True)
class SpecManifest:
    manifest_schema: str
    rows: tuple[ManifestRow, ...]


@dataclass(frozen=True, slots=True)
class SpecFinding:
    code: str
    spec_path: str | None = None
    line: int | None = None
    detail: str = ""

    def sort_key(self) -> tuple[str, str, int]:
        return (self.code, self.spec_path or "", self.line or 0)


class ManifestFormatError(ValueError):
    """Raised when the manifest bytes are not a valid registry table."""

    def __init__(self, code: str, *, line: int | None = None, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.line = line
        self.detail = detail


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

_EM_DASH = "—"
_REGISTRY_HEADING = "## File ownership registry"
_MANIFEST_SCHEMA_PATTERN = re.compile(r"^\*\*Manifest schema:\*\*\s*`([^`]+)`\s*$", re.MULTILINE)
_HEADER_COLUMNS: Final = (
    "Spec path",
    "Classification",
    "Future path",
    "Mapping",
    "Indexed prefix",
    "Wave",
    "Status",
    "Owner note",
)
_STANDARD_HEADINGS: Final = (
    "Purpose",
    "Public surface",
    "Behavior",
    "Errors and edge cases",
    "Invariants",
    "Tests",
    "Open questions",
)
_COORDINATION_ALLOWLIST: Final = frozenset(
    {
        "specs/README.md",
        "specs/INTERFACES.md",
        "specs/FILE_MANIFEST.md",
        "specs/OPEN_QUESTIONS.md",
    }
)
_RECOGNIZED_FUTURE_EXTENSIONS: Final = frozenset(
    {".py", ".sql", ".json", ".toml", ".yml", ".yaml", ".lock", ".md", ".txt", ".typed", ".js"}
)
_STATUS_VALUES: Final = frozenset({"draft", "reviewed", "locked"})
_BACKTICK_CELL = re.compile(r"^`([^`|]*)`$")
_HTML_TAG = re.compile(r"<[A-Za-z!/][^<>]*>")
_MAX_MANIFEST_BYTES: Final = 5_000_000
_MAX_SPEC_BYTES: Final = 2_000_000

# Repository-scope buckets rendered in specs/README.md's status board. Each bucket is an ordered
# set of exact-match / prefix predicates over future paths; the first matching bucket wins.
_SCOPE_BUCKETS: Final = (
    "Repository root",
    ".github/ workflows",
    ".yoetz/ project policy",
    "docs/ public protocol and runbooks",
    "schemas/",
    "fixtures/",
    "migrations/",
    "guidance/ harness-neutral agent guidance",
    "skills/",
    "support/",
    "src/yoetz/resources/",
    "src/yoetz/ Python/code files",
    "scripts/",
    "tests/",
)


def _classify_scope(future_path: str, mapping: PathMapping) -> str:
    if mapping is PathMapping.REPOSITORY_PROJECTION:
        return "Repository root"
    if future_path.startswith(".github/"):
        return ".github/ workflows"
    if future_path.startswith(".yoetz/"):
        return ".yoetz/ project policy"
    if future_path.startswith("docs/"):
        return "docs/ public protocol and runbooks"
    if future_path.startswith("schemas/"):
        return "schemas/"
    if future_path.startswith("fixtures/"):
        return "fixtures/"
    if future_path.startswith("migrations/"):
        return "migrations/"
    if future_path.startswith("guidance/"):
        return "guidance/ harness-neutral agent guidance"
    if future_path.startswith("skills/"):
        return "skills/"
    if future_path.startswith("support/"):
        return "support/"
    if future_path.startswith("src/yoetz/resources/"):
        return "src/yoetz/resources/"
    if future_path.startswith("src/yoetz/"):
        return "src/yoetz/ Python/code files"
    if future_path.startswith("scripts/"):
        return "scripts/"
    if future_path.startswith("tests/"):
        return "tests/"
    return "unknown"


# --------------------------------------------------------------------------
# Path safety
# --------------------------------------------------------------------------


def _is_safe_relative_path(path: str) -> bool:
    if not path or path != unicodedata.normalize("NFC", path):
        return False
    if path.startswith("/") or path.startswith("\\"):
        return False
    if "\\" in path or "\x00" in path:
        return False
    if any(ord(ch) < 0x20 for ch in path):
        return False
    if path.startswith("/") or path.endswith("/"):
        return False
    if "//" in path:
        return False
    segments = path.split("/")
    for segment in segments:
        if segment in {"", ".", ".."}:
            return False
    return True


def _casefold_key(path: str) -> str:
    return path.casefold()


# --------------------------------------------------------------------------
# Manifest parsing
# --------------------------------------------------------------------------


def _decode_manifest(data: bytes) -> str:
    if b"\x00" in data:
        raise ManifestFormatError("nul_byte_forbidden")
    if data[:3] == b"\xef\xbb\xbf":
        raise ManifestFormatError("byte_order_mark_forbidden")
    if b"\r" in data:
        raise ManifestFormatError("crlf_forbidden")
    if not data.endswith(b"\n"):
        raise ManifestFormatError("missing_final_newline")
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ManifestFormatError("invalid_utf8") from exc


def _split_row_cells(line: str, line_number: int) -> tuple[str, ...]:
    if "\\|" in line:
        raise ManifestFormatError("escaped_pipe_forbidden", line=line_number)
    if _HTML_TAG.search(line) is not None:
        raise ManifestFormatError("html_forbidden", line=line_number)
    if not line.startswith("|") or not line.endswith("|"):
        raise ManifestFormatError("malformed_table_row", line=line_number)
    inner = line[1:-1]
    return tuple(cell.strip() for cell in inner.split("|"))


def _parse_backtick_or_dash(cell: str, *, line: int) -> str | None:
    if cell == _EM_DASH:
        return None
    match = _BACKTICK_CELL.match(cell)
    if match is None:
        raise ManifestFormatError("invalid_cell_form", line=line, detail=cell)
    return match.group(1)


def parse_manifest(data: bytes) -> SpecManifest:
    """Parse ``specs/FILE_MANIFEST.md`` bytes into a strict, validated in-memory manifest.

    Raises ``ManifestFormatError`` for any structural violation: missing/duplicate registry
    section, wrong header columns, malformed cell form, unknown classification/mapping/status
    enum, duplicate or case-colliding spec path, empty owner note, or noncanonical row ordering.
    """

    if type(data) is not bytes:
        raise ManifestFormatError("input_not_bytes")
    if len(data) > _MAX_MANIFEST_BYTES:
        raise ManifestFormatError("manifest_too_large")

    text = _decode_manifest(data)
    lines = text.split("\n")

    schema_match = _MANIFEST_SCHEMA_PATTERN.search(text)
    if schema_match is None:
        raise ManifestFormatError("manifest_schema_missing")
    manifest_schema = schema_match.group(1)

    heading_indexes = [
        index for index, line in enumerate(lines) if line.strip() == _REGISTRY_HEADING
    ]
    if len(heading_indexes) == 0:
        raise ManifestFormatError("registry_heading_missing")
    if len(heading_indexes) > 1:
        raise ManifestFormatError("registry_heading_duplicated")
    heading_index = heading_indexes[0]

    table_start = heading_index + 1
    while table_start < len(lines) and lines[table_start].strip() == "":
        table_start += 1
    if table_start >= len(lines) or not lines[table_start].strip().startswith("|"):
        raise ManifestFormatError("registry_table_missing", line=heading_index + 1)

    header_cells = _split_row_cells(lines[table_start].strip(), table_start + 1)
    if len(header_cells) != len(_HEADER_COLUMNS):
        raise ManifestFormatError("unexpected_column_count", line=table_start + 1)
    for expected, actual in zip(_HEADER_COLUMNS, header_cells, strict=True):
        if expected != actual:
            raise ManifestFormatError("unexpected_column", line=table_start + 1, detail=actual)

    separator_index = table_start + 1
    if separator_index >= len(lines) or not lines[separator_index].strip().startswith("|"):
        raise ManifestFormatError("registry_separator_missing", line=separator_index + 1)
    separator_cells = _split_row_cells(lines[separator_index].strip(), separator_index + 1)
    if len(separator_cells) != len(_HEADER_COLUMNS) or any(
        not re.fullmatch(r":?-{3,}:?", cell) for cell in separator_cells
    ):
        raise ManifestFormatError("registry_separator_invalid", line=separator_index + 1)

    rows: list[ManifestRow] = []
    seen_spec_paths: dict[str, int] = {}
    seen_casefold: dict[str, int] = {}
    previous_key: str | None = None

    row_index = separator_index + 1
    while row_index < len(lines) and lines[row_index].strip().startswith("|"):
        line_number = row_index + 1
        cells = _split_row_cells(lines[row_index].strip(), line_number)
        if len(cells) != len(_HEADER_COLUMNS):
            raise ManifestFormatError("malformed_table_row", line=line_number)

        spec_path_cell, class_cell, future_cell, mapping_cell = cells[0:4]
        indexed_prefix_cell, wave_cell, status_cell, owner_note_cell = cells[4:8]

        spec_path = _parse_backtick_or_dash(spec_path_cell, line=line_number)
        if spec_path is None or not _is_safe_relative_path(spec_path):
            raise ManifestFormatError("spec_path_unsafe", line=line_number, detail=spec_path_cell)
        if not spec_path.startswith("specs/") or not spec_path.endswith(".md"):
            raise ManifestFormatError("spec_path_unsafe", line=line_number, detail=spec_path)

        classification_raw = _parse_backtick_or_dash(class_cell, line=line_number)
        if classification_raw is None:
            raise ManifestFormatError("unknown_classification", line=line_number)
        try:
            classification = ManifestClass(classification_raw)
        except ValueError as exc:
            raise ManifestFormatError(
                "unknown_classification", line=line_number, detail=classification_raw
            ) from exc

        future_path = _parse_backtick_or_dash(future_cell, line=line_number)
        if future_path is not None:
            if not _is_safe_relative_path(future_path) or future_path.startswith("specs/"):
                raise ManifestFormatError(
                    "future_path_unsafe", line=line_number, detail=future_path
                )

        mapping_raw = _parse_backtick_or_dash(mapping_cell, line=line_number)
        if mapping_raw is None:
            raise ManifestFormatError("unknown_mapping", line=line_number)
        try:
            mapping = PathMapping(mapping_raw)
        except ValueError as exc:
            raise ManifestFormatError(
                "unknown_mapping", line=line_number, detail=mapping_raw
            ) from exc

        indexed_prefix = _parse_backtick_or_dash(indexed_prefix_cell, line=line_number)
        if indexed_prefix is not None:
            if not indexed_prefix.endswith("/") or not _is_safe_relative_path(indexed_prefix + "x"):
                raise ManifestFormatError(
                    "indexed_prefix_unsafe", line=line_number, detail=indexed_prefix
                )

        wave = _parse_backtick_or_dash(wave_cell, line=line_number)
        if wave is None or not wave.strip():
            raise ManifestFormatError("wave_missing", line=line_number)

        status = _parse_backtick_or_dash(status_cell, line=line_number)
        if status is None or status not in _STATUS_VALUES:
            raise ManifestFormatError("unknown_status", line=line_number, detail=status_cell)

        owner_note = owner_note_cell
        if not owner_note or owner_note == _EM_DASH:
            raise ManifestFormatError("empty_owner_note", line=line_number)
        if _BACKTICK_CELL.match(owner_note) is not None:
            raise ManifestFormatError("owner_note_not_prose", line=line_number)

        if spec_path in seen_spec_paths:
            raise ManifestFormatError("duplicate_spec_path", line=line_number, detail=spec_path)
        casefold_key = _casefold_key(spec_path)
        if casefold_key in seen_casefold:
            raise ManifestFormatError(
                "case_colliding_spec_path", line=line_number, detail=spec_path
            )
        seen_spec_paths[spec_path] = line_number
        seen_casefold[casefold_key] = line_number

        sort_key = spec_path.encode("utf-8")
        if previous_key is not None and sort_key < previous_key.encode("utf-8"):
            raise ManifestFormatError("noncanonical_ordering", line=line_number, detail=spec_path)
        previous_key = spec_path

        rows.append(
            ManifestRow(
                spec_path=spec_path,
                classification=classification,
                future_path=future_path,
                mapping=mapping,
                indexed_prefix=indexed_prefix,
                wave=wave,
                status=status,
                owner_note=owner_note,
                line=line_number,
            )
        )
        row_index += 1

    return SpecManifest(manifest_schema=manifest_schema, rows=tuple(rows))


# --------------------------------------------------------------------------
# Spec inventory
# --------------------------------------------------------------------------


def inventory_specs(root: Path) -> tuple[str, ...]:
    """Return the sorted, safe, POSIX-relative inventory of ``specs/**/*.md`` regular files."""

    specs_root = root / "specs"
    if not specs_root.is_dir():
        return ()

    found: list[str] = []
    for candidate in specs_root.rglob("*.md"):
        if candidate.is_symlink():
            continue
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root).as_posix()
        if not _is_safe_relative_path(relative):
            continue
        found.append(relative)
    return tuple(sorted(found, key=lambda item: item.encode("utf-8")))


# --------------------------------------------------------------------------
# Extension mapping validation
# --------------------------------------------------------------------------


def validate_extension_mapping(row: ManifestRow) -> tuple[SpecFinding, ...]:
    """Validate that ``row``'s declared mapping exactly and unambiguously relates the two paths."""

    findings: list[SpecFinding] = []

    def _fail(code: str, detail: str = "") -> None:
        findings.append(
            SpecFinding(code=code, spec_path=row.spec_path, line=row.line, detail=detail)
        )

    if row.classification is not ManifestClass.FUTURE_FILE:
        if row.mapping is not PathMapping.NONE:
            _fail("mapping_forbidden_for_non_owner")
        return tuple(findings)

    if row.future_path is None:
        _fail("future_file_missing_future_path")
        return tuple(findings)
    if row.mapping is PathMapping.NONE:
        _fail("owner_mapping_cannot_be_none")
        return tuple(findings)

    future_path = row.future_path
    spec_path = row.spec_path

    if row.mapping is not PathMapping.REPOSITORY_PROJECTION:
        suffix = Path(future_path).suffix
        if suffix not in _RECOGNIZED_FUTURE_EXTENSIONS:
            _fail("unrecognized_future_extension", suffix)

    if row.mapping is PathMapping.EXACT_SUFFIX:
        if spec_path != f"specs/{future_path}.md":
            _fail("exact_suffix_mismatch")
    elif row.mapping is PathMapping.PYTHON_SHORTHAND:
        if not future_path.endswith(".py"):
            _fail("python_shorthand_requires_py")
        elif spec_path != f"specs/{future_path[: -len('.py')]}.md":
            _fail("python_shorthand_mismatch")
    elif row.mapping is PathMapping.MARKDOWN_SHORTHAND:
        if not future_path.endswith(".md"):
            _fail("markdown_shorthand_requires_md")
        elif spec_path != f"specs/{future_path}":
            _fail("markdown_shorthand_mismatch")
    elif row.mapping is PathMapping.REPOSITORY_PROJECTION:
        if "/" in future_path:
            _fail("repository_projection_requires_root_path")
        elif future_path.endswith(".md"):
            if spec_path != f"specs/repository/{future_path}":
                _fail("repository_projection_markdown_mismatch")
        elif spec_path != f"specs/repository/{future_path}.md":
            _fail("repository_projection_mismatch")

    return tuple(findings)


# --------------------------------------------------------------------------
# Heading validation
# --------------------------------------------------------------------------

_ATX_H2 = re.compile(r"^##\s+(.+?)\s*$")
_FENCE = re.compile(r"^\s*```")


def _strip_fences(text: str) -> list[str]:
    lines = text.split("\n")
    kept: list[str] = []
    in_fence = False
    for line in lines:
        if _FENCE.match(line):
            in_fence = not in_fence
            kept.append(line)
            continue
        kept.append("" if in_fence else line)
    return kept


def _extract_h2_headings(text: str) -> list[tuple[str, int, int]]:
    """Return (title, start_line, end_line_exclusive) for each ATX level-2 heading."""

    lines = _strip_fences(text)
    positions: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        match = _ATX_H2.match(line)
        if match is not None:
            positions.append((match.group(1), index))
    result: list[tuple[str, int, int]] = []
    for position, (title, start) in enumerate(positions):
        end = positions[position + 1][1] if position + 1 < len(positions) else len(lines)
        result.append((title, start, end))
    return result


def validate_headings(row: ManifestRow, data: bytes) -> tuple[SpecFinding, ...]:
    """Validate the standard seven-heading template on an owner/index spec's raw bytes."""

    findings: list[SpecFinding] = []

    def _fail(code: str, detail: str = "") -> None:
        findings.append(
            SpecFinding(code=code, spec_path=row.spec_path, line=row.line, detail=detail)
        )

    if row.classification is ManifestClass.COORDINATION:
        return tuple(findings)

    if len(data) > _MAX_SPEC_BYTES:
        _fail("spec_too_large")
        return tuple(findings)
    if b"\x00" in data:
        _fail("nul_byte_forbidden")
        return tuple(findings)
    if data[:3] == b"\xef\xbb\xbf":
        _fail("byte_order_mark_forbidden")
        return tuple(findings)
    if b"\r" in data:
        _fail("crlf_forbidden")
        return tuple(findings)
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _fail("invalid_utf8")
        return tuple(findings)

    headings = _extract_h2_headings(text)
    titles = [title for title, _, _ in headings]

    seen: set[str] = set()
    for title in titles:
        if title in _STANDARD_HEADINGS:
            if title in seen:
                _fail("duplicate_standard_heading", title)
            seen.add(title)

    present_standard = [title for title in titles if title in _STANDARD_HEADINGS]
    if present_standard != list(_STANDARD_HEADINGS):
        _fail("standard_headings_missing_or_out_of_order")
        return tuple(findings)

    lines = text.split("\n")
    first_title_line = next((line for line in lines if line.strip().startswith("# ")), None)
    if first_title_line is None:
        _fail("first_title_missing")
    else:
        title_text = first_title_line.strip()[2:].strip()
        expected_anchor = (
            row.future_path
            if row.classification is ManifestClass.FUTURE_FILE
            else row.indexed_prefix
        )
        if expected_anchor is None or expected_anchor not in title_text:
            _fail("first_title_mismatch", title_text)

    lookup = {title: (start, end) for title, start, end in headings}
    for prose_heading in ("Purpose", "Public surface", "Behavior", "Invariants", "Tests"):
        bounds = lookup.get(prose_heading)
        if bounds is None:
            continue
        start, end = bounds
        body = "\n".join(lines[start + 1 : end]).strip()
        if not _is_real_prose(body):
            _fail("placeholder_content", prose_heading)

    open_questions_bounds = lookup.get("Open questions")
    if open_questions_bounds is not None:
        start, end = open_questions_bounds
        body = "\n".join(lines[start + 1 : end]).strip()
        if body != "None." and not _is_real_prose(body):
            _fail("placeholder_content", "Open questions")

    return tuple(findings)


def _is_real_prose(body: str) -> bool:
    if not body:
        return False
    stripped = body.strip()
    if not stripped:
        return False
    upper = stripped.upper()
    if upper in {"TODO", "TBD", "SAME AS INDEX", "SAME AS INDEX."}:
        return False
    if stripped.startswith("TODO"):
        return False
    non_link_lines = [
        line
        for line in stripped.split("\n")
        if line.strip() and not _is_sole_link_line(line.strip())
    ]
    return len(non_link_lines) > 0


_SOLE_LINK_LINE = re.compile(r"^\[[^\]]*\]\([^)]*\)$|^<https?://[^>]+>$")


def _is_sole_link_line(line: str) -> bool:
    return _SOLE_LINK_LINE.match(line) is not None


# --------------------------------------------------------------------------
# Index coverage validation
# --------------------------------------------------------------------------

_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_SAFE_PATH_TOKEN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]*$")
_TREE_LINE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]*$")


def _looks_like_future_file(candidate: str) -> bool:
    if "..." in candidate or "*" in candidate or "<" in candidate or ">" in candidate:
        return False
    if candidate.endswith("/") or not candidate:
        return False
    if _SAFE_PATH_TOKEN.match(candidate) is None:
        return False
    return Path(candidate).suffix in _RECOGNIZED_FUTURE_EXTENSIONS


def _extract_fenced_tree_paths(section_lines: Sequence[str]) -> set[str]:
    """Resolve full relative paths out of indentation-based fenced directory trees."""

    paths: set[str] = set()
    stack: list[tuple[int, str]] = []
    in_fence = False
    for raw_line in section_lines:
        if _FENCE.match(raw_line):
            in_fence = not in_fence
            stack = []
            continue
        if not in_fence:
            continue
        if not raw_line.strip():
            continue
        name = raw_line.strip()
        if _TREE_LINE.match(name) is None:
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if name.endswith("/"):
            stack.append((indent, name))
            continue
        prefix = "".join(segment for _, segment in stack)
        candidate = prefix + name
        if _looks_like_future_file(candidate):
            paths.add(candidate)
    return paths


def validate_index_coverage(
    row: ManifestRow, data: bytes, manifest: SpecManifest
) -> tuple[SpecFinding, ...]:
    """Validate an ``index_only`` spec's Public surface inventory against the manifest."""

    findings: list[SpecFinding] = []

    def _fail(code: str, detail: str = "") -> None:
        findings.append(
            SpecFinding(code=code, spec_path=row.spec_path, line=row.line, detail=detail)
        )

    if row.classification is not ManifestClass.INDEX_ONLY:
        return tuple(findings)
    if row.indexed_prefix is None:
        _fail("index_missing_prefix")
        return tuple(findings)

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _fail("invalid_utf8")
        return tuple(findings)

    headings = _extract_h2_headings(text)
    lookup = {title: (start, end) for title, start, end in headings}
    bounds = lookup.get("Public surface")
    if bounds is None:
        _fail("index_missing_public_surface")
        return tuple(findings)

    lines = text.split("\n")
    start, end = bounds
    section_lines = lines[start + 1 : end]

    listed: set[str] = {
        path
        for path in _extract_fenced_tree_paths(section_lines)
        if path.startswith(row.indexed_prefix)
    }
    in_fence = False
    for line in section_lines:
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in _CODE_SPAN.finditer(line):
            candidate = match.group(1).strip()
            if not _looks_like_future_file(candidate):
                continue
            resolved = candidate if "/" in candidate else row.indexed_prefix + candidate
            if resolved.startswith(row.indexed_prefix):
                listed.add(resolved)

    nested_index_prefixes = {
        r.indexed_prefix
        for r in manifest.rows
        if r.classification is ManifestClass.INDEX_ONLY
        and r.indexed_prefix is not None
        and r.indexed_prefix != row.indexed_prefix
        and r.indexed_prefix.startswith(row.indexed_prefix)
    }

    owned_under_prefix = {
        r.future_path
        for r in manifest.rows
        if r.classification is ManifestClass.FUTURE_FILE
        and r.future_path is not None
        and r.future_path.startswith(row.indexed_prefix)
        and not any(r.future_path.startswith(nested) for nested in nested_index_prefixes)
    }

    for path in sorted(listed - owned_under_prefix, key=lambda item: item.encode("utf-8")):
        _fail("index_lists_unowned_path", path)
    for path in sorted(owned_under_prefix - listed, key=lambda item: item.encode("utf-8")):
        _fail("index_missing_owned_path", path)

    return tuple(findings)


# --------------------------------------------------------------------------
# One-to-one ownership validation
# --------------------------------------------------------------------------


def validate_one_to_one(
    manifest: SpecManifest, inventory: Sequence[str]
) -> tuple[SpecFinding, ...]:
    """Validate the injective spec<->future-file ownership maps against the spec inventory."""

    findings: list[SpecFinding] = []
    manifest_specs = {row.spec_path for row in manifest.rows}
    inventory_set = set(inventory)

    for missing in sorted(manifest_specs - inventory_set, key=lambda item: item.encode("utf-8")):
        findings.append(SpecFinding(code="spec_file_absent", spec_path=missing))
    for extra in sorted(inventory_set - manifest_specs, key=lambda item: item.encode("utf-8")):
        findings.append(SpecFinding(code="spec_file_unclassified", spec_path=extra))

    future_owners: dict[str, str] = {}
    future_casefold: dict[str, str] = {}
    for row in manifest.rows:
        if row.classification is ManifestClass.FUTURE_FILE:
            if row.future_path is None:
                continue
            if row.future_path in future_owners:
                findings.append(
                    SpecFinding(
                        code="duplicate_future_owner",
                        spec_path=row.spec_path,
                        line=row.line,
                        detail=row.future_path,
                    )
                )
            future_owners[row.future_path] = row.spec_path
            fold = _casefold_key(row.future_path)
            if fold in future_casefold and future_casefold[fold] != row.future_path:
                findings.append(
                    SpecFinding(
                        code="case_colliding_future_path",
                        spec_path=row.spec_path,
                        line=row.line,
                        detail=row.future_path,
                    )
                )
            future_casefold[fold] = row.future_path
        elif row.classification is ManifestClass.INDEX_ONLY:
            if row.future_path is not None or row.mapping is not PathMapping.NONE:
                findings.append(
                    SpecFinding(
                        code="index_owns_future_path",
                        spec_path=row.spec_path,
                        line=row.line,
                    )
                )
        elif row.classification is ManifestClass.COORDINATION:
            if row.spec_path not in _COORDINATION_ALLOWLIST:
                findings.append(
                    SpecFinding(
                        code="coordination_not_allowlisted",
                        spec_path=row.spec_path,
                        line=row.line,
                    )
                )
            if row.future_path is not None or row.mapping is not PathMapping.NONE:
                findings.append(
                    SpecFinding(
                        code="coordination_owns_future_path",
                        spec_path=row.spec_path,
                        line=row.line,
                    )
                )

    return tuple(findings)


# --------------------------------------------------------------------------
# Public self-containment validation
# --------------------------------------------------------------------------

_PRIVATE_CANARIES: Final = (
    # Absolute maintainer paths: a real occurrence, not a slash-separated word list like
    # "user/home/repository" or a documented XDG/platformdirs convention such as "~/.config".
    re.compile(r"(?<![\w/.])/Users/[A-Za-z0-9_.\-]+"),
    re.compile(r"(?<![\w/.])/home/[A-Za-z0-9_.\-]+"),
    re.compile(r"\.claude/"),
    re.compile(r"CLAUDE\.md"),
    re.compile(r"session[- _]transcript", re.IGNORECASE),
)
_NORMATIVE_DELEGATION = re.compile(
    r"as defined only in private notes|see local architecture", re.IGNORECASE
)
_SPEC_REFERENCE = re.compile(r"`(specs/[^`\s]+\.md)`")
_ADR_REFERENCE = re.compile(r"`?(docs/adr/ADR-\d{3}-[a-z0-9-]+\.md)`?")


def validate_public_self_containment(
    row: ManifestRow, data: bytes, repo_inventory: Sequence[str]
) -> tuple[SpecFinding, ...]:
    """Validate that one owner/index spec depends only on the public, manifest-listed tree."""

    findings: list[SpecFinding] = []

    def _fail(code: str, detail: str = "") -> None:
        findings.append(
            SpecFinding(code=code, spec_path=row.spec_path, line=row.line, detail=detail)
        )

    if row.classification is ManifestClass.COORDINATION:
        return tuple(findings)

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return tuple(findings)

    for pattern in _PRIVATE_CANARIES:
        if pattern.search(text) is not None:
            _fail("private_boundary_reference", pattern.pattern)

    if _NORMATIVE_DELEGATION.search(text) is not None:
        _fail("unavailable_source_delegation")

    inventory_set = set(repo_inventory)
    for match in _SPEC_REFERENCE.finditer(text):
        referenced = match.group(1)
        if referenced not in inventory_set:
            _fail("unresolved_spec_reference", referenced)

    for match in _ADR_REFERENCE.finditer(text):
        referenced = match.group(1)
        if referenced not in inventory_set:
            _fail("unresolved_adr_reference", referenced)

    return tuple(findings)


# --------------------------------------------------------------------------
# Coordination-count consistency
# --------------------------------------------------------------------------

_PREAMBLE_COUNT_PATTERN = re.compile(
    r"contains (\d+) spec files:\s*"
    r"(\d+) exact future-file owners,\s*"
    r"(\d+) directory indexes, and\s*"
    r"(\d+) coordination files",
)
_README_SUMMARY_PATTERN = re.compile(
    r"classifies (\d+) spec files:\s*"
    r"(\d+) unique future-file owners,\s*"
    r"(\d+) directory indexes, and\s*"
    r"(\d+) coordination files",
)
_STATUS_BOARD_HEADER_MARKER = "Future repository scope"
_MARKDOWN_DECORATION = re.compile(r"[`*]")
_README_TOTAL_PATTERN = re.compile(
    r"\*\*Total future files\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|\s*"
    r"\*\*(\d+) indexes \+ (\d+) coordination files = (\d+) spec files\*\*",
)


def _status_board_rows(readme_text: str) -> list[tuple[str, int]]:
    """Parse the README status-board table's (scope label, exact-owner count) rows."""

    lines = readme_text.split("\n")
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip().startswith("|") and _STATUS_BOARD_HEADER_MARKER in line
        ),
        None,
    )
    if header_index is None:
        return []

    rows: list[tuple[str, int]] = []
    index = header_index + 2
    while index < len(lines) and lines[index].strip().startswith("|"):
        cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
        if len(cells) >= 2:
            label = _MARKDOWN_DECORATION.sub("", cells[0]).strip()
            value = _MARKDOWN_DECORATION.sub("", cells[1]).strip()
            if label != "Total future files" and value.isdigit():
                rows.append((label, int(value)))
        index += 1
    return rows


def validate_coordination_counts(
    manifest: SpecManifest, manifest_bytes: bytes, readme_bytes: bytes
) -> tuple[SpecFinding, ...]:
    """Cross-check manifest preamble and README status-board counts against parsed rows."""

    findings: list[SpecFinding] = []

    def _fail(detail: str) -> None:
        findings.append(
            SpecFinding(
                code="SUMMARY_COUNT_DRIFT", spec_path="specs/FILE_MANIFEST.md", detail=detail
            )
        )

    owner_rows = [row for row in manifest.rows if row.classification is ManifestClass.FUTURE_FILE]
    index_rows = [row for row in manifest.rows if row.classification is ManifestClass.INDEX_ONLY]
    coordination_rows = [
        row for row in manifest.rows if row.classification is ManifestClass.COORDINATION
    ]
    owner_count = len({row.future_path for row in owner_rows if row.future_path is not None})
    index_count = len(index_rows)
    coordination_count = len(coordination_rows)
    total_count = owner_count + index_count + coordination_count

    try:
        manifest_text = manifest_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _fail("manifest_preamble_undecodable")
        return tuple(findings)
    preamble_match = _PREAMBLE_COUNT_PATTERN.search(manifest_text)
    if preamble_match is None:
        _fail("manifest_preamble_count_missing")
    else:
        total, owners, indexes, coordination = (int(value) for value in preamble_match.groups())
        if (total, owners, indexes, coordination) != (
            total_count,
            owner_count,
            index_count,
            coordination_count,
        ):
            _fail("manifest_preamble_count_mismatch")

    try:
        readme_text = readme_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _fail("readme_status_board_undecodable")
        return tuple(findings)

    summary_match = _README_SUMMARY_PATTERN.search(readme_text)
    if summary_match is None:
        _fail("readme_summary_missing")
    else:
        total, owners, indexes, coordination = (int(value) for value in summary_match.groups())
        if (total, owners, indexes, coordination) != (
            total_count,
            owner_count,
            index_count,
            coordination_count,
        ):
            _fail("readme_summary_mismatch")

    total_match = _README_TOTAL_PATTERN.search(readme_text)
    if total_match is None:
        _fail("readme_total_row_missing")
    else:
        owner_total, index_total, coordination_total, grand_total = (
            int(value) for value in total_match.groups()
        )
        if owner_total != owner_count:
            _fail("readme_owner_total_mismatch")
        if index_total != index_count:
            _fail("readme_index_total_mismatch")
        if coordination_total != coordination_count:
            _fail("readme_coordination_total_mismatch")
        if grand_total != total_count:
            _fail("readme_grand_total_mismatch")

    computed_buckets: dict[str, int] = {bucket: 0 for bucket in _SCOPE_BUCKETS}
    unknown_scope_paths: list[str] = []
    for row in owner_rows:
        if row.future_path is None:
            continue
        scope = _classify_scope(row.future_path, row.mapping)
        if scope == "unknown":
            unknown_scope_paths.append(row.future_path)
            continue
        computed_buckets[scope] += 1
    if unknown_scope_paths:
        _fail("readme_scope_bucket_unclassified:" + ",".join(sorted(unknown_scope_paths)[:5]))

    row_matches = {
        label: count for label, count in _status_board_rows(readme_text) if label in _SCOPE_BUCKETS
    }
    for bucket, expected in computed_buckets.items():
        observed = row_matches.get(bucket)
        if observed is None:
            _fail(f"readme_scope_row_missing:{bucket}")
        elif observed != expected:
            _fail(f"readme_scope_row_mismatch:{bucket}")

    return tuple(findings)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _read_guarded_file(path: Path, *, max_bytes: int) -> bytes:
    if path.is_symlink():
        raise ManifestFormatError("symlink_forbidden", detail=str(path))
    if not path.is_file():
        raise ManifestFormatError("file_missing", detail=str(path))
    size = path.stat().st_size
    if size > max_bytes:
        raise ManifestFormatError("file_too_large", detail=str(path))
    return path.read_bytes()


def _run_check(
    repo_root: Path, manifest_path: Path
) -> tuple[int, list[SpecFinding], dict[str, object]]:
    findings: list[SpecFinding] = []

    try:
        manifest_bytes = _read_guarded_file(manifest_path, max_bytes=_MAX_MANIFEST_BYTES)
    except ManifestFormatError as exc:
        findings.append(
            SpecFinding(code=exc.code, spec_path="specs/FILE_MANIFEST.md", detail=exc.detail)
        )
        return 1, findings, {}

    try:
        manifest = parse_manifest(manifest_bytes)
    except ManifestFormatError as exc:
        findings.append(
            SpecFinding(
                code=exc.code,
                spec_path="specs/FILE_MANIFEST.md",
                line=exc.line,
                detail=exc.detail,
            )
        )
        return 1, findings, {}

    inventory = inventory_specs(repo_root)
    findings.extend(validate_one_to_one(manifest, inventory))

    for row in manifest.rows:
        spec_file = repo_root / row.spec_path
        try:
            spec_bytes = _read_guarded_file(spec_file, max_bytes=_MAX_SPEC_BYTES)
        except ManifestFormatError:
            continue

        findings.extend(validate_extension_mapping(row))
        findings.extend(validate_headings(row, spec_bytes))
        findings.extend(validate_index_coverage(row, spec_bytes, manifest))
        findings.extend(validate_public_self_containment(row, spec_bytes, inventory))

    readme_path = repo_root / "specs" / "README.md"
    try:
        readme_bytes = _read_guarded_file(readme_path, max_bytes=_MAX_SPEC_BYTES)
    except ManifestFormatError as exc:
        findings.append(SpecFinding(code=exc.code, spec_path="specs/README.md", detail=exc.detail))
    else:
        findings.extend(validate_coordination_counts(manifest, manifest_bytes, readme_bytes))

    findings.sort(key=SpecFinding.sort_key)

    classification_counts = {
        classification.value: sum(
            1 for row in manifest.rows if row.classification is classification
        )
        for classification in ManifestClass
    }
    status_counts: dict[str, int] = {}
    mapping_counts: dict[str, int] = {}
    for row in manifest.rows:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1
        mapping_counts[row.mapping.value] = mapping_counts.get(row.mapping.value, 0) + 1

    digest = hashlib.sha256(manifest_bytes)
    for path in inventory:
        digest.update(path.encode("utf-8"))

    summary: dict[str, object] = {
        "manifest_schema": manifest.manifest_schema,
        "row_count": len(manifest.rows),
        "classification_counts": classification_counts,
        "status_counts": status_counts,
        "mapping_counts": mapping_counts,
        "future_owner_count": classification_counts.get(ManifestClass.FUTURE_FILE.value, 0),
        "inventory_digest": f"sha256:{digest.hexdigest()}",
        "finding_count": len(findings),
    }

    exit_code = 1 if findings else 0
    return exit_code, findings, summary


def _print_human(findings: Sequence[SpecFinding], summary: dict[str, object]) -> None:
    if not findings:
        print("verify_spec_manifest: PASS")
        for key in (
            "row_count",
            "classification_counts",
            "status_counts",
            "mapping_counts",
            "future_owner_count",
        ):
            if key in summary:
                print(f"  {key}: {summary[key]}")
        return
    print(f"verify_spec_manifest: FAIL ({len(findings)} finding(s))")
    for finding in findings:
        location = finding.spec_path or "-"
        if finding.line is not None:
            location = f"{location}:{finding.line}"
        detail = f" ({finding.detail})" if finding.detail else ""
        print(f"  {finding.code} {location}{detail}")


def _print_json(findings: Sequence[SpecFinding], summary: dict[str, object]) -> None:
    payload = {
        "summary": summary,
        "findings": [
            {
                "code": finding.code,
                "spec_path": finding.spec_path,
                "line": finding.line,
                "detail": finding.detail,
            }
            for finding in findings
        ],
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_spec_manifest.py",
        description=(
            "Validate specs/FILE_MANIFEST.md one-to-one ownership, extension mapping, heading "
            "template, index coverage, self-containment, and coordination-count consistency."
        ),
    )
    parser.add_argument("--check", action="store_true", help="Run the validation gate (required).")
    parser.add_argument(
        "--json", action="store_true", help="Emit a canonical structural JSON report."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Test-only: validate against a synthetic repository root instead of this checkout.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Test-only: use an explicit manifest path instead of specs/FILE_MANIFEST.md.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if not args.check:
        parser.error("--check is required")
        return 2

    repo_root = args.repo_root.resolve() if args.repo_root is not None else _default_repo_root()
    if not repo_root.is_dir():
        print(
            f"verify_spec_manifest: invocation error: repo root not found: {repo_root}",
            file=sys.stderr,
        )
        return 2

    manifest_path = (
        args.manifest.resolve()
        if args.manifest is not None
        else repo_root / "specs" / "FILE_MANIFEST.md"
    )
    if args.manifest is not None:
        try:
            manifest_path.relative_to(repo_root)
        except ValueError:
            print(
                "verify_spec_manifest: invocation error: --manifest must be inside --repo-root",
                file=sys.stderr,
            )
            return 2

    exit_code, findings, summary = _run_check(repo_root, manifest_path)

    if args.json:
        _print_json(findings, summary)
    else:
        _print_human(findings, summary)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
