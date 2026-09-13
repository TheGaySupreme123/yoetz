"""The additive 2.6 observation-status selection projection is a frozen wire vector."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from fixture_loader import FixtureLoader
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.schemas import validate_schema_instance
from yoetz.service.control_protocol import decode_control_frame, encode_control_frame

_FIXTURE = "canonical/control-observation-status-2.6.case.json"
_SCHEMA_ROOT = Path(__file__).parents[3] / "schemas"


def _document(fixture_loader: FixtureLoader) -> dict[str, Any]:
    return cast(dict[str, Any], fixture_loader.load_json(_FIXTURE))


def test_selection_runtime_status_is_valid_only_on_additive_26_wire(
    fixture_loader: FixtureLoader,
) -> None:
    document = _document(fixture_loader)
    expected = cast(dict[str, Any], document["expected"])
    frame = cast(dict[str, JsonValue], cast(dict[str, Any], document["input"])["frame"])

    validate_schema_instance("control-result", "2.6.0", frame)
    with pytest.raises(ProtocolValueError):
        validate_schema_instance("control-result", "2.5.0", frame)

    encoded = canonical_encode(frame)
    assert len(encoded) == expected["canonical_byte_length"]
    assert encoded.hex() == expected["canonical_hex"]
    assert canonical_digest(frame) == expected["canonical_sha256"]

    decoded = decode_control_frame(encode_control_frame(frame))
    assert canonical_encode(decoded) == encoded
    status = cast(dict[str, Any], cast(dict[str, Any], frame["body"])["status"])
    assert "selection_runtime" in status
    assert expected["selection_runtime_present"] is True


def test_active_26_control_result_tracks_current_workflow_result_schemas() -> None:
    document = cast(
        dict[str, Any],
        strict_json_parse((_SCHEMA_ROOT / "service/control-result-2.6.0.schema.json").read_bytes()),
    )
    refs: set[str] = set()

    def collect(value: object) -> None:
        if isinstance(value, dict):
            mapping = cast(dict[str, object], value)
            ref = mapping.get("$ref")
            if isinstance(ref, str):
                refs.add(ref)
            for child in mapping.values():
                collect(child)
        elif isinstance(value, list):
            for child in cast(list[object], value):
                collect(child)

    collect(document)
    assert "https://schemas.yoetz.dev/0.1/operations/check-result-1.2.0.schema.json" in refs
    assert "https://schemas.yoetz.dev/0.1/operations/receipt-result-1.2.0.schema.json" in refs
    assert "https://schemas.yoetz.dev/0.1/operations/status-result-1.3.0.schema.json" in refs
    assert "https://schemas.yoetz.dev/0.1/operations/check-result-1.1.0.schema.json" not in refs
    assert "https://schemas.yoetz.dev/0.1/operations/receipt-result-1.1.0.schema.json" not in refs
    assert "https://schemas.yoetz.dev/0.1/operations/status-result-1.2.0.schema.json" not in refs
