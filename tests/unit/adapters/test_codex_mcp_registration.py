"""Codex MCP registration adapter: classification, digest binding, and refusal rules."""

from __future__ import annotations

import json

import anyio
import pytest

from yoetz.adapters.integrations.codex_mcp import CodexMcpAdapter, CommandOutput
from yoetz.ports.harness_mcp import (
    HarnessBinary,
    McpRegistrationAction,
    McpRegistrationCommand,
    McpRegistrationError,
    McpRegistrationReason,
    McpRegistrationState,
)
from yoetz.ports.integrations import HarnessId

_BINARY = HarnessBinary(
    harness_id=HarnessId.CODEX,
    executable_path="/opt/harness/bin/codex",
    reported_version="0.144.5",
    compatibility="untested",
)


def _yoetz_entry(*, strict: bool = False) -> bytes:
    args = ["mcp", "serve"]
    if strict:
        args.extend(["--semantic", "off"])
    return json.dumps({"command": "yoetz", "args": args}).encode("utf-8")


class _Runner:
    """Scripted subprocess stand-in recording every argv it was asked to run."""

    def __init__(self, outputs: list[CommandOutput]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> CommandOutput:
        self.calls.append(argv)
        return self.outputs.pop(0)


def test_status_absent_on_nonzero_get() -> None:
    runner = _Runner([CommandOutput(1, b"")])
    adapter = CodexMcpAdapter(runner)
    state = anyio.run(lambda: adapter.status_registration(_BINARY))
    assert state is McpRegistrationState.ABSENT
    assert runner.calls == [("/opt/harness/bin/codex", "mcp", "get", "yoetz", "--json")]


def test_status_yoetz_owned_on_exact_command_match() -> None:
    for payload in (
        {"command": "yoetz", "args": ["mcp", "serve"]},
        {"command": ["yoetz", "mcp", "serve"]},
        {
            "name": "yoetz",
            "enabled": True,
            "transport": {
                "type": "stdio",
                "command": "yoetz",
                "args": ["mcp", "serve"],
            },
        },
    ):
        runner = _Runner([CommandOutput(0, json.dumps(payload).encode("utf-8"))])
        state = anyio.run(lambda: CodexMcpAdapter(runner).status_registration(_BINARY))
        assert state is McpRegistrationState.YOETZ_OWNED


def test_status_foreign_on_different_or_unreadable_command() -> None:
    for payload in (
        {"command": "other-server"},
        {"command": "yoetz", "args": ["serve", "--http"]},
        {"command": "wrapper", "args": ["yoetz", "mcp", "serve"]},
        {"command": "yoetz", "args": ["mcp", "serve", "--extra"]},
        {"command": ["wrapper", "yoetz", "mcp", "serve", "--semantic", "off"]},
        {"name": "yoetz"},
        {"command": 7},
        {
            "name": "yoetz",
            "transport": {"type": "stdio", "command": "other", "args": ["mcp", "serve"]},
        },
    ):
        runner = _Runner([CommandOutput(0, json.dumps(payload).encode("utf-8"))])
        state = anyio.run(lambda: CodexMcpAdapter(runner).status_registration(_BINARY))
        assert state is McpRegistrationState.FOREIGN_PRESENT, payload


def test_observe_reports_which_route_is_registered() -> None:
    """`yoetz_owned` alone cannot answer "can the agent get semantic review".

    Both serve commands classify as owned, so an operator reading only registration state sees a
    strict route and a policy route as the same thing. The observation carries the difference.
    """

    for strict, expected in ((False, "policy"), (True, "strict")):
        runner = _Runner([CommandOutput(0, _yoetz_entry(strict=strict))])
        observation = anyio.run(lambda: CodexMcpAdapter(runner).observe_registration(_BINARY))
        assert observation.state is McpRegistrationState.YOETZ_OWNED
        assert observation.route_profile == expected
        assert observation.harness_id is HarnessId.CODEX


def test_observe_reports_no_route_when_the_entry_is_not_ours() -> None:
    absent = anyio.run(
        lambda: CodexMcpAdapter(_Runner([CommandOutput(1, b"")])).observe_registration(_BINARY)
    )
    assert absent.state is McpRegistrationState.ABSENT
    assert absent.route_profile is None

    foreign_output = CommandOutput(0, json.dumps({"command": "other-server"}).encode("utf-8"))
    foreign = anyio.run(
        lambda: CodexMcpAdapter(_Runner([foreign_output])).observe_registration(_BINARY)
    )
    assert foreign.state is McpRegistrationState.FOREIGN_PRESENT
    assert foreign.route_profile is None


def test_observe_does_not_depend_on_the_adapter_route_profile() -> None:
    """The observation reports what is registered, never what this adapter would register."""

    runner = _Runner([CommandOutput(0, _yoetz_entry(strict=True))])
    observation = anyio.run(
        lambda: CodexMcpAdapter(runner, route_profile="policy").observe_registration(_BINARY)
    )
    assert observation.route_profile == "strict"


def test_observe_parse_failure_is_a_typed_error() -> None:
    runner = _Runner([CommandOutput(0, b"not json")])
    with pytest.raises(McpRegistrationError) as caught:
        anyio.run(lambda: CodexMcpAdapter(runner).observe_registration(_BINARY))
    assert caught.value.reason is McpRegistrationReason.PARSE_FAILED


def test_observe_rejects_a_non_codex_binary() -> None:
    with pytest.raises(McpRegistrationError) as caught:
        anyio.run(
            lambda: CodexMcpAdapter(_Runner([])).observe_registration(
                object()  # type: ignore[arg-type]
            )
        )
    assert caught.value.reason is McpRegistrationReason.HARNESS_UNAVAILABLE


def test_status_parse_failure_is_a_typed_error() -> None:
    for stdout in (b"not json", b"[1,2]", b"\xff\xfe"):
        runner = _Runner([CommandOutput(0, stdout)])
        with pytest.raises(McpRegistrationError) as caught:
            anyio.run(lambda: CodexMcpAdapter(runner).status_registration(_BINARY))
        assert caught.value.reason is McpRegistrationReason.PARSE_FAILED


def test_preview_register_for_absent_and_noop_for_owned() -> None:
    absent = anyio.run(
        lambda: CodexMcpAdapter(_Runner([CommandOutput(1, b"")])).preview_registration(_BINARY)
    )
    assert absent.action is McpRegistrationAction.REGISTER
    assert absent.state_before is McpRegistrationState.ABSENT
    assert absent.warnings == ()

    owned = anyio.run(
        lambda: CodexMcpAdapter(_Runner([CommandOutput(0, _yoetz_entry())])).preview_registration(
            _BINARY
        )
    )
    assert owned.action is McpRegistrationAction.NOOP
    assert owned.state_before is McpRegistrationState.YOETZ_OWNED


def test_preview_foreign_carries_warning_and_noop() -> None:
    runner = _Runner([CommandOutput(0, json.dumps({"command": "other"}).encode("utf-8"))])
    preview = anyio.run(lambda: CodexMcpAdapter(runner).preview_registration(_BINARY))
    assert preview.action is McpRegistrationAction.NOOP
    assert preview.warnings == ("foreign_entry_present",)


def test_strict_preview_binds_exact_command_and_changes_digest() -> None:
    policy = anyio.run(
        lambda: CodexMcpAdapter(_Runner([CommandOutput(1, b"")])).preview_registration(_BINARY)
    )
    strict = anyio.run(
        lambda: CodexMcpAdapter(
            _Runner([CommandOutput(1, b"")]),
            route_profile="strict",
        ).preview_registration(_BINARY)
    )

    assert policy.serve_command == ("yoetz", "mcp", "serve")
    assert policy.route_profile == "policy"
    assert strict.serve_command == ("yoetz", "mcp", "serve", "--semantic", "off")
    assert strict.route_profile == "strict"
    assert strict.preview_digest != policy.preview_digest


def test_explicit_preview_allows_reregistering_an_owned_route_profile() -> None:
    planned = CodexMcpAdapter(
        _Runner([CommandOutput(0, _yoetz_entry())]),
        route_profile="strict",
    )
    preview = anyio.run(lambda: planned.preview_registration(_BINARY))
    assert preview.action is McpRegistrationAction.REREGISTER

    runner = _Runner(
        [
            CommandOutput(0, _yoetz_entry()),
            CommandOutput(0, b""),
            CommandOutput(0, _yoetz_entry(strict=True)),
        ]
    )
    result = anyio.run(
        lambda: CodexMcpAdapter(
            runner,
            route_profile="strict",
        ).apply_registration(
            _BINARY,
            McpRegistrationCommand(preview.preview_digest, True),
        )
    )

    assert result.action is McpRegistrationAction.REREGISTER
    assert runner.calls[1][-5:] == ("yoetz", "mcp", "serve", "--semantic", "off")


def test_apply_requires_explicit_acceptance_and_exact_digest() -> None:
    adapter = CodexMcpAdapter(_Runner([CommandOutput(1, b"")]))
    preview = anyio.run(lambda: adapter.preview_registration(_BINARY))

    declined = CodexMcpAdapter(_Runner([CommandOutput(1, b"")]))
    with pytest.raises(McpRegistrationError) as caught:
        anyio.run(
            lambda: declined.apply_registration(
                _BINARY, McpRegistrationCommand(preview.preview_digest, False)
            )
        )
    assert caught.value.reason is McpRegistrationReason.CONFIRMATION_REQUIRED

    stale = CodexMcpAdapter(_Runner([CommandOutput(1, b"")]))
    with pytest.raises(McpRegistrationError) as caught:
        anyio.run(
            lambda: stale.apply_registration(
                _BINARY, McpRegistrationCommand("sha256:" + "0" * 64, True)
            )
        )
    assert caught.value.reason is McpRegistrationReason.PREVIEW_STALE


def test_apply_registers_then_verifies_by_rereading_state() -> None:
    plan_adapter = CodexMcpAdapter(_Runner([CommandOutput(1, b"")]))
    preview = anyio.run(lambda: plan_adapter.preview_registration(_BINARY))

    runner = _Runner(
        [
            CommandOutput(1, b""),  # get: absent
            CommandOutput(0, b""),  # add
            CommandOutput(0, _yoetz_entry()),  # verify get
        ]
    )
    result = anyio.run(
        lambda: CodexMcpAdapter(runner).apply_registration(
            _BINARY, McpRegistrationCommand(preview.preview_digest, True)
        )
    )
    assert result.action is McpRegistrationAction.REGISTER
    assert result.state_before is McpRegistrationState.ABSENT
    assert result.state_after is McpRegistrationState.YOETZ_OWNED
    assert runner.calls[1] == (
        "/opt/harness/bin/codex",
        "mcp",
        "add",
        "yoetz",
        "--",
        "yoetz",
        "mcp",
        "serve",
    )


def test_apply_reports_failure_when_verify_does_not_show_ownership() -> None:
    plan_adapter = CodexMcpAdapter(_Runner([CommandOutput(1, b"")]))
    preview = anyio.run(lambda: plan_adapter.preview_registration(_BINARY))

    runner = _Runner(
        [
            CommandOutput(1, b""),  # get: absent
            CommandOutput(0, b""),  # add claims success
            CommandOutput(1, b""),  # verify still absent
        ]
    )
    with pytest.raises(McpRegistrationError) as caught:
        anyio.run(
            lambda: CodexMcpAdapter(runner).apply_registration(
                _BINARY, McpRegistrationCommand(preview.preview_digest, True)
            )
        )
    assert caught.value.reason is McpRegistrationReason.REGISTRATION_FAILED


def test_apply_never_replaces_a_foreign_entry() -> None:
    foreign = CommandOutput(0, json.dumps({"command": "other"}).encode("utf-8"))
    plan_adapter = CodexMcpAdapter(_Runner([foreign]))
    preview = anyio.run(lambda: plan_adapter.preview_registration(_BINARY))

    runner = _Runner([CommandOutput(0, json.dumps({"command": "other"}).encode("utf-8"))])
    with pytest.raises(McpRegistrationError) as caught:
        anyio.run(
            lambda: CodexMcpAdapter(runner).apply_registration(
                _BINARY, McpRegistrationCommand(preview.preview_digest, True)
            )
        )
    assert caught.value.reason is McpRegistrationReason.FOREIGN_ENTRY_PRESENT
    # The refusal happened before any mutating `mcp add` invocation.
    assert all(call[1:3] == ("mcp", "get") for call in runner.calls)


def test_apply_noop_when_already_yoetz_owned() -> None:
    plan_adapter = CodexMcpAdapter(_Runner([CommandOutput(0, _yoetz_entry())]))
    preview = anyio.run(lambda: plan_adapter.preview_registration(_BINARY))

    runner = _Runner([CommandOutput(0, _yoetz_entry())])
    result = anyio.run(
        lambda: CodexMcpAdapter(runner).apply_registration(
            _BINARY, McpRegistrationCommand(preview.preview_digest, True)
        )
    )
    assert result.action is McpRegistrationAction.NOOP
    assert result.state_before is result.state_after is McpRegistrationState.YOETZ_OWNED
    assert all(call[1:3] == ("mcp", "get") for call in runner.calls)
