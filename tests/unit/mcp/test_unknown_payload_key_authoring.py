"""An unknown payload key must be named as such, never answered with satisfied requirements.

Replays the 2026-08-13 dogfood: `publish_work` drafts whose `obligation_published` payloads were
rejected for keys the family does not admit. The response recited the seven envelope keys the
request already carried and the family value it had already sent, and never said "unknown
property" (issue #240). The rejected key names stay caller-controlled and must never be echoed;
the admitted keys and a bounded count are frozen schema facts and must be.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

import pytest
from pydantic import ValidationError

import yoetz.mcp.server as bridge
from yoetz.mcp.descriptors import descriptor_for
from yoetz.mcp.errors import safe_validation_locations
from yoetz.mcp.server import invalid_request_message
from yoetz.protocol.errors import PublicErrorCode
from yoetz.protocol.models import PublishWorkRequestModel

_PUBLISH_SCHEMA = descriptor_for("publish_work").input_schema
_FAMILY = "obligation_published"
_ADMITTED_PAYLOAD_KEYS = (
    "acceptance_criteria",
    "description",
    "evidence_expectation",
    "obligation_id",
    "requested_items",
    "resolution_evidence_refs",
    "source_refs",
    "status",
)
# The public message bound the protocol validator enforces.
_MAX_MESSAGE_BYTES = 4096


def _obligation_request() -> dict[str, Any]:
    """One worked example reduced to its single `obligation_published` draft."""

    examples = cast(list[Any], _PUBLISH_SCHEMA["examples"])
    for example in examples:
        for draft in cast(list[Any], example["event_drafts"]):
            if draft["schema"]["name"] != _FAMILY:
                continue
            request = cast(dict[str, Any], json.loads(json.dumps(example)))
            request["event_drafts"] = [json.loads(json.dumps(draft))]
            return request
    raise AssertionError("the publish_work examples no longer carry an obligation draft")


def _locations(request: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    with pytest.raises(ValidationError) as captured:
        PublishWorkRequestModel.model_validate(request)
    return safe_validation_locations(captured.value)


def _message(request: Mapping[str, Any]) -> str:
    message = invalid_request_message("publish_work", _locations(request))
    assert len(message.encode("utf-8")) <= _MAX_MESSAGE_BYTES
    return message


def _with_payload_keys(**extra: Any) -> dict[str, Any]:
    request = _obligation_request()
    request["event_drafts"][0]["payload"].update(extra)
    return request


def test_unknown_payload_key_reports_extra_forbidden() -> None:
    locations = _locations(_with_payload_keys(zzz_unknown="x"))

    assert locations[0]["field"] == "/event_drafts/0/payload"
    assert locations[0]["reason"] == "extra_forbidden"


def test_unknown_payload_key_names_the_admitted_keys_for_the_family() -> None:
    message = _message(_with_payload_keys(zzz_unknown="x"))

    assert "does not admit" in message
    assert _FAMILY in message
    for key in _ADMITTED_PAYLOAD_KEYS:
        assert key in message, key


def test_unknown_payload_key_does_not_recite_satisfied_envelope_requirements() -> None:
    message = _message(_with_payload_keys(zzz_unknown="x"))

    assert "each event_drafts entry requires" not in message


@pytest.mark.anyio
async def test_unknown_payload_key_never_echoes_the_key_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "never_echo_unknown_key_7b2c"
    request = _with_payload_keys(**{secret: "x"})

    locations = _locations(request)
    message = invalid_request_message("publish_work", locations)
    assert secret not in message
    assert secret not in repr(locations)

    async def refuse(_runtime: object = bridge.BRIDGE_RUNTIME) -> object:
        raise AssertionError("a dry run must not reach the recovery oracle")

    monkeypatch.setattr(bridge, "ensure_service_client", refuse)
    runtime = bridge.build_bridge_runtime()
    result = await bridge.dispatch_publish_work({**request, "dry_run": True}, runtime)
    assert secret not in str(result.structuredContent)
    await bridge.close_bridge_runtime(runtime)


def test_unknown_payload_key_count_reaches_the_caller() -> None:
    request = _with_payload_keys(zzz_unknown="x", aaa_unknown="y")

    locations = _locations(request)
    message = invalid_request_message("publish_work", locations)

    assert "2 properties" in message
    assert locations[0]["count"] == "2"
    assert "zzz_unknown" not in message
    assert "aaa_unknown" not in message


def test_unknown_draft_envelope_key_keeps_the_envelope_recital() -> None:
    """An extra key on the draft object is an envelope defect, and the recital is right for it."""

    request = _obligation_request()
    request["event_drafts"][0]["zzz_unknown"] = "x"

    message = _message(request)

    assert "each event_drafts entry requires" in message
    assert "schema.name admits" in message


def test_an_unresolvable_family_still_names_admitted_families() -> None:
    """Zero or several surviving branches keep whole-tree scoring; the union must still be named."""

    request = _obligation_request()
    request["event_drafts"][0]["schema"] = {"name": 42, "version": "1.0.0"}

    locations = _locations(request)
    message = invalid_request_message("publish_work", locations)

    assert locations[0]["field"] == "/event_drafts/0/schema/name"
    assert locations[0]["reason"] != "extra_forbidden"
    assert "schema.name admits" in message
    assert _FAMILY in message


def test_a_bad_value_under_an_unallowlisted_payload_key_names_that_key() -> None:
    """The literal 14:01:55Z response: a value defect beneath a key the allowlist did not carry."""

    request = _with_payload_keys(source_refs=["not-an-event-id"])

    locations = _locations(request)
    message = invalid_request_message("publish_work", locations)

    assert locations[0]["field"].startswith("/event_drafts/0/payload/source_refs")
    assert locations[0]["reason"] != "extra_forbidden"
    assert "each event_drafts entry requires" not in message
    assert "not-an-event-id" not in message


@pytest.mark.anyio
async def test_safe_details_never_carries_the_internal_family_or_count_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(_runtime: object = bridge.BRIDGE_RUNTIME) -> object:
        raise AssertionError("a dry run must not reach the recovery oracle")

    monkeypatch.setattr(bridge, "ensure_service_client", refuse)
    runtime = bridge.build_bridge_runtime()

    request = {**_with_payload_keys(zzz_unknown="x"), "dry_run": True}
    result = await bridge.dispatch_publish_work(request, runtime)

    structured = cast(dict[str, object], result.structuredContent)
    error = cast(dict[str, object], structured["error"])
    assert error["code"] == PublicErrorCode.INVALID_REQUEST.value
    details = cast(dict[str, object], error["safe_details"])
    assert set(details) == {"fields", "reasons"}
    assert details["reasons"] == ["extra_forbidden"]
    assert "family" not in details
    await bridge.close_bridge_runtime(runtime)


def test_every_declared_schema_name_can_be_located_in_a_public_error() -> None:
    """A declared name outside the allowlist collapses its failure to an allowlisted parent."""

    from yoetz.mcp.errors import (
        _DELIBERATELY_UNLOCATABLE_DECLARED,  # pyright: ignore[reportPrivateUsage]
        _SAFE_LOCATION_SEGMENTS,  # pyright: ignore[reportPrivateUsage]
        _schema_names,  # pyright: ignore[reportPrivateUsage]
    )

    required: set[str] = set()
    declared: set[str] = set()
    for tool in ("start", "publish_work", "check", "respond", "status", "receipt"):
        _schema_names(cast(Any, descriptor_for(tool).input_schema), required, declared)

    assert declared
    assert not declared - _SAFE_LOCATION_SEGMENTS - _DELIBERATELY_UNLOCATABLE_DECLARED
    # The escape hatch stays a reviewed decision rather than a growing exemption list.
    assert _DELIBERATELY_UNLOCATABLE_DECLARED == frozenset()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
