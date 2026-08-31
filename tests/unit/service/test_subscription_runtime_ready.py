"""READY composition treats Codex binding facts as credential presence without spawning."""

from __future__ import annotations

from pathlib import Path

import pytest

from yoetz.adapters.providers import codex_app_server as runtime_module
from yoetz.adapters.providers.codex_app_server import (
    CODEX_APP_SERVER_SCHEMA_SHA256,
    CODEX_EVALUATOR_CAPABILITY_CELL_SHA256,
    CODEX_EVALUATOR_CAPABILITY_PROFILE,
    CODEX_EVALUATOR_CONFIG_SHA256,
    CODEX_EVALUATOR_EVIDENCE_EXPIRES_AT,
    CodexAppServerProfile,
)
from yoetz.config.write import codex_subscription_runtime
from yoetz.service.ready_composition import subscription_runtime_structurally_ready


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


def test_ready_credential_presence_is_binding_digest_and_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launches: list[object] = []

    async def launch(_profile: CodexAppServerProfile) -> object:
        launches.append(_profile)
        raise AssertionError("READY must not spawn a Codex app-server")

    async def account_status(_profile: CodexAppServerProfile) -> object:
        raise AssertionError("READY must not probe account/read")

    def binding_is_valid(_self: CodexAppServerProfile) -> None:
        return None

    monkeypatch.setattr(runtime_module, "_launch", launch)
    monkeypatch.setattr(runtime_module, "codex_account_status", account_status)
    monkeypatch.setattr(CodexAppServerProfile, "verify_local_binding", binding_is_valid)

    binding = _binding(tmp_path / "codex", tmp_path / "home")

    assert subscription_runtime_structurally_ready(binding) is True
    assert launches == []
    assert subscription_runtime_structurally_ready(object()) is False
