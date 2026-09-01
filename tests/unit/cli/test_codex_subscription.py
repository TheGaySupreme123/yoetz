"""Codex subscription setup retains only exact nonsecret runtime binding state."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yoetz.adapters.providers.codex_app_server import (
    CODEX_APP_SERVER_SCHEMA_SHA256,
    CODEX_EVALUATOR_CAPABILITY_CELL_SHA256,
    CODEX_EVALUATOR_CAPABILITY_PROFILE,
    CODEX_EVALUATOR_CONFIG_SHA256,
    CODEX_EVALUATOR_EVIDENCE_EXPIRES_AT,
    CodexRuntimeStatus,
)
from yoetz.cli import codex_subscription as module
from yoetz.config.models import ConfigError, ExternalRuntimeProfileConfig, YoetzConfig
from yoetz.config.write import codex_subscription_runtime, render_config_toml


@pytest.fixture(autouse=True)
def _ambient_config_environment(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Resolve the default evaluator home from an empty configuration, not the ambient one.

    ``default_codex_home`` reads the process environment through the strict loader, which
    refuses every unknown ``YOETZ_``-prefixed variable. The CI unit job exports
    ``YOETZ_DENY_NETWORK=1`` for the whole job and a developer machine carries its own
    ``config.toml``; neither is configuration resolution under test here, so every case in this
    module starts from no ``YOETZ_`` variables and a config path that does not exist.
    """

    for name in tuple(os.environ):
        if name.startswith("YOETZ_"):
            monkeypatch.delenv(name)
    absent = tmp_path_factory.mktemp("config") / "absent.toml"
    monkeypatch.setenv("YOETZ_CONFIG", str(absent))


def _binding(executable: Path, home: Path):
    return codex_subscription_runtime(
        executable_path=str(executable),
        executable_sha256="sha256:" + "a" * 64,
        runtime_version="0.150.1",
        source_identity="openai-codex-npm-darwin-arm64-0.150.1",
        app_server_schema_sha256=CODEX_APP_SERVER_SCHEMA_SHA256,
        capability_cell_sha256=CODEX_EVALUATOR_CAPABILITY_CELL_SHA256,
        isolated_config_sha256=CODEX_EVALUATOR_CONFIG_SHA256,
        capability_profile=CODEX_EVALUATOR_CAPABILITY_PROFILE,
        capability_evidence_expires_at=CODEX_EVALUATOR_EVIDENCE_EXPIRES_AT,
        codex_home=str(home),
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )


def test_preview_resolves_exact_cell_without_creating_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "codex"
    home = tmp_path / "dedicated-home"

    def resolve(selected: Path) -> tuple[Path, str, str]:
        return (
            selected,
            "sha256:" + "a" * 64,
            "openai-codex-npm-darwin-arm64-0.150.1",
        )

    monkeypatch.setattr(
        module,
        "resolve_supported_codex_executable",
        resolve,
    )

    preview = module.codex_subscription_preview(
        executable=executable,
        codex_home=home,
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )

    assert preview["credential_authority"] == "external_runtime_oauth"
    assert preview["capability_cell_sha256"] == CODEX_EVALUATOR_CAPABILITY_CELL_SHA256
    assert preview["capability_evidence_expires_at"] == CODEX_EVALUATOR_EVIDENCE_EXPIRES_AT
    assert preview["upstream_body_observability"] == "unavailable"
    assert preview["executable_path"] == str(executable)
    assert not home.exists()
    assert "token" not in repr(preview).casefold()


def test_same_name_executable_with_unknown_digest_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "codex"
    executable.write_bytes(b"not-the-reviewed-runtime")
    executable.chmod(0o700)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")

    with pytest.raises(ValueError, match="codex_runtime_capability_unsupported"):
        module.resolve_supported_codex_executable(executable)


def test_environment_selected_config_path_is_used_for_reads_and_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    selected = tmp_path / "isolated" / "config.toml"
    monkeypatch.setenv("YOETZ_CONFIG", str(selected))

    assert module._target_config_path(None) == selected  # pyright: ignore[reportPrivateUsage]


