from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

import yoetz.cli.app as cli
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.models import StartRequest, StartResult

_REQUEST = cast(StartRequest, object())
_FAILURE_WIRE: dict[str, JsonValue] = {
    "protocol_version": "0.1",
    "schema_version": "1.0.0",
    "ok": False,
    "request_id": "req_00000000-0000-4000-8000-000000000001",
    "error": {
        "code": "SESSION_CONFLICT",
        "message": "The session conflicts with the request.",
        "retryable": False,
        "correlation_id": "err_00000000-0000-4000-8000-000000000002",
    },
}
_FAILURE = StartResult.model_validate(_FAILURE_WIRE)


class _Client:
    def __init__(self) -> None:
        self.request: StartRequest | None = None
        self.closed = False

    async def start(self, request: StartRequest, *, deadline_ms: int | None = None) -> StartResult:
        assert deadline_ms is None
        self.request = request
        return _FAILURE

    async def close(self) -> None:
        self.closed = True


def test_root_help_exposes_complete_tree_without_doctor() -> None:
    result = CliRunner().invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "start",
        "publish-work",
        "check",
        "respond",
        "status",
        "receipt",
        "mcp",
        "state",
        "integrate",
        "service",
        "provider",
        "privacy",
    ):
        assert command in result.stdout
    assert "doctor" not in result.stdout


def test_exact_integration_and_confidential_command_shapes() -> None:
    runner = CliRunner()
    assert (
        runner.invoke(cli.app, ["integrate", "codex", "skill", "preview", "--help"]).exit_code == 0
    )
    assert runner.invoke(cli.app, ["service", "idle-relock", "059"]).exit_code == 2
    assert runner.invoke(cli.app, ["provider", "credential", "set", "--help"]).exit_code == 0
    assert runner.invoke(cli.app, ["privacy", "receipts", "list", "--help"]).exit_code == 0


def test_bare_privacy_setup_is_guided_not_a_malformed_support_request() -> None:
    result = CliRunner().invoke(cli.app, ["privacy", "setup"])

    assert result.exit_code == 20
    assert "privacy_setup_failed: local_terminal_required" in result.stderr
    assert "invalid_request" not in result.output


def test_empty_privacy_show_request_uses_the_machine_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yoetz.domain.values import JsonObject

    observed: list[JsonObject] = []

    class PrivacyClient:
        async def privacy_get_effective(
            self,
            request: JsonObject,
            *,
            deadline_ms: int | None = None,
        ) -> JsonObject:
            assert deadline_ms is None
            observed.append(request)
            return JsonObject({"schema_version": "1.0.0"})

        async def close(self) -> None:
            return None

    async def build() -> PrivacyClient:
        return PrivacyClient()

    scoped = JsonObject(
        {
            "schema_version": "1.0.0",
            "scope": {
                "kind": "machine",
                "installation_id": "ins_00000000-0000-4000-8000-000000000001",
            },
        }
    )
    monkeypatch.setattr(cli, "build_service_client", build)

    def machine_scope() -> JsonObject:
        return scoped

    monkeypatch.setattr(
        "yoetz.cli.provider_status.machine_scope_request",
        machine_scope,
    )

    result = CliRunner().invoke(cli.app, ["privacy", "show", "--request", "{}", "--json"])

    assert result.exit_code == 0
    assert observed == [scoped]


def test_workflow_uses_service_client_and_preserves_failure_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()

    async def build() -> object:
        return client

    def request_model(
        model_type: type[BaseModel], input_path: str | None, inline: str | None
    ) -> BaseModel:
        del model_type, input_path, inline
        return _REQUEST

    def result_wire(model: object) -> dict[str, JsonValue]:
        assert model is _FAILURE
        return _FAILURE_WIRE

    monkeypatch.setattr(
        "yoetz.cli.app.build_service_client", cast(Callable[[], Awaitable[object]], build)
    )
    monkeypatch.setattr("yoetz.cli.app._request_model", request_model)
    monkeypatch.setattr("yoetz.cli.app.public_model_to_wire", result_wire)
    result = CliRunner().invoke(cli.app, ["start", "--request", "{}", "--json"])
    assert result.exit_code == 10
    assert result.stderr == ""
    assert result.stdout.encode("utf-8") == canonical_encode(_FAILURE_WIRE) + b"\n"
    assert client.request == _REQUEST
    assert client.closed is True


def test_missing_or_ambiguous_input_is_usage_error_without_traceback() -> None:
    runner = CliRunner()
    for arguments in (["start"], ["start", "--request", "{}", "--input", "-"]):
        result = runner.invoke(cli.app, arguments)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr == "invalid_request: the command input is invalid\n"
        assert "Traceback" not in result.stderr
