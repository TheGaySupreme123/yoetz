"""Guided Codex-owned ChatGPT login for the exact subscription evaluator cell."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Final, Literal, cast

import typer

from yoetz.adapters.providers.codex_app_server import (
    CODEX_APP_SERVER_SCHEMA_SHA256,
    CODEX_EVALUATOR_CAPABILITY_CELL_SHA256,
    CODEX_EVALUATOR_CAPABILITY_PROFILE,
    CODEX_EVALUATOR_CONFIG_SHA256,
    CODEX_EVALUATOR_EVIDENCE_EXPIRES_AT,
    CODEX_EVALUATOR_RUNTIME_VERSION,
    CodexAppServerProfile,
    CodexLoginChallenge,
    CodexRuntimeStatus,
    codex_account_status,
    codex_login,
    codex_logout,
    prepare_codex_home,
)
from yoetz.config.load import load_config
from yoetz.config.models import ConfigError, ExternalRuntimeProfileConfig, YoetzConfig
from yoetz.config.paths import bundle_root, config_file_path
from yoetz.config.write import (
    cleared_external_runtime_config,
    codex_subscription_runtime,
    config_write_snapshot,
    external_runtime_binding_config,
    preflight_config_write,
    write_config_toml_if_unchanged,
)
from yoetz.protocol.canonical import JsonValue

__all__ = [
    "codex_subscription_preview",
    "codex_subscription_disconnect",
    "codex_subscription_rollback",
    "codex_subscription_setup",
    "codex_subscription_status",
    "default_codex_home",
    "prompt_codex_subscription_setup",
    "resolve_supported_codex_executable",
    "subscription_failure_reason",
]

_DARWIN_ARM64_EXECUTABLE_SHA256: Final = (
    "sha256:a14f9a907c12c8812878b70e6b7d65f81c39ed795513e46a55817d7428c0ca6b"
)
_DARWIN_ARM64_SOURCE_IDENTITY: Final = "openai-codex-npm-darwin-arm64-0.150.1"
_SUPPORTED_REASONING: Final = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})
_CLOSED_FAILURE_TOKEN: Final = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _bounded_config_operation[T](operation: Callable[[], T]) -> T:
    """Run one local config step; surface a ``ConfigError`` as its closed reason token.

    Every subscription surface (CLI commands, terminal menu, TUI runtime) already maps
    ``ValueError`` through :func:`subscription_failure_reason`; a raw ``ConfigError`` escaping
    any of them was masked as a generic ``internal_error`` exit (#520).
    """

    try:
        return operation()
    except ConfigError as error:
        raise ValueError(error.reason_code) from error


def subscription_failure_reason(error: BaseException) -> str:
    """Map subscription CLI failures to one closed token; never echo native OS text."""

    if isinstance(error, ConfigError):
        return error.reason_code
    if isinstance(error, FileNotFoundError):
        return "codex_runtime_not_found"
    if isinstance(error, TimeoutError):
        return "codex_subscription_timeout"
    if isinstance(error, OSError):
        return "codex_runtime_unavailable"
    if isinstance(error, ValueError):
        token = str(error)
        if _CLOSED_FAILURE_TOKEN.fullmatch(token) is not None:
            return token
    return "codex_subscription_failed"


def resolve_supported_codex_executable(selected: Path) -> tuple[Path, str, str]:
    """Resolve only the selected npm distribution to its exact native executable."""

    try:
        resolved = selected.expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError("codex_runtime_not_found") from error
    except OSError as error:
        raise ValueError("codex_runtime_unavailable") from error
    if resolved.name == "codex.js" and resolved.parent.name == "bin":
        package_root = resolved.parent.parent
        if sys.platform != "darwin" or platform.machine() != "arm64":
            raise ValueError("codex_runtime_platform_unsupported")
        try:
            resolved = (
                package_root
                / "node_modules"
                / "@openai"
                / "codex-darwin-arm64"
                / "vendor"
                / "aarch64-apple-darwin"
                / "bin"
                / "codex"
            ).resolve(strict=True)
        except FileNotFoundError as error:
            raise ValueError("codex_runtime_not_found") from error
        except OSError as error:
            raise ValueError("codex_runtime_unavailable") from error
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError("codex_runtime_executable_invalid")
    digest = _sha256_file(resolved)
    if (
        sys.platform != "darwin"
        or platform.machine() != "arm64"
        or digest != _DARWIN_ARM64_EXECUTABLE_SHA256
    ):
        raise ValueError("codex_runtime_capability_unsupported")
    return resolved, digest, _DARWIN_ARM64_SOURCE_IDENTITY


def default_codex_home() -> Path:
    config = _bounded_config_operation(lambda: load_config({}, os.environ, None))
    if config.external_runtime is not None:
        return Path(config.external_runtime.codex_home)
    return bundle_root(_data_dir=config.storage.data_dir) / "external-runtimes" / "codex-0.150.1"


def codex_subscription_preview(
    *, executable: Path, codex_home: Path, model: str, reasoning_effort: str
) -> dict[str, JsonValue]:
    """Resolve and validate the exact nonsecret cell without creating a home or logging in."""

    native, digest, source_identity = resolve_supported_codex_executable(executable)
    if not codex_home.is_absolute():
        raise ValueError("codex_home_invalid")
    if not model or reasoning_effort not in _SUPPORTED_REASONING:
        raise ValueError("codex_runtime_model_invalid")
    return {
        "schema": "yoetz.codex-subscription-preview/1",
        "credential_authority": "external_runtime_oauth",
        "runtime_version": CODEX_EVALUATOR_RUNTIME_VERSION,
        "runtime_source_identity": source_identity,
        "executable_path": str(native),
        "executable_sha256": digest,
        "app_server_schema_sha256": CODEX_APP_SERVER_SCHEMA_SHA256,
        "capability_cell_sha256": CODEX_EVALUATOR_CAPABILITY_CELL_SHA256,
        "capability_profile": CODEX_EVALUATOR_CAPABILITY_PROFILE,
        "capability_evidence_expires_at": CODEX_EVALUATOR_EVIDENCE_EXPIRES_AT,
        "isolated_config_sha256": CODEX_EVALUATOR_CONFIG_SHA256,
        "codex_home": str(codex_home),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "destination": "OpenAI through Codex-managed ChatGPT authentication",
        "data_use_posture": "unknown",
        "upstream_body_observability": "unavailable",
        "disconnect_command": "yoetz provider codex-subscription disconnect",
        "rollback_command": "yoetz provider codex-subscription rollback",
    }


def _base_config(path: Path | None) -> YoetzConfig:
    """Load the exact target config through the canonical loader; fail as one closed token.

    Strict validation of raw TOML rejected valid files whose ``storage.data_dir`` string the
    canonical loader converts to ``Path`` before model validation (#520). Loading through
    :func:`yoetz.config.load.load_config` keeps one rule: a file the service accepts is a file
    every subscription lifecycle command accepts. Sources stay file-only (no environment or
    override leaves) so a write base never persists ambient environment state.
    """

    target = _target_config_path(path)
    return _bounded_config_operation(lambda: load_config({}, {}, target))


def _config_snapshot(path: Path) -> tuple[YoetzConfig, bytes | None]:
    """Load the write base and exact preimage as one locked operation."""

    return _bounded_config_operation(lambda: config_write_snapshot(path))


def _target_config_path(path: Path | None) -> Path:
    if path is not None:
        return path
    selected = os.environ.get("YOETZ_CONFIG", "")
    return Path(selected) if selected else config_file_path()


def _binding(
    *, executable: Path, codex_home: Path, model: str, reasoning_effort: str
) -> ExternalRuntimeProfileConfig:
    """Validate and construct the exact nonsecret binding without creating any state."""

    preview = codex_subscription_preview(
        executable=executable,
        codex_home=codex_home,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    return codex_subscription_runtime(
        executable_path=cast(str, preview["executable_path"]),
        executable_sha256=cast(str, preview["executable_sha256"]),
        runtime_version=CODEX_EVALUATOR_RUNTIME_VERSION,
        source_identity=cast(str, preview["runtime_source_identity"]),
        app_server_schema_sha256=CODEX_APP_SERVER_SCHEMA_SHA256,
        capability_cell_sha256=CODEX_EVALUATOR_CAPABILITY_CELL_SHA256,
        isolated_config_sha256=CODEX_EVALUATOR_CONFIG_SHA256,
        capability_profile=CODEX_EVALUATOR_CAPABILITY_PROFILE,
        capability_evidence_expires_at=CODEX_EVALUATOR_EVIDENCE_EXPIRES_AT,
        codex_home=str(codex_home),
        model=model,
        reasoning_effort=reasoning_effort,
    )


def _profile(binding: ExternalRuntimeProfileConfig) -> CodexAppServerProfile:
    return CodexAppServerProfile.from_config(binding)


def _safe_status(
    binding: ExternalRuntimeProfileConfig, status: CodexRuntimeStatus
) -> dict[str, JsonValue]:
    return {
        "schema": "yoetz.codex-subscription-status/1",
        "credential_authority": binding.credential_authority,
        "runtime_version": binding.runtime_version,
        "runtime_source_identity": binding.source_identity,
        "executable_path": binding.executable_path,
        "executable_sha256": binding.executable_sha256,
        "app_server_schema_sha256": binding.app_server_schema_sha256,
        "capability_cell_sha256": binding.capability_cell_sha256,
        "capability_profile": binding.capability_profile,
        "capability_evidence_expires_at": binding.capability_evidence_expires_at,
        "isolated_config_sha256": binding.isolated_config_sha256,
        "codex_home": binding.codex_home,
        "model": binding.model,
        "reasoning_effort": binding.reasoning_effort,
        "runtime_ready": status.runtime_ready,
        "auth_mode": status.auth_mode,
        "plan_type": status.plan_type,
        "model_available": status.model_available,
        "process_cleanup": status.cleanup,
        "upstream_body_observability": "unavailable",
    }


async def codex_subscription_setup(
    *,
    executable: Path,
    codex_home: Path,
    model: str,
    reasoning_effort: str,
    login_mode: Literal["browser", "device_code"],
    open_browser: bool,
    switch_account: bool,
    config_path: Path | None = None,
) -> dict[str, JsonValue]:
    """Validate the binding and its persistence, login through Codex, then persist it.

    Every deterministic local requirement — the exact runtime cell, the canonical target
    configuration, and a render-validated, lock-probed write of the staged binding — is proven
    before the Codex login flow opens, so a configuration failure can never follow login side
    effects (#520).
    """

    binding = _binding(
        executable=executable,
        codex_home=codex_home,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    target = _target_config_path(config_path)
    base, expected_bytes = _config_snapshot(target)
    _bounded_config_operation(
        lambda: preflight_config_write(
            external_runtime_binding_config(binding, base=base),
            target,
            expected_bytes=expected_bytes,
        )
    )
    prepare_codex_home(codex_home)
    profile = _profile(binding)
    if switch_account:
        logout_status = await codex_logout(profile)
        if logout_status.cleanup == "failed":
            raise ValueError("codex_logout_unconfirmed")

    def present(challenge: CodexLoginChallenge) -> None:
        typer.echo("")
        typer.echo("Codex owns this ChatGPT sign-in. Yoetz does not receive the credentials.")
        typer.echo(f"Open: {challenge.url}")
        if challenge.user_code is not None:
            typer.echo(f"One-time code: {challenge.user_code}")
        if open_browser and challenge.mode == "browser":
            if not webbrowser.open(challenge.url):
                raise ValueError("codex_login_browser_unavailable")

    status = await codex_login(profile, mode=login_mode, present_challenge=present)
    if status.auth_mode != "chatgpt" or not status.model_available or status.cleanup == "failed":
        raise ValueError("codex_subscription_readiness_unproven")
    _bounded_config_operation(
        lambda: write_config_toml_if_unchanged(
            external_runtime_binding_config(binding, base=base),
            expected_bytes=expected_bytes,
            path=target,
        )
    )
    return _safe_status(binding, status)


async def prompt_codex_subscription_setup() -> dict[str, JsonValue]:
    """Run the shared terminal setup screen used by first-run and prompt-loop menus."""

    from yoetz.adapters.integrations.codex_discovery import discover_codex_binaries

    binaries = discover_codex_binaries()
    executable_default = "" if not binaries else binaries[0].executable_path
    executable = Path(
        typer.prompt(
            "Exact Codex executable",
            default=executable_default or None,
            show_default=bool(executable_default),
        )
    ).expanduser()
    codex_home = Path(
        typer.prompt("Dedicated evaluator CODEX_HOME", default=str(default_codex_home()))
    ).expanduser()
    model = typer.prompt("Exact model", default="gpt-5.6-sol").strip()
    reasoning_effort = typer.prompt("Reasoning effort", default="high").strip()
    login_choice = typer.prompt("Login method (browser/device_code)", default="browser").strip()
    if login_choice not in {"browser", "device_code"}:
        raise ValueError("codex_login_method_invalid")
    preview = codex_subscription_preview(
        executable=executable,
        codex_home=codex_home,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    typer.echo("")
    typer.echo("Codex with ChatGPT subscription")
    typer.echo(f"  runtime: {preview['executable_path']}")
    typer.echo(f"  executable_sha256: {preview['executable_sha256']}")
    typer.echo(f"  Codex version: {preview['runtime_version']}")
    typer.echo(f"  capability cell: {preview['capability_cell_sha256']}")
    typer.echo(f"  cell evidence expires: {preview['capability_evidence_expires_at']}")
    typer.echo(f"  dedicated CODEX_HOME: {preview['codex_home']}")
    typer.echo(f"  model/reasoning: {model} / {reasoning_effort}")
    typer.echo("  destination: OpenAI through Codex-managed ChatGPT authentication")
    typer.echo("  data-use posture: unknown; your ChatGPT plan and terms apply")
    typer.echo("  Yoetz receives no OAuth credential and cannot observe the upstream body")
    typer.echo(f"  disconnect: {preview['disconnect_command']}")
    typer.echo(f"  rollback only: {preview['rollback_command']}")
    if not typer.confirm("Continue to Codex sign-in?", default=False):
        raise ValueError("cancelled")
    switch_account = typer.confirm(
        "Log out the dedicated home first (switch ChatGPT account)?",
        default=False,
    )
    return await codex_subscription_setup(
        executable=executable,
        codex_home=codex_home,
        model=model,
        reasoning_effort=reasoning_effort,
        login_mode=cast(Literal["browser", "device_code"], login_choice),
        open_browser=login_choice == "browser",
        switch_account=switch_account,
    )


async def codex_subscription_status(*, config_path: Path | None = None) -> dict[str, JsonValue]:
    config = _base_config(config_path)
    binding = config.external_runtime
    if binding is None:
        raise ValueError("codex_subscription_not_configured")
    return _safe_status(binding, await codex_account_status(_profile(binding)))


async def codex_subscription_disconnect(*, config_path: Path | None = None) -> dict[str, JsonValue]:
    """Prove the cleared config persists, confirm Codex logout, then remove only the binding.

    The removal write is render-validated and lock-probed before Codex logs the dedicated home
    out, so a local persistence failure cannot strand a logged-out home behind a binding that
    still claims to be active.
    """

    target = _target_config_path(config_path)
    config, expected_bytes = _config_snapshot(target)
    binding = config.external_runtime
    if binding is None:
        raise ValueError("codex_subscription_not_configured")
    _bounded_config_operation(
        lambda: preflight_config_write(
            cleared_external_runtime_config(config),
            target,
            expected_bytes=expected_bytes,
        )
    )
    status = await codex_logout(_profile(binding))
    if status.cleanup == "failed":
        raise ValueError("codex_logout_unconfirmed")
    written = _bounded_config_operation(
        lambda: write_config_toml_if_unchanged(
            cleared_external_runtime_config(config),
            expected_bytes=expected_bytes,
            path=target,
        )
    )
    result = _safe_status(binding, status)
    result["binding_removed"] = True
    result["config_path"] = str(written)
    return result


def codex_subscription_rollback(*, config_path: Path | None = None) -> dict[str, JsonValue]:
    """Remove only the nonsecret binding; preserve the dedicated home and Codex install."""

    target = _target_config_path(config_path)
    config, expected_bytes = _config_snapshot(target)
    home = None if config.external_runtime is None else config.external_runtime.codex_home
    written = _bounded_config_operation(
        lambda: write_config_toml_if_unchanged(
            cleared_external_runtime_config(config),
            expected_bytes=expected_bytes,
            path=target,
        )
    )
    return {
        "schema": "yoetz.codex-subscription-rollback/1",
        "binding_removed": config.external_runtime is not None,
        "config_path": str(written),
        "codex_home_preserved": home,
        "codex_installation_preserved": True,
    }
