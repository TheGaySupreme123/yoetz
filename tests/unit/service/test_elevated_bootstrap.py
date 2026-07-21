"""Elevated consent pending + catalog contract (ADR-015/016)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from yoetz.protocol.canonical import canonical_digest
from yoetz.service.elevated_bootstrap import (
    ElevatedBootstrapError,
    approve_pending,
    catalog_payload,
    clear_pending,
    load_pending,
    prepare_pending,
    projection_for_status,
    read_secret_fd,
    status_payload,
)

_TARGET = canonical_digest({"expected_mode": "uninitialized", "kind": "empty_vault"})
_BINDING = {
    "endpoint_profile_id": "ep_fireworks",
    "endpoint_profile_version": "1",
    "model_id": "accounts/fireworks/models/minimax-m3",
    "provider_id": "fireworks",
    "purpose": "semantic_review",
    "purpose_digest": "sha256:" + ("a" * 64),
    "scope_digest": "sha256:" + ("b" * 64),
}


def test_catalog_lists_risk_classes_and_default_safe() -> None:
    catalog = catalog_payload()
    assert catalog["schema"] == "yoetz.consent.catalog/1"
    assert "mcp.start" in catalog["default_safe"]
    assert catalog["rules"]["no_standing_yolo"] is True
    names = {item["operation"] for item in catalog["operations"]}  # type: ignore[index]
    assert "vault_initialize" in names
    assert "backup_execute" in names
    assert "provider_credential_rotate" in names


def test_prepare_vault_initialize_projection_and_approve(tmp_path: Path) -> None:
    pending = prepare_pending(
        "vault_initialize", target_digest=_TARGET, _state=tmp_path
    )
    assert pending.operation == "vault_initialize"
    assert pending.risk_class == "secret_ingress"
    assert pending.secret_fds == ("passphrase-fd",)
    assert pending.expires_at_unix - pending.created_at_unix == 15 * 60

    projection = projection_for_status(pending)
    assert projection["required"] is True
    assert projection["risk_class"] == "secret_ingress"
    assert projection["approve_command"][-2:] == ["--passphrase-fd", "3"]
    assert "user_steps" in projection

    approved = approve_pending(
        pending_id=pending.pending_id,
        danger_digest=pending.danger_digest,
        confirm=pending.confirmation_phrase,
        _state=tmp_path,
    )
    assert approved == pending
    clear_pending(_state=tmp_path)
    assert load_pending(_state=tmp_path) is None


def test_phrase_only_backup_consent(tmp_path: Path) -> None:
    plan = canonical_digest({"kind": "backup_plan", "n": 1})
    pending = prepare_pending("backup_execute", target_digest=plan, _state=tmp_path)
    assert pending.risk_class == "phrase_only"
    assert pending.secret_fds == ()
    command = projection_for_status(pending)["approve_command"]
    assert "--passphrase-fd" not in command
    assert "--reauth-fd" not in command


def test_prepare_provider_binding_rules(tmp_path: Path) -> None:
    with pytest.raises(ElevatedBootstrapError) as missing:
        prepare_pending("provider_credential_set", target_digest=_TARGET, _state=tmp_path)
    assert missing.value.reason == "provider_binding_required"
    with pytest.raises(ElevatedBootstrapError) as forbidden:
        prepare_pending(
            "vault_initialize",
            target_digest=_TARGET,
            provider_binding=_BINDING,
            _state=tmp_path,
        )
    assert forbidden.value.reason == "provider_binding_forbidden"


def test_unimplemented_privacy_widen_refused(tmp_path: Path) -> None:
    with pytest.raises(ElevatedBootstrapError) as exc:
        prepare_pending(
            "privacy_policy_widen",
            target_digest=canonical_digest({"pending": "x"}),
            _state=tmp_path,
        )
    assert exc.value.reason == "operation_not_implemented"


def test_singleton_and_exact_approve(tmp_path: Path) -> None:
    first = prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    with pytest.raises(ElevatedBootstrapError) as duplicate:
        prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    assert duplicate.value.reason == "pending_already_active"
    with pytest.raises(ElevatedBootstrapError) as bad_phrase:
        approve_pending(
            pending_id=first.pending_id,
            danger_digest=first.danger_digest,
            confirm=first.confirmation_phrase.lower(),
            _state=tmp_path,
        )
    assert bad_phrase.value.reason == "confirmation_mismatch"


def test_read_secret_fd_bounds() -> None:
    with pytest.raises(ElevatedBootstrapError):
        read_secret_fd(0, maximum=16)
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, b"secret-value\n")
        os.close(write_end)
        write_end = -1
        value = read_secret_fd(read_end, maximum=64)
        assert bytes(value) == b"secret-value"
    finally:
        os.close(read_end)
        if write_end >= 0:
            os.close(write_end)


def test_status_includes_catalog(tmp_path: Path) -> None:
    payload = status_payload(_state=tmp_path)
    assert payload["elevated_bootstrap"]["required"] is False
    assert payload["consent_catalog"]["schema"] == "yoetz.consent.catalog/1"
