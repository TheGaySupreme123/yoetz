"""Closure preparation against real application status/publish validation, without a service."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from builders.projection_workflow import (
    ProjectionCase,
    build_projection_application,
    frontier_json,
    project_case,
    request_base,
)
from builders.start_application import protocol_id, start_request
from yoetz.cli.closure import Selection, prepare_closure
from yoetz.ports.control import ControlMethod
from yoetz.ports.ledger import CheckCommitResult
from yoetz.protocol.models import (
    CheckRequest,
    PublishWorkRequest,
    ReceiptRequest,
    RespondRequest,
    StatusRequest,
    StatusResult,
    public_model_to_wire,
)

pytestmark = pytest.mark.anyio


async def test_composer_paginates_and_requires_explicit_attempt_and_resolution() -> None:
    app, _ = await build_projection_application()
    app = replace(app, status_cursor_key=b"synthetic-closure-cursor-key-32-bytes")
    started = await app.start(start_request(8700, title="Synthetic closure"))
    obligation = protocol_id("obl_", 8701)
    result_id = protocol_id("res_", 8702)
    action_id = protocol_id("act_", 8703)
    number = 8800

    def draft(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal number
        number += 1
        return {
            "event_id": protocol_id("evt_", number),
            "schema": {"name": name, "version": "1.0.0"},
            "occurred_at": "2026-07-28T12:00:00.000Z",
            "causal_parents": [],
            "artifact_refs": [],
            "evidence_refs": [],
            "payload": payload,
        }

    initial = [
        draft(
            "plan_published",
            {"plan_version": 1, "summary": "Synthetic plan", "obligation_refs": [obligation]},
        ),
        draft(
            "obligation_published",
            {
                "obligation_id": obligation,
                "description": "Run exact test",
                "evidence_expectation": "Observed result",
                "status": "open",
                "requested_items": [{"item_kind": "command", "value": "pytest tests/a.py"}],
            },
        ),
        draft(
            "action_recorded",
            {
                "action_id": action_id,
                "action_kind": "command",
                "command": "pytest tests/b.py",
                "description": "Replacement needs revision",
                "obligation_refs": [obligation],
            },
        ),
        draft(
            "result_recorded",
            {"result_id": result_id, "action_id": action_id, "outcome": "partial"},
        ),
    ]
    frontier = started.frontier

    async def publish(drafts: list[dict[str, Any]], request_number: int) -> None:
        nonlocal frontier
        result = await app.publish_work(
            PublishWorkRequest.model_validate(
                {
                    **request_base(protocol_id("req_", request_number)),
                    "session_id": started.session_id,
                    "writer_id": started.writer_id,
                    "expected_frontier": frontier_json(frontier),
                    "event_drafts": drafts,
                }
            )
        )
        frontier = result.result_frontier

    await publish(initial, 9000)
    # More than one evidence page; captures need not all be relevant to the claim.
    evidence = [
        draft(
            "evidence_recorded",
            {
                "evidence_id": protocol_id("evd_", 9100 + i),
                "evidence_kind": "other",
                "strength": "metadata_only",
                "observed_at": "2026-07-28T12:00:00.000Z",
                "description": "Synthetic identity",
            },
        )
        for i in range(101)
    ]
    await publish(evidence[:100], 9300)
    await publish(evidence[100:], 9301)
    calls: list[StatusRequest] = []

    async def status(request: StatusRequest) -> StatusResult:
        calls.append(request)
        internal = await app.status(request)
        projected = await project_case(
            app,
            ProjectionCase("status", ControlMethod.STATUS, public_model_to_wire(request), internal),
            9500 + len(calls) * 2,
        )
        return StatusResult.model_validate(projected)

    inventory = await prepare_closure(status, started.session_id, started.writer_id, Selection())
    assert inventory["request"] is None
    assert len(inventory["inventory"]["evidence"]) == 101  # type: ignore[index, arg-type]
    continued = [call for call in calls if call.cursor is not None]
    assert continued and all(
        call.limit == "100" and call.at_frontier == str(frontier.sequence) for call in continued
    )
    with pytest.raises(ValueError, match="closure_command_revision_required"):
        await prepare_closure(
            status,
            started.session_id,
            started.writer_id,
            Selection(
                phase="attempt",
                obligation_ids=(obligation,),
                requested_item_indexes=(0,),
                action_kind="edit",
                description="Do not copy command into edit",
            ),
        )
    attempt = await prepare_closure(
        status,
        started.session_id,
        started.writer_id,
        Selection(
            phase="attempt",
            obligation_ids=(obligation,),
            requested_item_indexes=(0,),
            action_kind="command",
            command="pytest tests/a.py",
            description="Explicit caller attestation",
        ),
    )
    request = PublishWorkRequest.model_validate(attempt["request"])
    assert request.dry_run is True
    preview = await app.publish_work(request)
    assert frontier_json(preview.result_frontier) == frontier_json(frontier)
    committed = await app.publish_work(request.model_copy(update={"dry_run": False}))
    frontier = committed.result_frontier
    resolved = await prepare_closure(
        status,
        started.session_id,
        started.writer_id,
        Selection(
            phase="resolve",
            obligation_ids=(obligation,),
            evidence_refs=(protocol_id("evd_", 9100),),
        ),
    )
    resolved_request = PublishWorkRequest.model_validate(resolved["request"])
    await app.publish_work(resolved_request)
    resolved_result = await app.publish_work(resolved_request.model_copy(update={"dry_run": False}))
    frontier = resolved_result.result_frontier
    # A selected partial result becomes a limitation, never supporting evidence.
    claim = await prepare_closure(
        status,
        started.session_id,
        started.writer_id,
        Selection(
            phase="claim",
            obligation_ids=(obligation,),
            description="Bounded assertion",
            result_ids=(result_id,),
            evidence_refs=(protocol_id("evd_", 9100),),
        ),
    )
    claim_request = PublishWorkRequest.model_validate(claim["request"])
    payload = claim_request.event_drafts[0]["payload"]  # type: ignore[index]
    assert payload["limitation_refs"] == [result_id]  # type: ignore[index]
    assert result_id not in payload["supporting_refs"]  # type: ignore[index, operator]
    recovery = StatusRequest.model_validate(claim["recovery_request"])
    assert recovery.filter.operation_request_id == claim_request.request_id  # type: ignore[union-attr]
    await app.publish_work(claim_request)
    claimed = await app.publish_work(claim_request.model_copy(update={"dry_run": False}))
    checked = await app.check(
        CheckRequest.model_validate(
            {
                **request_base(protocol_id("req_", 9700)),
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": frontier_json(claimed.result_frontier),
                "mode": "deterministic_only",
                "max_findings": "10",
            }
        )
    )
    assert isinstance(checked, CheckCommitResult)
    assert "command_attempt_uncorroborated" in checked.coverage.known_gaps
    assert checked.findings
    response = await prepare_closure(
        status,
        started.session_id,
        started.writer_id,
        Selection(
            phase="respond",
            finding_id=checked.findings[0].finding_id,
            disposition="acknowledged",
            reason="Retain the synthetic evidence limitation.",
        ),
    )
    response_request = RespondRequest.model_validate(response["request"])
    assert frontier_json(response_request.finding_frontier) == frontier_json(
        checked.result_frontier
    )
    await app.respond(response_request)
    receipt = await prepare_closure(
        status, started.session_id, started.writer_id, Selection(phase="receipt")
    )
    receipt_result = await app.receipt(ReceiptRequest.model_validate(receipt["request"]))
    assert "command_attempt_uncorroborated" in receipt_result.coverage.known_gaps
    with pytest.raises(ValueError, match="closure_response_decision_required"):
        await prepare_closure(
            status, started.session_id, started.writer_id, Selection(phase="respond")
        )