@pytest.mark.anyio
async def test_setup_persists_binding_only_after_codex_confirms_chatgpt_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "codex"
    home = tmp_path / "dedicated-home"
    binding = _binding(executable, home)
    written: list[object] = []

    def build_binding(**_kwargs: object) -> ExternalRuntimeProfileConfig:
        return binding

    def build_profile(_binding: ExternalRuntimeProfileConfig) -> object:
        return object()

    def write_binding(value: ExternalRuntimeProfileConfig, **_kwargs: object) -> Path:
        written.append(value)
        return tmp_path / "config.toml"

    monkeypatch.setattr(module, "_binding", build_binding)
    monkeypatch.setattr(module, "_profile", build_profile)

    def isolated_config(_path: Path | None) -> YoetzConfig:
        return YoetzConfig()

    monkeypatch.setattr(module, "_base_config", isolated_config)

    async def login(*_args: object, **_kwargs: object) -> CodexRuntimeStatus:
        return CodexRuntimeStatus(True, "chatgpt", "plus", True, "terminated")

    monkeypatch.setattr(module, "codex_login", login)
    monkeypatch.setattr(module, "write_external_runtime_binding", write_binding)

    result = await module.codex_subscription_setup(
        executable=executable,
        codex_home=home,
        model="gpt-5.6-sol",
        reasoning_effort="high",
        login_mode="browser",
        open_browser=False,
        switch_account=False,
    )

    assert written == [binding]
    assert result["auth_mode"] == "chatgpt"
    assert result["plan_type"] == "plus"
    assert result["process_cleanup"] == "terminated"


@pytest.mark.anyio
async def test_setup_failure_never_writes_a_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _binding(tmp_path / "codex", tmp_path / "dedicated-home")

    def build_binding(**_kwargs: object) -> ExternalRuntimeProfileConfig:
        return binding

    def build_profile(_binding: ExternalRuntimeProfileConfig) -> object:
        return object()

    def forbidden_write(*_args: object, **_kwargs: object) -> Path:
        pytest.fail("failed readiness must not persist a binding")

    monkeypatch.setattr(module, "_binding", build_binding)
    monkeypatch.setattr(module, "_profile", build_profile)

    def isolated_config(_path: Path | None) -> YoetzConfig:
        return YoetzConfig()

    monkeypatch.setattr(module, "_base_config", isolated_config)

    async def login(*_args: object, **_kwargs: object) -> CodexRuntimeStatus:
        return CodexRuntimeStatus(True, None, None, False, "terminated")

    monkeypatch.setattr(module, "codex_login", login)
    monkeypatch.setattr(module, "write_external_runtime_binding", forbidden_write)

    with pytest.raises(ValueError, match="codex_subscription_readiness_unproven"):
        await module.codex_subscription_setup(
            executable=tmp_path / "codex",
            codex_home=tmp_path / "dedicated-home",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            login_mode="browser",
            open_browser=False,
            switch_account=False,
        )


@pytest.mark.anyio
async def test_disconnect_confirms_logout_before_removing_only_the_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _binding(tmp_path / "codex", tmp_path / "dedicated-home")
    config = YoetzConfig(profile="codex-subscription", external_runtime=binding)
    removed: list[object] = []

    def base_config(_path: Path | None) -> YoetzConfig:
        return config

    def build_profile(_binding: ExternalRuntimeProfileConfig) -> object:
        return object()

    def clear_binding(**_kwargs: object) -> Path:
        removed.append(binding)
        return tmp_path / "config.toml"

    monkeypatch.setattr(module, "_base_config", base_config)
    monkeypatch.setattr(module, "_profile", build_profile)

    async def logout(_profile: object) -> CodexRuntimeStatus:
        return CodexRuntimeStatus(True, None, None, False, "terminated")

    monkeypatch.setattr(module, "codex_logout", logout)
    monkeypatch.setattr(module, "clear_external_runtime_binding", clear_binding)

    result = await module.codex_subscription_disconnect(config_path=tmp_path / "config.toml")

    assert removed == [binding]
    assert result["binding_removed"] is True
    assert result["process_cleanup"] == "terminated"


