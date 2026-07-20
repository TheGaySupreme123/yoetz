"""Thin command-line client for the persistent local Yoetz service."""

from __future__ import annotations

import dataclasses
import importlib
import sys
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from pathlib import Path
from typing import Annotated, BinaryIO, Final, cast

import anyio
import typer
from pydantic import BaseModel, ValidationError

from yoetz import __version__
from yoetz.cli.exits import exit_code_for
from yoetz.cli.render import (
    render_human_check,
    render_human_error,
    render_human_receipt,
    render_human_status,
)
from yoetz.domain.values import JsonObject
from yoetz.ports.control import ControlClientKind, ControlError
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode
from yoetz.protocol.models import (
    CheckRequest,
    CheckResult,
    CheckSuccessModel,
    OperationFailureModel,
    PublishWorkRequest,
    PublishWorkResult,
    ReceiptRequest,
    ReceiptResult,
    ReceiptSuccessModel,
    RespondRequest,
    RespondResult,
    StartRequest,
    StartResult,
    StatusRequest,
    StatusResult,
    StatusSuccessModel,
    public_model_to_wire,
)
from yoetz.service.client import ServiceClient, connect_service
from yoetz.service.control_protocol import public_error_code_for_control_reason

__all__ = ["app", "build_service_client", "main", "run_async"]

_MAX_INPUT_BYTES: Final = 1_048_576
_INPUT = Annotated[
    str | None,
    typer.Option("--input", help="Strict JSON request path, or - for stdin."),
]
_INLINE = Annotated[
    str | None,
    typer.Option("--request", help="Inline strict JSON request."),
]
_JSON = Annotated[bool, typer.Option("--json", help="Emit canonical JSON.")]
_DEADLINE = Annotated[
    int | None,
    typer.Option("--deadline-ms", min=1, max=86_400_000),
]

app = typer.Typer(
    name="yoetz",
    help="Local-first evidence ledger and review engine.",
    no_args_is_help=True,
    invoke_without_command=True,
    pretty_exceptions_enable=False,
    add_completion=False,
)
mcp_app = typer.Typer(help="Run protocol bridges.", no_args_is_help=True)
state_app = typer.Typer(help="Capture bounded local structural state.", no_args_is_help=True)
integrate_app = typer.Typer(help="Manage explicit harness integrations.", no_args_is_help=True)
integrate_skill_app = typer.Typer(help="Manage the Yoetz harness skill.", no_args_is_help=True)
service_app = typer.Typer(help="Manage the foreground local service.", no_args_is_help=True)
provider_app = typer.Typer(help="Manage provider setup.", no_args_is_help=True)
credential_app = typer.Typer(
    help="Provision credentials through a trusted ceremony.", no_args_is_help=True
)
privacy_app = typer.Typer(help="Inspect and configure local privacy policy.", no_args_is_help=True)
privacy_receipts_app = typer.Typer(
    help="Inspect bounded structural privacy receipts.", no_args_is_help=True
)
backup_app = typer.Typer(help="Preview or execute a backup.", no_args_is_help=True)
restore_app = typer.Typer(help="Preview or execute a restore.", no_args_is_help=True)
migrate_app = typer.Typer(help="Preview or execute a migration.", no_args_is_help=True)

app.add_typer(mcp_app, name="mcp")
app.add_typer(state_app, name="state")
app.add_typer(integrate_app, name="integrate")
integrate_app.add_typer(integrate_skill_app, name="skill")
app.add_typer(service_app, name="service")
app.add_typer(provider_app, name="provider")
provider_app.add_typer(credential_app, name="credential")
app.add_typer(privacy_app, name="privacy")
privacy_app.add_typer(privacy_receipts_app, name="receipts")
app.add_typer(backup_app, name="backup")
app.add_typer(restore_app, name="restore")
app.add_typer(migrate_app, name="migrate")


def run_async(operation: Callable[[], Awaitable[int]]) -> int:
    """Own exactly one event-loop bridge for a CLI operation."""

    return anyio.run(operation)


