"""Project CLI status uses the canonical public serializer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

from yoetz.cli import project as project_cli
from yoetz.cli.render import render_human_status
from yoetz.domain.values import JsonValue
from yoetz.protocol.models import StatusResultModel, StatusSuccessModel, public_model_to_wire

pytestmark = pytest.mark.anyio


PROJECT_ID = "prj_69000000-0000-4000-8000-000000000001"
TASK_ID = "tsk_69000000-0000-4000-8000-000000000002"
SESSION_ID = "ses_69000000-0000-4000-8000-000000000003"
WRITER_ID = "wri_69000000-0000-4000-8000-000000000004"
REQUEST_ID = "req_69000000-0000-4000-8000-000000000005"


def _status_result() -> StatusResultModel:
    return StatusResultModel.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": REQUEST_ID,
            "ok": True,
            "task_id": TASK_ID,
            "session_id": SESSION_ID,
            "writer_id": WRITER_ID,
            "view": "project",
            "requested_frontier": {"sequence": "0", "head_digest": "genesis"},
            "head_frontier": {"sequence": "0", "head_digest": "genesis"},
            "subject_frontier": {"sequence": "0", "head_digest": "genesis"},
            "result_frontier": {"sequence": "0", "head_digest": "genesis"},
            "projection_lag": "0",
            "projection_version": "0.1.0",
            "rebuild_state": "current",
            "page": {
                "project_id": PROJECT_ID,
                "kind": "general",
                "membership_generation": "1",
                "grant_state": None,
                "members": [],
                "lineage": {
                    "parent_task_id": None,
                    "children": [],
                    "annotations": [],
                    "next_cursor": None,
                },
                "detections": [],
                "receipts": [],
                "coverage": [
                    {
                        "coverage_id": "evt_69000000-0000-4000-8000-000000000008",
                        "project_id": PROJECT_ID,
                        "task_id": TASK_ID,
                        "membership_generation": "1",
                        "coverage": "unobservable",
                        "gap_code": "not_observable",
                    }
                ],
                "next_cursor": None,
            },
            "coverage": {
                "publication_channels": ["cooperative_mcp"],
                "authorship_assurance": "self_asserted",
                "artifact_observation": "published_only",
                "evidence_immutability": "content_digest",
                "ledger_freshness": "current",
                "check_types": ["none"],
                "known_gaps": [],
            },
            "gaps": [],
            "import_status": {
                "pending_count": "0",
                "terminal_count": "0",
                "phase": None,
                "report_evidence_id": None,
                "source_identity_digest": None,
            },
            "closure_readiness": {
                "declared_obligation_count": "0",
                "no_obligations_reason": None,
                "open_obligation_count": "0",
                "unanswered_finding_count": "0",
                "receipt_blocking_finding_count": "0",
                "blocking_conditions": ["no_obligations_declared"],
            },
            "privacy_projection": {
                "sink": "agent_context",
                "local_disclosure_receipt_id": "egr_69000000-0000-4000-8000-000000000006",
                "policy_id": "pvy_69000000-0000-4000-8000-000000000007",
                "policy_version": "1",
                "policy_digest": "sha256:" + "0" * 64,
                "included_categories": [],
                "blocked_categories": [],
                "omitted_pointers": [],
                "projection_commitment": "hmac-sha256:" + "1" * 64,
            },
        }
    )


class _Client:
    def __init__(self, result: StatusResultModel) -> None:
        self.result = result
        self.request: object | None = None
        self.closed = False

    async def status(self, request: object, *, deadline_ms: int | None = None) -> object:
        del deadline_ms
        self.request = request
        return self.result

    async def close(self) -> None:
        self.closed = True


async def test_project_status_json_omits_unset_optional_project_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(_status_result())

    async def build(**_kwargs: object) -> _Client:
        return client

    monkeypatch.setattr("yoetz.cli.app.build_service_client", build)

    wire = await project_cli._invoke_status(  # pyright: ignore[reportPrivateUsage]
        {"project_id": PROJECT_ID},
        session_id=SESSION_ID,
        writer_id=WRITER_ID,
        deadline_ms=None,
        json_output=True,
    )

    page = cast(Mapping[str, JsonValue], wire["page"])
    assert "title" not in page
    assert "description" not in page
    assert "title_ref" not in page
    assert "description_ref" not in page
    assert cast(list[JsonValue], page["coverage"])[0] == {
        "coverage_id": "evt_69000000-0000-4000-8000-000000000008",
        "project_id": PROJECT_ID,
        "task_id": TASK_ID,
        "membership_generation": "1",
        "coverage": "unobservable",
        "gap_code": "not_observable",
    }
    assert client.closed is True
    assert client.request is not None


def test_project_status_human_output_includes_coordination_coverage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_cli._emit(  # pyright: ignore[reportPrivateUsage]
        cast(Mapping[str, JsonValue], public_model_to_wire(_status_result())),
        json_output=False,
    )
    result = _status_result().root
    assert isinstance(result, StatusSuccessModel)
    assert capsys.readouterr().out == render_human_status(result) + "\n"


def test_shared_status_renderer_includes_coordination_coverage_for_tui() -> None:
    result = _status_result().root
    assert isinstance(result, StatusSuccessModel)
    rendered = render_human_status(result)
    assert f"- {TASK_ID}: unobservable (not_observable)" in rendered
