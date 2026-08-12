"""Explicit owner decisions for cached recommended-default advisories."""

from __future__ import annotations

import importlib
import json
import os
import shlex
from pathlib import Path
from typing import Annotated, Final, cast

import anyio
import typer

from yoetz.adapters.integrations.codex_session_stream import resolve_codex_home
from yoetz.application.package_update import PACKAGE_UPDATE_UPGRADE_COMMAND
from yoetz.application.recommendations import (
    RecommendationContext,
    RecommendationState,
    RecommendationStoreError,
    decline_cached_recommendation,
    evaluate_recommendation_context,
    recommendation_by_id,
    record_recommendation_decision,
    refresh_pending,
)
from yoetz.config.load import load_config
from yoetz.config.models import ConfigError, YoetzConfig
from yoetz.config.paths import config_file_path
from yoetz.config.write import write_config_toml_if_unchanged
from yoetz.ports.integrations import IntegrationError, IntegrationScope, IntegrationTarget

__all__ = ["recommend_app"]

_JSON = Annotated[bool, typer.Option("--json", help="Emit structural JSON.")]
_ACTIVATION_MODULE: Final = "yoetz.adapters.integrations.codex_marketplace"

recommend_app = typer.Typer(
    help="Review and explicitly decide recommended defaults.", no_args_is_help=True
)


class _RecommendationCliError(Exception):
    reason_code: str

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _load_current_config(path: Path | None = None) -> YoetzConfig:
    selected = config_file_path() if path is None else path
    if not selected.is_file():
        return YoetzConfig()
    return load_config({}, {}, selected)


def _integration_target(path: Path) -> IntegrationTarget:
    return IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, os.fspath(path.resolve(strict=True)))


def _selected_codex_home(explicit: Path | str | None) -> Path:
    isolated = any(
        type(os.environ.get(name)) is str and bool(os.environ.get(name, "").strip())
        for name in ("CODEX_HOME", "CODEX_TESTING_HOME")
    )
    if explicit is None and not isolated:
        raise _RecommendationCliError("activation_home_ambiguous")
    return resolve_codex_home(explicit).resolve(strict=False)


def _activation_state(
    target: Path,
    *,
    executable_path: Path | str | None = None,
    codex_home: Path | str | None = None,
) -> str | None:
    if executable_path is None:
        return None
    try:
        module = importlib.import_module(_ACTIVATION_MODULE)
        selected_home = _selected_codex_home(codex_home)
        inspection = module.inspect_activation(
            _integration_target(target),
            executable_path=os.fspath(executable_path),
            codex_home=selected_home,
        )
    except (
        _RecommendationCliError,
        AttributeError,
        ImportError,
        IntegrationError,
        OSError,
        TypeError,
        ValueError,
    ):
        return None
    state = getattr(inspection, "state", None)
    value = getattr(state, "value", state)
    return value if type(value) is str else None


async def _current_context(
    *,
    config_path: Path | None = None,
    activation_target: Path | None = None,
    codex_path: Path | str | None = None,
    codex_home: Path | str | None = None,
) -> RecommendationContext:
    config = _load_current_config(config_path)
    target = Path.cwd() if activation_target is None else activation_target
    # Phase 2 supplies the durable privacy-policy posture. Until then this remains deliberately
    # no-egress and cannot manufacture package-update advice from an unauthorized network call.
    return await evaluate_recommendation_context(
        observation_enabled=config.observation.enabled,
        codex_activation_state=_activation_state(
            target, executable_path=codex_path, codex_home=codex_home
        ),
        allow_network=False,
    )


def _state_payload(state: RecommendationState) -> dict[str, object]:
    return {
        "schema": state.schema,
        "last_evaluated_version": state.last_evaluated_version,
        "pending": [
            {
                "id": item.id,
                "kind": item.kind,
                "summary": item.summary,
                "title": item.title,
            }
            for recommendation_id in state.pending
            if (item := recommendation_by_id(recommendation_id)) is not None
        ],
        "decisions": {
            key: {
                "decision": row.decision,
                "decided_at": row.decided_at.isoformat(),
                "version": row.version,
            }
            for key, row in sorted(state.decisions.items())
        },
    }


