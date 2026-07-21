"""First-run setup wizard: harness discovery, MCP registration, and honest next steps.

The wizard orchestrates only operations a human could already run by hand: it
discovers Codex binaries, previews and (after explicit confirmation) applies the
runbook's ``codex mcp get``/``codex mcp add`` sequence, checks whether the local
service is reachable, and prints the exact follow-up commands for the privacy
setup and the confidential provider-credential ceremony. It never spawns the
service, never touches a secret, and never claims a step it did not verify.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final

import typer

from yoetz.adapters.integrations.codex_discovery import discover_codex_binaries
from yoetz.adapters.integrations.codex_mcp import CodexMcpAdapter
from yoetz.application.harness_mcp import HarnessMcpService, McpRegistrationConfirmation
from yoetz.config.paths import PathSafetyError, setup_marker_path
from yoetz.ports.harness_mcp import (
    HarnessBinary,
    McpRegistrationAction,
    McpRegistrationError,
    McpRegistrationState,
)
from yoetz.ports.integrations import HarnessId
from yoetz.protocol.canonical import JsonValue, canonical_encode

__all__ = [
    "SETUP_MARKER_SCHEMA",
    "integrate_mcp",
    "run_setup_wizard",
    "setup_marker_present",
    "setup_status",
    "should_offer_first_run",
]

SETUP_MARKER_SCHEMA: Final = "yoetz.setup-wizard-marker/1"
_REPORT_SCHEMA: Final = "yoetz.setup-wizard-report/1"
_STATUS_SCHEMA: Final = "yoetz.setup-status/1"

_NEXT_SERVICE: Final = "run 'yoetz service run' under your selected user supervisor"
_NEXT_UNLOCK: Final = "run 'yoetz service unlock' from a local terminal if the vault is locked"
_NEXT_PRIVACY: Final = "run 'yoetz privacy setup' to review recipes and provider binding"
_NEXT_PROVIDER_TOML: Final = (
    "run 'yoetz provider endpoint' (or edit config.toml) to choose Official OpenAI "
    "or an owner-declared HTTPS origin+model — never put API keys in TOML"
)
_NEXT_CREDENTIAL: Final = (
    "run 'yoetz provider credential set' from a local terminal to provision the "
    "provider credential through the confidential ceremony"
)


def _stdout_json(value: JsonValue) -> None:
    sys.stdout.buffer.write(canonical_encode(value) + b"\n")
    sys.stdout.buffer.flush()


def _emit(value: JsonValue, *, json_output: bool) -> None:
    if json_output or not sys.stdout.isatty():
        _stdout_json(value)
    else:
        typer.echo(canonical_encode(value).decode("utf-8"))


def _usage_failure(message: str) -> int:
    typer.echo(f"invalid_request: {message}", err=True)
    return 2


class _UsageExit(Exception):
    """Internal control flow for a selection failure with an already-printed message."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def setup_marker_present() -> bool:
    """Report whether the wizard completion marker exists, failing closed on unsafe paths."""

    try:
        return setup_marker_path().is_file()
    except PathSafetyError, OSError:
        # An unsafe or unreadable state directory must never trigger the wizard.
        return True


def should_offer_first_run() -> bool:
    """True only for an interactive terminal with no completion marker recorded."""

    try:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    except OSError, ValueError:
        return False
    return interactive and not setup_marker_present()


def _write_setup_marker(outcome: str) -> bool:
    try:
        path = setup_marker_path()
        payload = canonical_encode({"outcome": outcome, "schema": SETUP_MARKER_SCHEMA}) + b"\n"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
    except PathSafetyError, OSError:
        return False
    return True


def _binary_row(binary: HarnessBinary) -> dict[str, JsonValue]:
    return {
        "compatibility": binary.compatibility,
        "executable_path": binary.executable_path,
        "harness": binary.harness_id.value,
        "reported_version": binary.reported_version,
    }


def _explicit_binary(codex_path: str) -> HarnessBinary | None:
    candidate = Path(os.path.abspath(codex_path))
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        return None
    return HarnessBinary(
        harness_id=HarnessId.CODEX,
        executable_path=str(candidate),
        reported_version=None,
        compatibility="untested",
    )


