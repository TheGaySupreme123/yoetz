"""Unit and property coverage for frozen domain values."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from fractions import Fraction
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from yoetz.domain.values import (
    GENESIS_DIGEST,
    Actor,
    ActorId,
    ActorType,
    Frontier,
    JsonObject,
    SubjectStateRef,
    SubjectStateRelation,
    action_id,
    actor_id,
    add_utc_milliseconds,
    claim_id,
    event_id,
    evidence_id,
    finding_id,
    format_rfc3339_millis,
    freeze_json,
    frontier_from_json,
    object_id,
    obligation_id,
    parse_rfc3339_millis,
    parse_wire_sequence,
    receipt_id,
    render_wire_sequence,
    request_id,
    result_id,
    session_id,
    subject_state_relation,
    task_id,
    timestamp_from_datetime,
    timestamp_from_string,
    validate_commitment,
    validate_sha256_digest,
    writer_id,
)
from yoetz.protocol.canonical import (
    MAX_JSON_DEPTH,
    canonical_encode,
)
from yoetz.protocol.canonical import (
    JsonValue as CanonicalJsonValue,
)
from yoetz.protocol.coverage import AuthorshipAssurance
from yoetz.protocol.errors import ProtocolValueError

_UUID = "00000000-0000-4000-8000-000000000001"
_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_COMMITMENT = "hmac-sha256:" + "c" * 64
_MAX_SQLITE_INTEGER = 2**63 - 1
_MAX_SAFE_INTEGER = 2**53 - 1


def _assert_reason(reason: str, function: Callable[[], object]) -> None:
    with pytest.raises(ProtocolValueError) as raised:
        function()
    assert raised.value.reason_code == reason


class _IntSubclass(int):
    pass


class _FloatSubclass(float):
    pass


class _StrSubclass(str):
    def __str__(self) -> str:
        return "caller-controlled"


class _ListSubclass(list[object]):
    pass


class _DatetimeSubclass(datetime):
    pass


class _KeyBeforeValueMapping(Mapping[object, object]):
    def __getitem__(self, key: object) -> object:
        raise AssertionError(f"value read before all keys validated: {key!r}")

    def __iter__(self) -> Iterator[object]:
        yield "valid"
        yield _StrSubclass("invalid-subclass")

    def __len__(self) -> int:
        return 2


class _DuplicateYieldMapping(Mapping[object, object]):
    def __getitem__(self, key: object) -> object:
        return 1

    def __iter__(self) -> Iterator[object]:
        yield "duplicate"
        yield "duplicate"

    def __len__(self) -> int:
        return 2


def test_freeze_json_accepts_only_safe_json_shapes() -> None:
    source = {"z": [None, True, 7], "a": {"nested": "value"}}
    frozen = freeze_json(source)
    assert type(frozen) is JsonObject
    frozen_object = frozen
    assert tuple(frozen_object) == ("z", "a")
    assert frozen_object["z"] == (None, True, 7)
    assert frozen_object["a"] == JsonObject({"nested": "value"})
    assert freeze_json(frozen_object) is frozen_object

    reordered = JsonObject({"a": {"nested": "value"}, "z": [None, True, 7]})
    assert reordered == frozen_object
    assert hash(reordered) == hash(frozen_object)

    with pytest.raises(AttributeError):
        frozen_object._items = ()  # type: ignore[reportPrivateUsage]
    with pytest.raises(TypeError):
        frozen_object._index["new"] = 1  # type: ignore[reportIndexIssue,reportPrivateUsage]


def test_json_object_pair_shape_duplicate_and_key_precedence_are_exact() -> None:
    assert JsonObject([("a", 1), ("b", [2])]) == JsonObject({"b": [2], "a": 1})
    _assert_reason(
        "duplicate_object_key",
        lambda: JsonObject([("same", 1), ("same", object())]),
    )
    _assert_reason("unsupported_json_type", lambda: JsonObject([["a", 1]]))
    _assert_reason("unsupported_json_type", lambda: JsonObject([("a", 1, 2)]))
    _assert_reason("unsupported_json_type", lambda: JsonObject("not-an-object"))
    _assert_reason("object_key_not_string", lambda: JsonObject(_KeyBeforeValueMapping()))
    _assert_reason("object_key_not_string", lambda: freeze_json(_KeyBeforeValueMapping()))
    _assert_reason("duplicate_object_key", lambda: JsonObject(_DuplicateYieldMapping()))
    _assert_reason("duplicate_object_key", lambda: freeze_json(_DuplicateYieldMapping()))


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (1.25, "float_forbidden"),
        (_FloatSubclass(1.25), "float_forbidden"),
        (_IntSubclass(1), "unsupported_json_type"),
        (Decimal("1"), "unsupported_json_type"),
        (Fraction(1, 2), "unsupported_json_type"),
        (1 + 2j, "unsupported_json_type"),
        (b"bytes", "unsupported_json_type"),
        (_StrSubclass("text"), "unsupported_json_type"),
        (_ListSubclass([1]), "unsupported_json_type"),
        (_MAX_SAFE_INTEGER + 1, "integer_out_of_safe_range"),
        (-_MAX_SAFE_INTEGER - 1, "integer_out_of_safe_range"),
    ],
)
def test_freeze_json_rejects_noncanonical_strings_and_subclasses(
    value: object,
    reason: str,
) -> None:
    _assert_reason(reason, lambda: freeze_json(value))

    _assert_reason("nul_byte_forbidden", lambda: freeze_json("before\x00after"))
    _assert_reason("lone_surrogate", lambda: freeze_json("\ud800"))
    _assert_reason("nul_byte_forbidden", lambda: freeze_json({"bad\x00key": 1}))
    _assert_reason("object_key_not_string", lambda: freeze_json({_StrSubclass("key"): 1}))


def test_freeze_json_depth_is_bounded_by_the_canonical_owner() -> None:
    value: object = 0
    for _ in range(MAX_JSON_DEPTH + 1):
        value = [value]
    _assert_reason("nesting_too_deep", lambda: freeze_json(value))


@pytest.mark.parametrize(
    ("constructor", "prefix"),
    [
        (request_id, "req_"),
        (task_id, "tsk_"),
        (session_id, "ses_"),
        (writer_id, "wri_"),
        (event_id, "evt_"),
        (obligation_id, "obl_"),
        (claim_id, "clm_"),
        (action_id, "act_"),
        (result_id, "res_"),
        (evidence_id, "evd_"),
        (finding_id, "fnd_"),
        (object_id, "obj_"),
        (receipt_id, "rcp_"),
    ],
)
def test_id_constructors_snapshot_valid_string_subclasses(
    constructor: Callable[[object], str],
    prefix: str,
) -> None:
    raw = _StrSubclass(prefix + _UUID)
    result = constructor(raw)
    assert type(result) is str
    assert result == prefix + _UUID
    assert str(raw) == "caller-controlled"

    actor = actor_id(_StrSubclass("logical-agent:reviewer"))
    assert type(actor) is str
    assert actor == "logical-agent:reviewer"


def test_actor_validation_is_nominal_and_ordered() -> None:
    actor = Actor(
        actor_id("human:shay"),
        ActorType.HUMAN,
        AuthorshipAssurance.LOCALLY_AUTHENTICATED,
    )
    assert actor.actor_id == "human:shay"
    assert actor.actor_type.value == "human"

    _assert_reason(
        "invalid_actor_type",
        lambda: Actor(
            actor_id("human:shay"),
            cast(ActorType, "human"),
            cast(AuthorshipAssurance, "self_asserted"),
        ),
    )
    _assert_reason(
        "invalid_coverage_value",
        lambda: Actor(
            actor_id("human:shay"),
            ActorType.HUMAN,
            cast(AuthorshipAssurance, "self_asserted"),
        ),
    )
    _assert_reason(
        "actor_id_malformed",
        lambda: Actor(
            cast(ActorId, "bad actor"),
            cast(ActorType, "human"),
            cast(AuthorshipAssurance, "self_asserted"),
        ),
    )


def test_timestamp_round_trip_is_utc_exact() -> None:
    dt = datetime(2026, 7, 13, 9, 14, 31, 10_000, tzinfo=UTC)
    wire = "2026-07-13T09:14:31.010Z"
    assert format_rfc3339_millis(dt) == wire
    assert parse_rfc3339_millis(wire) == dt
    assert timestamp_from_datetime(dt) == timestamp_from_string(wire)
    assert timestamp_from_string(wire).wire == wire
    assert timestamp_from_string("2026-07-13T09:14:31.009Z") < timestamp_from_string(wire)
    assert format_rfc3339_millis(datetime(1, 1, 1, tzinfo=UTC)).startswith("0001-")

    zero_offset = timezone(timedelta(0), name="zero-offset")
    assert timestamp_from_datetime(dt.replace(tzinfo=zero_offset)).wire == wire

    for invalid in (
        "2026-07-13 09:14:31.010Z",
        "2026-07-13T09:14:31Z",
        "2026-07-13T09:14:31.010+00:00",
        "2026-07-13T09:14:60.010Z",
    ):
        _assert_reason("invalid_timestamp", lambda invalid=invalid: timestamp_from_string(invalid))


def test_timestamp_and_duration_validation_order_is_frozen() -> None:
    _assert_reason(
        "invalid_timestamp",
        lambda: timestamp_from_datetime(_DatetimeSubclass(2026, 1, 1, tzinfo=UTC)),
    )
    _assert_reason(
        "timestamp_timezone_missing",
        lambda: timestamp_from_datetime(datetime(2026, 1, 1)),
    )
    _assert_reason(
        "timestamp_not_utc",
        lambda: timestamp_from_datetime(datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2)))),
    )
    _assert_reason(
        "timestamp_submillisecond_precision",
        lambda: timestamp_from_datetime(datetime(2026, 1, 1, microsecond=1, tzinfo=UTC)),
    )

    base = datetime(2026, 1, 1, tzinfo=UTC)
    for invalid in (True, 0, -1, _IntSubclass(1), _MAX_SAFE_INTEGER + 1):
        _assert_reason(
            "invalid_duration", lambda invalid=invalid: add_utc_milliseconds(base, invalid)
        )
    _assert_reason(
        "timestamp_out_of_range",
        lambda: add_utc_milliseconds(datetime.max.replace(microsecond=999_000, tzinfo=UTC), 1),
    )
    _assert_reason(
        "timestamp_timezone_missing",
        lambda: add_utc_milliseconds(datetime(2026, 1, 1), 0),
    )


class _SyntheticTransitionZone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta:
        if dt is not None and dt.day >= 2:
            return timedelta(hours=1)
        return timedelta(0)

    def dst(self, dt: datetime | None) -> timedelta:
        return self.utcoffset(dt)

    def tzname(self, dt: datetime | None) -> str:
        return "synthetic-transition"


class _StatefulZeroThenNonzeroZone(tzinfo):
    def __init__(self) -> None:
        self.calls = 0

    def utcoffset(self, dt: datetime | None) -> timedelta:
        self.calls += 1
        if self.calls == 1:
            return timedelta(0)
        return timedelta(hours=1)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "stateful-zone"


def test_utc_duration_arithmetic_ignores_named_zone_transitions() -> None:
    local = datetime(2026, 1, 1, 23, 59, 59, tzinfo=_SyntheticTransitionZone())
    result = add_utc_milliseconds(local, 2_000)
    assert result == datetime(2026, 1, 2, 0, 0, 1, tzinfo=UTC)
    assert result.tzinfo is UTC

    format_zone = _StatefulZeroThenNonzeroZone()
    stateful = datetime(2026, 1, 1, 12, 0, tzinfo=format_zone)
    assert format_rfc3339_millis(stateful) == "2026-01-01T12:00:00.000Z"
    assert format_zone.calls == 1

    add_zone = _StatefulZeroThenNonzeroZone()
    stateful_for_add = datetime(2026, 1, 1, 12, 0, tzinfo=add_zone)
    assert add_utc_milliseconds(stateful_for_add, 1_000) == datetime(
        2026, 1, 1, 12, 0, 1, tzinfo=UTC
    )
    assert add_zone.calls == 1


@pytest.mark.parametrize(
    "value",
    ["", "00", "01", "+1", "-1", " 1", "1 ", "9223372036854775808"],
)
def test_wire_sequence_parsing_rejects_noncanonical_strings(value: str) -> None:
    _assert_reason("noncanonical_integer_string", lambda: parse_wire_sequence(value))

    assert parse_wire_sequence("0") == 0
    assert parse_wire_sequence(str(_MAX_SQLITE_INTEGER)) == _MAX_SQLITE_INTEGER
    assert render_wire_sequence(_MAX_SQLITE_INTEGER) == str(_MAX_SQLITE_INTEGER)
    _assert_reason("integer_out_of_sqlite_range", lambda: render_wire_sequence(True))
    _assert_reason(
        "integer_out_of_sqlite_range", lambda: render_wire_sequence(_MAX_SQLITE_INTEGER + 1)
    )


def test_frontier_json_codec_is_closed_and_invertible() -> None:
    genesis = Frontier.genesis()
    assert genesis == Frontier(0, GENESIS_DIGEST)
    assert frontier_from_json(genesis.as_wire()) == genesis
    frontier = Frontier(9, _DIGEST_A)
    assert frontier_from_json(frontier.as_wire()) == frontier
    assert tuple(frontier.as_wire()) == ("sequence", "head_digest")

    _assert_reason("invalid_frontier", lambda: Frontier(0, _DIGEST_A))
    _assert_reason("invalid_frontier", lambda: Frontier(1, GENESIS_DIGEST))
    _assert_reason("invalid_frontier", lambda: Frontier(True, GENESIS_DIGEST))
    _assert_reason("invalid_frontier", lambda: frontier_from_json({"sequence": "0"}))
    _assert_reason(
        "invalid_frontier",
        lambda: frontier_from_json({"sequence": "0", "head_digest": GENESIS_DIGEST, "extra": True}),
    )
    _assert_reason("invalid_frontier", lambda: frontier_from_json(_DuplicateYieldMapping()))
    _assert_reason(
        "noncanonical_integer_string",
        lambda: frontier_from_json({"sequence": "01", "head_digest": GENESIS_DIGEST}),
    )

    assert Frontier(1, _DIGEST_A) < Frontier(2, _DIGEST_B)
    same_height = Frontier(1, _DIGEST_A)
    divergent = Frontier(1, _DIGEST_B)
    for comparison in (
        lambda: same_height < divergent,
        lambda: same_height <= divergent,
        lambda: same_height > divergent,
        lambda: same_height >= divergent,
    ):
        _assert_reason("frontier_digest_mismatch", comparison)
    assert same_height != divergent


def test_subject_state_ref_validation_is_strict() -> None:
    assert SubjectStateRef(tree_digest=_DIGEST_A).tree_digest == _DIGEST_A
    assert SubjectStateRef(diff_digest=_DIGEST_B, described_state="git_structural_v1")
    _assert_reason("empty_subject_state", SubjectStateRef)
    _assert_reason("invalid_digest", lambda: SubjectStateRef(tree_digest="sha256:ABC"))
    _assert_reason("invalid_subject_state", lambda: SubjectStateRef(described_state=""))
    _assert_reason("invalid_subject_state", lambda: SubjectStateRef(described_state="x" * 257))
    _assert_reason(
        "invalid_subject_state", lambda: SubjectStateRef(described_state=_StrSubclass("state"))
    )
    _assert_reason("nul_byte_forbidden", lambda: SubjectStateRef(described_state="bad\x00state"))
    _assert_reason("lone_surrogate", lambda: SubjectStateRef(described_state="\udfff"))


def test_subject_state_relation_full_matrix() -> None:
    tree_a = SubjectStateRef(tree_digest=_DIGEST_A)
    tree_a_labeled = SubjectStateRef(tree_digest=_DIGEST_A, described_state="other label")
    tree_b = SubjectStateRef(tree_digest=_DIGEST_B)
    diff_a = SubjectStateRef(diff_digest=_DIGEST_A)
    diff_b = SubjectStateRef(diff_digest=_DIGEST_B)
    label = SubjectStateRef(described_state="tree-A")

    assert subject_state_relation(None, tree_a) is SubjectStateRelation.UNKNOWN
    assert subject_state_relation(tree_a, None) is SubjectStateRelation.UNKNOWN
    assert subject_state_relation(tree_a, tree_a_labeled) is SubjectStateRelation.SAME
    assert subject_state_relation(tree_a, tree_b) is SubjectStateRelation.DIFFERENT
    assert subject_state_relation(diff_a, diff_a) is SubjectStateRelation.SAME
    assert subject_state_relation(diff_a, diff_b) is SubjectStateRelation.UNKNOWN
    assert subject_state_relation(tree_a, diff_a) is SubjectStateRelation.UNKNOWN
    assert subject_state_relation(label, label) is SubjectStateRelation.UNKNOWN


def test_digest_and_commitment_spelling_is_exact() -> None:
    assert validate_sha256_digest(_DIGEST_A) == _DIGEST_A
    assert validate_commitment(_COMMITMENT) == _COMMITMENT
    _assert_reason("invalid_digest", lambda: validate_sha256_digest("sha256:" + "A" * 64))
    _assert_reason("invalid_commitment", lambda: validate_commitment("sha256:" + "c" * 64))


_CANONICAL_TEXT = st.text(
    alphabet=st.characters(blacklist_characters="\x00", blacklist_categories=("Cs",)),
    max_size=20,
)
_SAFE_JSON = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(-_MAX_SAFE_INTEGER, _MAX_SAFE_INTEGER)
    | _CANONICAL_TEXT,
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(_CANONICAL_TEXT, children, max_size=4)
    ),
    max_leaves=20,
)


@given(st.integers(0, _MAX_SQLITE_INTEGER))
def test_generated_wire_sequence_round_trip(value: int) -> None:
    assert parse_wire_sequence(render_wire_sequence(value)) == value


@given(st.integers(0, 86_400_000 - 1))
def test_generated_timestamp_round_trip(milliseconds: int) -> None:
    value = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(milliseconds=milliseconds)
    timestamp = timestamp_from_datetime(value)
    assert timestamp_from_string(timestamp.wire) == timestamp
    assert parse_rfc3339_millis(timestamp.wire) == value


@given(_SAFE_JSON)
def test_generated_value_round_trips_are_idempotent(value: object) -> None:
    frozen = freeze_json(value)
    assert freeze_json(frozen) == frozen
    assert canonical_encode(cast(CanonicalJsonValue, frozen)) == canonical_encode(
        cast(CanonicalJsonValue, freeze_json(frozen))
    )
