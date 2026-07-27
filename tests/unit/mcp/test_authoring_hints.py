"""Invalid tool arguments must say what is admitted, not only where the problem is.

Replays the two `start` calls that failed in the 2026-07-27 Codex dogfood. Both were answerable
from the frozen presentation schema, but the response named only field locations, so the agent
read product source and conformance tests to author a request instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest

from yoetz.mcp.descriptors import descriptor_for
from yoetz.mcp.errors import authoring_hint
from yoetz.protocol.canonical import JsonValue

_START_SCHEMA = descriptor_for("start").input_schema


def _locations(*fields: str) -> Sequence[Mapping[str, str]]:
    return tuple({"field": field, "reason": "invalid_value"} for field in fields)


def test_an_unadmitted_mode_is_answered_with_the_admitted_modes() -> None:
    # The dogfood sent `mode: start`, which is not one of the three admitted values.
    hint = authoring_hint(_START_SCHEMA, _locations("/mode"))
    assert "attach, create, create_or_attach" in hint
    assert hint.startswith(" Hint: ")
    assert hint.endswith(".")


def test_a_guessed_request_id_is_answered_with_the_required_shape() -> None:
    # The dogfood sent a free-form id. The shape lives behind a $defs reference.
    hint = authoring_hint(_START_SCHEMA, _locations("/request_id"))
    assert "^req_" in hint


def test_an_empty_request_names_the_constant_versions_and_the_example() -> None:
    hint = authoring_hint(
        _START_SCHEMA, _locations("/protocol_version", "/schema_version", "/mode")
    )
    assert "protocol_version admits 0.1" in hint
    assert "schema_version admits 1.0.0" in hint
    assert "examples entry" in hint


def test_the_hint_is_bounded() -> None:
    hint = authoring_hint(
        _START_SCHEMA,
        _locations(
            "/protocol_version", "/schema_version", "/mode", "/request_id", "/requested_view"
        ),
    )
    # At most three fields plus the example pointer, so the hint never buries the locations.
    assert hint.count(" admits ") <= 3


def test_nested_and_unknown_locations_are_skipped() -> None:
    # Nested pointers would need a full $defs walk; unknown names must never be echoed back.
    assert authoring_hint(_START_SCHEMA, _locations("/actor/actor_type")).count(" admits ") == 0
    assert authoring_hint(_START_SCHEMA, _locations("/not_a_field")).count(" admits ") == 0


@pytest.mark.parametrize("schema", [None, "", 7, [], {}, {"properties": "not a mapping"}])
def test_a_malformed_schema_yields_no_hint_rather_than_raising(schema: object) -> None:
    assert authoring_hint(schema, _locations("/mode")) == ""


def test_no_locations_still_points_at_the_example() -> None:
    assert "examples entry" in authoring_hint(_START_SCHEMA, ())


def test_a_schema_without_an_example_stays_silent_when_nothing_is_admitted() -> None:
    schema: dict[str, JsonValue] = {"properties": {"mode": {"type": "string"}}}
    assert authoring_hint(schema, _locations("/mode")) == ""


def test_an_oversized_enum_is_not_dumped_into_the_message() -> None:
    schema: dict[str, JsonValue] = {
        "properties": {"mode": {"enum": [f"value_{index}" for index in range(9)]}}
    }
    assert authoring_hint(schema, _locations("/mode")) == ""


def test_an_unbounded_pattern_is_not_dumped_into_the_message() -> None:
    schema: dict[str, JsonValue] = {"properties": {"request_id": {"pattern": "a" * 200}}}
    assert authoring_hint(schema, _locations("/request_id")) == ""


def test_every_workflow_tool_can_produce_a_hint() -> None:
    # A tool whose schema carries no example and no admitted values would leave an agent with the
    # same bare "arguments are invalid" the dogfood got.
    for name in ("start", "publish_work", "check", "respond", "status", "receipt"):
        schema = cast(dict[str, Any], descriptor_for(name).input_schema)
        assert "examples entry" in authoring_hint(schema, ()), name