def _choose_binary(
    binaries: tuple[HarnessBinary, ...],
    *,
    codex_path: str | None,
    interactive: bool,
) -> HarnessBinary | None:
    """Return the selected binary or ``None`` when none exists; raise ``_UsageExit`` otherwise."""

    if codex_path is not None:
        explicit = _explicit_binary(codex_path)
        if explicit is None:
            raise _UsageExit(_usage_failure("the --codex-path executable was not found"))
        return explicit
    if not binaries:
        return None
    if len(binaries) == 1:
        return binaries[0]
    if not interactive:
        raise _UsageExit(
            _usage_failure("multiple codex executables found; select one with --codex-path")
        )
    typer.echo("Multiple codex executables were found:")
    for index, binary in enumerate(binaries, start=1):
        version = binary.reported_version or "unknown version"
        typer.echo(f"  {index}. {binary.executable_path} ({version})")
    raw = typer.prompt("Select the codex to configure", default="1")
    if not raw.isdecimal() or not 1 <= int(raw) <= len(binaries):
        raise _UsageExit(_usage_failure("the harness selection is not one of the listed numbers"))
    return binaries[int(raw) - 1]


async def _service_reachability() -> dict[str, JsonValue]:
    from yoetz.cli.app import build_service_client
    from yoetz.ports.control import ControlError

    try:
        client = await build_service_client()
        try:
            status = await client.service_status()
        finally:
            await client.close()
    except ControlError:
        return {"reachable": False, "vault_mode": None}
    vault_mode = getattr(status, "vault_mode", None)
    return {
        "reachable": True,
        "vault_mode": vault_mode if type(vault_mode) is str else None,
    }


def _registration_report(
    state: McpRegistrationState | None,
    *,
    outcome: str,
    reason: str | None = None,
) -> dict[str, JsonValue]:
    return {
        "outcome": outcome,
        "reason": reason,
        "state": None if state is None else state.value,
    }


async def _register_step(
    binary: HarnessBinary,
    *,
    interactive: bool,
    accept: bool,
) -> dict[str, JsonValue]:
    service = HarnessMcpService(CodexMcpAdapter())
    try:
        preview = await service.preview(binary)
    except McpRegistrationError as error:
        return _registration_report(None, outcome="failed", reason=error.reason.value)
    if preview.state_before is McpRegistrationState.YOETZ_OWNED:
        return _registration_report(preview.state_before, outcome="already_registered")
    if preview.state_before is McpRegistrationState.FOREIGN_PRESENT:
        # The runbook rule: preserve a foreign same-name entry, never replace it.
        return _registration_report(
            preview.state_before, outcome="skipped", reason="foreign_entry_present"
        )
    accepted = accept
    if interactive and not accepted:
        typer.echo("Proposed change: register 'yoetz mcp serve' as MCP server 'yoetz' with:")
        typer.echo(f"  {binary.executable_path}")
        accepted = typer.confirm("Apply this registration?", default=False)
    if not accepted:
        return _registration_report(preview.state_before, outcome="declined")
    try:
        result = await service.register(
            binary,
            McpRegistrationConfirmation(
                preview.preview_digest,
                True,
                "interactive" if interactive else "noninteractive_flag",
            ),
        )
    except McpRegistrationError as error:
        return _registration_report(
            preview.state_before, outcome="failed", reason=error.reason.value
        )
    return _registration_report(result.state_after, outcome="registered")


async def run_setup_wizard(
    *,
    non_interactive: bool,
    codex_path: str | None,
    accept: bool,
    json_output: bool,
) -> int:
    """Run the guided first-run setup and report each step honestly."""

    try:
        interactive = not non_interactive and sys.stdin.isatty() and sys.stdout.isatty()
    except OSError, ValueError:
        interactive = False
    binaries = discover_codex_binaries()
    try:
        chosen = _choose_binary(binaries, codex_path=codex_path, interactive=interactive)
    except _UsageExit as failure:
        return failure.code

    if chosen is None:
        registration = _registration_report(None, outcome="skipped", reason="codex_not_found")
    else:
        registration = await _register_step(chosen, interactive=interactive, accept=accept)

    service = await _service_reachability()

    if interactive:
        from yoetz.cli.provider_binding import prompt_provider_endpoint_binding

        prompt_provider_endpoint_binding()

    next_steps: list[JsonValue] = []
    if not service["reachable"]:
        next_steps.append(_NEXT_SERVICE)
    if service.get("vault_mode") == "passphrase" or not service["reachable"]:
        next_steps.append(_NEXT_UNLOCK)
    next_steps.append(_NEXT_PRIVACY)
    next_steps.append(_NEXT_PROVIDER_TOML)
    next_steps.append(_NEXT_CREDENTIAL)

    mutating_run = interactive or accept
    marker_written = _write_setup_marker(str(registration["outcome"])) if mutating_run else False

    report: dict[str, JsonValue] = {
        "discovered": [_binary_row(binary) for binary in binaries],
        "marker_written": marker_written,
        "next_steps": next_steps,
        "registration": registration,
        "schema": _REPORT_SCHEMA,
        "selected": None if chosen is None else _binary_row(chosen),
        "service": service,
    }
    if interactive:
        _emit_human_report(report)
    else:
        _emit(report, json_output=json_output)
    return 0


