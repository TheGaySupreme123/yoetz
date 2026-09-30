"""Manifest-bound golden vector for evidence read-back and the author filter (issue #914)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast

import pytest

from fixture_loader import load_fixture_json
from yoetz.cli.render import render_human_status
from yoetz.domain.values import freeze_json
from yoetz.mcp.summaries import render_safe_compact_summary
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.models import (
    StatusEvidencePageModel,
    StatusRequestModel,
    StatusResultModel,
    StatusSuccessModel,
    public_model_to_wire,
)
from yoetz.protocol.schemas import SchemaInstanceInvalid, validate_schema_instance

_PATH = "canonical/status-evidence-author-1.5.0.case.json"
_CASES = ("mine", "unfiltered")
_GENERATOR = Path(__file__).resolve().parents[3] / "scripts" / "generate_status_evidence_fixture.py"


def _document() -> dict[str, Any]:
    return cast(dict[str, Any], load_fixture_json(_PATH))


def test_vector_is_owned_by_its_generator() -> None:
    spec = importlib.util.spec_from_file_location("_status_evidence_fixture", _GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.build_fixture() == _document()


@pytest.mark.parametrize("case", _CASES)
def test_vector_validates_and_every_rendering_matches(case: str) -> None:
    document = _document()
    request = cast(dict[str, Any], document["input"]["requests"][case])
    result = cast(dict[str, Any], document["input"]["results"][case])
    expected = cast(dict[str, Any], document["expected"])

    validate_schema_instance("status-request", "1.3.0", freeze_json(request))
    StatusRequestModel.model_validate(request)
    validate_schema_instance("status-result", "1.5.0", freeze_json(result))
    wrapped = StatusResultModel.model_validate(result)
    assert public_model_to_wire(wrapped) == result
    success = wrapped.root
    assert isinstance(success, StatusSuccessModel)
    assert isinstance(success.page, StatusEvidencePageModel)
    assert render_human_status(success).splitlines() == expected["human_lines"][case]
    assert render_safe_compact_summary(cast(JsonValue, result)) == expected["mcp_summaries"][case]
    rows = {item.evidence_id: item for item in success.page.items}
    for evidence_id in expected["own_evidence_ids"]:
        assert rows[evidence_id].publication_channel == "cooperative_mcp"
        assert isinstance(rows[evidence_id].description, str)


def test_older_schemas_reject_the_new_fields() -> None:
    document = _document()
    with pytest.raises(SchemaInstanceInvalid):
        validate_schema_instance(
            "status-request", "1.2.0", freeze_json(document["input"]["requests"]["mine"])
        )
    with pytest.raises(SchemaInstanceInvalid):
        validate_schema_instance(
            "status-result", "1.4.0", freeze_json(document["input"]["results"]["mine"])
        )


@pytest.mark.parametrize("author", ("theirs", "", "MINE"))
def test_author_admits_only_mine(author: str) -> None:
    request = {**_document()["input"]["requests"]["mine"], "filter": {"author": author}}
    with pytest.raises(SchemaInstanceInvalid):
        validate_schema_instance("status-request", "1.3.0", freeze_json(request))


def test_every_row_must_name_a_closed_channel() -> None:
    result = cast(dict[str, Any], _document()["input"]["results"]["unfiltered"])
    items = [dict(item) for item in result["page"]["items"]]
    del items[0]["publication_channel"]
    missing = {**result, "page": {**result["page"], "items": items}}
    with pytest.raises(SchemaInstanceInvalid):
        validate_schema_instance("status-result", "1.5.0", freeze_json(missing))
    items[0]["publication_channel"] = "caller_asserted"
    unknown = {**result, "page": {**result["page"], "items": items}}
    with pytest.raises(SchemaInstanceInvalid):
        validate_schema_instance("status-result", "1.5.0", freeze_json(unknown))
