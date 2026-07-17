"""Read-only, manifest-bound access to the reviewed public fixture corpus."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Never, cast

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_FIXTURE_MEDIA_TYPE: Final = "application/vnd.yoetz.fixture-case+json"
_FIXTURE_MANIFEST_SCHEMA: Final = "yoetz.fixture-manifest/1.0.0"
_FIXTURE_MANIFEST_VERSION: Final = "1.0.0"
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_MAX_JSON_DEPTH: Final = 64
_MAX_SAFE_INTEGER: Final = 2**53 - 1
_MANIFEST_FIELDS: Final = frozenset({"manifest_schema", "manifest_version", "members"})
_MEMBER_FIELDS: Final = frozenset({"path", "fixture_id", "media_type", "byte_length", "sha256"})


@dataclass(frozen=True, slots=True)
class _FixtureMember:
    path: str
    fixture_id: str
    byte_length: int
    sha256: str


@dataclass(frozen=True, slots=True)
class FixtureLoader:
    """An immutable binding to one reviewed fixture root and its manifest."""

    fixture_root: Path
    manifest_path: Path

    def load_bytes(self, path: str, /) -> bytes:
        member = self._members().get(_validate_relative_path(path))
        if member is None:
            raise ValueError("fixture_not_reviewed")

        candidate = self.fixture_root.joinpath(*PurePosixPath(member.path).parts)
        _require_plain_file(self.fixture_root, candidate)
        try:
            data = candidate.read_bytes()
        except OSError as exc:
            raise ValueError("fixture_unreadable") from exc
        if len(data) != member.byte_length:
            raise ValueError("fixture_size_mismatch")
        if hashlib.sha256(data).hexdigest() != member.sha256:
            raise ValueError("fixture_digest_mismatch")
        return data

    def load_json(self, path: str, /) -> JsonValue:
        return _strict_json(self.load_bytes(path))

    def _members(self) -> dict[str, _FixtureMember]:
        _require_plain_file(self.manifest_path.parent, self.manifest_path)
        try:
            manifest_bytes = self.manifest_path.read_bytes()
        except OSError as exc:
            raise ValueError("fixture_manifest_unreadable") from exc
        manifest = _strict_json(manifest_bytes)
        if not isinstance(manifest, dict) or frozenset(manifest) != _MANIFEST_FIELDS:
            raise ValueError("fixture_manifest_shape_invalid")
        if manifest["manifest_schema"] != _FIXTURE_MANIFEST_SCHEMA:
            raise ValueError("fixture_manifest_schema_invalid")
        if manifest["manifest_version"] != _FIXTURE_MANIFEST_VERSION:
            raise ValueError("fixture_manifest_version_invalid")
        raw_members = manifest["members"]
        if not isinstance(raw_members, list):
            raise ValueError("fixture_manifest_members_invalid")

        result: dict[str, _FixtureMember] = {}
        fixture_ids: set[str] = set()
        declared_order: list[str] = []
        for raw_member in raw_members:
            member = _parse_member(raw_member)
            if member.path in result:
                raise ValueError("fixture_manifest_duplicate_path")
            if member.fixture_id in fixture_ids:
                raise ValueError("fixture_manifest_duplicate_id")
            result[member.path] = member
            fixture_ids.add(member.fixture_id)
            declared_order.append(member.path)
        if declared_order != sorted(declared_order, key=lambda item: item.encode("ascii")):
            raise ValueError("fixture_manifest_order_invalid")
        return result


def build_fixture_loader() -> FixtureLoader:
    """Bind to the repository fixture corpus without reading it during collection."""

    repository_root = Path(__file__).resolve().parent.parent
    fixture_root = repository_root / "fixtures"
    return FixtureLoader(fixture_root=fixture_root, manifest_path=fixture_root / "manifest.json")


def load_fixture_bytes(path: str, /) -> bytes:
    return build_fixture_loader().load_bytes(path)


def load_fixture_json(path: str, /) -> JsonValue:
    return build_fixture_loader().load_json(path)


def _parse_member(value: JsonValue) -> _FixtureMember:
    if not isinstance(value, dict) or frozenset(value) != _MEMBER_FIELDS:
        raise ValueError("fixture_member_shape_invalid")
    path = value["path"]
    fixture_id = value["fixture_id"]
    media_type = value["media_type"]
    byte_length = value["byte_length"]
    sha256 = value["sha256"]
    if not isinstance(path, str):
        raise ValueError("fixture_member_path_invalid")
    path = _validate_relative_path(path)
    if not path.endswith(".case.json"):
        raise ValueError("fixture_member_path_invalid")
    if not isinstance(fixture_id, str) or not fixture_id:
        raise ValueError("fixture_member_id_invalid")
    if media_type != _FIXTURE_MEDIA_TYPE:
        raise ValueError("fixture_member_media_type_invalid")
    if isinstance(byte_length, bool) or not isinstance(byte_length, int) or byte_length < 0:
        raise ValueError("fixture_member_size_invalid")
    if not isinstance(sha256, str) or _SHA256_PATTERN.fullmatch(sha256) is None:
        raise ValueError("fixture_member_digest_invalid")
    return _FixtureMember(
        path=path,
        fixture_id=fixture_id,
        byte_length=byte_length,
        sha256=sha256,
    )


def _validate_relative_path(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("fixture_path_wrong_type")
    if not value or "\\" in value:
        raise ValueError("fixture_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise ValueError("fixture_path_invalid")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("fixture_path_invalid")
    return value


def _require_plain_file(root: Path, candidate: Path) -> None:
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("fixture_path_escape") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("fixture_symlink_forbidden")
    if not candidate.is_file():
        raise ValueError("fixture_missing")


def _reject_number(_: str) -> Never:
    raise ValueError("fixture_json_number_forbidden")


def _object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("fixture_json_duplicate_key")
        result[key] = value
    return result


def _strict_json(data: bytes) -> JsonValue:
    if data.startswith(b"\xef\xbb\xbf") or b"\x00" in data:
        raise ValueError("fixture_json_encoding_invalid")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("fixture_json_encoding_invalid") from exc
    try:
        parsed = cast(
            object,
            json.loads(
                text,
                object_pairs_hook=_object_from_pairs,
                parse_float=_reject_number,
                parse_constant=_reject_number,
            ),
        )
    except json.JSONDecodeError as exc:
        raise ValueError("fixture_json_invalid") from exc
    return _validate_json_value(parsed)


def _validate_json_value(value: object, *, depth: int = 0) -> JsonValue:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("fixture_json_too_deep")
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        if not -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
            raise ValueError("fixture_json_integer_out_of_range")
        return value
    if isinstance(value, list):
        sequence = cast(list[object], value)
        return [_validate_json_value(item, depth=depth + 1) for item in sequence]
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        result: dict[str, JsonValue] = {}
        for key, item in mapping.items():
            if not isinstance(key, str):
                raise TypeError("fixture_json_nonstring_key")
            result[key] = _validate_json_value(item, depth=depth + 1)
        return result
    raise TypeError("fixture_json_type_invalid")