async def build_service_client(
    client_kind: ControlClientKind = ControlClientKind.CLI,
) -> ServiceClient:
    """Connect to the fixed same-user service endpoint; never spawn one."""

    return await connect_service(client_kind)


def _bounded_input(input_path: str | None, inline: str | None) -> JsonValue:
    if (input_path is None) == (inline is None):
        raise ProtocolValueError("invalid_event_value_type")
    if inline is not None:
        raw = inline.encode("utf-8")
    elif input_path == "-":
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    else:
        if input_path is None:
            raise ProtocolValueError("invalid_event_value_type")
        with Path(input_path).open("rb") as stream:
            raw = stream.read(_MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > _MAX_INPUT_BYTES:
        raise ProtocolValueError("invalid_event_value_type")
    return strict_json_parse(raw)


def _request_model(
    model_type: type[BaseModel], input_path: str | None, inline: str | None
) -> BaseModel:
    return model_type.model_validate(_bounded_input(input_path, inline))


def _json_object(input_path: str | None, inline: str | None) -> JsonObject:
    parsed = _bounded_input(input_path, inline)
    if not isinstance(parsed, Mapping):
        raise ProtocolValueError("unsupported_json_type")
    return JsonObject(parsed)


def _safe_write(stream: BinaryIO, data: bytes) -> None:
    try:
        stream.write(data)
        stream.flush()
    except BrokenPipeError:
        raise typer.Exit(70) from None


def _stdout_json(value: JsonValue) -> None:
    _safe_write(sys.stdout.buffer, canonical_encode(value) + b"\n")


def _stderr(message: str) -> None:
    try:
        typer.echo(message, err=True)
    except BrokenPipeError:
        pass


def _plain_json(value: object) -> JsonValue:
    if value is None or type(value) in {bool, int, str}:
        return cast(JsonValue, value)
    if isinstance(value, Enum):
        return cast(JsonValue, value.value)
    if isinstance(value, BaseModel):
        return cast(JsonValue, value.model_dump(mode="json", by_alias=True, exclude_none=False))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain_json(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        return {str(key): _plain_json(item) for key, item in source.items()}
    if isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        return [_plain_json(item) for item in sequence]
    if isinstance(value, (set, frozenset)):
        members = cast(set[object] | frozenset[object], value)
        return [_plain_json(item) for item in sorted(members, key=str)]
    raise TypeError("cli_result_not_json")


def _human_or_json(value: object, *, json_output: bool) -> None:
    if json_output or not sys.stdout.isatty():
        _stdout_json(_plain_json(value))
    else:
        typer.echo(canonical_encode(_plain_json(value)).decode("utf-8"))


def _usage_failure() -> int:
    _stderr("invalid_request: the command input is invalid")
    return 2


def _control_failure(error: ControlError) -> int:
    code = public_error_code_for_control_reason(error.reason)
    guidance = {
        PublicErrorCode.VAULT_LOCKED: "vault_locked: run 'yoetz service unlock' from a local terminal",
        PublicErrorCode.SERVICE_UNAVAILABLE: (
            "service_unavailable: run 'yoetz service run' under your selected user supervisor"
        ),
    }.get(code, f"{code.value.lower()}: the local request could not be completed")
    _stderr(guidance)
    return exit_code_for(code)


type WorkflowRequest = (
    StartRequest
    | PublishWorkRequest
    | CheckRequest
    | RespondRequest
    | StatusRequest
    | ReceiptRequest
)
type WorkflowResult = (
    StartResult | PublishWorkResult | CheckResult | RespondResult | StatusResult | ReceiptResult
)


async def _call_workflow(
    method: str,
    request_type: type[BaseModel],
    input_path: str | None,
    inline: str | None,
    json_output: bool,
    deadline_ms: int | None,
) -> int:
    try:
        request = cast(WorkflowRequest, _request_model(request_type, input_path, inline))
        client = await build_service_client()
        try:
            call = getattr(client, method)
            result = cast(WorkflowResult, await call(request, deadline_ms=deadline_ms))
        finally:
            await client.close()
        wire = public_model_to_wire(result)
        branch = result.root
        if json_output or not sys.stdout.isatty():
            _stdout_json(wire)
        elif isinstance(branch, OperationFailureModel):
            _stderr(render_human_error(branch.error))
        elif isinstance(branch, CheckSuccessModel):
            typer.echo(render_human_check(branch))
        elif isinstance(branch, StatusSuccessModel):
            typer.echo(render_human_status(branch))
        elif isinstance(branch, ReceiptSuccessModel):
            typer.echo(render_human_receipt(branch))
        else:
            typer.echo(canonical_encode(wire).decode("utf-8"))
        if isinstance(branch, OperationFailureModel):
            return exit_code_for(branch.error.code)
        return 0
    except OSError, ProtocolValueError, ValidationError, ValueError:
        return _usage_failure()
    except ControlError as error:
        return _control_failure(error)


def _finish(code: int) -> None:
    if code:
        raise typer.Exit(code)


def _workflow_command(method: str, request_type: type[BaseModel]) -> Callable[..., None]:
    def command(
        input_path: _INPUT = None,
        request: _INLINE = None,
        json_output: _JSON = False,
        deadline_ms: _DEADLINE = None,
    ) -> None:
        _finish(
            run_async(
                lambda: _call_workflow(
                    method, request_type, input_path, request, json_output, deadline_ms
                )
            )
        )

    return command


app.command("start")(_workflow_command("start", StartRequest))
app.command("publish-work")(_workflow_command("publish_work", PublishWorkRequest))
app.command("check")(_workflow_command("check", CheckRequest))
app.command("respond")(_workflow_command("respond", RespondRequest))
app.command("status")(_workflow_command("status", StatusRequest))
app.command("receipt")(_workflow_command("receipt", ReceiptRequest))


async def _call_support(
    method: str,
    input_path: str | None,
    inline: str | None,
    json_output: bool,
    deadline_ms: int | None,
) -> int:
    try:
        request = _json_object(input_path, inline)
        client = await build_service_client()
        try:
            result = await getattr(client, method)(request, deadline_ms=deadline_ms)
        finally:
            await client.close()
        _human_or_json(result, json_output=json_output)
        return 0
    except OSError, ProtocolValueError, ValidationError, ValueError:
        return _usage_failure()
    except ControlError as error:
        return _control_failure(error)


def _support_command(method: str) -> Callable[..., None]:
    def command(
        input_path: _INPUT = None,
        request: _INLINE = None,
        json_output: _JSON = False,
        deadline_ms: _DEADLINE = None,
    ) -> None:
        _finish(
            run_async(lambda: _call_support(method, input_path, request, json_output, deadline_ms))
        )

    return command


app.command("import")(_support_command("import_codex_jsonl"))
app.command("review")(_support_command("review"))
backup_app.command("preview")(_support_command("backup_preview"))
backup_app.command("execute")(_support_command("backup_execute"))
restore_app.command("preview")(_support_command("restore_preview"))
restore_app.command("execute")(_support_command("restore_execute"))
migrate_app.command("preview")(_support_command("migrate_preview"))
migrate_app.command("execute")(_support_command("migrate_execute"))
privacy_app.command("setup")(_support_command("privacy_get_setup"))
privacy_app.command("show")(_support_command("privacy_get_effective"))
privacy_app.command("propose")(_support_command("privacy_propose_policy"))
privacy_app.command("tighten")(_support_command("privacy_tighten_policy"))


async def _service_call(method: str, json_output: bool) -> int:
    try:
        client = await build_service_client()
        try:
            result = await getattr(client, method)()
        finally:
            await client.close()
        _human_or_json(result, json_output=json_output)
        return 0
    except ControlError as error:
        return _control_failure(error)


def _service_command(method: str) -> Callable[..., None]:
    def command(json_output: _JSON = False) -> None:
        _finish(run_async(lambda: _service_call(method, json_output)))

    return command


service_app.command("status")(_service_command("service_status"))
service_app.command("lock")(_service_command("lock"))
service_app.command("stop")(_service_command("stop"))


@service_app.command("run")
def service_run() -> None:
    """Run the persistent service in the foreground."""

    from yoetz.service.daemon import main as daemon_main

    daemon_main()


@mcp_app.command("serve")
def mcp_serve() -> None:
    """Run the MCP stdio bridge."""

    module = importlib.import_module("yoetz.mcp.server")
    mcp_main = cast(Callable[[], None], getattr(module, "main"))
    mcp_main()


@state_app.command("capture")
def state_capture(
    workspace: Annotated[Path, typer.Option("--workspace", exists=True, file_okay=False)],
    json_output: _JSON = False,
) -> None:
    """Capture content-withholding Git structural state."""

    try:
        from yoetz.adapters.git_subject_state import GitSubjectStateAdapter, open_local_workspace
        from yoetz.ports.subject_state import SubjectStateCaptureCommand, SubjectStateFormat

        handle = open_local_workspace(workspace)
        result = GitSubjectStateAdapter().capture(
            SubjectStateCaptureCommand(handle, SubjectStateFormat.GIT_STRUCTURAL_V1)
        )
        state = result.subject_state
        output: JsonValue = {
            "bytes_hashed": result.bytes_hashed,
            "files_hashed": result.files_hashed,
            "format": result.format.value,
            "limitations": [item.value for item in result.limitations],
            "status": result.status.value,
            "tree_digest": state.tree_digest if state is not None else None,
            "diff_digest": state.diff_digest if state is not None else None,
        }
        _human_or_json(output, json_output=json_output)
    except OSError, ValueError:
        _finish(_usage_failure())


async def _integration(action: str, harness: str, json_output: bool) -> int:
    if harness != "codex":
        return _usage_failure()
    request = JsonObject({"action": action, "harness": harness, "kind": "skill"})
    method = "integration_preview" if action == "preview" else "integration_execute"
    try:
        client = await build_service_client()
        try:
            result = await getattr(client, method)(request)
        finally:
            await client.close()
        _human_or_json(result, json_output=json_output)
        return 0
    except ControlError as error:
        return _control_failure(error)


@integrate_app.callback()
def integrate_root(
    context: typer.Context,
    harness: Annotated[str, typer.Argument(help="Exact harness ID (codex).")],
) -> None:
    context.obj = harness


def _integration_command(action: str) -> Callable[..., None]:
    def command(
        context: typer.Context,
        json_output: _JSON = False,
    ) -> None:
        harness = cast(str, context.find_root().find_object(str) or context.obj)
        _finish(run_async(lambda: _integration(action, harness, json_output)))

    return command


for _action in ("preview", "install", "status", "remove"):
    integrate_skill_app.command(_action)(_integration_command(_action))


async def _trusted_call(operation: Callable[[], Awaitable[object]], json_output: bool) -> int:
    try:
        result = await operation()
        _human_or_json(result, json_output=json_output)
        return 0
    except OSError, ProtocolValueError, ValueError:
        return _usage_failure()
    except Exception as error:
        unlock_module = importlib.import_module("yoetz.cli.unlock")
        client_module = importlib.import_module("yoetz.service.confidential_client")
        ceremony_error = cast(type[Exception], getattr(unlock_module, "HumanCeremonyCliError"))
        client_error = cast(type[Exception], getattr(client_module, "ConfidentialClientError"))
        if isinstance(error, ceremony_error):
            reason = cast(str, getattr(error, "reason"))
            if reason in {"cancelled", "interrupted"}:
                _stderr("cancelled")
                return exit_code_for("cancelled")
            if reason in {"preview_invalid", "result_invalid"}:
                _stderr("internal_error: the confidential ceremony could not be completed")
                return exit_code_for(PublicErrorCode.INTERNAL_ERROR)
            return _usage_failure()
        if isinstance(error, client_error):
            reason = cast(str, getattr(error, "reason"))
            if reason == "cancelled":
                _stderr("cancelled")
                return exit_code_for("cancelled")
            _stderr("service_unavailable: the confidential ceremony could not be completed")
            return exit_code_for(PublicErrorCode.SERVICE_UNAVAILABLE)
        raise


def _unlock_operation(name: str) -> Callable[[], Awaitable[object]]:
    module = importlib.import_module("yoetz.cli.unlock")
    return cast(Callable[[], Awaitable[object]], getattr(module, name))


@service_app.command("unlock")
def service_unlock(json_output: _JSON = False) -> None:
    _finish(run_async(lambda: _service_unlock(json_output)))


async def _service_unlock(json_output: bool) -> int:
    try:
        client = await build_service_client()
        try:
            status = await client.service_status()
        finally:
            await client.close()
    except ControlError as error:
        return _control_failure(error)
    if status.vault_mode == "os_keyring":
        return await _trusted_call(_unlock_operation("retry_keyring"), json_output)
    if status.vault_mode == "passphrase":
        return await _trusted_call(_unlock_operation("unlock_vault"), json_output)
    _stderr(
        "vault_locked: the vault is uninitialized; run "
        "'yoetz service initialize-passphrase' from a local terminal"
    )
    return exit_code_for(PublicErrorCode.VAULT_LOCKED)


@service_app.command("initialize-passphrase")
def service_initialize_passphrase(json_output: _JSON = False) -> None:
    _finish(
        run_async(
            lambda: _trusted_call(_unlock_operation("initialize_passphrase_vault"), json_output)
        )
    )


def _idle_relock_target(raw: str) -> int | str:
    if raw == "disabled":
        return raw
    if not raw.isascii() or not raw.isdecimal() or (len(raw) > 1 and raw.startswith("0")):
        raise ValueError("idle_relock_target_invalid")
    value = int(raw)
    if not 60 <= value <= 86_400:
        raise ValueError("idle_relock_target_invalid")
    return value


@service_app.command("idle-relock")
def service_idle_relock(
    target: Annotated[str, typer.Argument(help="60..86400 or disabled.")],
    json_output: _JSON = False,
) -> None:
    try:
        parsed = _idle_relock_target(target)
    except ValueError:
        _finish(_usage_failure())
        return
    module = importlib.import_module("yoetz.cli.unlock")
    operation = cast(
        Callable[[int | str], Awaitable[object]], getattr(module, "change_idle_relock_policy")
    )
    _finish(run_async(lambda: _trusted_call(lambda: operation(parsed), json_output)))


def _provider_credential_command(action: str) -> Callable[..., None]:
    def command(
        provider_id: Annotated[str, typer.Option("--provider-id")],
        model_id: Annotated[str, typer.Option("--model-id")],
        endpoint_profile_id: Annotated[str, typer.Option("--endpoint-profile-id")],
        endpoint_profile_version: Annotated[str, typer.Option("--endpoint-profile-version")],
        purpose: Annotated[str, typer.Option("--purpose")],
        scope_digest: Annotated[str, typer.Option("--scope-digest")],
        purpose_digest: Annotated[str, typer.Option("--purpose-digest")],
        json_output: _JSON = False,
    ) -> None:
        try:
            module = importlib.import_module("yoetz.service.confidential_protocol")
            target_type = cast(Callable[..., object], getattr(module, "ProviderCredentialTarget"))
            target = target_type(
                action=action,
                provider_id=provider_id,
                model_id=model_id,
                endpoint_profile_id=endpoint_profile_id,
                endpoint_profile_version=endpoint_profile_version,
                purpose=purpose,
                scope_digest=scope_digest,
                purpose_digest=purpose_digest,
            )
            unlock_module = importlib.import_module("yoetz.cli.unlock")
            function_name = (
                "set_provider_credential" if action == "set" else "rotate_provider_credential"
            )
            operation = cast(
                Callable[[object], Awaitable[object]], getattr(unlock_module, function_name)
            )
            _finish(run_async(lambda: _trusted_call(lambda: operation(target), json_output)))
        except ProtocolValueError, ValueError:
            _finish(_usage_failure())

    return command


credential_app.command("set")(_provider_credential_command("set"))
credential_app.command("rotate")(_provider_credential_command("rotate"))


def _privacy_decision_command(kind: str) -> Callable[..., None]:
    def command(
        pending_id: Annotated[str, typer.Argument(help="Exact pending privacy decision ID.")],
        json_output: _JSON = False,
    ) -> None:
        module = importlib.import_module("yoetz.cli.privacy_control")
        operation = cast(Callable[[str], Awaitable[object]], getattr(module, f"decide_{kind}"))
        _finish(run_async(lambda: _trusted_call(lambda: operation(pending_id), json_output)))

    return command


privacy_app.command("decide-policy")(_privacy_decision_command("policy"))
privacy_app.command("decide-disclosure")(_privacy_decision_command("disclosure"))


async def _privacy_receipts_list(page_size: int, cursor: str | None, json_output: bool) -> int:
    from yoetz.service.client import ListPrivacyReceiptsRequest

    try:
        client = await build_service_client()
        try:
            result = await client.privacy_receipts_list(
                ListPrivacyReceiptsRequest(page_size=page_size, cursor=cursor)
            )
        finally:
            await client.close()
        _human_or_json(result, json_output=json_output)
        return 0
    except ProtocolValueError, ValueError:
        return _usage_failure()
    except ControlError as error:
        return _control_failure(error)


@privacy_receipts_app.command("list")
def privacy_receipts_list(
    page_size: Annotated[int, typer.Option("--page-size", min=1, max=100)] = 50,
    cursor: Annotated[str | None, typer.Option("--cursor")] = None,
    json_output: _JSON = False,
) -> None:
    _finish(run_async(lambda: _privacy_receipts_list(page_size, cursor, json_output)))


async def _privacy_receipts_get(receipt_id: str, json_output: bool) -> int:
    from yoetz.service.client import GetPrivacyReceiptRequest

    try:
        client = await build_service_client()
        try:
            result = await client.privacy_receipts_get(GetPrivacyReceiptRequest(receipt_id))
        finally:
            await client.close()
        _human_or_json(result, json_output=json_output)
        return 0
    except ProtocolValueError, ValueError:
        return _usage_failure()
    except ControlError as error:
        return _control_failure(error)


@privacy_receipts_app.command("get")
def privacy_receipts_get(
    receipt_id: Annotated[str, typer.Argument(help="Exact privacy receipt ID.")],
    json_output: _JSON = False,
) -> None:
    _finish(run_async(lambda: _privacy_receipts_get(receipt_id, json_output)))


@app.command("version")
def version_command(
    json_output: _JSON = False,
    resources: Annotated[
        bool,
        typer.Option("--resources", help="Enumerate every reviewed installed resource identity."),
    ] = False,
) -> None:
    """Show installed public package/runtime identity."""

    if not json_output:
        typer.echo(__version__)
        return
    try:
        package = importlib.import_module("yoetz")
        module = importlib.import_module("yoetz.version")
        builder = cast(Callable[[], object], getattr(package, "get_version_manifest"))
        serializer = cast(
            Callable[..., bytes],
            getattr(module, "version_manifest_json"),
        )
        _safe_write(sys.stdout.buffer, serializer(builder(), include_resources=resources) + b"\n")
    except ImportError:
        _stdout_json({"package_name": "yoetz", "package_version": __version__})


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", is_eager=True, help="Show the installed package version."),
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit(0)


def main() -> None:
    """Installed console entry point with bounded exception handling."""

    try:
        app(prog_name="yoetz")
    except KeyboardInterrupt:
        _stderr("cancelled")
        raise SystemExit(130) from None
    except Exception:
        _stderr("internal_error: the command could not be completed")
        raise SystemExit(70) from None
