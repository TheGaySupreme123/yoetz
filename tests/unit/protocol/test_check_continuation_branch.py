"""The nonterminal CHECK branch must be recoverable and must not look like a conclusion.

The supervised run produced a check whose only honest content was "a human must approve" — with
no pending id, no command, and no way back. This branch exists so that result is actionable, and
these tests hold the two properties that make it so: it carries the exact command, and it carries
nothing a caller could mistake for a verdict.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import uuid
from typing import Any, Final

import pytest
from pydantic import ValidationError

from yoetz.protocol.models import (
    CheckAwaitingHumanModel,
    CheckResultModel,
    CheckSuccessModel,
    StatusOperationPageModel,
)

_PENDING: Final = f"ppr_{uuid.uuid4()}"
_REQUEST: Final = f"req_{uuid.uuid4()}"
_INSTRUCTION: Final = "Run the command, then replay this exact check request with the same id."


def _privacy_projection() -> dict[str, Any]:
    """Every projected success body carries one, including this nonterminal branch."""

    return {
        "sink": "agent_context",
        "local_disclosure_receipt_id": f"egr_{uuid.uuid4()}",
        "policy_id": f"pvy_{uuid.uuid4()}",
        "policy_version": "1",
        "policy_digest": "sha256:" + "0" * 64,
        "included_categories": [],
        "blocked_categories": [],
        "omitted_pointers": [],
        "projection_commitment": "hmac-sha256:" + "1" * 64,
    }


def _frontier() -> dict[str, Any]:
    return {"sequence": "4", "head_digest": "sha256:" + "2" * 64}


def _continuation(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "privacy_disclosure_decision",
        "pending_id": _PENDING,
        "expires_at": "2026-08-05T13:00:00.000Z",
        "command": ["yoetz", "privacy", "decide-disclosure", _PENDING],
        "replay_request_id": _REQUEST,
        "instruction": _INSTRUCTION,
    }
    body.update(overrides)
    return body


def _awaiting(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": _REQUEST,
        "ok": True,
        "state": "awaiting_human",
        "task_id": f"tsk_{uuid.uuid4()}",
        "session_id": f"ses_{uuid.uuid4()}",
        "writer_id": f"wri_{uuid.uuid4()}",
        "subject_frontier": _frontier(),
        "result_frontier": _frontier(),
        "semantic_status": "awaiting_human",
        "semantic_reason": "human_approval_required",
        "continuation": _continuation(),
        "privacy_projection": _privacy_projection(),
        "versions": {
            "protocol_version": "0.1",
            "engine_version": "0.1.0",
            "projection_version": "yoetz/0.1.0",
            "policy_packs": ["research-evidence/0.1.0", "work-integrity/0.1.0"],
        },
    }
    body.update(overrides)
    return body


def test_the_awaiting_branch_validates_and_discriminates_on_state() -> None:
    result = CheckResultModel.model_validate(_awaiting())

    assert type(result.root) is CheckAwaitingHumanModel
    assert result.root.semantic_status == "awaiting_human"
    assert result.root.continuation.pending_id == _PENDING


def test_the_branch_carries_the_exact_command_the_user_must_run() -> None:
    result = CheckResultModel.model_validate(_awaiting())
    assert type(result.root) is CheckAwaitingHumanModel

    assert result.root.continuation.command == (
        "yoetz",
        "privacy",
        "decide-disclosure",
        _PENDING,
    )


def test_the_branch_returns_no_verdict_and_no_coverage() -> None:
    """A nonterminal result must offer nothing to conclude from."""

    result = CheckResultModel.model_validate(_awaiting())
    root = result.root

    # privacy_projection is deliberately not in this list: it describes how this body was
    # disclosed, not what the check concluded.
    for field in ("verdict", "findings", "coverage", "semantic_provenance", "suppressed_count"):
        assert not hasattr(root, field), f"{field} must not appear on a suspended check"


def test_a_command_that_does_not_match_the_pending_id_is_rejected() -> None:
    """The command is the whole point; a mismatched one sends the user to approve nothing."""

    other = f"ppr_{uuid.uuid4()}"
    body = _awaiting(
        continuation=_continuation(
            command=["yoetz", "privacy", "decide-disclosure", other],
        )
    )

    with pytest.raises(ValidationError):
        CheckResultModel.model_validate(body)


@pytest.mark.parametrize(
    "command",
    (
        ["yoetz", "privacy", "decide-disclosure"],
        ["yoetz", "privacy", "decide-policy", _PENDING],
        ["sh", "-c", "decide-disclosure", _PENDING],
    ),
)
def test_only_the_fixed_disclosure_command_is_admitted(command: list[str]) -> None:
    with pytest.raises(ValidationError):
        CheckResultModel.model_validate(_awaiting(continuation=_continuation(command=command)))


def test_the_continuation_must_name_the_request_to_replay() -> None:
    """Replaying a different request abandons the proposal the user just approved."""

    body = _awaiting(
        continuation=_continuation(replay_request_id=f"req_{uuid.uuid4()}"),
    )

    with pytest.raises(ValidationError):
        CheckResultModel.model_validate(body)


def test_a_terminal_check_still_validates_and_declares_complete() -> None:
    """The added discriminator must not disturb the ordinary branch."""

    from unit.protocol.test_models_and_schemas import (  # pyright: ignore[reportMissingImports]
        _check_result_wire,
    )

    result = CheckResultModel.model_validate(_check_result_wire())
    assert type(result.root) is CheckSuccessModel
    assert result.root.state == "complete"


def test_operation_status_returns_the_same_continuation_for_a_pending_check() -> None:
    page = StatusOperationPageModel.model_validate(
        {
            "operation_request_id": _REQUEST,
            "found": True,
            "state": "pending",
            "operation_kind": "check",
            "continuation": _continuation(),
        }
    )

    assert page.continuation is not None
    assert page.continuation.pending_id == _PENDING
    assert page.continuation.command[3] == _PENDING


def test_operation_status_returns_no_continuation_for_other_states() -> None:
    page = StatusOperationPageModel.model_validate(
        {
            "operation_request_id": _REQUEST,
            "found": True,
            "state": "complete",
            "operation_kind": "check",
        }
    )

    assert page.continuation is None


@pytest.mark.parametrize(
    ("state", "kind"),
    (("complete", "check"), ("pending", "receipt"), ("quarantined", "check")),
)
def test_a_continuation_outside_a_pending_check_is_rejected(state: str, kind: str) -> None:
    """Only a suspended check has one; anywhere else it would be a false instruction."""

    with pytest.raises(ValidationError):
        StatusOperationPageModel.model_validate(
            {
                "operation_request_id": _REQUEST,
                "found": True,
                "state": state,
                "operation_kind": kind,
                "continuation": _continuation(),
            }
        )