def _render_state(state: RecommendationState, *, json_output: bool) -> None:
    payload = _state_payload(state)
    if json_output:
        typer.echo(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        return
    if not state.pending:
        typer.echo("No recommended changes are pending.")
        return
    typer.echo("Recommended changes awaiting your decision:")
    for recommendation_id in state.pending:
        item = recommendation_by_id(recommendation_id)
        if item is None:
            continue
        typer.echo(f"  {item.id}: {item.title}")
        typer.echo(f"    {item.summary}")
        typer.echo(
            f"    Accept: yoetz recommend accept {item.id}  |  "
            f"Decline: yoetz recommend decline {item.id}"
        )


async def _refresh_for_cli(
    *, codex_path: Path | None = None, codex_home: Path | None = None
) -> RecommendationState:
    async def context() -> RecommendationContext:
        return await _current_context(codex_path=codex_path, codex_home=codex_home)

    return await refresh_pending(context_factory=context)


@recommend_app.command("list")
def recommend_list(
    json_output: _JSON = False,
    codex_path: Annotated[
        Path | None,
        typer.Option(
            "--codex-path",
            help="Exact Codex executable to inspect; activation status stays unknown without it.",
        ),
    ] = None,
    codex_home: Annotated[
        Path | None,
        typer.Option("--codex-home", help="Exact Codex home expected from that executable."),
    ] = None,
) -> None:
    """Refresh at this heavy touchpoint and list cached pending recommendations."""

    try:
        state = anyio.run(lambda: _refresh_for_cli(codex_path=codex_path, codex_home=codex_home))
    except RecommendationStoreError as exc:
        typer.echo(f"recommendation_error: {exc.reason_code}", err=True)
        raise typer.Exit(2) from exc
    except ConfigError as exc:
        typer.echo(f"invalid_request: {exc.reason_code}", err=True)
        raise typer.Exit(2) from exc
    _render_state(state, json_output=json_output)


def _apply_observation_enabled(*, path: Path | None) -> str:
    target = config_file_path() if path is None else path
    try:
        expected = target.read_bytes()
    except FileNotFoundError:
        expected = None
    except OSError as exc:
        raise _RecommendationCliError("config_read_failed") from exc
    current = _load_current_config(target)
    if current.observation.enabled:
        return "already enabled in [observation]"
    updated = current.model_copy(
        update={"observation": current.observation.model_copy(update={"enabled": True})}
    )
    typer.echo("Exact config change:")
    typer.echo("  [observation] enabled = false -> true")
    try:
        written = write_config_toml_if_unchanged(updated, expected_bytes=expected, path=target)
    except ConfigError as exc:
        if exc.reason_code == "config_preimage_mismatch":
            raise _RecommendationCliError("config_preview_stale") from exc
        raise
    return (
        f"wrote [observation] enabled = true to {written}; "
        "restart the Yoetz service so the running composition loads this setting"
    )


def _preview_text(preview: object) -> tuple[str, str]:
    digest = getattr(preview, "preview_digest", None)
    if type(digest) is not str or not digest:
        raise _RecommendationCliError("activation_preview_invalid")
    details: list[str] = []
    for name in (
        "marketplace_json",
        "marketplace_bytes",
        "marketplace_payload",
        "config_toml_block",
        "toml_block",
    ):
        value = getattr(preview, name, None)
        if isinstance(value, bytes):
            try:
                details.append(value.decode("utf-8"))
            except UnicodeError as exc:
                raise _RecommendationCliError("activation_preview_invalid") from exc
        elif type(value) is str and value:
            details.append(value)
    if not details:
        details.append(repr(preview))
    return digest, "\n".join(details)


def _command_text(executable: Path, command: object) -> str:
    if type(command) is not tuple:
        raise _RecommendationCliError("activation_preview_invalid")
    parts: list[str] = []
    for item in cast(tuple[object, ...], command):
        if type(item) is not str:
            raise _RecommendationCliError("activation_preview_invalid")
        parts.append(item)
    return shlex.join((os.fspath(executable), *parts))


def _environment_lines(environment: object) -> tuple[str, ...]:
    if type(environment) is not tuple:
        raise _RecommendationCliError("activation_preview_invalid")
    lines: list[str] = []
    names: set[str] = set()
    for raw in cast(tuple[object, ...], environment):
        if type(raw) is not tuple:
            raise _RecommendationCliError("activation_preview_invalid")
        row = cast(tuple[object, ...], raw)
        if len(row) != 2:
            raise _RecommendationCliError("activation_preview_invalid")
        name, value = row
        if type(name) is not str or type(value) is not str or name in names:
            raise _RecommendationCliError("activation_preview_invalid")
        names.add(name)
        lines.append(f"{name}={value}")
    if names != {"CODEX_HOME", "CODEX_TESTING_HOME"}:
        raise _RecommendationCliError("activation_preview_invalid")
    return tuple(lines)


def _apply_codex_activation(*, target: Path, executable_path: Path, codex_home: Path) -> str:
    try:
        module = importlib.import_module(_ACTIVATION_MODULE)
        integration_target = _integration_target(target)
        selected_home = _selected_codex_home(codex_home)
        preview = module.preview_activation(
            integration_target,
            executable_path=os.fspath(executable_path),
            codex_home=selected_home,
        )
        digest, exact_change = _preview_text(preview)
        preview_home = getattr(preview, "codex_home", None)
        if not isinstance(preview_home, Path) or preview_home != selected_home:
            raise _RecommendationCliError("activation_preview_invalid")
        preview_executable = getattr(preview, "executable_path", None)
        executable_digest = getattr(preview, "executable_digest", None)
        codex_version = getattr(preview, "codex_version", None)
        install_path = getattr(preview, "plugin_install_path", None)
        source_digest = getattr(preview, "plugin_source_digest", None)
        install_digest = getattr(preview, "plugin_install_digest", None)
        marketplace_preimage = getattr(preview, "marketplace_preimage_digest", None)
        config_preimage = getattr(preview, "config_preimage_digest", None)
        cache_mutation = getattr(preview, "cache_mutation_planned", None)
        probe_environment = getattr(preview, "probe_environment", None)
        activation_environment = _environment_lines(
            getattr(preview, "activation_environment", None)
        )
        inspection = getattr(preview, "inspection", None)
        inventory_verified = getattr(inspection, "inventory_verified", None)
        if (
            not isinstance(preview_executable, Path)
            or type(executable_digest) is not str
            or not executable_digest
            or type(codex_version) is not str
            or not codex_version
            or not isinstance(install_path, Path)
            or type(source_digest) is not str
            or not source_digest
            or type(install_digest) is not str
            or not install_digest
            or type(marketplace_preimage) is not str
            or not marketplace_preimage
            or type(config_preimage) is not str
            or not config_preimage
            or type(cache_mutation) is not bool
            or probe_environment != "temporary_owner_private_home"
            or type(inventory_verified) is not bool
        ):
            raise _RecommendationCliError("activation_preview_invalid")
        probe = _command_text(preview_executable, getattr(preview, "probe_command", None))
        inventory = _command_text(preview_executable, getattr(preview, "inventory_command", None))
        install = _command_text(preview_executable, getattr(preview, "install_command", None))
        typer.echo(f"Codex executable: {preview_executable}")
        typer.echo(f"Codex executable digest: {executable_digest}")
        typer.echo(f"Codex version: {codex_version}")
        typer.echo(f"Codex home: {preview_home}")
        typer.echo(f"Codex config target: {preview_home / 'config.toml'}")
        typer.echo(f"Exact plugin install target: {install_path}")
        typer.echo(f"Plugin source digest: {source_digest}")
        typer.echo(f"Plugin install digest: {install_digest}")
        typer.echo(f"Marketplace preimage digest: {marketplace_preimage}")
        typer.echo(f"Config preimage digest: {config_preimage}")
        typer.echo(f"Plugin cache mutation planned: {'yes' if cache_mutation else 'no'}")
        typer.echo(f"Probe argv: {probe}")
        typer.echo(f"Probe environment: {probe_environment}")
        typer.echo(
            "Canonical plugin inventory verified before consent: "
            f"{'yes' if inventory_verified else 'no (verification occurs during approved apply)'}"
        )
        typer.echo(f"Inventory argv: {inventory}")
        typer.echo(f"Install argv: {install}")
        typer.echo("Inventory/install environment overrides:")
        for line in activation_environment:
            typer.echo(f"  {line}")
        typer.echo("Exact activation change:")
        typer.echo(exact_change.rstrip())
        typer.echo(f"preview digest: {digest}")
        if not typer.confirm("Apply exactly this digest-bound activation change?", default=False):
            raise _RecommendationCliError("activation_not_approved")
        module.apply_activation(
            integration_target,
            approved_digest=digest,
            executable_path=os.fspath(executable_path),
            codex_home=selected_home,
        )
    except _RecommendationCliError:
        raise
    except (
        AttributeError,
        ImportError,
        IntegrationError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        raise _RecommendationCliError("activation_unavailable") from exc
    return (
        f"applied exact activation preview {digest}; "
        "start a fresh Codex process/session to load the plugin and hooks"
    )


def _apply_recommendation(
    recommendation_id: str,
    *,
    codex_path: Path | None = None,
    codex_home: Path | None = None,
) -> str:
    if recommendation_id == "observation-enabled":
        return _apply_observation_enabled(path=None)
    if recommendation_id == "codex-plugin-activation":
        if codex_path is None:
            raise _RecommendationCliError("activation_codex_path_required")
        if codex_home is None:
            raise _RecommendationCliError("activation_codex_home_required")
        return _apply_codex_activation(
            target=Path.cwd(), executable_path=codex_path, codex_home=codex_home
        )
    if recommendation_id == "package-update":
        return f"run: {PACKAGE_UPDATE_UPGRADE_COMMAND}"
    raise _RecommendationCliError("recommendation_unknown")


@recommend_app.command("accept")
def recommend_accept(
    recommendation_id: Annotated[str, typer.Argument(help="Exact recommendation id.")],
    codex_path: Annotated[
        Path | None,
        typer.Option(
            "--codex-path",
            help="Exact Codex executable whose digest, version, home, and plugin commands are approved.",
        ),
    ] = None,
    codex_home: Annotated[
        Path | None,
        typer.Option(
            "--codex-home",
            help="Exact Codex home to preview and update; otherwise the isolated runtime environment is resolved.",
        ),
    ] = None,
) -> None:
    """Explicitly approve and apply one currently recommended action."""

    if recommendation_by_id(recommendation_id) is None:
        typer.echo("invalid_request: recommendation_unknown", err=True)
        raise typer.Exit(2)
    if recommendation_id == "codex-plugin-activation" and codex_path is None:
        typer.echo("recommendation_error: activation_codex_path_required", err=True)
        raise typer.Exit(2)
    if recommendation_id == "codex-plugin-activation" and codex_home is None:
        typer.echo("recommendation_error: activation_codex_home_required", err=True)
        raise typer.Exit(2)
    try:

        async def refresh_exact() -> RecommendationState:
            async def context() -> RecommendationContext:
                return await _current_context(codex_path=codex_path, codex_home=codex_home)

            return await refresh_pending(context_factory=context, force=True)

        current = anyio.run(refresh_exact)
        if recommendation_id not in current.pending:
            raise _RecommendationCliError("recommendation_not_pending")
        # The command itself is the approval. Re-preview at this point binds activation to fresh
        # bytes; activation additionally confirms that displayed digest before it mutates trust.
        outcome = _apply_recommendation(
            recommendation_id, codex_path=codex_path, codex_home=codex_home
        )
        record_recommendation_decision(recommendation_id, "accepted")
    except RecommendationStoreError as exc:
        typer.echo(f"recommendation_error: {exc.reason_code}", err=True)
        raise typer.Exit(2) from exc
    except ConfigError as exc:
        typer.echo(f"invalid_request: {exc.reason_code}", err=True)
        raise typer.Exit(2) from exc
    except _RecommendationCliError as exc:
        typer.echo(f"recommendation_error: {exc.reason_code}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"accepted {recommendation_id}: {outcome}")


@recommend_app.command("decline")
def recommend_decline(
    recommendation_id: Annotated[str, typer.Argument(help="Exact recommendation id.")],
    codex_path: Annotated[
        Path | None,
        typer.Option(
            "--codex-path",
            help="Accepted for compatibility; decline never inspects a Codex executable.",
        ),
    ] = None,
    codex_home: Annotated[
        Path | None,
        typer.Option(
            "--codex-home",
            help="Accepted for compatibility; decline never touches a Codex home.",
        ),
    ] = None,
) -> None:
    """Remember an explicit decline; this recommendation will not be shown again."""

    # Decline consumes no Codex authority, so these selectors are deliberately inert; the
    # wrapper command in cli/app.py still forwards them for invocation compatibility.
    del codex_path, codex_home
    if recommendation_by_id(recommendation_id) is None:
        typer.echo("invalid_request: recommendation_unknown", err=True)
        raise typer.Exit(2)
    # Decline is a pure durable memory write against the already-cached pending advice.
    # It must never require per-kind authority (an exact Codex path/home, network posture)
    # or force a fresh evaluation: the hook-advertised `yoetz recommend decline <id>` line
    # has to be honorable exactly as printed.
    try:
        decline_cached_recommendation(recommendation_id)
    except RecommendationStoreError as exc:
        typer.echo(f"recommendation_error: {exc.reason_code}", err=True)
        raise typer.Exit(2) from exc
    except ValueError as exc:
        typer.echo(f"recommendation_error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"declined {recommendation_id}; this recommendation will not be shown again")