def test_rollback_preserves_the_dedicated_home_and_installation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _binding(tmp_path / "codex", tmp_path / "dedicated-home")
    config = YoetzConfig(profile="codex-subscription", external_runtime=binding)

    def base_config(_path: Path | None) -> YoetzConfig:
        return config

    def clear_binding(**_kwargs: object) -> Path:
        return tmp_path / "config.toml"

    monkeypatch.setattr(module, "_base_config", base_config)
    monkeypatch.setattr(module, "clear_external_runtime_binding", clear_binding)

    result = module.codex_subscription_rollback(config_path=tmp_path / "config.toml")

    assert result["binding_removed"] is True
    assert result["codex_home_preserved"] == str(tmp_path / "dedicated-home")
    assert result["codex_installation_preserved"] is True


def test_subscription_failure_reason_never_echoes_native_os_text() -> None:
    missing = FileNotFoundError(2, "No such file or directory", "/secret/codex")
    denied = PermissionError(13, "Permission denied", "/secret/codex")

    assert module.subscription_failure_reason(missing) == "codex_runtime_not_found"
    assert module.subscription_failure_reason(denied) == "codex_runtime_unavailable"
    assert module.subscription_failure_reason(TimeoutError()) == "codex_subscription_timeout"
    assert (
        module.subscription_failure_reason(ConfigError("config_value_invalid"))
        == "config_value_invalid"
    )
    assert (
        module.subscription_failure_reason(ValueError("codex_runtime_capability_unsupported"))
        == "codex_runtime_capability_unsupported"
    )
    assert "secret" not in module.subscription_failure_reason(missing)
    assert "Permission denied" not in module.subscription_failure_reason(denied)


