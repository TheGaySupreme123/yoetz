"""Harness MCP registration service: confirmation binding and diagnostics."""

from __future__ import annotations

import anyio
import pytest

from yoetz.application.harness_mcp import (
    HarnessMcpService,
    McpRegistrationConfirmation,
    McpRegistrationDiagnostic,
)
from yoetz.ports.harness_mcp import (
    HarnessBinary,
    McpRegistrationAction,
    McpRegistrationCommand,
    McpRegistrationError,
    McpRegistrationPreview,
    McpRegistrationReason,
    McpRegistrationResult,
    McpRegistrationState,
)
from yoetz.ports.integrations import HarnessId

_DIGEST = "sha256:" + "a" * 64
_BINARY = HarnessBinary(
    harness_id=HarnessId.CODEX,
    executable_path="/opt/harness/bin/codex",
    reported_version=None,
    compatibility="untested",
)


class _Sink:
    def __init__(self) -> None:
        self.records: list[McpRegistrationDiagnostic] = []

    def record_mcp_registration(self, diagnostic: McpRegistrationDiagnostic) -> None:
        self.records.append(diagnostic)


class _Port:
    def __init__(
        self,
        state: McpRegistrationState = McpRegistrationState.ABSENT,
        fail_with: McpRegistrationReason | None = None,
    ) -> None:
        self.state = state
        self.fail_with = fail_with
        self.applied: list[McpRegistrationCommand] = []

    async def status_registration(self, binary: HarnessBinary) -> McpRegistrationState:
        del binary
        if self.fail_with is not None:
            raise McpRegistrationError(self.fail_with, {})
        return self.state

    async def preview_registration(self, binary: HarnessBinary) -> McpRegistrationPreview:
        if self.fail_with is not None:
            raise McpRegistrationError(self.fail_with, {})
        action = (
            McpRegistrationAction.REGISTER
            if self.state is McpRegistrationState.ABSENT
            else McpRegistrationAction.NOOP
        )
        return McpRegistrationPreview(binary.harness_id, action, self.state, (), _DIGEST)

    async def apply_registration(
        self,
        binary: HarnessBinary,
        command: McpRegistrationCommand,
    ) -> McpRegistrationResult:
        self.applied.append(command)
        if self.fail_with is not None:
            raise McpRegistrationError(self.fail_with, {})
        return McpRegistrationResult(
            binary.harness_id,
            McpRegistrationAction.REGISTER,
            self.state,
            McpRegistrationState.YOETZ_OWNED,
            command.preview_digest,
        )


def test_status_and_preview_record_success_diagnostics() -> None:
    sink = _Sink()
    service = HarnessMcpService(_Port(), sink)
    state = anyio.run(lambda: service.status(_BINARY))
    preview = anyio.run(lambda: service.preview(_BINARY))
    assert state is McpRegistrationState.ABSENT
    assert preview.preview_digest == _DIGEST
    assert [record.phase for record in sink.records] == ["status", "preview"]
    assert all(record.outcome == "success" for record in sink.records)


def test_register_requires_explicit_acceptance() -> None:
    sink = _Sink()
    port = _Port()
    service = HarnessMcpService(port, sink)
    with pytest.raises(McpRegistrationError) as caught:
        anyio.run(
            lambda: service.register(
                _BINARY,
                McpRegistrationConfirmation(_DIGEST, False, "interactive"),
            )
        )
    assert caught.value.reason is McpRegistrationReason.CONFIRMATION_REQUIRED
    # The port was never asked to mutate anything.
    assert port.applied == []
    assert sink.records[-1].outcome == "failed"
    assert sink.records[-1].reason is McpRegistrationReason.CONFIRMATION_REQUIRED


def test_register_passes_exact_digest_and_records_result() -> None:
    sink = _Sink()
    port = _Port()
    service = HarnessMcpService(port, sink)
    result = anyio.run(
        lambda: service.register(
            _BINARY,
            McpRegistrationConfirmation(_DIGEST, True, "noninteractive_flag"),
        )
    )
    assert result.state_after is McpRegistrationState.YOETZ_OWNED
    assert port.applied == [McpRegistrationCommand(_DIGEST, True)]
    assert sink.records[-1].phase == "execute"
    assert sink.records[-1].outcome == "success"
    assert sink.records[-1].preview_digest == _DIGEST


def test_port_failure_is_recorded_and_reraised() -> None:
    sink = _Sink()
    service = HarnessMcpService(_Port(fail_with=McpRegistrationReason.FOREIGN_ENTRY_PRESENT), sink)
    with pytest.raises(McpRegistrationError):
        anyio.run(
            lambda: service.register(
                _BINARY,
                McpRegistrationConfirmation(_DIGEST, True, "interactive"),
            )
        )
    assert sink.records[-1].outcome == "failed"
    assert sink.records[-1].reason is McpRegistrationReason.FOREIGN_ENTRY_PRESENT


def test_confirmation_channel_is_closed() -> None:
    with pytest.raises(ValueError):
        McpRegistrationConfirmation(_DIGEST, True, "release_automation")  # type: ignore[arg-type]


def test_invalid_binary_rejected() -> None:
    service = HarnessMcpService(_Port())
    with pytest.raises(ValueError):
        anyio.run(lambda: service.status(object()))  # type: ignore[arg-type]
