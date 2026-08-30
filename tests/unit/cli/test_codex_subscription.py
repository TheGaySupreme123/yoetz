"""Codex subscription setup retains only exact nonsecret runtime binding state."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from yoetz.adapters.providers.codex_app_server import (
    CODEX_APP_SERVER_SCHEMA_SHA256,
    CODEX_EVALUATOR_CAPABILITY_CELL_SHA256,
    CODEX_EVALUATOR_CAPABILITY_PROFILE,
    CODEX_EVALUATOR_CONFIG_SHA256,
    CODEX_EVALUATOR_EVIDENCE_EXPIRES_AT,
    CodexRuntimeStatus,
)
from yoetz.cli import codex_subscription as module
from yoetz.config.models import ExternalRuntimeProfileConfig, YoetzConfig
from yoetz.config.write import codex_subscription_runtime


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
