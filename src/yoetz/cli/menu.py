"""Interactive terminal menu over the existing command tree.

Bare ``yoetz`` in a terminal (after first-run setup) and the explicit
``yoetz menu`` command open one navigable screen for the operations users
otherwise assemble from subcommands: harness (Codex) MCP registration, the
provider-credential ceremonies, privacy inspection, and service lifecycle.
The menu adds no authority: every mutation keeps its preview/confirm gate,
trusted ceremonies still run through ``cli/unlock.py`` on the controlling
TTY, no service is ever spawned, and no secret enters a menu prompt.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from typing import Final, Literal, cast

import click
import typer
from pydantic import BaseModel

from yoetz import __version__
from yoetz.cli.exits import ceremony_refusal_message
from yoetz.domain.values import JsonObject
from yoetz.ports.control import ControlError
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.errors import PublicErrorCode
from yoetz.service.control_protocol import public_error_code_for_control_reason

__all__ = ["menu_available", "run_menu"]

_HARNESS: Final = "codex"
_BACK: Final = "b"
_QUIT: Final = "q"


def menu_available() -> bool:
    """True only when stdin and stdout are both real TTYs."""

    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except OSError, ValueError:
        return False


def _control_guidance(error: ControlError) -> str:
    code = public_error_code_for_control_reason(error.reason)
    return {
        PublicErrorCode.VAULT_LOCKED: (
            "vault_locked: unlock from this menu (Service -> Unlock vault) "
            "or run 'yoetz service unlock'"
        ),
        PublicErrorCode.SERVICE_UNAVAILABLE: (
            "service_unavailable: run 'yoetz service run' under your selected user supervisor"
        ),
    }.get(code, f"{code.value.lower()}: the local request could not be completed")


def _plain(value: object) -> JsonValue:
    if value is None or type(value) in {bool, int, str}:
        return cast(JsonValue, value)
    if isinstance(value, Enum):
        return cast(JsonValue, value.value)
    if isinstance(value, BaseModel):
        return cast(JsonValue, value.model_dump(mode="json", by_alias=True, exclude_none=False))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        return {str(key): _plain(item) for key, item in source.items()}
    if isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        return [_plain(item) for item in sequence]
    if isinstance(value, (set, frozenset)):
        members = cast(set[object] | frozenset[object], value)
        return [_plain(item) for item in sorted(members, key=str)]
    raise TypeError("cli_result_not_json")


def _show(value: object) -> None:
    typer.echo(json.dumps(_plain(value), ensure_ascii=False, indent=2, sort_keys=True))


def _run(operation: Callable[[], Awaitable[None]]) -> None:
    """One event-loop bridge per action; bounded guidance instead of exiting the menu."""

    from yoetz.cli.app import run_async

    async def wrapped() -> int:
        try:
            await operation()
        except ControlError as error:
            typer.echo(_control_guidance(error), err=True)
        return 0

    run_async(wrapped)


def _run_ceremony(operation: Callable[[], Awaitable[object]]) -> None:
    from yoetz.cli.app import run_async
    from yoetz.cli.unlock import HumanCeremonyCliError
    from yoetz.service.confidential_client import ConfidentialClientError

    async def wrapped() -> int:
        try:
            _show(await operation())
        except ControlError as error:
            typer.echo(_control_guidance(error), err=True)
        except OSError, ValueError:
            typer.echo("invalid_request: the ceremony input is invalid", err=True)
        except HumanCeremonyCliError as error:
            if error.reason in {"cancelled", "interrupted"}:
                typer.echo("cancelled", err=True)
            elif error.reason in {"preview_invalid", "result_invalid"}:
                typer.echo(
                    "internal_error: the confidential ceremony could not be completed", err=True
                )
            else:
                typer.echo("invalid_request: the ceremony input is invalid", err=True)
        except ConfidentialClientError as error:
            if error.reason == "cancelled":
                typer.echo("cancelled", err=True)
            else:
                refusal = ceremony_refusal_message(error.reason)
                typer.echo(
                    refusal
                    or "service_unavailable: the confidential ceremony could not be completed",
                    err=True,
                )
        return 0

    run_async(wrapped)


@dataclasses.dataclass(frozen=True, slots=True)
class _Overview:
    service_line: str
    harness_lines: tuple[str, ...]
    setup_line: str


async def _service_summary() -> str:
    from yoetz.cli.app import build_service_client

    try:
        client = await build_service_client()
        try:
            status = await client.service_status()
        finally:
            await client.close()
    except ControlError:
        return "not reachable — start it with 'yoetz service run'"
    return f"reachable ({status.state.value}, vault: {status.vault_mode})"


async def _harness_summary() -> tuple[str, ...]:
    from yoetz.adapters.integrations.codex_discovery import discover_codex_binaries
    from yoetz.adapters.integrations.codex_mcp import CodexMcpAdapter
    from yoetz.application.harness_mcp import HarnessMcpService
    from yoetz.ports.harness_mcp import McpRegistrationError

    binaries = discover_codex_binaries()
    if not binaries:
        return ("no codex executable found on PATH",)
    service = HarnessMcpService(CodexMcpAdapter())
    lines: list[str] = []
    for binary in binaries:
        version = binary.reported_version or "unknown version"
        try:
            state = (await service.status(binary)).value
        except McpRegistrationError as error:
            state = f"unknown ({error.reason.value})"
        lines.append(f"codex ({version}) — MCP registration: {state}")
    return tuple(lines)


def _gather_overview() -> _Overview:
    from yoetz.cli.setup import setup_marker_present

    lines: dict[str, object] = {}

    async def collect() -> int:
        lines["service"] = await _service_summary()
        lines["harness"] = await _harness_summary()
        return 0

    from yoetz.cli.app import run_async

    run_async(collect)
    setup_line = "complete" if setup_marker_present() else "not completed — option 2 runs it"
    return _Overview(
        service_line=cast(str, lines["service"]),
        harness_lines=cast(tuple[str, ...], lines["harness"]),
        setup_line=setup_line,
    )


def _print_home(overview: _Overview) -> None:
    typer.echo("")
    typer.echo(f"Yoetz {__version__} — local evidence ledger and review engine")
    typer.echo("")
    typer.echo(f"  Service    {overview.service_line}")
    for line in overview.harness_lines:
        typer.echo(f"  Harness    {line}")
    typer.echo(f"  First-run  {overview.setup_line}")
    typer.echo("")
    typer.echo("  1  Refresh status      re-probe the summary above")
    typer.echo("  2  Setup wizard        guided harness discovery and MCP registration")
    typer.echo("  3  Harness connection  inspect or register the Codex integration")
    typer.echo("  4  LLM provider        provision or rotate a provider credential")
    typer.echo("  5  Privacy             inspect policy and setup posture")
    typer.echo("  6  Service             unlock, lock, or stop the local service")
    typer.echo("  q  Quit")
    typer.echo("")


def _ask(choices: tuple[str, ...]) -> str:
    while True:
        raw = cast(str, typer.prompt("Select")).strip().lower()
        if raw in choices:
            return raw
        typer.echo(f"  choose one of: {', '.join(choices)}")


def _run_wizard() -> None:
    from yoetz.cli.app import run_async
    from yoetz.cli.setup import run_setup_wizard

    run_async(
        lambda: run_setup_wizard(
            non_interactive=False, codex_path=None, accept=False, json_output=False
        )
    )


def _harness_registration(action: str) -> None:
    from yoetz.cli.app import run_async
    from yoetz.cli.setup import integrate_mcp

    run_async(
        lambda: integrate_mcp(
            action,
            _HARNESS,
            codex_path=None,
            accept=False,
            preview_digest=None,
            json_output=False,
        )
    )


async def _skill_action(action: str) -> None:
    from yoetz.cli.app import build_service_client

    request = JsonObject({"action": action, "harness": _HARNESS, "kind": "skill"})
    method = "integration_preview" if action == "preview" else "integration_execute"
    client = await build_service_client()
    try:
        result = await getattr(client, method)(request)
    finally:
        await client.close()
    _show(result)


def _harness_menu() -> None:
    typer.echo("")
    typer.echo(f"Harness connection ({_HARNESS})")
    typer.echo("  1  Registration status   is the yoetz MCP server registered?")
    typer.echo("  2  Preview registration  show the exact proposed change")
    typer.echo("  3  Install registration  confirm before anything is applied")
    typer.echo("  4  Skill status")
    typer.echo("  5  Skill preview")
    typer.echo("  6  Skill install")
    typer.echo("  7  Skill remove")
    typer.echo("  b  Back")
    choice = _ask(("1", "2", "3", "4", "5", "6", "7", _BACK))
    if choice == _BACK:
        return
    if choice in {"1", "2", "3"}:
        _harness_registration({"1": "status", "2": "preview", "3": "install"}[choice])
        return
    action = {"4": "status", "5": "preview", "6": "install", "7": "remove"}[choice]
    _run(lambda: _skill_action(action))


def _provider_menu() -> None:
    typer.echo("")
    typer.echo("LLM provider connection")
    typer.echo("  Nonsecret endpoint binding is editable in config.toml (or option 1).")
    typer.echo("  Credentials are typed only inside the confidential ceremony.")
    typer.echo("  1  Choose provider and model (writes TOML)")
    typer.echo("  2  Add API key for the configured provider")
    typer.echo("  3  Replace API key for the configured provider")
    typer.echo("  b  Back")
    choice = _ask(("1", "2", "3", _BACK))
    if choice == _BACK:
        return
    if choice == "1":
        from yoetz.cli.provider_binding import prompt_provider_endpoint_binding

        prompt_provider_endpoint_binding()
        return
    action: Literal["set", "rotate"] = "set" if choice == "2" else "rotate"
    from yoetz.config.load import load_config
    from yoetz.service.confidential_protocol import ProviderCredentialTarget
    from yoetz.service.vault import provider_credential_profile_binding

    try:
        provider = load_config({}, {}, None).provider
        if provider is None:
            typer.echo("provider_not_configured: choose provider and model first", err=True)
            return
        binding = provider_credential_profile_binding(
            provider.provider_id,
            provider.model,
            provider.endpoint_profile_id,
            provider.endpoint_profile_version,
        )
        target = ProviderCredentialTarget(
            action=action,
            provider_id=binding.provider_id,
            model_id=binding.model_id,
            endpoint_profile_id=binding.endpoint_profile_id,
            endpoint_profile_version=binding.endpoint_profile_version,
            purpose=binding.purpose,
            scope_digest=binding.authorization_scope_digest,
            purpose_digest=binding.purpose_digest,
        )
    except ValueError:
        typer.echo("invalid_request: one of the identifiers is not valid", err=True)
        return

    from yoetz.cli.unlock import rotate_provider_credential, set_provider_credential

    operation = set_provider_credential if action == "set" else rotate_provider_credential
    _run_ceremony(lambda: operation(target))


async def _privacy_show(method: str) -> None:
    from yoetz.cli.app import build_service_client

    client = await build_service_client()
    try:
        result = await getattr(client, method)(JsonObject({}))
    finally:
        await client.close()
    _show(result)


def _privacy_menu() -> None:
    typer.echo("")
    typer.echo("Privacy")
    typer.echo("  1  Show effective policy")
    typer.echo("  2  Show setup posture")
    typer.echo("  b  Back")
    typer.echo("  (policy changes stay explicit: 'yoetz privacy setup|propose|tighten',")
    typer.echo("   receipts via 'yoetz privacy receipts list|get')")
    choice = _ask(("1", "2", _BACK))
    if choice == _BACK:
        return
    method = "privacy_get_effective" if choice == "1" else "privacy_get_setup"
    _run(lambda: _privacy_show(method))


async def _service_simple(method: str) -> None:
    from yoetz.cli.app import build_service_client

    client = await build_service_client()
    try:
        result = await getattr(client, method)()
    finally:
        await client.close()
    _show(result)


def _service_unlock() -> None:
    from yoetz.cli.app import build_service_client, run_async

    state: dict[str, object] = {}

    async def probe() -> int:
        try:
            client = await build_service_client()
            try:
                state["status"] = await client.service_status()
            finally:
                await client.close()
        except ControlError as error:
            state["error"] = error
        return 0

    run_async(probe)
    error = state.get("error")
    if isinstance(error, ControlError):
        typer.echo(_control_guidance(error), err=True)
        return
    vault_mode = getattr(state.get("status"), "vault_mode", None)
    from yoetz.cli import unlock

    if vault_mode == "os_keyring":
        _run_ceremony(unlock.retry_keyring)
    elif vault_mode == "passphrase":
        _run_ceremony(unlock.unlock_vault)
    else:
        typer.echo(
            "vault_locked: the vault is uninitialized; choose 'Initialize passphrase vault'",
            err=True,
        )


def _service_menu() -> None:
    typer.echo("")
    typer.echo("Service")
    typer.echo("  1  Status")
    typer.echo("  2  Unlock vault")
    typer.echo("  3  Initialize passphrase vault (first install only)")
    typer.echo("  4  Lock now")
    typer.echo("  5  Stop the service")
    typer.echo("  b  Back")
    choice = _ask(("1", "2", "3", "4", "5", _BACK))
    if choice == _BACK:
        return
    if choice == "1":
        _run(lambda: _service_simple("service_status"))
    elif choice == "2":
        _service_unlock()
    elif choice == "3":
        from yoetz.cli import unlock

        _run_ceremony(unlock.initialize_passphrase_vault)
    elif choice == "4":
        _run(lambda: _service_simple("lock"))
    elif typer.confirm("Stop the local service now?", default=False):
        _run(lambda: _service_simple("stop"))


def run_menu() -> int:
    """Drive the interactive menu until the user quits; requires a real terminal."""

    if not menu_available():
        typer.echo(
            "invalid_request: the interactive menu needs a terminal; see 'yoetz --help'",
            err=True,
        )
        return 2
    overview = _gather_overview()
    while True:
        _print_home(overview)
        try:
            choice = _ask(("1", "2", "3", "4", "5", "6", _QUIT))
            if choice == _QUIT:
                return 0
            if choice == "1":
                overview = _gather_overview()
            elif choice == "2":
                _run_wizard()
                overview = _gather_overview()
            elif choice == "3":
                _harness_menu()
            elif choice == "4":
                _provider_menu()
            elif choice == "5":
                _privacy_menu()
            else:
                _service_menu()
        except click.exceptions.Abort, EOFError:
            typer.echo("")
            return 0
