"""Frozen, canonical value types used by the pure Yoetz domain."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from functools import total_ordering
from types import MappingProxyType, NotImplementedType
from typing import Final, NewType, cast, final

from yoetz.protocol.canonical import (
    MAX_JSON_DEPTH,
    canonical_integer_string,
    ensure_canonical_value,
    parse_canonical_integer_string,
)
from yoetz.protocol.coverage import AuthorshipAssurance
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.ids import IdKind, validate_actor_id, validate_id

__all__ = [
    "GENESIS_DIGEST",
    "ActionId",
    "Actor",
    "ActorId",
    "ActorType",
    "ClaimId",
    "EventId",
    "EvidenceId",
    "FindingId",
    "Frontier",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "ObjectId",
    "ObligationId",
    "ReceiptId",
    "RequestId",
    "ResultId",
    "SessionId",
    "SubjectStateRef",
    "SubjectStateRelation",
    "TaskId",
    "Timestamp",
    "WriterId",
    "action_id",
    "actor_id",
    "add_utc_milliseconds",
    "claim_id",
    "event_id",
    "evidence_id",
    "finding_id",
    "format_rfc3339_millis",
    "freeze_json",
    "frontier_from_json",
    "object_id",
    "obligation_id",
    "parse_rfc3339_millis",
    "parse_wire_sequence",
    "receipt_id",
    "render_wire_sequence",
    "request_id",
    "result_id",
    "session_id",
    "subject_state_relation",
    "task_id",
    "timestamp_from_datetime",
    "timestamp_from_string",
    "validate_commitment",
    "validate_sha256_digest",
    "writer_id",
]

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | tuple[JsonValue, ...] | JsonObject

GENESIS_DIGEST: Final = "genesis"

_MAX_SAFE_INTEGER: Final = 2**53 - 1
_MAX_SQLITE_SIGNED_INTEGER: Final = 2**63 - 1
_RFC3339_MILLIS_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$",
    re.ASCII,
)
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_COMMITMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^hmac-sha256:[0-9a-f]{64}$",
    re.ASCII,
)


def _is_actual_mapping(value: object) -> bool:
    try:
        return issubclass(type(value), Mapping)
    except BaseException:
        return False


def _is_actual_float(value: object) -> bool:
    try:
        return issubclass(type(value), float)
    except BaseException:
        return False


def _validate_object_key(value: object) -> str:
    if type(value) is not str:
        raise ProtocolValueError("object_key_not_string")
    ensure_canonical_value(value)
    return value


@final
class JsonObject(Mapping[str, JsonValue]):
    """A deeply frozen, insertion-ordered JSON object."""

    __slots__ = ("_index", "_items")

    _items: tuple[tuple[str, JsonValue], ...]
    _index: Mapping[str, JsonValue]

    def __init__(self, source: object) -> None:
        raw_items: list[tuple[str, object]] = []
        seen: set[str] = set()

        if type(source) is list or type(source) is tuple:
            for raw_pair in cast(list[object] | tuple[object, ...], source):
                if type(raw_pair) is not tuple:
                    raise ProtocolValueError("unsupported_json_type")
                pair = cast(tuple[object, ...], raw_pair)
                if len(pair) != 2:
                    raise ProtocolValueError("unsupported_json_type")
                raw_key, raw_value = pair
                key = _validate_object_key(raw_key)
                if key in seen:
                    raise ProtocolValueError("duplicate_object_key")
                seen.add(key)
                raw_items.append((key, raw_value))
        elif _is_actual_mapping(source):
            mapping = cast(Mapping[object, object], source)
            keys: list[str] = []
            try:
                for raw_key in mapping:
                    key = _validate_object_key(raw_key)
                    if key in seen:
                        raise ProtocolValueError("duplicate_object_key")
                    seen.add(key)
                    keys.append(key)
                raw_items.extend((key, mapping[key]) for key in keys)
            except ProtocolValueError:
                raise
            except Exception as exc:
                raise ProtocolValueError("unsupported_json_type") from exc
        else:
            raise ProtocolValueError("unsupported_json_type")

        if MAX_JSON_DEPTH <= 0:
            raise ProtocolValueError("nesting_too_deep")
        items = tuple((key, _freeze_json(value, depth=1)) for key, value in raw_items)
        object.__setattr__(self, "_items", items)
        object.__setattr__(self, "_index", MappingProxyType(dict(items)))

    def __getitem__(self, key: str) -> JsonValue:
        return self._index[key]

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("JsonObject is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("JsonObject is immutable")

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(tuple(sorted(self._items, key=lambda item: item[0])))

    def __eq__(self, other: object) -> bool:
        if type(other) is JsonObject:
            return self._index == other._index
        if not _is_actual_mapping(other):
            return False
        try:
            mapping = cast(Mapping[object, object], other)
            return len(mapping) == len(self) and all(
                mapping.get(key, object()) == value for key, value in self._items
            )
        except BaseException:
            return False

    def __repr__(self) -> str:
        return f"JsonObject({self._items!r})"


def _check_frozen_depth(value: JsonObject, *, depth: int) -> None:
    if depth >= MAX_JSON_DEPTH:
        raise ProtocolValueError("nesting_too_deep")
    for item in value.values():
        if type(item) is JsonObject:
            _check_frozen_depth(item, depth=depth + 1)
        elif type(item) is tuple:
            _check_tuple_depth(item, depth=depth + 1)


def _check_tuple_depth(value: tuple[JsonValue, ...], *, depth: int) -> None:
    if depth >= MAX_JSON_DEPTH:
        raise ProtocolValueError("nesting_too_deep")
    for item in value:
        if type(item) is JsonObject:
            _check_frozen_depth(item, depth=depth + 1)
        elif type(item) is tuple:
            _check_tuple_depth(item, depth=depth + 1)


def _freeze_json(value: object, *, depth: int) -> JsonValue:
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
            raise ProtocolValueError("integer_out_of_safe_range")
        return value
    if _is_actual_float(value):
        raise ProtocolValueError("float_forbidden")
    if type(value) is str:
        ensure_canonical_value(value)
        return value
    if type(value) is JsonObject:
        _check_frozen_depth(value, depth=depth)
        return value
    if type(value) is list or type(value) is tuple:
        if depth >= MAX_JSON_DEPTH:
            raise ProtocolValueError("nesting_too_deep")
        sequence = cast(list[object] | tuple[object, ...], value)
        return tuple(_freeze_json(item, depth=depth + 1) for item in sequence)
    if _is_actual_mapping(value):
        if depth >= MAX_JSON_DEPTH:
            raise ProtocolValueError("nesting_too_deep")
        mapping = cast(Mapping[object, object], value)
        raw_items: list[tuple[str, object]] = []
        seen: set[str] = set()
        keys: list[str] = []
        try:
            for raw_key in mapping:
                key = _validate_object_key(raw_key)
                if key in seen:
                    raise ProtocolValueError("duplicate_object_key")
                seen.add(key)
                keys.append(key)
            raw_items.extend((key, mapping[key]) for key in keys)
        except ProtocolValueError:
            raise
        except Exception as exc:
            raise ProtocolValueError("unsupported_json_type") from exc
        instance = object.__new__(JsonObject)
        items = tuple((key, _freeze_json(item, depth=depth + 1)) for key, item in raw_items)
        object.__setattr__(instance, "_items", items)
        object.__setattr__(instance, "_index", MappingProxyType(dict(items)))
        return instance
    raise ProtocolValueError("unsupported_json_type")


def freeze_json(value: object) -> JsonValue:
    """Validate and deeply freeze one restricted JSON-profile value."""

    return _freeze_json(value, depth=0)


RequestId = NewType("RequestId", str)
TaskId = NewType("TaskId", str)
SessionId = NewType("SessionId", str)
WriterId = NewType("WriterId", str)
EventId = NewType("EventId", str)
ObligationId = NewType("ObligationId", str)
ClaimId = NewType("ClaimId", str)
ActionId = NewType("ActionId", str)
ResultId = NewType("ResultId", str)
EvidenceId = NewType("EvidenceId", str)
FindingId = NewType("FindingId", str)
ObjectId = NewType("ObjectId", str)
ReceiptId = NewType("ReceiptId", str)
ActorId = NewType("ActorId", str)


def _validated_id(kind: IdKind, value: object) -> str:
    validated = validate_id(kind, value)
    return str.__getitem__(validated, slice(None))


def request_id(value: object) -> RequestId:
    return RequestId(_validated_id(IdKind.REQUEST, value))


def task_id(value: object) -> TaskId:
    return TaskId(_validated_id(IdKind.TASK, value))


def session_id(value: object) -> SessionId:
    return SessionId(_validated_id(IdKind.SESSION, value))


def writer_id(value: object) -> WriterId:
    return WriterId(_validated_id(IdKind.WRITER, value))


def event_id(value: object) -> EventId:
    return EventId(_validated_id(IdKind.EVENT, value))


def obligation_id(value: object) -> ObligationId:
    return ObligationId(_validated_id(IdKind.OBLIGATION, value))


def claim_id(value: object) -> ClaimId:
    return ClaimId(_validated_id(IdKind.CLAIM, value))


def action_id(value: object) -> ActionId:
    return ActionId(_validated_id(IdKind.ACTION, value))


def result_id(value: object) -> ResultId:
    return ResultId(_validated_id(IdKind.RESULT, value))


def evidence_id(value: object) -> EvidenceId:
    return EvidenceId(_validated_id(IdKind.EVIDENCE, value))


def finding_id(value: object) -> FindingId:
    return FindingId(_validated_id(IdKind.FINDING, value))


def object_id(value: object) -> ObjectId:
    return ObjectId(_validated_id(IdKind.OBJECT, value))


def receipt_id(value: object) -> ReceiptId:
    return ReceiptId(_validated_id(IdKind.RECEIPT, value))


def actor_id(value: object) -> ActorId:
    validated = validate_actor_id(value)
    return ActorId(str.__getitem__(validated, slice(None)))


class ActorType(str, Enum):  # noqa: UP042 - exact wire-valued Enum is required
    HUMAN = "human"
    HARNESS = "harness"
    LOGICAL_AGENT = "logical_agent"
    MODEL_BACKED_WORKER = "model_backed_worker"
    DELEGATED_SUBAGENT = "delegated_subagent"
    YOETZ_ENGINE = "yoetz_engine"
    IMPORTER = "importer"


@dataclass(frozen=True, slots=True)
class Actor:
    actor_id: ActorId
    actor_type: ActorType
    assurance: AuthorshipAssurance

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_id", actor_id(self.actor_id))
        if type(self.actor_type) is not ActorType:
            raise ProtocolValueError("invalid_actor_type")
        if type(self.assurance) is not AuthorshipAssurance:
            raise ProtocolValueError("invalid_coverage_value")


def _validated_utc_datetime(value: object) -> datetime:
    if type(value) is not datetime:
        raise ProtocolValueError("invalid_timestamp")
    dt = value
    if dt.tzinfo is None:
        raise ProtocolValueError("timestamp_timezone_missing")
    try:
        offset = dt.utcoffset()
    except Exception as exc:
        raise ProtocolValueError("timestamp_timezone_missing") from exc
    if offset is None:
        raise ProtocolValueError("timestamp_timezone_missing")
    if offset != timedelta(0):
        raise ProtocolValueError("timestamp_not_utc")
    if dt.microsecond % 1000 != 0:
        raise ProtocolValueError("timestamp_submillisecond_precision")
    return dt.replace(tzinfo=UTC)


def format_rfc3339_millis(dt: object) -> str:
    normalized = _validated_utc_datetime(dt)
    return (
        f"{normalized.year:04d}-{normalized.month:02d}-{normalized.day:02d}"
        f"T{normalized.hour:02d}:{normalized.minute:02d}:{normalized.second:02d}."
        f"{normalized.microsecond // 1000:03d}Z"
    )


def parse_rfc3339_millis(value: object) -> datetime:
    if type(value) is not str or _RFC3339_MILLIS_PATTERN.fullmatch(value) is None:
        raise ProtocolValueError("invalid_timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ProtocolValueError("invalid_timestamp") from exc
    normalized = parsed.replace(tzinfo=UTC)
    if format_rfc3339_millis(normalized) != value:
        raise ProtocolValueError("invalid_timestamp")
    return normalized


@total_ordering
@dataclass(frozen=True, slots=True)
class Timestamp:
    _wire: str

    def __post_init__(self) -> None:
        if type(self._wire) is not str:
            raise ProtocolValueError("invalid_timestamp")
        parse_rfc3339_millis(self._wire)

    @property
    def wire(self) -> str:
        return self._wire

    def __lt__(self, other: object) -> bool | NotImplementedType:
        if type(other) is not Timestamp:
            return NotImplemented
        return self._wire < other._wire


def timestamp_from_string(value: object) -> Timestamp:
    parse_rfc3339_millis(value)
    return Timestamp(str(value))


def timestamp_from_datetime(dt: object) -> Timestamp:
    return Timestamp(format_rfc3339_millis(dt))


def add_utc_milliseconds(dt: object, milliseconds: object) -> datetime:
    normalized = _validated_utc_datetime(dt)
    if type(milliseconds) is not int or not 1 <= milliseconds <= _MAX_SAFE_INTEGER:
        raise ProtocolValueError("invalid_duration")
    try:
        return normalized + timedelta(milliseconds=milliseconds)
    except (OverflowError, ValueError) as exc:
        raise ProtocolValueError("timestamp_out_of_range") from exc


def validate_sha256_digest(value: str) -> str:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        raise ProtocolValueError("invalid_digest")
    return value


def validate_commitment(value: str) -> str:
    if type(value) is not str or _COMMITMENT_PATTERN.fullmatch(value) is None:
        raise ProtocolValueError("invalid_commitment")
    return value


@dataclass(frozen=True, slots=True)
class Frontier:
    sequence: int
    head_digest: str

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or not 0 <= self.sequence <= _MAX_SQLITE_SIGNED_INTEGER:
            raise ProtocolValueError("invalid_frontier")
        if self.sequence == 0:
            if type(self.head_digest) is not str or self.head_digest != GENESIS_DIGEST:
                raise ProtocolValueError("invalid_frontier")
        else:
            try:
                validate_sha256_digest(self.head_digest)
            except ProtocolValueError as exc:
                raise ProtocolValueError("invalid_frontier") from exc

    @classmethod
    def genesis(cls) -> Frontier:
        return cls(0, GENESIS_DIGEST)

    def as_wire(self) -> JsonObject:
        return JsonObject(
            {
                "sequence": render_wire_sequence(self.sequence),
                "head_digest": self.head_digest,
            }
        )

    def _other_sequence(self, other: object) -> int | NotImplementedType:
        if type(other) is not Frontier:
            return NotImplemented
        candidate = other
        if self.sequence == candidate.sequence and self.head_digest != candidate.head_digest:
            raise ProtocolValueError("frontier_digest_mismatch")
        return candidate.sequence

    def __lt__(self, other: object) -> bool | NotImplementedType:
        sequence = self._other_sequence(other)
        if sequence is NotImplemented:
            return NotImplemented
        return self.sequence < sequence

    def __le__(self, other: object) -> bool | NotImplementedType:
        sequence = self._other_sequence(other)
        if sequence is NotImplemented:
            return NotImplemented
        return self.sequence <= sequence

    def __gt__(self, other: object) -> bool | NotImplementedType:
        sequence = self._other_sequence(other)
        if sequence is NotImplemented:
            return NotImplemented
        return self.sequence > sequence

    def __ge__(self, other: object) -> bool | NotImplementedType:
        sequence = self._other_sequence(other)
        if sequence is NotImplemented:
            return NotImplemented
        return self.sequence >= sequence


def frontier_from_json(value: object) -> Frontier:
    if not _is_actual_mapping(value):
        raise ProtocolValueError("invalid_frontier")
    mapping = cast(Mapping[object, object], value)
    try:
        keys = tuple(mapping)
    except Exception as exc:
        raise ProtocolValueError("invalid_frontier") from exc
    if any(type(key) is not str for key in keys):
        raise ProtocolValueError("invalid_frontier")
    string_keys = cast(tuple[str, ...], keys)
    if len(string_keys) != 2 or frozenset(string_keys) != frozenset({"sequence", "head_digest"}):
        raise ProtocolValueError("invalid_frontier")
    try:
        raw_sequence = mapping["sequence"]
        raw_digest = mapping["head_digest"]
    except Exception as exc:
        raise ProtocolValueError("invalid_frontier") from exc
    sequence = parse_wire_sequence(cast(str, raw_sequence))
    if type(raw_digest) is not str:
        raise ProtocolValueError("invalid_frontier")
    return Frontier(sequence, raw_digest)


class SubjectStateRelation(str, Enum):  # noqa: UP042 - exact wire-valued Enum is required
    SAME = "same"
    DIFFERENT = "different"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SubjectStateRef:
    tree_digest: str | None = None
    diff_digest: str | None = None
    described_state: str | None = None

    def __post_init__(self) -> None:
        if self.tree_digest is None and self.diff_digest is None and self.described_state is None:
            raise ProtocolValueError("empty_subject_state")
        if self.tree_digest is not None:
            validate_sha256_digest(self.tree_digest)
        if self.diff_digest is not None:
            validate_sha256_digest(self.diff_digest)
        if self.described_state is not None:
            if type(self.described_state) is not str or not 1 <= len(self.described_state) <= 256:
                raise ProtocolValueError("invalid_subject_state")
            ensure_canonical_value(self.described_state)


def subject_state_relation(
    a: SubjectStateRef | None,
    b: SubjectStateRef | None,
) -> SubjectStateRelation:
    if a is None or b is None:
        return SubjectStateRelation.UNKNOWN
    if a.tree_digest is not None and b.tree_digest is not None:
        if a.tree_digest == b.tree_digest:
            return SubjectStateRelation.SAME
        return SubjectStateRelation.DIFFERENT
    if a.diff_digest is not None and b.diff_digest is not None:
        if a.diff_digest == b.diff_digest:
            return SubjectStateRelation.SAME
    return SubjectStateRelation.UNKNOWN


def parse_wire_sequence(value: str) -> int:
    return parse_canonical_integer_string(value)


def render_wire_sequence(value: int) -> str:
    return canonical_integer_string(value)