def _emit_human_report(report: dict[str, JsonValue]) -> None:
    registration = report["registration"]
    service = report["service"]
    typer.echo("Setup summary:")
    if isinstance(registration, dict):
        outcome = registration.get("outcome")
        reason = registration.get("reason")
        line = f"  harness MCP registration: {outcome}"
        if reason:
            line += f" ({reason})"
        typer.echo(line)
    if isinstance(service, dict):
        reachable = service.get("reachable")
        typer.echo(f"  local service reachable: {'yes' if reachable else 'no'}")
    steps = report["next_steps"]
    if isinstance(steps, list) and steps:
        typer.echo("Next steps:")
        for step in steps:
            typer.echo(f"  - {step}")


async def setup_status(*, json_output: bool) -> int:
    """Read-only setup posture: discovery, registration state, service, marker."""

    binaries = discover_codex_binaries()
    service_port = CodexMcpAdapter()
    rows: list[JsonValue] = []
    for binary in binaries:
        row = _binary_row(binary)
        try:
            state = await HarnessMcpService(service_port).status(binary)
            row["registration_state"] = state.value
        except McpRegistrationError as error:
            row["registration_state"] = None
            row["registration_error"] = error.reason.value
        rows.append(row)
    report: dict[str, JsonValue] = {
        "discovered": rows,
        "marker_present": setup_marker_present(),
        "schema": _STATUS_SCHEMA,
        "service": await _service_reachability(),
    }
    _emit(report, json_output=json_output)
    return 0


_MCP_EXIT_USAGE: Final = frozenset(
    {"confirmation_required", "preview_stale", "foreign_entry_present"}
)


def _mcp_error_exit(reason: str) -> int:
    typer.echo(f"mcp_registration_{reason}", err=True)
    return 2 if reason in _MCP_EXIT_USAGE else 20


async def integrate_mcp(
    action: str,
    harness: str,
    *,
    codex_path: str | None,
    accept: bool,
    preview_digest: str | None,
    json_output: bool,
) -> int:
    """Client-local ``integrate <harness> mcp status|preview|install`` commands."""

    if harness != "codex" or action not in {"status", "preview", "install"}:
        return _usage_failure("the harness or action is not supported")
    try:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    except OSError, ValueError:
        interactive = False
    binaries = discover_codex_binaries()
    try:
        chosen = _choose_binary(binaries, codex_path=codex_path, interactive=False)
    except _UsageExit as failure:
        return failure.code
    if chosen is None:
        return _usage_failure("no codex executable was found on PATH")

    service = HarnessMcpService(CodexMcpAdapter())
    try:
        if action == "status":
            state = await service.status(chosen)
            _emit({"harness": harness, "state": state.value}, json_output=json_output)
            return 0
        preview = await service.preview(chosen)
        if action == "preview":
            _emit(
                {
                    "action": preview.action.value,
                    "harness": harness,
                    "preview_digest": preview.preview_digest,
                    "state_before": preview.state_before.value,
                    "warnings": list(preview.warnings),
                },
                json_output=json_output,
            )
            return 0
        if preview_digest is not None and preview_digest != preview.preview_digest:
            return _mcp_error_exit("preview_stale")
        if preview.state_before is McpRegistrationState.FOREIGN_PRESENT:
            return _mcp_error_exit("foreign_entry_present")
        accepted = accept
        if interactive and not accepted:
            typer.echo("Proposed change: register 'yoetz mcp serve' as MCP server 'yoetz' with:")
            typer.echo(f"  {chosen.executable_path}")
            accepted = typer.confirm("Apply this registration?", default=False)
        if not accepted:
            return _mcp_error_exit("confirmation_required")
        if preview.action is McpRegistrationAction.NOOP:
            _emit(
                {
                    "action": "noop",
                    "harness": harness,
                    "state_after": preview.state_before.value,
                    "state_before": preview.state_before.value,
                },
                json_output=json_output,
            )
            return 0
        result = await service.register(
            chosen,
            McpRegistrationConfirmation(
                preview.preview_digest,
                True,
                "interactive" if interactive else "noninteractive_flag",
            ),
        )
    except McpRegistrationError as error:
        return _mcp_error_exit(error.reason.value)
    _emit(
        {
            "action": result.action.value,
            "harness": harness,
            "state_after": result.state_after.value,
            "state_before": result.state_before.value,
        },
        json_output=json_output,
    )
    return 0
