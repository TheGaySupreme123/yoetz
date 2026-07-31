"""Thin command-line client for the persistent local Yoetz service."""

from __future__ import annotations

import dataclasses
import importlib
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, BinaryIO, Final, Literal, cast

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
    no_args_is_help=False,
    invoke_without_command=True,
    pretty_exceptions_enable=False,
    add_completion=False,
)
mcp_app = typer.Typer(help="Run protocol bridges.", no_args_is_help=True)
state_app = typer.Typer(help="Capture bounded local structural state.", no_args_is_help=True)
integrate_app = typer.Typer(help="Manage explicit harness integrations.", no_args_is_help=True)
integrate_skill_app = typer.Typer(help="Manage the Yoetz harness skill.", no_args_is_help=True)
integrate_mcp_app = typer.Typer(
    help="Manage the Yoetz MCP server registration.", no_args_is_help=True
)
setup_app = typer.Typer(help="Guided first-run harness and provider setup.", no_args_is_help=True)
service_app = typer.Typer(help="Manage the foreground local service.", no_args_is_help=True)
auto_unlock_app = typer.Typer(
    help="Inspect or repair restart-safe passphrase unlock.", no_args_is_help=True
)
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
elevated_app = typer.Typer(
    help=(
        "Human-review consent for non-default actions (ADR-015/016). "
        "Authority and secrets stay inside a verified foreground-console review."
    ),
    no_args_is_help=True,
)
hooks_app = typer.Typer(
    help="Codex lifecycle hook commands (activation, correlation, re-ground, observe).",
    no_args_is_help=True,
)
observe_app = typer.Typer(
    help="Live Codex observation consent and status (local control; not MCP).",
    no_args_is_help=True,
)
observe_checks_app = typer.Typer(
    help="Preview and manage exact-digest approved workspace checks.",
    no_args_is_help=True,
)

app.add_typer(mcp_app, name="mcp")
app.add_typer(state_app, name="state")
app.add_typer(integrate_app, name="integrate")
integrate_app.add_typer(integrate_skill_app, name="skill")
integrate_app.add_typer(integrate_mcp_app, name="mcp")
app.add_typer(setup_app, name="setup")
app.add_typer(service_app, name="service")
service_app.add_typer(auto_unlock_app, name="auto-unlock")
app.add_typer(provider_app, name="provider")
provider_app.add_typer(credential_app, name="credential")
app.add_typer(privacy_app, name="privacy")
privacy_app.add_typer(privacy_receipts_app, name="receipts")
app.add_typer(backup_app, name="backup")
app.add_typer(restore_app, name="restore")
app.add_typer(migrate_app, name="migrate")
app.add_typer(elevated_app, name="elevated-bootstrap")
app.add_typer(elevated_app, name="consent")
app.add_typer(hooks_app, name="hooks")
app.add_typer(observe_app, name="observe")
observe_app.add_typer(observe_checks_app, name="checks")


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
        PublicErrorCode.VAULT_LOCKED: (
            "vault_locked: unlock from a local terminal "
            "(`yoetz service unlock`); if uninitialized with no TTY, "
            "prepare `vault_initialize`, then run `yoetz consent review` on a trusted console"
        ),
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


def _hooks_operation(name: str) -> Callable[..., int]:
    module = importlib.import_module("yoetz.cli.hooks")
    return cast(Callable[..., int], getattr(module, name))


@hooks_app.command("user-prompt-submit")
def hooks_user_prompt_submit() -> None:
    """Inject the materiality/activation cue for UserPromptSubmit."""

    _finish(_hooks_operation("handle_user_prompt_submit")())


@hooks_app.command("post-tool-use")
def hooks_post_tool_use() -> None:
    """Correlate a successful Yoetz start tool call for PostToolUse."""

    _finish(_hooks_operation("handle_post_tool_use")())


@hooks_app.command("session-start")
def hooks_session_start() -> None:
    """Re-ground after SessionStart resume/compact; clear removes mapping."""

    _finish(_hooks_operation("handle_session_start")())


