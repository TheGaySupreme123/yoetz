"""Consent CLI driver vectors (ADR-015/016)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import anyio
import pytest

from yoetz.cli import elevated
from yoetz.protocol.canonical import canonical_digest
from yoetz.service.elevated_bootstrap import ElevatedBootstrapError


def test_catalog_elevated() -> None:
    payload = elevated.catalog_elevated()
    assert payload["schema"] == "yoetz.consent.catalog/1"
    assert payload["rules"]["no_standing_yolo"] is True


def test_prepare_elevated_vault_initialize(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with patch("yoetz.service.elevated_bootstrap.state_dir", return_value=tmp_path):
        payload = elevated.prepare_elevated("vault_initialize")
    assert payload["schema"] == "yoetz.elevated-bootstrap.prepare-result/1"
    projection = payload["elevated_bootstrap"]
    assert projection["required"] is True
    assert projection["risk_class"] == "secret_ingress"


def test_prepare_phrase_only_backup(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    plan = canonical_digest({"kind": "backup", "n": 1})
    with patch("yoetz.service.elevated_bootstrap.state_dir", return_value=tmp_path):
        payload = elevated.prepare_elevated("backup_execute", target_digest=plan)
    assert payload["elevated_bootstrap"]["risk_class"] == "phrase_only"


def test_approve_phrase_only_returns_consented(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    plan = canonical_digest({"kind": "migrate", "n": 2})

    async def _run() -> None:
        with patch("yoetz.service.elevated_bootstrap.state_dir", return_value=tmp_path):
            prepared = elevated.prepare_elevated("migrate_execute", target_digest=plan)
            projection = prepared["elevated_bootstrap"]
            result = await elevated.approve_elevated(
                pending_id=str(projection["pending_id"]),
                danger_digest=str(projection["danger_digest"]),
                confirm=str(projection["confirmation_phrase"]),
            )
            assert result["outcome"] == "consented"
            assert result["risk_class"] == "phrase_only"
            assert elevated.status_elevated()["elevated_bootstrap"]["required"] is False

    anyio.run(_run)


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


def test_status_elevated_schema(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with patch("yoetz.service.elevated_bootstrap.state_dir", return_value=tmp_path):
        payload = elevated.status_elevated()
    assert payload["schema"] == "yoetz.elevated-bootstrap.status/1"
    assert "consent_catalog" in payload
