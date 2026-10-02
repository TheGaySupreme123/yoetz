"""Exact-span TOML table helpers shared by the host-configuration adapters.

Yoetz never rewrites owner-authored TOML. It appends whole generated tables and removes only a
table whose entire byte span is still exactly the block it generated: matching a generated prefix
is insufficient, because an owner-added key would then survive removal at the parent scope.
"""

from __future__ import annotations

import re
from typing import Final, cast

__all__ = ["append_table_block", "exact_table_span", "strip_exact_table"]

_TOML_TABLE_HEADER_RE: Final = re.compile(rb"[ \t]*\[\[?[^\r\n\]]+\]\]?[ \t]*(?:#[^\r\n]*)?")


def exact_table_span(raw: bytes, table: str) -> tuple[int, int] | None:
    """Return the whole byte span only when one TOML table is exactly ``table``.

    Blank separator lines are outside the table identity, but comments and fields before the
    next header are not. A header that appears more than once is never matched.
    """

    expected = table.encode("utf-8")
    header = expected.splitlines(keepends=True)[0]
    lines = raw.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        if line == header:
            offsets.append(offset)
        offset += len(line)
    if len(offsets) != 1:
        return None
    start = offsets[0]
    end = len(raw)
    offset = 0
    seen = False
    for line in lines:
        if offset == start:
            seen = True
        elif seen and _TOML_TABLE_HEADER_RE.fullmatch(line.rstrip(b"\r\n")):
            end = offset
            break
        offset += len(line)
    candidate = raw[start:end].rstrip(b"\r\n") + b"\n"
    if candidate != expected:
        return None
    return start, end


def strip_exact_table(raw: bytes, table: str) -> bytes:
    """Remove one exact generated table, and only the separator newline that preceded it."""

    span = exact_table_span(raw, table)
    if span is None:
        return raw
    start, _end = span
    table_bytes = table.encode("utf-8")
    before = raw[:start]
    after = raw[start + len(table_bytes) :]
    # Generation inserts exactly one separator newline before its first block and between
    # generated blocks. Remove only that adjacent byte; never normalize blank lines elsewhere in
    # owner-authored TOML.
    if before.endswith(b"\n\n"):
        before = before[:-1]
    merged = before + after
    if merged in {b"", b"\n"}:
        return b""
    if not merged.endswith(b"\n"):
        merged += b"\n"
    return merged


def append_table_block(raw: bytes, block: str) -> bytes:
    """Append one generated block after exactly one separator newline."""

    if not block:
        return raw
    prefix = raw
    if prefix and not prefix.endswith(b"\n"):
        prefix += b"\n"
    if prefix:
        prefix += b"\n"
    return prefix + block.encode("utf-8")


def _bind_native() -> None:
    from yoetz._native import native_functions

    # ``append_table_block`` stays in Python: it is one concatenation, already as fast as a call.
    resolved = native_functions("toml_exact_table_span", "toml_strip_exact_table")
    if resolved is None:
        return
    native_span, native_strip = resolved
    python_span = exact_table_span
    python_strip = strip_exact_table
    header_re = _TOML_TABLE_HEADER_RE

    # Only exact ``bytes``/``str`` take the native path: a ``bytearray`` or subclass keeps the
    # reference's own semantics, and an empty table keeps the reference's ``IndexError``. A
    # replaced header pattern defers too.
    def native_exact_table_span(raw: bytes, table: str) -> tuple[int, int] | None:
        if (
            type(raw) is bytes
            and type(table) is str
            and table
            and _TOML_TABLE_HEADER_RE is header_re
        ):
            return cast(tuple[int, int] | None, native_span(raw, table))
        return python_span(raw, table)

    def native_strip_exact_table(raw: bytes, table: str) -> bytes:
        if (
            type(raw) is bytes
            and type(table) is str
            and table
            and _TOML_TABLE_HEADER_RE is header_re
            and exact_table_span is native_exact_table_span
        ):
            return cast(bytes, native_strip(raw, table))
        return python_strip(raw, table)

    globals().update(
        exact_table_span=native_exact_table_span,
        strip_exact_table=native_strip_exact_table,
    )


_bind_native()