def test_missing_executable_is_a_closed_token(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-codex"

    with pytest.raises(ValueError, match="codex_runtime_not_found"):
        module.resolve_supported_codex_executable(missing)


def test_cli_setup_maps_missing_executable_without_echoing_the_path(tmp_path: Path) -> None:
    from yoetz.cli.app import app

    missing = tmp_path / "no-such-codex"
    result = CliRunner().invoke(
        app,
        [
            "provider",
            "codex-subscription",
            "setup",
            "--executable",
            str(missing),
            "--accept",
        ],
    )

    assert result.exit_code == 20
    assert "codex_runtime_not_found" in result.stderr
    assert str(missing) not in result.stderr
    assert "No such file" not in result.stderr


def test_cli_setup_disconnect_and_rollback_recompose_the_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yoetz.cli.app import app

    restarts: list[str] = []

    def resolve(selected: Path) -> tuple[Path, str, str]:
        return selected, "sha256:" + "a" * 64, "openai-codex-npm-darwin-arm64-0.150.1"

    async def setup(**_kwargs: object) -> dict[str, object]:
        return {"schema": "yoetz.codex-subscription-status/1", "model_available": True}

    async def disconnect(**_kwargs: object) -> dict[str, object]:
        return {"binding_removed": True, "process_cleanup": "terminated"}

    def rollback(**_kwargs: object) -> dict[str, object]:
        return {"binding_removed": True, "codex_installation_preserved": True}

    async def restart() -> dict[str, object]:
        restarts.append("restart")
        return {"reachable": True, "state": "ready", "vault_mode": None}

    monkeypatch.setattr(module, "resolve_supported_codex_executable", resolve)
    monkeypatch.setattr(module, "codex_subscription_setup", setup)
    monkeypatch.setattr(module, "codex_subscription_disconnect", disconnect)
    monkeypatch.setattr(module, "codex_subscription_rollback", rollback)
    monkeypatch.setattr(
        "yoetz.cli.setup.restart_service_for_semantic_composition",
        restart,
    )

    runner = CliRunner()
    executable = tmp_path / "codex"
    setup_result = runner.invoke(
        app,
        [
            "provider",
            "codex-subscription",
            "setup",
            "--executable",
            str(executable),
            "--accept",
            "--no-open-browser",
        ],
    )
    disconnect_result = runner.invoke(
        app,
        ["provider", "codex-subscription", "disconnect", "--accept"],
    )
    rollback_result = runner.invoke(app, ["provider", "codex-subscription", "rollback"])

    assert setup_result.exit_code == 0
    assert disconnect_result.exit_code == 0
    assert rollback_result.exit_code == 0
    assert restarts == ["restart", "restart", "restart"]


def test_menu_disconnect_and_rollback_recompose_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yoetz.cli import menu

    restarts: list[str] = []

    async def disconnect(**_kwargs: object) -> dict[str, object]:
        return {"binding_removed": True}

    def rollback(**_kwargs: object) -> dict[str, object]:
        return {"binding_removed": True}

    async def restart() -> dict[str, object]:
        restarts.append("restart")
        return {"reachable": True}

    def ask_disconnect(_choices: object) -> str:
        return "6"

    def always_confirm(*_args: object, **_kwargs: object) -> bool:
        return True

    def hide(_value: object) -> None:
        return None

    monkeypatch.setattr(menu, "_ask", ask_disconnect)
    monkeypatch.setattr("typer.confirm", always_confirm)
    monkeypatch.setattr(module, "codex_subscription_disconnect", disconnect)
    monkeypatch.setattr(
        "yoetz.cli.setup.restart_service_for_semantic_composition",
        restart,
    )
    monkeypatch.setattr(menu, "_show", hide)

    menu._provider_menu()  # pyright: ignore[reportPrivateUsage]

    def ask_rollback(_choices: object) -> str:
        return "7"

    monkeypatch.setattr(menu, "_ask", ask_rollback)
    monkeypatch.setattr(module, "codex_subscription_rollback", rollback)
    menu._provider_menu()  # pyright: ignore[reportPrivateUsage]

    assert restarts == ["restart", "restart"]


def test_guided_setup_offers_account_switch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompts = iter(
        [
            str(tmp_path / "codex"),
            str(tmp_path / "home"),
            "gpt-5.6-sol",
            "high",
            "browser",
        ]
    )
    confirms: list[str] = []

    def prompt(message: str, **_kwargs: object) -> str:
        del message
        return next(prompts)

    def confirm(message: str, **_kwargs: object) -> bool:
        confirms.append(message)
        return True

    captured: list[bool] = []

    async def setup(**kwargs: object) -> dict[str, object]:
        captured.append(bool(kwargs["switch_account"]))
        return {"auth_mode": "chatgpt"}

    def preview(**_kwargs: object) -> dict[str, str]:
        return {
            "executable_path": "/opt/codex",
            "executable_sha256": "sha256:" + "a" * 64,
            "runtime_version": "0.150.1",
            "capability_cell_sha256": "sha256:" + "b" * 64,
            "capability_evidence_expires_at": "2026-11-30T00:00:00Z",
            "codex_home": "/home",
            "disconnect_command": "yoetz provider codex-subscription disconnect",
            "rollback_command": "yoetz provider codex-subscription rollback",
        }

    monkeypatch.setattr("yoetz.adapters.integrations.codex_discovery.discover_codex_binaries", list)
    monkeypatch.setattr("typer.prompt", prompt)
    monkeypatch.setattr("typer.confirm", confirm)
    monkeypatch.setattr(module, "codex_subscription_preview", preview)
    monkeypatch.setattr(module, "codex_subscription_setup", setup)

    import anyio

    anyio.run(module.prompt_codex_subscription_setup)

    assert any(item.startswith("Continue to Codex sign-in") for item in confirms)
    assert any("switch ChatGPT account" in item for item in confirms)
    assert captured == [True]


def _data_dir_config_text(data_dir: Path) -> str:
    return f'schema_version = "1"\nprofile = "strict-local"\n\n[storage]\ndata_dir = "{data_dir}"\n'


def _bound_config_file(tmp_path: Path, binding: ExternalRuntimeProfileConfig) -> Path:
    """Write the exact rendered config selecting the binding, with an explicit data_dir."""

    from yoetz.config.models import StorageConfig

    config = YoetzConfig(
        profile="codex-subscription",
        storage=StorageConfig(data_dir=tmp_path / "state"),
        external_runtime=binding,
    )
    target = tmp_path / "config.toml"
    target.write_text(render_config_toml(config), encoding="utf-8")
    return target


def test_base_config_accepts_valid_storage_data_dir_through_the_canonical_loader(
    tmp_path: Path,
) -> None:
    """A file the service loader accepts is a file the lifecycle commands accept (#520)."""

    target = tmp_path / "config.toml"
    target.write_text(_data_dir_config_text(tmp_path / "state"), encoding="utf-8")

    config = module._base_config(target)  # pyright: ignore[reportPrivateUsage]

    assert config.storage.data_dir == tmp_path / "state"


def test_base_config_fails_as_one_closed_token_on_invalid_configuration(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text("[storage]\ndata_dir = 5\n", encoding="utf-8")

    with pytest.raises(ValueError, match="config_value_invalid"):
        module._base_config(target)  # pyright: ignore[reportPrivateUsage]

    target.write_text("not [ valid toml\n", encoding="utf-8")
    with pytest.raises(ValueError, match="config_toml_invalid"):
        module._base_config(target)  # pyright: ignore[reportPrivateUsage]


def test_rollback_preserves_storage_data_dir_while_removing_only_the_binding(
    tmp_path: Path,
) -> None:
    binding = _binding(tmp_path / "codex", tmp_path / "dedicated-home")
    target = _bound_config_file(tmp_path, binding)

    result = module.codex_subscription_rollback(config_path=target)

    assert result["binding_removed"] is True
    rewritten = module._base_config(target)  # pyright: ignore[reportPrivateUsage]
    assert rewritten.external_runtime is None
    assert rewritten.storage.data_dir == tmp_path / "state"
    assert rewritten.profile == "strict-local"


@pytest.mark.anyio
async def test_status_reads_a_binding_from_a_config_with_explicit_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _binding(tmp_path / "codex", tmp_path / "dedicated-home")
    target = _bound_config_file(tmp_path, binding)

    async def account_status(_profile: object) -> CodexRuntimeStatus:
        return CodexRuntimeStatus(True, "chatgpt", "plus", True, "terminated")

    monkeypatch.setattr(module, "codex_account_status", account_status)

    result = await module.codex_subscription_status(config_path=target)

    assert result["codex_home"] == str(tmp_path / "dedicated-home")
    assert result["auth_mode"] == "chatgpt"


@pytest.mark.anyio
async def test_disconnect_preserves_storage_data_dir_from_a_valid_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _binding(tmp_path / "codex", tmp_path / "dedicated-home")
    target = _bound_config_file(tmp_path, binding)

    async def logout(_profile: object) -> CodexRuntimeStatus:
        return CodexRuntimeStatus(True, None, None, False, "terminated")

    monkeypatch.setattr(module, "codex_logout", logout)

    result = await module.codex_subscription_disconnect(config_path=target)

    assert result["binding_removed"] is True
    rewritten = module._base_config(target)  # pyright: ignore[reportPrivateUsage]
    assert rewritten.external_runtime is None
    assert rewritten.storage.data_dir == tmp_path / "state"


@pytest.mark.anyio
async def test_setup_validates_configuration_before_any_login_or_home_side_effect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An invalid config is a deterministic local failure; login must never open (#520)."""

    target = tmp_path / "config.toml"
    target.write_text("[storage]\ndata_dir = 5\n", encoding="utf-8")
    binding = _binding(tmp_path / "codex", tmp_path / "dedicated-home")

    def build_binding(**_kwargs: object) -> ExternalRuntimeProfileConfig:
        return binding

    monkeypatch.setattr(module, "_binding", build_binding)

    async def forbidden_login(*_args: object, **_kwargs: object) -> CodexRuntimeStatus:
        pytest.fail("an invalid configuration must fail before the Codex login flow")

    def forbidden_prepare(_home: Path) -> None:
        pytest.fail("an invalid configuration must fail before the dedicated home is created")

    monkeypatch.setattr(module, "codex_login", forbidden_login)
    monkeypatch.setattr(module, "prepare_codex_home", forbidden_prepare)

    with pytest.raises(ValueError, match="config_value_invalid"):
        await module.codex_subscription_setup(
            executable=tmp_path / "codex",
            codex_home=tmp_path / "dedicated-home",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            login_mode="browser",
            open_browser=False,
            switch_account=False,
            config_path=target,
        )


@pytest.mark.anyio
async def test_setup_probes_the_writable_binding_target_before_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    target = blocked / "config.toml"
    target.write_text(_data_dir_config_text(tmp_path / "state"), encoding="utf-8")
    binding = _binding(tmp_path / "codex", tmp_path / "dedicated-home")

    def build_binding(**_kwargs: object) -> ExternalRuntimeProfileConfig:
        return binding

    monkeypatch.setattr(module, "_binding", build_binding)

    async def forbidden_login(*_args: object, **_kwargs: object) -> CodexRuntimeStatus:
        pytest.fail("an unwritable binding target must fail before the Codex login flow")

    monkeypatch.setattr(module, "codex_login", forbidden_login)

    blocked.chmod(0o500)
    try:
        with pytest.raises(ValueError, match="config_value_invalid"):
            await module.codex_subscription_setup(
                executable=tmp_path / "codex",
                codex_home=tmp_path / "dedicated-home",
                model="gpt-5.6-sol",
                reasoning_effort="high",
                login_mode="browser",
                open_browser=False,
                switch_account=False,
                config_path=target,
            )
    finally:
        blocked.chmod(0o700)


@pytest.mark.anyio
async def test_disconnect_probes_the_removal_write_before_codex_logout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _binding(tmp_path / "codex", tmp_path / "dedicated-home")
    target = _bound_config_file(tmp_path, binding)

    async def forbidden_logout(_profile: object) -> CodexRuntimeStatus:
        pytest.fail("an unpersistable removal must fail before Codex logs the home out")

    monkeypatch.setattr(module, "codex_logout", forbidden_logout)

    tmp_path.chmod(0o500)
    try:
        with pytest.raises(ValueError, match="config_value_invalid"):
            await module.codex_subscription_disconnect(config_path=target)
    finally:
        tmp_path.chmod(0o700)


def test_cli_status_reports_a_bounded_config_token_instead_of_internal_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 70 ``internal_error`` for a local configuration problem was the #520 defect."""

    from yoetz.cli.app import app

    target = tmp_path / "config.toml"
    target.write_text("[storage]\ndata_dir = 5\n", encoding="utf-8")
    monkeypatch.setenv("YOETZ_CONFIG", str(target))

    result = CliRunner().invoke(app, ["provider", "codex-subscription", "status", "--json"])

    assert result.exit_code == 20
    assert "codex_subscription: config_value_invalid" in result.stderr
    assert "internal_error" not in result.stderr
    assert str(tmp_path) not in result.stderr


def test_cli_status_accepts_a_valid_storage_data_dir_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yoetz.cli.app import app

    target = tmp_path / "config.toml"
    target.write_text(_data_dir_config_text(tmp_path / "state"), encoding="utf-8")
    monkeypatch.setenv("YOETZ_CONFIG", str(target))

    result = CliRunner().invoke(app, ["provider", "codex-subscription", "status"])

    assert result.exit_code == 20
    assert "codex_subscription_not_configured" in result.stderr
    assert "internal_error" not in result.stderr
