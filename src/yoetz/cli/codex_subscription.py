"""Guided Codex-owned ChatGPT login for the exact subscription evaluator cell."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import stat
import sys
import webbrowser
from collections.abc import Callable, Mapping
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
from yoetz.protocol.canonical import JsonValue, strict_json_parse

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
_CODEX_PACKAGE_NAME: Final = "@openai/codex"
_CODEX_NATIVE_PACKAGE_DIRECTORY: Final = "codex-darwin-arm64"
_CODEX_NATIVE_PACKAGE_VERSION: Final = f"{CODEX_EVALUATOR_RUNTIME_VERSION}-darwin-arm64"
_CODEX_NATIVE_PACKAGE_SPEC: Final = f"npm:{_CODEX_PACKAGE_NAME}@{_CODEX_NATIVE_PACKAGE_VERSION}"
_CODEX_NATIVE_EXECUTABLE_RELATIVE: Final = Path("vendor/aarch64-apple-darwin/bin/codex")
_CODEX_PACKAGE_JSON_MAX_BYTES: Final = 64 * 1024
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


def _runtime_path(path: Path, *, missing_token: str) -> Path:
    """Resolve one path selected by the caller without widening its search scope."""

    try:
        return path.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(missing_token) from error
    except (OSError, RuntimeError) as error:
        raise ValueError("codex_runtime_unavailable") from error


def _package_json(package_root: Path) -> Mapping[str, JsonValue]:
    """Read one bounded npm manifest and convert all parse failures to safe tokens."""

    path = package_root / "package.json"
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as error:
        raise ValueError("codex_runtime_not_found") from error
    except OSError as error:
        raise ValueError("codex_runtime_capability_unsupported") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > _CODEX_PACKAGE_JSON_MAX_BYTES
        ):
            raise ValueError("codex_runtime_capability_unsupported")
        chunks: list[bytes] = []
        remaining = _CODEX_PACKAGE_JSON_MAX_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except OSError as error:
        raise ValueError("codex_runtime_capability_unsupported") from error
    finally:
        os.close(descriptor)
    if not raw or len(raw) > _CODEX_PACKAGE_JSON_MAX_BYTES:
        raise ValueError("codex_runtime_capability_unsupported")
    try:
        document = strict_json_parse(raw)
    except (ValueError, RecursionError) as error:
        raise ValueError("codex_runtime_capability_unsupported") from error
    if not isinstance(document, Mapping):
        raise ValueError("codex_runtime_capability_unsupported")
    return cast(Mapping[str, JsonValue], document)


def _validate_codex_wrapper_manifest(document: Mapping[str, JsonValue]) -> None:
    """Validate the exact wrapper metadata for the closed evaluator cell."""

    if document.get("name") != _CODEX_PACKAGE_NAME:
        raise ValueError("codex_runtime_capability_unsupported")
    if document.get("version") != CODEX_EVALUATOR_RUNTIME_VERSION:
        raise ValueError("codex_runtime_capability_unsupported")
    binary_map = document.get("bin")
    if not isinstance(binary_map, Mapping) or binary_map.get("codex") != "bin/codex.js":
        raise ValueError("codex_runtime_capability_unsupported")
    optional_dependencies = document.get("optionalDependencies")
    if (
        not isinstance(optional_dependencies, Mapping)
        or optional_dependencies.get(f"@openai/{_CODEX_NATIVE_PACKAGE_DIRECTORY}")
        != _CODEX_NATIVE_PACKAGE_SPEC
    ):
        raise ValueError("codex_runtime_capability_unsupported")


def _validate_codex_native_manifest(document: Mapping[str, JsonValue]) -> None:
    """Validate the exact native package identity and macOS arm64 selectors."""

    if document.get("name") != _CODEX_PACKAGE_NAME:
        raise ValueError("codex_runtime_capability_unsupported")
    if document.get("version") != _CODEX_NATIVE_PACKAGE_VERSION:
        raise ValueError("codex_runtime_capability_unsupported")
    if document.get("os") != ["darwin"] or document.get("cpu") != ["arm64"]:
        raise ValueError("codex_runtime_capability_unsupported")


def _package_candidate_present(path: Path) -> bool:
    """Return presence without following an absent/broken candidate into a parent search."""

    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ValueError("codex_runtime_unavailable") from error


def _reject_symlinked_package_root(path: Path) -> None:
    """Keep package identity bound to the selected layout, including same-parent aliases."""

    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            raise ValueError("codex_runtime_capability_unsupported")
    except FileNotFoundError as error:
        raise ValueError("codex_runtime_not_found") from error
    except OSError as error:
        raise ValueError("codex_runtime_unavailable") from error


def _resolve_codex_package_layout(selected: Path) -> Path:
    """Resolve a selected wrapper to its exact nested or npm-prefix native executable.

    The only allowed package roots are the optional dependency nested below the selected wrapper
    and its direct hoisted sibling. The nested candidate wins deterministically; a present but
    malformed nested package is terminal and never causes an unbounded parent/PATH search.
    """

    wrapper = _runtime_path(selected.expanduser(), missing_token="codex_runtime_not_found")
    if wrapper.name != "codex.js" or wrapper.parent.name != "bin":
        return wrapper
    package_root = wrapper.parent.parent
    if package_root == Path(package_root.anchor):
        raise ValueError("codex_runtime_capability_unsupported")
    _validate_codex_wrapper_manifest(_package_json(package_root))

    nested = package_root / "node_modules" / "@openai" / _CODEX_NATIVE_PACKAGE_DIRECTORY
    hoisted = package_root.parent / _CODEX_NATIVE_PACKAGE_DIRECTORY
    if _package_candidate_present(nested):
        _reject_symlinked_package_root(nested)
        expected_parent = nested.parent
        allowed_parent = _runtime_path(nested.parent, missing_token="codex_runtime_not_found")
        native_root = _runtime_path(nested, missing_token="codex_runtime_not_found")
    elif _package_candidate_present(hoisted):
        _reject_symlinked_package_root(hoisted)
        expected_parent = hoisted.parent
        allowed_parent = _runtime_path(hoisted.parent, missing_token="codex_runtime_not_found")
        native_root = _runtime_path(hoisted, missing_token="codex_runtime_not_found")
    else:
        raise ValueError("codex_runtime_not_found")
    if (
        allowed_parent != expected_parent
        or native_root.parent != allowed_parent
        or not native_root.is_dir()
    ):
        raise ValueError("codex_runtime_capability_unsupported")
    _validate_codex_native_manifest(_package_json(native_root))
    native = _runtime_path(
        native_root / _CODEX_NATIVE_EXECUTABLE_RELATIVE,
        missing_token="codex_runtime_not_found",
    )
    try:
        native.relative_to(native_root)
    except ValueError as error:
        raise ValueError("codex_runtime_capability_unsupported") from error
    return native


def resolve_supported_codex_executable(selected: Path) -> tuple[Path, str, str]:
    """Resolve only the selected npm distribution to its exact native executable."""

    resolved = _runtime_path(selected.expanduser(), missing_token="codex_runtime_not_found")
    if resolved.name == "codex.js" and resolved.parent.name == "bin":
        if sys.platform != "darwin" or platform.machine() != "arm64":
            raise ValueError("codex_runtime_platform_unsupported")
        resolved = _resolve_codex_package_layout(resolved)
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


def _already_ready(status: CodexRuntimeStatus) -> bool:
    """Codex proved a ChatGPT login and the exact model/reasoning cell for this home."""

    return status.runtime_ready and status.auth_mode == "chatgpt" and status.model_available


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
    as_fallback: bool = False,
) -> dict[str, JsonValue]:
    """Validate the binding and its persistence, prove or obtain Codex login, then persist it.

    ``as_fallback`` keeps the already-bound API provider as the primary and declares this
    runtime as its fallback (issue #582); it never replaces that binding.

    Every deterministic local requirement — the exact runtime cell, the canonical target
    configuration, and a render-validated, lock-probed write of the staged binding — is proven
    before any Codex process starts, so a configuration failure can never follow login side
    effects (#520).

    Login is Codex-owned state that lives once per dedicated home. Unless the caller asked to
    switch accounts, the same structural probe ``status`` uses (app-server ``account/read`` with
    ``refreshToken: false`` and ``model/list``) runs first; a home Codex already reports as
    signed in with the exact model cell available is bound without a new ``account/login/start``
    challenge (#534). Yoetz still never reads or copies ``auth.json``: readiness is only ever what
    Codex itself answers. ``switch_account`` remains the explicit override that logs the home out
    and signs in again.
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
            external_runtime_binding_config(binding, base=base, as_fallback=as_fallback),
            target,
            expected_bytes=expected_bytes,
        )
    )
    prepare_codex_home(codex_home)
    profile = _profile(binding)
    login_reused = False
    if switch_account:
        logout_status = await codex_logout(profile)
        if logout_status.cleanup == "failed":
            raise ValueError("codex_logout_unconfirmed")
        status: CodexRuntimeStatus | None = None
    else:
        status = await codex_account_status(profile)
        if status.cleanup == "failed":
            raise ValueError("codex_subscription_readiness_unproven")
        if _already_ready(status):
            login_reused = True
            typer.echo("")
            typer.echo(
                "Codex reports this dedicated home is already signed in with the exact model "
                "available; reusing it without a new sign-in."
            )
        else:
            status = None

    def present(challenge: CodexLoginChallenge) -> None:
        typer.echo("")
        typer.echo("Codex owns this ChatGPT sign-in. Yoetz does not receive the credentials.")
        typer.echo(f"Open: {challenge.url}")
        if challenge.user_code is not None:
            typer.echo(f"One-time code: {challenge.user_code}")
        if open_browser and challenge.mode == "browser":
            if not webbrowser.open(challenge.url):
                raise ValueError("codex_login_browser_unavailable")

    if status is None:
        status = await codex_login(profile, mode=login_mode, present_challenge=present)
        if not _already_ready(status) or status.cleanup == "failed":
            raise ValueError("codex_subscription_readiness_unproven")
    _bounded_config_operation(
        lambda: write_config_toml_if_unchanged(
            external_runtime_binding_config(binding, base=base, as_fallback=as_fallback),
            expected_bytes=expected_bytes,
            path=target,
        )
    )
    result = _safe_status(binding, status)
    result["login_reused"] = login_reused
    result["endpoint_role"] = "fallback" if as_fallback else "primary"
    return result


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
    model = typer.prompt("Exact model", default="gpt-5.6-luna").strip()
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
    typer.echo("  a dedicated home Codex already reports signed in is reused without a new sign-in")
    if not typer.confirm("Continue to Codex sign-in?", default=False):
        raise ValueError("cancelled")
    switch_account = typer.confirm(
        "Log out the dedicated home first and sign in again (switch ChatGPT account)?",
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
