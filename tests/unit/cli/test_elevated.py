"""Elevated-bootstrap CLI driver vectors (ADR-015)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import anyio
import pytest

from yoetz.cli import elevated
from yoetz.protocol.canonical import canonical_digest
from yoetz.service.elevated_bootstrap import ElevatedBootstrapError


def test_prepare_elevated_vault_initialize(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with patch("yoetz.service.elevated_bootstrap.state_dir", return_value=tmp_path):
        payload = elevated.prepare_elevated("vault_initialize")
    assert payload["schema"] == "yoetz.elevated-bootstrap.prepare-result/1"
    projection = payload["elevated_bootstrap"]
    assert projection["required"] is True
    assert projection["operation"] == "vault_initialize"
    assert "--passphrase-fd" in projection["approve_command"]


def test_approve_elevated_requires_fds_and_clears_on_failure(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _run() -> None:
        with patch("yoetz.service.elevated_bootstrap.state_dir", return_value=tmp_path):
            prepared = elevated.prepare_elevated("vault_initialize")
            projection = prepared["elevated_bootstrap"]
            with pytest.raises(ElevatedBootstrapError) as missing:
                await elevated.approve_elevated(
                    pending_id=str(projection["pending_id"]),
                    danger_digest=str(projection["danger_digest"]),
                    confirm=str(projection["confirmation_phrase"]),
                )
            assert missing.value.reason == "passphrase_fd_required"
            assert elevated.status_elevated()["elevated_bootstrap"]["required"] is False

    anyio.run(_run)


def test_approve_vault_initialize_success_path(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _run() -> None:
        with patch("yoetz.service.elevated_bootstrap.state_dir", return_value=tmp_path):
            prepared = elevated.prepare_elevated("vault_initialize")
            projection = prepared["elevated_bootstrap"]

            async def _fake_complete(pending: Any, passphrase_fd: int) -> dict[str, Any]:
                del pending, passphrase_fd
                return {"state": "passphrase", "reason": "initialized"}

            with patch.object(elevated, "_complete_vault_initialize", new=_fake_complete):
                result = await elevated.approve_elevated(
                    pending_id=str(projection["pending_id"]),
                    danger_digest=str(projection["danger_digest"]),
                    confirm=str(projection["confirmation_phrase"]),
                    passphrase_fd=3,
                )
            assert result["outcome"] == "completed"
            assert result["operation"] == "vault_initialize"
            assert elevated.status_elevated()["elevated_bootstrap"]["required"] is False

    anyio.run(_run)


def test_target_digest_provider_binding() -> None:
    binding = {
        "endpoint_profile_id": "ep",
        "endpoint_profile_version": "1",
        "model_id": "m",
        "provider_id": "p",
        "purpose": "semantic_review",
        "purpose_digest": "sha256:" + ("c" * 64),
        "scope_digest": "sha256:" + ("d" * 64),
    }
    digest = elevated._target_digest("provider_credential_set", binding)
    assert digest == canonical_digest(
        {
            "action": "set",
            "endpoint_profile_id": "ep",
            "endpoint_profile_version": "1",
            "kind": "provider_credential",
            "model_id": "m",
            "provider_id": "p",
            "purpose": "semantic_review",
            "purpose_digest": binding["purpose_digest"],
            "scope_digest": binding["scope_digest"],
        }
    )


def test_status_elevated_schema(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with patch("yoetz.service.elevated_bootstrap.state_dir", return_value=tmp_path):
        payload = elevated.status_elevated()
    assert payload["schema"] == "yoetz.elevated-bootstrap.status/1"
    assert payload["elevated_bootstrap"]["required"] is False
