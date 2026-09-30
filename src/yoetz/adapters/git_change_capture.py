"""Bounded, read-only check-time change capture from one validated Git workspace (ADR-031).

The service calls this adapter with the repository root its own authenticated control connection
supplied. Every Git call goes through the hardened runner of ``git_subject_state`` (no shell, no
global or system config, no hooks, fsmonitor, external diff, textconv or credential helper), with
Git transports disabled so a partial clone cannot fetch. Untracked files are opened one path
component at a time relative to the validated root descriptor without following symlinks, so a
link or a hard link can never make the capture read outside the workspace.

The result is plain text: a short header naming the base, the file counts and every changed file,
then a unified diff. It is bounded to ``MAX_CHECK_CHANGE_TEXT_BYTES``. When the change is larger,
whole files are left out rather than cut mid-hunk, and the header names each file not shown.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from yoetz.adapters.git_subject_state import (
    GitOutputTruncated,
    discover_workspace_root,
    local_workspace_root,
    open_local_workspace,
    run_read_only_git,
)
from yoetz.ports.change_capture import (
    MAX_CHECK_CHANGE_TEXT_BYTES,
    ChangeBaseKind,
    ChangeCaptureUnavailable,
    CheckChangeCapture,
    TaskChangeBase,
)
from yoetz.ports.subject_state import LocalWorkspaceHandle

__all__ = ["GitChangeCaptureAdapter"]

_EMPTY_TREES: Final = {
    "sha1": "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
    "sha256": "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321",
}
_CAPTURE_DEADLINE_SECONDS: Final = 20.0
_GIT_CALL_SECONDS: Final = 10.0
_GLOBAL_CONFIG_SECONDS: Final = 5.0
_LIST_LIMIT: Final = 8 * 1024 * 1024
_WHOLE_DIFF_LIMIT: Final = 8 * 1024 * 1024
_PER_FILE_DIFF_LIMIT: Final = 1024 * 1024
_MAX_PER_FILE_DIFFS: Final = 400
_MAX_UNTRACKED_FILES: Final = 500
_MAX_UNTRACKED_FILE_BYTES: Final = 65_536
_BINARY_PROBE_BYTES: Final = 8_000
_HEADER_BUDGET: Final = 24_576
_MAX_LISTED_FILES: Final = 100
_MAX_LISTED_PATH_BYTES: Final = 200
# Room kept for the header lines written after the file listing.
_HEADER_TRAILER_ROOM: Final = 512
_READ_CHUNK: Final = 65_536
# Transports are disabled outright: diffing a partial clone must fail, never fetch a blob.
_CAPTURE_CONFIG: Final = ("-c", "core.quotePath=true", "-c", "protocol.allow=never")
_DIFF_OPTIONS: Final = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-renames",
    "--no-relative",
    "--submodule=short",
    "-O/dev/null",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)
# ``--unified`` implies a patch, so it is added only where the patch itself is read.
_PATCH_OPTIONS: Final = (*_DIFF_OPTIONS, "--unified=3")
_DIFF_SECTION: Final = b"diff --git "
# Files with these names hold local credentials far more often than code, and the pattern scanner
# cannot recognise every provider's key format (``DB_PASS=...``, ``rk_live_...``). Tracked or
# untracked, they are listed by name only and their content is never shown (ADR-031). The list is a
# name heuristic: it catches common conventions, not every file that holds a secret.
_CREDENTIAL_NAMES: Final = frozenset(
    {
        b".env",
        b".envrc",
        b".git-credentials",
        b".htpasswd",
        b".netrc",
        b"_netrc",
        b".npmrc",
        b".pgpass",
        b".pypirc",
        b"credentials",
        b"credentials.json",
        b"id_dsa",
        b"id_ecdsa",
        b"id_ed25519",
        b"id_rsa",
    }
)
_CREDENTIAL_PREFIXES: Final = (b".env.",)
_CREDENTIAL_SUFFIXES: Final = (
    b".jks",
    b".kdbx",
    b".key",
    b".keystore",
    b".p12",
    b".pem",
    b".pfx",
    b".tfstate",
    b".tfvars",
)


def _credential_name(path: bytes) -> bool:
    name = path.rsplit(b"/", 1)[-1].lower()
    return (
        name in _CREDENTIAL_NAMES
        or name.startswith(_CREDENTIAL_PREFIXES)
        or name.endswith(_CREDENTIAL_SUFFIXES)
    )


type _OmitReason = Literal[
    "capture_limit", "too_large", "not_regular_file", "unreadable", "credential_name"
]
_OMIT_TEXT: Final[dict[_OmitReason, str]] = {
    "credential_name": "not shown: listed by name only, as for every file named like this",
    "capture_limit": "not shown: the capture reached its size, time or file limit",
    "too_large": "not shown: too large for this capture",
    "not_regular_file": "not shown: links, special files and multiply linked files are never read",
    "unreadable": "not shown: could not be read safely",
}


@dataclass(slots=True)
class _Section:
    path: bytes
    status: str
    untracked: bool
    added: str
    deleted: str
    text: bytes | None
    omitted: _OmitReason | None


def _quote_path(path: bytes) -> str:
    """Quote one Git path the way ``core.quotePath=true`` does; ASCII-only output."""

    if not any(byte < 0x20 or byte >= 0x7F or byte in b'"\\' for byte in path):
        return path.decode("ascii")
    escapes = {7: "\\a", 8: "\\b", 9: "\\t", 10: "\\n", 11: "\\v", 12: "\\f", 13: "\\r"}
    rendered: list[str] = ['"']
    for byte in path:
        if byte in escapes:
            rendered.append(escapes[byte])
        elif byte in b'"\\':
            rendered.append("\\" + chr(byte))
        elif byte < 0x20 or byte >= 0x7F:
            rendered.append(f"\\{byte:03o}")
        else:
            rendered.append(chr(byte))
    rendered.append('"')
    return "".join(rendered)


def _prefixed(prefix: str, path: bytes) -> str:
    quoted = _quote_path(path)
    if quoted.startswith('"'):
        return '"' + prefix + quoted[1:]
    return prefix + quoted


def _nul_fields(payload: bytes) -> list[bytes]:
    if not payload:
        return []
    if not payload.endswith(b"\0"):
        raise ChangeCaptureUnavailable("git_failed")
    return payload[:-1].split(b"\0")


def _safe_relative(path: bytes) -> bool:
    return (
        bool(path)
        and not path.startswith(b"/")
        and all(part not in {b"", b".", b".."} for part in path.split(b"/"))
    )


def _decode(text: bytes) -> bytes:
    """Return valid UTF-8: undecodable bytes become U+FFFD, never raw bytes in the object."""

    return text.decode("utf-8", errors="replace").encode("utf-8")


def _section_path(section: bytes) -> bytes | None:
    """Return the path of one ``diff --git a/P b/P`` section header written with ``--no-renames``."""

    line = section.split(b"\n", 1)[0]
    rest = line[len(_DIFF_SECTION) :]
    if rest.startswith(b'"'):
        # Quoted headers keep both sides quoted; decode the first quoted operand.
        out = bytearray()
        index = 1
        escapes = {ord("a"): 7, ord("b"): 8, ord("t"): 9, ord("n"): 10}
        escapes.update({ord("v"): 11, ord("f"): 12, ord("r"): 13})
        while index < len(rest):
            byte = rest[index]
            if byte == ord('"'):
                value = bytes(out)
                return value[2:] if value.startswith(b"a/") else None
            if byte == ord("\\") and index + 1 < len(rest):
                following = rest[index + 1]
                if following in escapes:
                    out.append(escapes[following])
                    index += 2
                    continue
                if 0x30 <= following <= 0x37 and index + 3 < len(rest):
                    out.append(int(rest[index + 1 : index + 4], 8) & 0xFF)
                    index += 4
                    continue
                out.append(following)
                index += 2
                continue
            out.append(byte)
            index += 1
        return None
    # Unquoted: ``a/P b/P`` with the same P on both sides.
    if not rest.startswith(b"a/"):
        return None
    body = rest[2:]
    if len(body) < 3 or (len(body) - 3) % 2:
        return None
    half = (len(body) - 3) // 2
    left, middle, right = body[:half], body[half : half + 3], body[half + 3 :]
    if middle != b" b/" or left != right:
        return None
    return left


class GitChangeCaptureAdapter:
    """Read the task base and render one bounded check-time change for a local workspace."""

    def __init__(
        self,
        *,
        _deadline_seconds: float = _CAPTURE_DEADLINE_SECONDS,
        _max_text_bytes: int = MAX_CHECK_CHANGE_TEXT_BYTES,
    ) -> None:
        if not 0.0 < _deadline_seconds <= 120.0:
            raise ValueError("change_capture_deadline_invalid")
        if not _HEADER_BUDGET + 1_024 <= _max_text_bytes <= MAX_CHECK_CHANGE_TEXT_BYTES:
            raise ValueError("change_capture_limit_invalid")
        self._deadline_seconds = _deadline_seconds
        self._max_text_bytes = _max_text_bytes

    # --- public port -------------------------------------------------------------------------

    def read_task_base(self, workspace: str) -> TaskChangeBase:
        deadline = time.monotonic() + self._deadline_seconds
        handle = self._open(workspace, deadline)
        object_format = self._object_format(handle, deadline)
        head = self._rev(handle, "HEAD^{commit}", deadline)
        # A repository with no commit yet starts from the empty tree, so every file the task adds
        # is still part of its change when HEAD later gains commits.
        return TaskChangeBase(object_format, head or _EMPTY_TREES[object_format])

    def capture(self, workspace: str, base: TaskChangeBase | None) -> CheckChangeCapture:
        # One deadline bounds the whole capture: every Git call gets at most what is left of it.
        deadline = time.monotonic() + self._deadline_seconds
        handle = self._open(workspace, deadline)
        self._refuse_unsafe_config(handle, deadline)
        object_format = self._object_format(handle, deadline)
        base_kind: ChangeBaseKind
        base_id: str | None = None
        if (
            base is not None
            and base.object_format == object_format
            and self._rev(handle, base.commit + "^{tree}", deadline) is not None
        ):
            base_kind, base_id = "task_start", base.commit
        else:
            head = self._rev(handle, "HEAD^{commit}", deadline)
            if head is not None:
                base_kind, base_id = "head", head
            else:
                base_kind, base_id = "empty", _EMPTY_TREES[object_format]
        sections = self._tracked_sections(handle, base_id, deadline)
        untracked_total, untracked_sections, listing_truncated = 0, [], True
        if not _expired(deadline):
            try:
                untracked_total, untracked_sections, listing_truncated = self._untracked_sections(
                    handle, deadline
                )
            except ChangeCaptureUnavailable:
                if not _expired(deadline):
                    raise
        # Past the deadline no untracked file is listed or read; the header says the untracked
        # count is a lower bound, and the tracked change above still stands.
        sections.extend(untracked_sections)
        sections.sort(key=lambda item: item.path)
        return self._render(
            base_kind,
            sections,
            tracked=len(sections) - len(untracked_sections),
            untracked=untracked_total,
            unlisted_untracked=untracked_total - len(untracked_sections),
            untracked_listing_truncated=listing_truncated,
        )

    # --- Git access --------------------------------------------------------------------------

    @staticmethod
    def _open(workspace: str, deadline: float) -> LocalWorkspaceHandle:
        if shutil.which("git", path=os.defpath) is None:
            raise ChangeCaptureUnavailable("git_unavailable")
        try:
            root = discover_workspace_root(Path(workspace), timeout_seconds=_remaining(deadline))
            return open_local_workspace(root, timeout_seconds=_remaining(deadline))
        except ValueError as exc:
            if time.monotonic() >= deadline:
                raise ChangeCaptureUnavailable("git_failed") from None
            reason = str(exc)
            if reason == "not_git":
                raise ChangeCaptureUnavailable("not_git") from None
            if reason in {"unsafe_root"}:
                raise ChangeCaptureUnavailable("unsafe_root") from None
            raise ChangeCaptureUnavailable("unsupported_repository") from None

    @staticmethod
    def _git(
        handle: LocalWorkspaceHandle,
        arguments: Sequence[str],
        *,
        deadline: float,
        limit: int,
        accepted: frozenset[int] = frozenset({0}),
        keep_prefix_on_limit: bool = False,
    ) -> tuple[int, bytes]:
        try:
            return run_read_only_git(
                handle,
                (*_CAPTURE_CONFIG, *arguments),
                stdout_limit=limit,
                timeout_seconds=_remaining(deadline),
                accepted_returncodes=accepted,
                keep_prefix_on_limit=keep_prefix_on_limit,
            )
        except ValueError as exc:
            if str(exc) == "git_output_limit":
                raise
            raise ChangeCaptureUnavailable("git_failed") from None

    def _object_format(
        self, handle: LocalWorkspaceHandle, deadline: float
    ) -> Literal["sha1", "sha256"]:
        _, raw = self._git(
            handle, ("rev-parse", "--show-object-format"), deadline=deadline, limit=32
        )
        value = raw.strip()
        if value == b"sha1":
            return "sha1"
        if value == b"sha256":
            return "sha256"
        raise ChangeCaptureUnavailable("unsupported_repository")

    def _rev(self, handle: LocalWorkspaceHandle, spec: str, deadline: float) -> str | None:
        code, raw = self._git(
            handle,
            ("rev-parse", "--verify", "--quiet", "--end-of-options", spec),
            deadline=deadline,
            limit=128,
            accepted=frozenset({0, 1, 128}),
        )
        if code != 0:
            return None
        value = raw.strip().decode("ascii", errors="replace")
        if not value or any(character not in "0123456789abcdef" for character in value):
            raise ChangeCaptureUnavailable("git_failed")
        return value

    def _refuse_unsafe_config(self, handle: LocalWorkspaceHandle, deadline: float) -> None:
        """Refuse any effective repository config that could run a command or fetch.

        The ADR-011 metadata fence reads ``.git/config`` lexically, so a filter driver defined in
        ``config.worktree`` (``extensions.worktreeConfig``), through an ``include`` there, or on a
        line that opens two sections (``[core][filter "x"]``) passes it, and ``git diff`` against
        the working tree then runs that driver's ``clean`` command. Git's own view of its
        effective config names every such key, whichever file or include it came from.
        ``--show-scope`` needs Git 2.26; an older Git rejects it, and the capture is then
        unavailable rather than taken without this check.
        """

        try:
            _, listing = self._git(
                handle,
                ("config", "--list", "--name-only", "--includes", "--show-scope", "-z"),
                deadline=deadline,
                limit=1_048_576,
            )
        except ValueError:
            raise ChangeCaptureUnavailable("unsupported_repository") from None
        fields = _nul_fields(listing)
        if len(fields) % 2:
            raise ChangeCaptureUnavailable("git_failed")
        for index in range(0, len(fields), 2):
            if fields[index] == b"command":
                # The hardened runner's own ``-c`` overrides.
                continue
            name = fields[index + 1].lower()
            if (
                name.startswith((b"filter.", b"include.", b"includeif."))
                or name == b"extensions.partialclone"
                or (
                    name.startswith(b"remote.")
                    and name.endswith((b".promisor", b".partialclonefilter"))
                )
            ):
                raise ChangeCaptureUnavailable("unsupported_repository")

    def _tracked_sections(
        self, handle: LocalWorkspaceHandle, base_id: str, deadline: float
    ) -> list[_Section]:
        try:
            _, names = self._git(
                handle,
                ("diff", "--name-status", "-z", *_DIFF_OPTIONS, base_id, "--"),
                deadline=deadline,
                limit=_LIST_LIMIT,
            )
            _, numbers = self._git(
                handle,
                ("diff", "--numstat", "-z", *_DIFF_OPTIONS, base_id, "--"),
                deadline=deadline,
                limit=_LIST_LIMIT,
            )
        except ValueError:
            raise ChangeCaptureUnavailable("unsupported_repository") from None
        fields = _nul_fields(names)
        if len(fields) % 2:
            raise ChangeCaptureUnavailable("git_failed")
        entries: list[tuple[str, bytes]] = []
        for index in range(0, len(fields), 2):
            status = fields[index].decode("ascii", errors="replace")[:1] or "M"
            path = fields[index + 1]
            if not _safe_relative(path):
                raise ChangeCaptureUnavailable("unsafe_root")
            entries.append((status, path))
        counts: dict[bytes, tuple[str, str]] = {}
        for record in _nul_fields(numbers):
            parts = record.split(b"\t", 2)
            if len(parts) != 3:
                raise ChangeCaptureUnavailable("git_failed")
            counts[parts[2]] = (
                parts[0].decode("ascii", errors="replace"),
                parts[1].decode("ascii", errors="replace"),
            )
        sections = [
            _Section(
                path,
                status,
                False,
                *counts.get(path, ("-", "-")),
                None,
                "credential_name" if _credential_name(path) else None,
            )
            for status, path in entries
        ]
        if not sections:
            return sections
        by_path = {section.path: section for section in sections}
        try:
            _, whole = self._git(
                handle,
                ("diff", *_PATCH_OPTIONS, base_id, "--"),
                deadline=deadline,
                limit=_WHOLE_DIFF_LIMIT,
            )
        except ValueError:
            whole = None
        except ChangeCaptureUnavailable:
            if not _expired(deadline):
                raise
            # The deadline ran out inside the diff: every changed file is listed, none shown.
            for section in sections:
                if section.omitted is None:
                    section.omitted = "capture_limit"
            return sections
        if whole is not None:
            for chunk in self._split_sections(whole):
                path = _section_path(chunk)
                section = by_path.get(path) if path is not None else None
                if section is not None and section.text is None and section.omitted is None:
                    section.text = _decode(chunk)
            for section in sections:
                if section.text is None and section.omitted is None:
                    section.omitted = "unreadable"
            return sections
        # The whole diff is larger than one bounded read. Fall back to one bounded read per file
        # so an oversized generated file cannot hide the rest of the change.
        count = 0
        for section in sections:
            if section.omitted is not None:
                continue
            if count >= _MAX_PER_FILE_DIFFS or _expired(deadline):
                section.omitted = "capture_limit"
                continue
            count += 1
            try:
                _, text = self._git(
                    handle,
                    (
                        "diff",
                        *_PATCH_OPTIONS,
                        base_id,
                        "--",
                        ":(literal)" + os.fsdecode(section.path),
                    ),
                    deadline=deadline,
                    limit=_PER_FILE_DIFF_LIMIT,
                )
            except ValueError:
                section.omitted = "too_large"
                continue
            except ChangeCaptureUnavailable:
                if not _expired(deadline):
                    raise
                # The deadline ran out inside this file's diff: it and the rest are not shown.
                section.omitted = "capture_limit"
                continue
            section.text = _decode(text) if text else None
            if section.text is None:
                section.omitted = "unreadable"
        return sections

    @staticmethod
    def _split_sections(diff: bytes) -> list[bytes]:
        if not diff:
            return []
        chunks: list[bytes] = []
        start = 0 if diff.startswith(_DIFF_SECTION) else diff.find(b"\n" + _DIFF_SECTION) + 1
        if start <= 0 and not diff.startswith(_DIFF_SECTION):
            return []
        while start < len(diff):
            following = diff.find(b"\n" + _DIFF_SECTION, start)
            end = len(diff) if following < 0 else following + 1
            chunks.append(diff[start:end])
            start = end
        return chunks

    def _untracked_sections(
        self, handle: LocalWorkspaceHandle, deadline: float
    ) -> tuple[int, list[_Section], bool]:
        """Return the untracked total, their sections, and whether the listing was cut short."""

        excludes = self._repository_excludes_file(handle, deadline)
        if excludes is None:
            excludes = _global_excludes_file(deadline)
        config = ("-c", f"core.excludesFile={excludes}") if excludes is not None else ()
        listing_truncated = False
        try:
            _, listing = self._git(
                handle,
                (*config, "ls-files", "--others", "--exclude-standard", "-z"),
                deadline=deadline,
                limit=_LIST_LIMIT,
                keep_prefix_on_limit=True,
            )
        except GitOutputTruncated as exc:
            # Too many untracked paths to list: keep every whole path that fit and disclose the
            # rest as not listed, rather than losing the tracked change along with them.
            listing = exc.prefix[: exc.prefix.rfind(b"\0") + 1]
            listing_truncated = True
        except ValueError:
            raise ChangeCaptureUnavailable("unsupported_repository") from None
        paths = sorted(path for path in _nul_fields(listing))
        _, root_descriptor = local_workspace_root(handle)
        sections: list[_Section] = []
        for index, path in enumerate(paths):
            if index >= _MAX_UNTRACKED_FILES:
                break
            if path.endswith(b"/"):
                # An embedded repository is listed as its directory; its files are not this
                # repository's change.
                sections.append(_Section(path, "A", True, "-", "-", None, "not_regular_file"))
                continue
            if not _safe_relative(path):
                raise ChangeCaptureUnavailable("unsafe_root")
            if _credential_name(path):
                sections.append(_Section(path, "A", True, "-", "-", None, "credential_name"))
                continue
            if _expired(deadline):
                sections.append(_Section(path, "A", True, "-", "-", None, "capture_limit"))
                continue
            outcome, content = _read_beneath(root_descriptor, path, _MAX_UNTRACKED_FILE_BYTES)
            if outcome != "regular" and outcome != "executable":
                sections.append(_Section(path, "A", True, "-", "-", None, outcome))
                continue
            assert content is not None
            text, added = _new_file_diff(path, content, outcome == "executable")
            sections.append(_Section(path, "A", True, added, "0", text, None))
        return len(paths), sections, listing_truncated

    def _repository_excludes_file(
        self, handle: LocalWorkspaceHandle, deadline: float
    ) -> str | None:
        """Return a repository-level ``core.excludesFile``, which Git prefers over the global one.

        The hardened runner pins ``core.excludesFile`` on its command line, which would otherwise
        hide the repository's own setting and let files the owner ignores there be read.
        """

        code, raw = self._git(
            handle,
            ("config", "--get-all", "--show-scope", "-z", "core.excludesFile"),
            deadline=deadline,
            limit=65_536,
            accepted=frozenset({0, 1}),
        )
        if code != 0:
            return None
        fields = _nul_fields(raw)
        if len(fields) % 2:
            raise ChangeCaptureUnavailable("git_failed")
        value: bytes | None = None
        for index in range(0, len(fields), 2):
            # Lowest precedence first; the runner's own ``command`` entry is last and skipped.
            if fields[index] in {b"local", b"worktree"}:
                value = fields[index + 1]
        if not value:
            return None
        root, _ = local_workspace_root(handle)
        path = os.path.expanduser(os.fsdecode(value))
        return _existing_regular_file(path if os.path.isabs(path) else os.path.join(root, path))

    # --- rendering ---------------------------------------------------------------------------

    def _render(
        self,
        base: ChangeBaseKind,
        sections: list[_Section],
        *,
        tracked: int,
        untracked: int,
        unlisted_untracked: int,
        untracked_listing_truncated: bool = False,
    ) -> CheckChangeCapture:
        body_budget = self._max_text_bytes - _HEADER_BUDGET
        body: list[bytes] = []
        used = 0
        for section in sections:
            if section.text is None:
                continue
            if used + len(section.text) > body_budget:
                section.text = None
                section.omitted = "capture_limit"
                continue
            body.append(section.text)
            used += len(section.text)
        omitted = sum(1 for section in sections if section.omitted is not None)
        omitted += unlisted_untracked
        header = _header(
            base,
            sections,
            tracked=tracked,
            untracked=untracked,
            omitted=omitted,
            unlisted_untracked=unlisted_untracked,
            untracked_listing_truncated=untracked_listing_truncated,
        )
        text = header + b"".join(body)
        return CheckChangeCapture(
            base=base,
            text=text,
            tracked_files=tracked,
            untracked_files=untracked,
            omitted_files=omitted,
            truncated=omitted > 0 or untracked_listing_truncated,
        )


def _header(
    base: ChangeBaseKind,
    sections: list[_Section],
    *,
    tracked: int,
    untracked: int,
    omitted: int,
    unlisted_untracked: int,
    untracked_listing_truncated: bool = False,
) -> bytes:
    lines = [
        "Yoetz check-time change: captured by the Yoetz service when this check ran; "
        "the agent did not supply it.",
        {
            "task_start": "Base: the commit HEAD named when this task started.",
            "head": (
                "Base: HEAD when this check ran. The commit at task start was not recorded or "
                "is no longer available, so commits made during the task may be missing from "
                "this change."
            ),
            "empty": "Base: an empty tree. The repository had no commit when this check ran.",
        }[base],
        "Scope: committed and uncommitted changes to tracked files since the base, plus "
        "untracked files Git does not ignore. Uncommitted or untracked work that already existed "
        "when the task started is included too.",
    ]
    if not tracked and not untracked and not untracked_listing_truncated:
        lines.append("Changed files: none. The working tree matches the base.")
    else:
        lines.append(
            f"Changed files: {tracked} tracked, {untracked} untracked. Not shown: {omitted}."
        )
        lines.append("Files:")
        # The body was fitted to ``_HEADER_BUDGET`` before this header existed, so the listing
        # stops early rather than letting long paths and notes overrun the object bound.
        used = sum(len(line) + 1 for line in lines) + _HEADER_TRAILER_ROOM
        listed = 0
        for section in sections[:_MAX_LISTED_FILES]:
            quoted = _quote_path(section.path)
            if len(quoted) > _MAX_LISTED_PATH_BYTES:
                quoted = quoted[: _MAX_LISTED_PATH_BYTES - 3] + "..."
            if section.added != "-":
                counts = f" (+{section.added} -{section.deleted})"
            elif section.untracked and section.text is None:
                counts = ""
            else:
                counts = " (binary)"
            notes = [
                note
                for note in (
                    "untracked" if section.untracked else "",
                    "" if section.omitted is None else _OMIT_TEXT[section.omitted],
                )
                if note
            ]
            suffix = "" if not notes else " " + "; ".join(notes)
            line = f"  {section.status} {quoted}{counts}{suffix}"
            if used + len(line) + 1 > _HEADER_BUDGET:
                break
            lines.append(line)
            used += len(line) + 1
            listed += 1
        if len(sections) > listed:
            hidden = sum(1 for section in sections[listed:] if section.omitted)
            lines.append(
                f"  ... {len(sections) - listed} more changed files are not listed "
                f"({hidden} of them not shown)."
            )
        if unlisted_untracked:
            lines.append(
                f"  ... {unlisted_untracked} more untracked files are not listed or shown "
                "(untracked file limit)."
            )
        if untracked_listing_truncated:
            lines.append(
                "  ... further untracked files may exist but are not listed or shown: the "
                "untracked listing hit its size or time limit, so the untracked count above is a "
                "lower bound."
            )
    lines.append("End of header. The unified diff follows.")
    return ("\n".join(lines) + "\n").encode("ascii", errors="replace")


def _new_file_diff(path: bytes, content: bytes, executable: bool) -> tuple[bytes, str]:
    mode = "100755" if executable else "100644"
    head = [
        f"diff --git {_prefixed('a/', path)} {_prefixed('b/', path)}",
        f"new file mode {mode}",
    ]
    if not content:
        return ("\n".join(head) + "\n").encode("ascii"), "0"
    if b"\0" in content[:_BINARY_PROBE_BYTES]:
        head.append(f"Binary files /dev/null and {_prefixed('b/', path)} differ")
        return ("\n".join(head) + "\n").encode("ascii"), "-"
    lines = content.split(b"\n")
    trailing_newline = content.endswith(b"\n")
    if trailing_newline:
        lines = lines[:-1]
    head.append("--- /dev/null")
    head.append(f"+++ {_prefixed('b/', path)}")
    head.append(f"@@ -0,0 +1,{len(lines)} @@" if len(lines) != 1 else "@@ -0,0 +1 @@")
    rendered = ("\n".join(head) + "\n").encode("ascii") + b"".join(
        b"+" + line + b"\n" for line in lines
    )
    if not trailing_newline:
        rendered += b"\\ No newline at end of file\n"
    return _decode(rendered), str(len(lines))


def _read_beneath(
    root_descriptor: int, path: bytes, limit: int
) -> tuple[_OmitReason | Literal["regular", "executable"], bytes | None]:
    """Open ``path`` under the root one component at a time, never following a link."""

    parts = path.split(b"/")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    opened: list[int] = []
    current = root_descriptor
    try:
        for part in parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            opened.append(current)
        descriptor = os.open(parts[-1], file_flags, dir_fd=current)
        opened.append(descriptor)
        facts = os.fstat(descriptor)
        expected_uid = os.geteuid() if hasattr(os, "geteuid") else os.getuid()
        if not stat.S_ISREG(facts.st_mode) or facts.st_uid != expected_uid:
            return "not_regular_file", None
        if facts.st_nlink != 1:
            # A second name can be a link to content outside the workspace.
            return "not_regular_file", None
        if facts.st_size > limit:
            return "too_large", None
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(_READ_CHUNK, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                return "too_large", None
        executable = bool(stat.S_IMODE(facts.st_mode) & 0o111)
        return ("executable" if executable else "regular"), b"".join(chunks)
    except OSError as exc:
        # ELOOP/ENOTDIR: a component is a link or not a directory. Anything else is unreadable.
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENXIO}:
            return "not_regular_file", None
        return "unreadable", None
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _remaining(deadline: float) -> float:
    """Time left for one Git call: at most ``_GIT_CALL_SECONDS`` and never past the deadline."""

    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        raise ChangeCaptureUnavailable("git_failed")
    return min(_GIT_CALL_SECONDS, remaining)


def _expired(deadline: float) -> bool:
    return time.monotonic() >= deadline


def _global_excludes_file(deadline: float) -> str | None:
    """Return the owner's own global Git ignore file, read without loading global config helpers.

    The hardened runner disables global config, which would otherwise make files the owner ignores
    everywhere (for example local secrets) look untracked. Only the ``core.excludesFile`` value is
    read here; no other global setting reaches the capture. The read shares the capture deadline,
    and a read that runs out of time makes the capture unavailable rather than guessing the file.
    """

    executable = shutil.which("git", path=os.defpath)
    home = os.environ.get("HOME") or os.path.expanduser("~")
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": home,
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
    }
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        environment["XDG_CONFIG_HOME"] = xdg
    candidate: str | None = None
    if executable is not None:
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                (
                    executable,
                    "config",
                    "--global",
                    "--includes",
                    "--path",
                    "--get",
                    "core.excludesFile",
                ),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=min(_GLOBAL_CONFIG_SECONDS, _remaining(deadline)),
                env=environment,
                cwd=home if os.path.isdir(home) else "/",
                check=False,
            )
            if completed.returncode == 0 and len(completed.stdout) <= 4_096:
                candidate = os.fsdecode(completed.stdout.rstrip(b"\n")) or None
        except subprocess.TimeoutExpired:
            raise ChangeCaptureUnavailable("git_failed") from None
        except OSError, subprocess.SubprocessError:
            candidate = None
    if candidate is None:
        base = xdg if xdg else os.path.join(home, ".config")
        candidate = os.path.join(base, "git", "ignore")
    return _existing_regular_file(candidate)


def _existing_regular_file(candidate: str) -> str | None:
    if not os.path.isabs(candidate) or any(marker in candidate for marker in "\0\n\r"):
        return None
    try:
        if not stat.S_ISREG(os.stat(candidate).st_mode):
            return None
    except OSError:
        return None
    return candidate
