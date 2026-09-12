"""CLI rendering for bounded project-control refusals."""

from __future__ import annotations

import pytest

from yoetz.cli.app import control_failure
from yoetz.domain.coordination import CoordinationErrorCode
from yoetz.ports.control import ControlError


def test_implicit_project_refusal_has_json_reason_and_invalid_request_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[object] = []
    monkeypatch.setattr("yoetz.cli.app._stdout_json", emitted.append)

    code = control_failure(ControlError("implicit_project_requires_opt_out"), json_output=True)

    assert code == 2
    assert emitted == [
        {
            "ok": False,
            "public_code": "INVALID_REQUEST",
            "reason": "implicit_project_requires_opt_out",
            "retryable": False,
        }
    ]


def test_implicit_project_refusal_has_human_opt_out_remedy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = control_failure(ControlError("implicit_project_requires_opt_out"))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "implicit_project_requires_opt_out" in captured.err
    assert "yoetz project opt-out" in captured.err


@pytest.mark.parametrize("reason", tuple(code.value for code in CoordinationErrorCode))
def test_every_project_reason_maps_to_invalid_request_json(
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[object] = []
    monkeypatch.setattr("yoetz.cli.app._stdout_json", emitted.append)

    code = control_failure(ControlError(reason), json_output=True)

    assert code == 2
    assert len(emitted) == 1
    payload = emitted[0]
    assert isinstance(payload, dict)
    assert payload["ok"] is False
    assert payload["public_code"] == "INVALID_REQUEST"
    assert payload["reason"] == reason
    assert payload["retryable"] is False
