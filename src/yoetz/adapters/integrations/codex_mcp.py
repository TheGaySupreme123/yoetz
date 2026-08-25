"""Codex adapter for the harness MCP registration port.

Automates exactly the runbook's manual check-then-add sequence:
``codex mcp get yoetz --json`` first, ``codex mcp add yoetz -- yoetz mcp serve``
only when no entry exists, and never replacing a foreign same-name entry.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, cast

from yoetz.ports.harness_mcp import (
    MCP_SERVE_COMMAND,
    MCP_SERVER_NAME,
    MCP_STRICT_SERVE_COMMAND,
    HarnessBinary,
    McpRegistrationAction,
    McpRegistrationCommand,
    McpRegistrationError,
    McpRegistrationObservation,
    McpRegistrationPreview,
    McpRegistrationReason,
    McpRegistrationResult,
    McpRegistrationState,
)
from yoetz.ports.integrations import HarnessId
from yoetz.protocol.canonical import canonical_digest

__all__ = [
    "CodexMcpAdapter",
    "CommandOutput",
]

_COMMAND_TIMEOUT_SECONDS: Final = 10.0
_OUTPUT_LIMIT_BYTES: Final = 65_536
# The registered argv is the only durable evidence of which route the agent actually gets.
_ROUTE_PROFILE_BY_COMMAND: Final[Mapping[tuple[str, ...], Literal["policy", "strict"]]] = (
    MappingProxyType({MCP_SERVE_COMMAND: "policy", MCP_STRICT_SERVE_COMMAND: "strict"})
)


@dataclass(frozen=True, slots=True)
class CommandOutput:
    """Bounded structural result of one harness subprocess invocation."""

    exit_code: int
    stdout: bytes


type CommandRunner = Callable[[tuple[str, ...]], CommandOutput]


def _default_runner(argv: tuple[str, ...]) -> CommandOutput:
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise McpRegistrationError(McpRegistrationReason.TIMEOUT, {}) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise McpRegistrationError(McpRegistrationReason.HARNESS_UNAVAILABLE, {}) from exc
    return CommandOutput(completed.returncode, completed.stdout[:_OUTPUT_LIMIT_BYTES])


def _entry_command_tokens(entry: Mapping[str, object]) -> tuple[str, ...] | None:
    # Codex ≤0.144.5 returned top-level command/args; ≥0.144.6 nests them under transport.
    transport = entry.get("transport")
    source: Mapping[str, object] = entry
    if isinstance(transport, Mapping):
        nested = cast(Mapping[str, object], transport)
        if "command" in nested or "args" in nested:
            source = nested
    command = source.get("command")
    args = source.get("args")
    if isinstance(command, str):
        tokens: list[str] = [command]
        if args is None:
            return tuple(tokens)
        if isinstance(args, Sequence) and not isinstance(args, (str, bytes)):
            items = cast(Sequence[object], args)
            if all(isinstance(item, str) for item in items):
                tokens.extend(cast(Sequence[str], items))
                return tuple(tokens)
        return None
    if isinstance(command, Sequence) and not isinstance(command, (str, bytes)):
        items = cast(Sequence[object], command)
        if all(isinstance(item, str) for item in items):
            return tuple(cast(Sequence[str], items))
    return None


class CodexMcpAdapter:
    """Implements ``HarnessMcpPort`` for the Codex CLI via bounded subprocesses."""

    __slots__ = ("_route_profile", "_runner", "_serve_command")

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        route_profile: Literal["policy", "strict"] = "policy",
    ) -> None:
        if route_profile not in {"policy", "strict"}:
            raise ValueError("mcp_route_profile_invalid")
        self._runner = _default_runner if runner is None else runner
        self._route_profile: Literal["policy", "strict"] = route_profile
        self._serve_command: tuple[str, ...] = (
            MCP_STRICT_SERVE_COMMAND if route_profile == "strict" else MCP_SERVE_COMMAND
        )

    def _run(self, argv: tuple[str, ...]) -> CommandOutput:
        return self._runner(argv)

    def _classify_get(
        self, output: CommandOutput
    ) -> tuple[McpRegistrationState, tuple[str, ...] | None]:
        if output.exit_code != 0:
            return McpRegistrationState.ABSENT, None
        try:
            parsed = json.loads(output.stdout.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise McpRegistrationError(McpRegistrationReason.PARSE_FAILED, {}) from exc
        if not isinstance(parsed, Mapping):
            raise McpRegistrationError(McpRegistrationReason.PARSE_FAILED, {})
        entry = cast(Mapping[str, object], parsed)
        tokens = _entry_command_tokens(entry)
        for command in (MCP_STRICT_SERVE_COMMAND, MCP_SERVE_COMMAND):
            if tokens == command:
                # Return the matched constant, not the parsed tokens, so the route mapping below
                # reads off one source of truth instead of re-comparing the argv.
                return McpRegistrationState.YOETZ_OWNED, command
        # An unreadable or different command is preserved, never replaced.
        return McpRegistrationState.FOREIGN_PRESENT, None

    async def status_registration(self, binary: HarnessBinary) -> McpRegistrationState:
        self._require_codex(binary)
        return self._classify_get(
            self._run((binary.executable_path, "mcp", "get", MCP_SERVER_NAME, "--json"))
        )[0]

    async def observe_registration(self, binary: HarnessBinary) -> McpRegistrationObservation:
        self._require_codex(binary)
        state, command = self._classify_get(
            self._run((binary.executable_path, "mcp", "get", MCP_SERVER_NAME, "--json"))
        )
        route_profile = None if command is None else _ROUTE_PROFILE_BY_COMMAND.get(command)
        return McpRegistrationObservation(binary.harness_id, state, route_profile)

    @staticmethod
    def _require_codex(binary: HarnessBinary) -> None:
        if type(binary) is not HarnessBinary or binary.harness_id is not HarnessId.CODEX:
            raise McpRegistrationError(McpRegistrationReason.HARNESS_UNAVAILABLE, {})

    def _preview_for(
        self,
        binary: HarnessBinary,
        state: McpRegistrationState,
        current_command: tuple[str, ...] | None,
    ) -> McpRegistrationPreview:
        action = (
            McpRegistrationAction.REGISTER
            if state is McpRegistrationState.ABSENT
            else (
                McpRegistrationAction.REREGISTER
                if state is McpRegistrationState.YOETZ_OWNED
                and current_command != self._serve_command
                else McpRegistrationAction.NOOP
            )
        )
        warnings: tuple[str, ...] = ()
        if state is McpRegistrationState.FOREIGN_PRESENT:
            warnings = ("foreign_entry_present",)
        digest = canonical_digest(
            {
                "action": action.value,
                "executable_path": binary.executable_path,
                "harness": binary.harness_id.value,
                "schema": "yoetz.mcp-registration-preview/1",
                "serve_command": list(self._serve_command),
                "server_name": MCP_SERVER_NAME,
                "state_before": state.value,
            }
        )
        return McpRegistrationPreview(
            binary.harness_id,
            action,
            state,
            warnings,
            digest,
            self._serve_command,
            self._route_profile,
        )

    async def preview_registration(self, binary: HarnessBinary) -> McpRegistrationPreview:
        self._require_codex(binary)
        state, current_command = self._classify_get(
            self._run((binary.executable_path, "mcp", "get", MCP_SERVER_NAME, "--json"))
        )
        return self._preview_for(binary, state, current_command)

    async def apply_registration(
        self,
        binary: HarnessBinary,
        command: McpRegistrationCommand,
    ) -> McpRegistrationResult:
        self._require_codex(binary)
        if type(command) is not McpRegistrationCommand:
            raise McpRegistrationError(McpRegistrationReason.CONFIRMATION_REQUIRED, {})
        if not command.explicitly_accepted:
            raise McpRegistrationError(McpRegistrationReason.CONFIRMATION_REQUIRED, {})
        preview = await self.preview_registration(binary)
        if command.preview_digest != preview.preview_digest:
            raise McpRegistrationError(McpRegistrationReason.PREVIEW_STALE, {})
        if preview.state_before is McpRegistrationState.FOREIGN_PRESENT:
            raise McpRegistrationError(McpRegistrationReason.FOREIGN_ENTRY_PRESENT, {})
        if preview.action is McpRegistrationAction.NOOP:
            return McpRegistrationResult(
                binary.harness_id,
                McpRegistrationAction.NOOP,
                preview.state_before,
                preview.state_before,
                preview.preview_digest,
            )
        add_output = self._run(
            (binary.executable_path, "mcp", "add", MCP_SERVER_NAME, "--", *self._serve_command)
        )
        if add_output.exit_code != 0:
            raise McpRegistrationError(
                McpRegistrationReason.REGISTRATION_FAILED,
                {"exit_code_class": "nonzero"},
            )
        # Verify by re-reading state rather than trusting the add exit code alone.
        state_after, command_after = self._classify_get(
            self._run((binary.executable_path, "mcp", "get", MCP_SERVER_NAME, "--json"))
        )
        if (
            state_after is not McpRegistrationState.YOETZ_OWNED
            or command_after != self._serve_command
        ):
            raise McpRegistrationError(
                McpRegistrationReason.REGISTRATION_FAILED,
                {"verified_state": state_after.value},
            )
        return McpRegistrationResult(
            binary.harness_id,
            preview.action,
            preview.state_before,
            state_after,
            preview.preview_digest,
        )

    def _unregistration_preview_for(
        self,
        binary: HarnessBinary,
        state: McpRegistrationState,
        current_command: tuple[str, ...] | None,
    ) -> McpRegistrationPreview:
        action = (
            McpRegistrationAction.NOOP
            if state is McpRegistrationState.ABSENT
            else McpRegistrationAction.UNREGISTER
        )
        warnings: tuple[str, ...] = ()
        if state is McpRegistrationState.FOREIGN_PRESENT:
            warnings = ("foreign_entry_present",)
        if (
            state is McpRegistrationState.YOETZ_OWNED
            and current_command is not None
            and current_command in _ROUTE_PROFILE_BY_COMMAND
        ):
            serve_command = current_command
            route_profile = _ROUTE_PROFILE_BY_COMMAND[current_command]
        else:
            # Never copy a foreign argv into the preview shape. The digest still
            # binds ``state_before``, so apply refuses the same-name entry.
            serve_command = self._serve_command
            route_profile = self._route_profile
        digest = canonical_digest(
            {
                "action": action.value,
                "executable_path": binary.executable_path,
                "harness": binary.harness_id.value,
                "schema": "yoetz.mcp-unregistration-preview/1",
                "serve_command": list(serve_command),
                "server_name": MCP_SERVER_NAME,
                "state_before": state.value,
            }
        )
        return McpRegistrationPreview(
            binary.harness_id,
            action,
            state,
            warnings,
            digest,
            serve_command,
            route_profile,
        )

    async def preview_unregistration(self, binary: HarnessBinary) -> McpRegistrationPreview:
        self._require_codex(binary)
        state, current_command = self._classify_get(
            self._run((binary.executable_path, "mcp", "get", MCP_SERVER_NAME, "--json"))
        )
        return self._unregistration_preview_for(binary, state, current_command)

    async def apply_unregistration(
        self,
        binary: HarnessBinary,
        command: McpRegistrationCommand,
    ) -> McpRegistrationResult:
        self._require_codex(binary)
        if type(command) is not McpRegistrationCommand:
            raise McpRegistrationError(McpRegistrationReason.CONFIRMATION_REQUIRED, {})
        if not command.explicitly_accepted:
            raise McpRegistrationError(McpRegistrationReason.CONFIRMATION_REQUIRED, {})
        preview = await self.preview_unregistration(binary)
        if command.preview_digest != preview.preview_digest:
            raise McpRegistrationError(McpRegistrationReason.PREVIEW_STALE, {})
        if preview.state_before is McpRegistrationState.FOREIGN_PRESENT:
            raise McpRegistrationError(McpRegistrationReason.FOREIGN_ENTRY_PRESENT, {})
        if preview.action is McpRegistrationAction.NOOP:
            return McpRegistrationResult(
                binary.harness_id,
                McpRegistrationAction.NOOP,
                preview.state_before,
                preview.state_before,
                preview.preview_digest,
            )
        remove_output = self._run((binary.executable_path, "mcp", "remove", MCP_SERVER_NAME))
        if remove_output.exit_code != 0:
            raise McpRegistrationError(
                McpRegistrationReason.REGISTRATION_FAILED,
                {"exit_code_class": "nonzero"},
            )
        state_after, _command_after = self._classify_get(
            self._run((binary.executable_path, "mcp", "get", MCP_SERVER_NAME, "--json"))
        )
        if state_after is not McpRegistrationState.ABSENT:
            raise McpRegistrationError(
                McpRegistrationReason.REGISTRATION_FAILED,
                {"verified_state": state_after.value},
            )
        return McpRegistrationResult(
            binary.harness_id,
            McpRegistrationAction.UNREGISTER,
            preview.state_before,
            state_after,
            preview.preview_digest,
        )