@hooks_app.command("observe")
def hooks_observe(
    event: Annotated[str, typer.Option("--event", help="Codex hook event name.")],
    workspace: Annotated[
        str | None,
        typer.Option(
            "--workspace",
            help=(
                "Project workspace path (use '.' for cwd). Resolved locally; only the "
                "private commitment is retained—never logged or persisted as plaintext."
            ),
        ),
    ] = None,
) -> None:
    """Unified bounded observation ingress for Codex lifecycle hooks."""

    module = importlib.import_module("yoetz.cli.observe_hooks")
    handler = cast(Callable[..., int], getattr(module, "handle_observe"))
    _finish(handler(event_name=event, workspace=workspace))


def _observe_operation(name: str) -> Callable[..., int]:
    module = importlib.import_module("yoetz.cli.observe")
    return cast(Callable[..., int], getattr(module, name))


@observe_app.command("status")
def observe_status_cmd(
    workspace: Annotated[
        str | None, typer.Option("--workspace", help="Workspace path (commitment only stored).")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show observation consent and lifecycle status for a workspace."""

    _finish(_observe_operation("observe_status")(workspace=workspace, json_output=json_output))


@observe_app.command("grant")
def observe_grant_cmd(
    workspace: Annotated[str, typer.Option("--workspace", help="Workspace path to consent.")],
) -> None:
    """One-time observation consent; stores a private workspace commitment only."""

    _finish(_observe_operation("grant_observation")(workspace=workspace))


@observe_app.command("pause")
def observe_pause_cmd(
    workspace: Annotated[str, typer.Option("--workspace")],
) -> None:
    """Pause new observation ingest while retaining consent and evidence."""

    _finish(_observe_operation("pause_observation")(workspace=workspace))


@observe_app.command("resume")
def observe_resume_cmd(
    workspace: Annotated[str, typer.Option("--workspace")],
) -> None:
    """Resume observation ingest after pause."""

    _finish(_observe_operation("resume_observation")(workspace=workspace))


@observe_app.command("revoke")
def observe_revoke_cmd(
    workspace: Annotated[str, typer.Option("--workspace")],
) -> None:
    """Stop new ingest permanently; retained evidence is kept."""

    _finish(_observe_operation("revoke_observation")(workspace=workspace))


@observe_app.command("reconcile")
def observe_reconcile_cmd(
    session_file: Annotated[str, typer.Option("--session-file", help="Codex session JSONL path.")],
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Advance the session-stream cursor for a consented workspace."""

    _finish(
        _observe_operation("reconcile_session_stream")(
            session_file=session_file,
            workspace=workspace,
            json_output=json_output,
        )
    )


@observe_checks_app.command("preview")
def observe_checks_preview_cmd(
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show exact policy bytes digest and proposed argv without activating it."""

    _finish(
        _observe_operation("observe_checks_preview")(workspace=workspace, json_output=json_output)
    )


@observe_checks_app.command("trust")
def observe_checks_trust_cmd(
    policy_digest: Annotated[str, typer.Option("--policy-digest")],
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
) -> None:
    """Trust only the exact raw policy digest currently present."""

    _finish(
        _observe_operation("observe_checks_trust")(workspace=workspace, policy_digest=policy_digest)
    )


@observe_checks_app.command("status")
def observe_checks_status_cmd(
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show whether the current exact policy digest is trusted."""

    _finish(
        _observe_operation("observe_checks_status")(workspace=workspace, json_output=json_output)
    )


@observe_checks_app.command("revoke")
def observe_checks_revoke_cmd(
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
) -> None:
    """Revoke workspace check-policy activation."""

    _finish(_observe_operation("observe_checks_revoke")(workspace=workspace))


@observe_checks_app.command("run")
def observe_checks_run_cmd(
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run exact trusted argv under the enforcing sandbox."""

    _finish(_observe_operation("observe_checks_run")(workspace=workspace, json_output=json_output))


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
        if input_path is None and inline is None and method == "privacy_get_effective":
            from yoetz.cli.provider_status import machine_scope_request

            request = machine_scope_request()
        else:
            request = _json_object(input_path, inline)
            if method == "privacy_get_effective" and len(request) == 0:
                from yoetz.cli.provider_status import machine_scope_request

                request = machine_scope_request()
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
privacy_app.command("show")(_support_command("privacy_get_effective"))
privacy_app.command("propose")(_support_command("privacy_propose_policy"))
privacy_app.command("tighten")(_support_command("privacy_tighten_policy"))


async def _run_privacy_setup_command() -> int:
    from yoetz.cli.privacy_setup import run_privacy_setup
    from yoetz.cli.unlock import HumanCeremonyCliError

    try:
        report = await run_privacy_setup(
            recipe_hint="metadata_only",
            offer_recommended=True,
        )
    except ControlError as error:
        return _control_failure(error)
    except (HumanCeremonyCliError, OSError, ValueError) as error:
        reason = getattr(error, "reason", None)
        typer.echo(
            f"privacy_setup_failed: {reason if type(reason) is str else 'privacy_setup_failed'}",
            err=True,
        )
        return 20
    if report.outcome == "failed":
        typer.echo(f"privacy_setup_failed: {report.reason or 'invalid'}", err=True)
        return 20
    _human_or_json(report, json_output=False)
    return 0


@privacy_app.command("setup")
def privacy_setup() -> None:
    """Review privacy choices and apply them through the trusted local ceremony."""

    _finish(run_async(_run_privacy_setup_command))


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


@service_app.command("diagnostics")
def service_diagnostics(
    correlation_id: Annotated[
        str,
        typer.Option(
            "--correlation-id",
            help="Exact err_… correlation id from a public error or reduced accept envelope.",
        ),
    ],
    json_output: _JSON = False,
) -> None:
    """Resolve one durable owner-only diagnostic record by correlation id."""

    try:
        from yoetz.observability.diagnostics import lookup_diagnostic_records
        from yoetz.protocol.ids import IdKind, validate_id

        validate_id(IdKind.CORRELATION, correlation_id)
        records = lookup_diagnostic_records(correlation_id)
        output = cast(
            JsonValue,
            {
                "correlation_id": correlation_id,
                "count": len(records),
                "records": [dict(item) for item in records],
            },
        )
        _human_or_json(output, json_output=json_output)
        _finish(0 if records else 1)
    except OSError, TypeError, ValueError:
        _finish(_usage_failure())


@mcp_app.command("serve")
def mcp_serve(
    semantic: Annotated[
        Literal["on", "off"],
        typer.Option(
            "--semantic",
            help="Semantic route posture: on follows policy; off fixes a process-lifetime ceiling.",
        ),
    ] = "on",
) -> None:
    """Run the MCP stdio bridge."""

    module = importlib.import_module("yoetz.mcp.server")
    mcp_main = cast(Callable[..., None], getattr(module, "main"))
    mcp_main(semantic=semantic)


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


_CODEX_PATH = Annotated[
    str | None,
    typer.Option("--codex-path", help="Exact codex executable to configure."),
]
_ACCEPT = Annotated[
    bool,
    typer.Option("--accept", help="Explicitly accept the previewed registration."),
]


def _setup_operation(name: str) -> Callable[..., Awaitable[int]]:
    module = importlib.import_module("yoetz.cli.setup")
    return cast(Callable[..., Awaitable[int]], getattr(module, name))


def _integration_mcp_command(action: str) -> Callable[..., None]:
    def command(
        context: typer.Context,
        codex_path: _CODEX_PATH = None,
        accept: _ACCEPT = False,
        preview_digest: Annotated[
            str | None,
            typer.Option("--preview-digest", help="Exact preview digest to bind."),
        ] = None,
        json_output: _JSON = False,
    ) -> None:
        harness = cast(str, context.find_root().find_object(str) or context.obj)
        operation = _setup_operation("integrate_mcp")
        _finish(
            run_async(
                lambda: operation(
                    action,
                    harness,
                    codex_path=codex_path,
                    accept=accept,
                    preview_digest=preview_digest,
                    json_output=json_output,
                )
            )
        )

    return command


for _mcp_action in ("preview", "install", "status"):
    integrate_mcp_app.command(_mcp_action)(_integration_mcp_command(_mcp_action))


@setup_app.command("run")
def setup_run(
    non_interactive: Annotated[
        bool,
        typer.Option("--non-interactive", help="Never prompt; report without mutating."),
    ] = False,
    codex_path: _CODEX_PATH = None,
    accept: _ACCEPT = False,
    json_output: _JSON = False,
) -> None:
    """Run the guided first-run setup wizard."""

    operation = _setup_operation("run_setup_wizard")
    _finish(
        run_async(
            lambda: operation(
                non_interactive=non_interactive,
                codex_path=codex_path,
                accept=accept,
                json_output=json_output,
            )
        )
    )


@setup_app.command("status")
def setup_status(json_output: _JSON = False) -> None:
    """Show read-only setup posture without mutating anything."""

    operation = _setup_operation("setup_status")
    _finish(run_async(lambda: operation(json_output=json_output)))


async def _trusted_call(operation: Callable[[], Awaitable[object]], json_output: bool) -> int:
    try:
        result = await operation()
        _human_or_json(result, json_output=json_output)
        return 0
    except OSError, ProtocolValueError, ValueError:
        return _usage_failure()
    except Exception as error:
        failure = _trusted_exception_failure(error)
        if failure is not None:
            return failure
        raise


def _trusted_exception_failure(error: Exception) -> int | None:
    """Map trusted-ceremony failures to bounded public CLI outcomes."""

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
    return None


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


def _auto_unlock_store() -> Any:
    """Build the store for the same environment-selected bundle as the daemon."""

    from yoetz.adapters.keys.os_keyring import AutoUnlockPassphraseStore
    from yoetz.config.load import load_config
    from yoetz.config.paths import bundle_root

    config = load_config({}, os.environ, None)
    return AutoUnlockPassphraseStore(bundle_root(_data_dir=config.storage.data_dir))


@auto_unlock_app.command("status")
def service_auto_unlock_status(json_output: _JSON = False) -> None:
    """Report scoped restart-unlock state without exposing credential material."""

    _finish(run_async(lambda: _service_auto_unlock_status(json_output)))


async def _service_auto_unlock_status(json_output: bool) -> int:
    """Compose platform-entry and live-service evidence into one bounded report."""

    store = _auto_unlock_store()
    secret, reason = store.load_with_reason()
    if secret is not None:
        for index in range(len(secret)):
            secret[index] = 0
    service_state: str | None = None
    service_reason: str | None = None
    try:
        client = await build_service_client()
        try:
            status = await client.service_status()
            service_state = status.state.value
            service_reason = status.state_reason
        finally:
            await client.close()
    except ControlError as error:
        service_state = error.reason
        service_reason = error.reason
    state = (
        "rejected"
        if service_reason == "auto_unlock_rejected"
        else "stale"
        if service_reason == "auto_unlock_stale"
        else "provisioned"
        if reason == "none"
        else "absent"
        if reason == "auto_unlock_absent"
        else "backend_unsupported"
        if reason == "auto_unlock_backend_unavailable"
        else "rejected"
    )
    report: JsonValue = {
        "schema": "yoetz.auto-unlock-status/1",
        "state": state,
        "service_state": service_state,
        "service_state_reason": service_reason,
        "next_command": (
            "yoetz service auto-unlock repair" if state in {"stale", "absent", "rejected"} else None
        ),
    }
    _human_or_json(report, json_output=json_output)
    return 0 if state == "provisioned" else 20


@auto_unlock_app.command("repair")
def service_auto_unlock_repair(json_output: _JSON = False) -> None:
    """Repair restart unlock after proving the current vault passphrase."""

    _finish(run_async(lambda: _service_auto_unlock_repair(json_output)))


@auto_unlock_app.command("enable")
def service_auto_unlock_enable(json_output: _JSON = False) -> None:
    """Enable restart-safe unlock for an existing passphrase vault."""

    _finish(run_async(lambda: _service_auto_unlock_repair(json_output)))


async def _service_auto_unlock_repair(json_output: bool) -> int:
    """Prove, persist, and wipe one bundle-scoped passphrase."""

    from yoetz.adapters.keys.os_keyring import OSKeyringError
    from yoetz.cli.unlock import read_vault_passphrase_for_auto_unlock, unlock_vault

    try:
        client = await build_service_client()
        try:
            status = await client.service_status()
        finally:
            await client.close()
    except ControlError as error:
        return _control_failure(error)
    if status.vault_mode != "passphrase":
        _stderr("auto_unlock_unavailable: the vault is not in passphrase mode")
        return 20
    if status.state.value != "locked":
        _stderr("auto_unlock_repair_requires_locked_service: run 'yoetz service lock' and retry")
        return 20

    try:
        passphrase = read_vault_passphrase_for_auto_unlock()
        try:
            result = await unlock_vault(bytearray(passphrase))
            if result.state != "ready":
                _stderr("vault_locked: the passphrase did not unlock the vault")
                return exit_code_for(PublicErrorCode.VAULT_LOCKED)
            store = _auto_unlock_store()
            store.save(passphrase)
        finally:
            for index in range(len(passphrase)):
                passphrase[index] = 0
    except OSKeyringError as error:
        _stderr(f"auto_unlock_{error.reason}")
        return 20
    except Exception as error:
        failure = _trusted_exception_failure(error)
        if failure is not None:
            return failure
        raise
    report: JsonValue = {
        "schema": "yoetz.auto-unlock-repair/1",
        "outcome": "repaired",
        "service_state": "ready",
        "next_command": "yoetz service auto-unlock status",
    }
    _human_or_json(report, json_output=json_output)
    return 0


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


@provider_app.command("status")
def provider_status(json_output: _JSON = False) -> None:
    """Report whether external semantic review is structurally ready to dispatch."""

    from yoetz.cli.provider_status import run_provider_status

    _finish(run_async(lambda: run_provider_status(json_output=json_output)))


@provider_app.command("endpoint")
def provider_endpoint(
    official: Annotated[
        bool, typer.Option("--official", help="Bind the bundled Official OpenAI Responses profile.")
    ] = False,
    grok: Annotated[
        bool,
        typer.Option("--grok", help="Bind the bundled Grok / xAI Chat Completions profile."),
    ] = False,
    fireworks: Annotated[
        bool,
        typer.Option(
            "--fireworks",
            help="Bind the reviewed Fireworks Responses profile.",
        ),
    ] = False,
    provider_name: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help=(
                "Reviewed preset: openai, fireworks, anthropic, gemini, openrouter, grok, "
                "or vercel-ai-gateway."
            ),
        ),
    ] = None,
    https_origin: Annotated[
        str | None,
        typer.Option(
            "--https-origin",
            help="Owner-declared HTTPS origin (https://host[:port] only).",
        ),
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Model identifier.")] = None,
    interactive: Annotated[
        bool, typer.Option("--interactive/--no-interactive", help="Prompt on a TTY.")
    ] = True,
    json_output: _JSON = False,
) -> None:
    """Write a nonsecret reviewed provider or owner-declared binding to config.toml."""

    from yoetz.cli.provider_binding import (
        ProviderEndpointChoice,
        apply_provider_endpoint_choice,
        prompt_provider_endpoint_binding,
        prompt_provider_model,
    )
    from yoetz.config.models import ConfigError
    from yoetz.config.write import provider_preset

    try:
        if (
            interactive
            and not official
            and not fireworks
            and not grok
            and provider_name is None
            and https_origin is None
            and model is None
        ):
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                _finish(_usage_failure())
                return
            prompt_provider_endpoint_binding()
            _finish(0)
            return
        if (
            (official and (fireworks or grok))
            or (fireworks and grok)
            or (
                provider_name is not None
                and (official or fireworks or grok or https_origin is not None)
            )
            or ((official or fireworks or grok) and https_origin is not None)
        ):
            _finish(_usage_failure())
            return
        choice: ProviderEndpointChoice
        if provider_name is not None:
            choice = cast(ProviderEndpointChoice, provider_preset(provider_name).choice)
        elif official:
            choice = "official_openai"
        elif fireworks:
            choice = "fireworks"
        elif grok:
            choice = "grok"
        elif https_origin is not None:
            choice = "owner_declared"
        else:
            _finish(_usage_failure())
            return
        if model is None:
            if not interactive or not (sys.stdin.isatty() and sys.stdout.isatty()):
                _finish(_usage_failure())
                return
            if choice == "owner_declared":
                model = typer.prompt("Model id", show_default=False).strip()
            else:
                model = prompt_provider_model(choice)
            if not model:
                _finish(_usage_failure())
                return
        if choice == "owner_declared":
            path, provider = apply_provider_endpoint_choice(
                "owner_declared", model=model, https_origin=https_origin
            )
        else:
            path, provider = apply_provider_endpoint_choice(choice, model=model)
        payload = {
            "config_path": str(path),
            "endpoint_profile_id": provider.endpoint_profile_id,
            "endpoint_profile_version": provider.endpoint_profile_version,
            "model": provider.model,
            "provider_id": provider.provider_id,
        }
        if provider.owner_declared_endpoint is not None:
            payload["https_origin"] = provider.owner_declared_endpoint.https_origin
        _human_or_json(payload, json_output=json_output)
        _finish(0)
    except ConfigError as error:
        typer.echo(f"invalid_request: {error.reason_code}", err=True)
        _finish(2)


@privacy_app.command("export-desired")
def privacy_export_desired(
    output: Annotated[Path, typer.Option("--output", help="Destination TOML path.")],
    json_output: _JSON = False,
) -> None:
    """Export effective nonsecret privacy policy as desired-state TOML (never secrets)."""

    async def _run() -> int:
        from yoetz.adapters.privacy.catalog import decode_privacy_policy_canonical
        from yoetz.config.privacy_desired import write_privacy_desired_toml

        try:
            client = await build_service_client()
            try:
                from yoetz.cli.provider_status import machine_scope_request

                effective = await client.privacy_get_effective(machine_scope_request())
            finally:
                await client.close()
        except ControlError as error:
            return _control_failure(error)
        plain = _plain_json(effective)
        if type(plain) is not dict or "policy" not in plain:
            return _usage_failure()
        try:
            policy = decode_privacy_policy_canonical(
                canonical_encode(cast(JsonValue, plain["policy"]))
            )
        except Exception:
            return _usage_failure()
        path = write_privacy_desired_toml(policy, output)
        _human_or_json(
            {"path": str(path), "schema": "yoetz.privacy-desired/1"}, json_output=json_output
        )
        return 0

    _finish(run_async(_run))


@privacy_app.command("apply-desired")
def privacy_apply_desired(
    input_path: Annotated[Path, typer.Option("--input", help="Desired-state TOML path.")],
    json_output: _JSON = False,
) -> None:
    """Classify privacy desired-state TOML: tighten may proceed via gates; widen never silent."""

    async def _run() -> int:
        from yoetz.adapters.privacy.catalog import decode_privacy_policy_canonical
        from yoetz.application.privacy_policy import is_privacy_tightening
        from yoetz.config.models import ConfigError
        from yoetz.config.privacy_desired import load_privacy_desired_canonical

        try:
            candidate = decode_privacy_policy_canonical(load_privacy_desired_canonical(input_path))
        except ConfigError as error:
            typer.echo(f"invalid_request: {error.reason_code}", err=True)
            return 2
        try:
            client = await build_service_client()
            try:
                from yoetz.cli.provider_status import machine_scope_request

                effective = await client.privacy_get_effective(machine_scope_request())
            finally:
                await client.close()
        except ControlError as error:
            return _control_failure(error)
        plain = _plain_json(effective)
        if type(plain) is not dict or "policy" not in plain:
            return _usage_failure()
        try:
            current = decode_privacy_policy_canonical(
                canonical_encode(cast(JsonValue, plain["policy"]))
            )
        except Exception:
            return _usage_failure()
        if current == candidate:
            _human_or_json({"outcome": "equivalent"}, json_output=json_output)
            return 0
        if is_privacy_tightening(current, candidate):
            _human_or_json(
                {
                    "next": "yoetz privacy tighten",
                    "note": "desired-state is a tighten; apply via the existing tighten gate",
                    "outcome": "tighten",
                },
                json_output=json_output,
            )
            return 0
        typer.echo(
            "widening_requires_decide: editing desired-state TOML cannot silently widen egress; "
            "run 'yoetz privacy propose' then 'yoetz privacy decide-policy'.",
            err=True,
        )
        _human_or_json(
            {
                "next": "yoetz privacy propose → yoetz privacy decide-policy",
                "outcome": "widen",
            },
            json_output=json_output,
        )
        return 2

    _finish(run_async(_run))


def _privacy_decision_command(kind: str) -> Callable[..., None]:
    def command(
        pending_id: Annotated[str, typer.Argument(help="Exact pending privacy decision ID.")],
        json_output: _JSON = False,
    ) -> None:
        module = importlib.import_module("yoetz.cli.privacy_control")
        operation = cast(Callable[[str], Awaitable[object]], getattr(module, f"decide_{kind}"))

        async def run_decision() -> object:
            if kind != "policy":
                return await operation(pending_id)
            passphrase = _auto_unlock_store().load()
            if passphrase is None:
                return await operation(pending_id)
            auto_operation = cast(
                Callable[[str, bytearray], Awaitable[object]],
                getattr(module, "decide_policy_with_local_reauthentication"),
            )
            return await auto_operation(pending_id, passphrase)

        _finish(run_async(lambda: _trusted_call(run_decision, json_output)))

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


@app.command("menu")
def menu_command() -> None:
    """Open the interactive Yoetz interface for setup, connections, privacy, and service."""

    if _open_tui(first_run=False):
        return
    module = importlib.import_module("yoetz.cli.menu")
    operation = cast(Callable[[], int], getattr(module, "run_menu"))
    _finish(operation())


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
    context: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", is_eager=True, help="Show the installed package version."),
    ] = False,
    set_provider: Annotated[
        bool,
        typer.Option(
            "--set",
            help="Set up the LLM model and API key through the secure local wizard.",
        ),
    ] = False,
    privacy: Annotated[
        bool,
        typer.Option(
            "--privacy",
            help="Review or change the privacy policy through the trusted local wizard.",
        ),
    ] = False,
    fireworks: Annotated[
        bool,
        typer.Option("--fireworks", help="Use the bundled Fireworks Responses profile."),
    ] = False,
    grok: Annotated[
        bool,
        typer.Option("--grok", help="Use the bundled Grok / xAI Chat Completions profile."),
    ] = False,
    provider_name: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help="Reviewed provider preset used with --set.",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Provider model identifier used with --set."),
    ] = None,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit(0)
    if privacy:
        if context.invoked_subcommand is not None:
            raise typer.BadParameter("--privacy cannot be combined with a subcommand")
        if set_provider or grok or fireworks or provider_name is not None or model is not None:
            raise typer.BadParameter("--privacy cannot be combined with provider setup options")
        _finish(run_async(_run_privacy_setup_command))
        return
    if set_provider:
        if context.invoked_subcommand is not None:
            raise typer.BadParameter("--set cannot be combined with a subcommand")
        if (fireworks or grok) and provider_name is not None:
            raise typer.BadParameter("provider shortcuts and --provider are mutually exclusive")
        if fireworks and grok:
            raise typer.BadParameter("--fireworks and --grok are mutually exclusive")
        operation = _setup_operation("run_provider_setup")
        arguments: dict[str, object] = {
            "fireworks": fireworks,
            "model": model,
        }
        if provider_name is not None:
            arguments["provider"] = provider_name
        if grok:
            arguments["grok"] = True
        _finish(run_async(lambda: operation(**arguments)))
        return
    if grok or fireworks or provider_name is not None or model is not None:
        raise typer.BadParameter("--fireworks, --grok, --provider, and --model require --set")
    if context.invoked_subcommand is not None:
        return
    # Bare invocation (ADR-017, amending ADR-013): a human at a real terminal gets
    # the full-screen interface, with first run folded into it as its opening
    # steps rather than a separate wizard. When the terminal UI is unavailable or
    # declined we fall back to the ADR-013 prompt-loop menu, and every non-TTY,
    # piped, CI, or `--help` invocation keeps the historical help text.
    module = importlib.import_module("yoetz.cli.setup")
    offer = cast(Callable[[], bool], getattr(module, "should_offer_first_run"))
    first_run = offer()
    if _open_tui(first_run=first_run):
        return
    if first_run:
        operation = _setup_operation("run_setup_wizard")
        _finish(
            run_async(
                lambda: operation(
                    non_interactive=False,
                    codex_path=None,
                    accept=False,
                    json_output=False,
                )
            )
        )
    menu_module = importlib.import_module("yoetz.cli.menu")
    available = cast(Callable[[], bool], getattr(menu_module, "menu_available"))
    if available():
        run_menu = cast(Callable[[], int], getattr(menu_module, "run_menu"))
        _finish(run_menu())
        return
    typer.echo(context.get_help())
    raise typer.Exit(0)


def _open_tui(*, first_run: bool) -> bool:
    """Open the full-screen interface when this really is a human terminal.

    Returns ``False`` — having emitted nothing — whenever the caller should keep
    the historical behaviour instead: automation, a pipe, an opt-out, or an
    installation whose optional rendering dependency is absent.
    """

    tui = importlib.import_module("yoetz.tui")
    available = cast(Callable[[], bool], getattr(tui, "tui_available"))
    supported = cast(Callable[[], bool], getattr(tui, "tui_supported"))
    if not available() or not supported():
        return False
    runner = cast(Callable[..., int], getattr(tui, "run_tui"))
    unavailable = cast(int, getattr(tui, "TUI_UNAVAILABLE"))
    code = runner(first_run=first_run)
    if code == unavailable:
        return False
    _finish(code)
    return True


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


@elevated_app.command("status")
def elevated_status(json_output: _JSON = True) -> None:
    """Show pending consent status plus the non-default operation catalog."""

    module = importlib.import_module("yoetz.cli.elevated")
    payload = cast(Callable[[], object], getattr(module, "status_elevated"))()
    _human_or_json(payload, json_output=json_output)


@elevated_app.command("catalog")
def elevated_catalog(json_output: _JSON = True) -> None:
    """List default-safe vs consent-required operations for agents."""

    module = importlib.import_module("yoetz.cli.elevated")
    payload = cast(Callable[[], object], getattr(module, "catalog_elevated"))()
    _human_or_json(payload, json_output=json_output)


@elevated_app.command("prepare")
def elevated_prepare(
    operation: Annotated[
        str,
        typer.Argument(help="Consent catalog operation name (see `catalog`)."),
    ],
    provider_id: Annotated[str | None, typer.Option("--provider-id")] = None,
    model_id: Annotated[str | None, typer.Option("--model-id")] = None,
    endpoint_profile_id: Annotated[str | None, typer.Option("--endpoint-profile-id")] = None,
    endpoint_profile_version: Annotated[
        str | None, typer.Option("--endpoint-profile-version")
    ] = None,
    purpose: Annotated[str | None, typer.Option("--purpose")] = None,
    scope_digest: Annotated[str | None, typer.Option("--scope-digest")] = None,
    purpose_digest: Annotated[str | None, typer.Option("--purpose-digest")] = None,
    target_digest: Annotated[
        str | None,
        typer.Option("--target-digest", help="Exact plan/preview digest when required."),
    ] = None,
    json_output: _JSON = True,
) -> None:
    """Create a pending consent challenge (no secrets)."""

    module = importlib.import_module("yoetz.cli.elevated")
    errors = importlib.import_module("yoetz.service.elevated_bootstrap")
    elevated_error = cast(type[Exception], getattr(errors, "ElevatedBootstrapError"))
    prepare = cast(Callable[..., object], getattr(module, "prepare_elevated"))
    binding: dict[str, str] | None = None
    if operation in {"provider_credential_set", "provider_credential_rotate"}:
        required = {
            "provider_id": provider_id,
            "model_id": model_id,
            "endpoint_profile_id": endpoint_profile_id,
            "endpoint_profile_version": endpoint_profile_version,
            "purpose": purpose,
            "scope_digest": scope_digest,
            "purpose_digest": purpose_digest,
        }
        if any(value is None or value == "" for value in required.values()):
            _finish(_usage_failure())
        binding = {key: cast(str, value) for key, value in required.items()}
    try:
        payload = prepare(operation, provider_binding=binding, target_digest=target_digest)
    except elevated_error as exc:
        _stderr(f"elevated_bootstrap: {getattr(exc, 'reason', 'failed')}")
        raise SystemExit(2) from None
    _human_or_json(payload, json_output=json_output)


@elevated_app.command("review")
def elevated_review(
    json_output: _JSON = True,
) -> None:
    """Review one pending action on a verified foreground console."""

    module = importlib.import_module("yoetz.cli.elevated")
    errors = importlib.import_module("yoetz.service.elevated_bootstrap")
    elevated_error = cast(type[Exception], getattr(errors, "ElevatedBootstrapError"))
    review = cast(Callable[[], Awaitable[object]], getattr(module, "review_elevated"))

    async def _run() -> int:
        try:
            payload = await review()
        except elevated_error as exc:
            _stderr(f"elevated_bootstrap: {getattr(exc, 'reason', 'failed')}")
            return 2
        _human_or_json(payload, json_output=json_output)
        return 0

    _finish(run_async(_run))
