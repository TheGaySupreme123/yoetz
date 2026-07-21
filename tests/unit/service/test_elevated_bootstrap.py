"""Elevated consent pending + catalog contract (ADR-015/016)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

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
    catalog = cast(dict[str, Any], catalog_payload())
    assert catalog["schema"] == "yoetz.consent.catalog/1"
    assert "mcp.start" in catalog["default_safe"]
    assert catalog["rules"]["no_standing_yolo"] is True
    by_name = {item["operation"]: item for item in catalog["operations"]}
    assert by_name["vault_initialize"]["implemented"] is True
    assert by_name["provider_credential_rotate"]["implemented"] is True
    assert by_name["backup_execute"]["implemented"] is False
    assert by_name["privacy_policy_widen"]["implemented"] is False


def test_prepare_vault_initialize_projection_and_approve(tmp_path: Path) -> None:
    pending = prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    assert pending.operation == "vault_initialize"
    assert pending.risk_class == "secret_ingress"
    assert pending.secret_fds == ("passphrase-fd",)
    assert pending.expires_at_unix - pending.created_at_unix == 15 * 60

    projection = cast(dict[str, Any], projection_for_status(pending))
    assert projection["required"] is True
    assert projection["risk_class"] == "secret_ingress"
    approve_command = cast(list[str], projection["approve_command"])
    assert approve_command[-2:] == ["--passphrase-fd", "3"]
    assert "<confirmation_phrase>" in approve_command
    assert pending.confirmation_phrase not in approve_command
    assert "user_steps" in projection

    approved = approve_pending(
        pending_id=pending.pending_id,
        danger_digest=pending.danger_digest,
        confirm=pending.confirmation_phrase,
        _state=tmp_path,
    )
    assert approved == pending
    assert load_pending(_state=tmp_path) is None
    with pytest.raises(ElevatedBootstrapError) as reused:
        approve_pending(
            pending_id=pending.pending_id,
            danger_digest=pending.danger_digest,
            confirm=pending.confirmation_phrase,
            _state=tmp_path,
        )
    assert reused.value.reason == "pending_absent"


def test_phrase_only_ops_not_implemented(tmp_path: Path) -> None:
    plan = canonical_digest({"kind": "backup_plan", "n": 1})
    with pytest.raises(ElevatedBootstrapError) as exc:
        prepare_pending("backup_execute", target_digest=plan, _state=tmp_path)
    assert exc.value.reason == "operation_not_implemented"


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
    with pytest.raises(ElevatedBootstrapError) as incomplete:
        prepare_pending(
            "provider_credential_set",
            target_digest=_TARGET,
            provider_binding={"provider_id": "x"},
            _state=tmp_path,
        )
    assert incomplete.value.reason == "provider_binding_invalid"


def test_target_digest_must_be_sha256(tmp_path: Path) -> None:
    with pytest.raises(ElevatedBootstrapError) as exc:
        prepare_pending("vault_initialize", target_digest="sha256:not-hex", _state=tmp_path)
    assert exc.value.reason == "target_digest_invalid"


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
    assert load_pending(_state=tmp_path) is not None


def test_load_pending_rejects_tampered_danger_text(tmp_path: Path) -> None:
    prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    path = tmp_path / "elevated-bootstrap" / "elevated-bootstrap-pending.json"
    payload = json.loads(path.read_text())
    payload["danger_text"] = "tampered"
    path.write_text(json.dumps(payload))
    with pytest.raises(ElevatedBootstrapError) as exc:
        load_pending(_state=tmp_path)
    assert exc.value.reason in {"pending_tampered", "pending_corrupt"}
    clear_pending(_state=tmp_path)


def test_read_secret_fd_bounds() -> None:
    with pytest.raises(ElevatedBootstrapError):
        read_secret_fd(0, maximum=16)
    with pytest.raises(ElevatedBootstrapError):
        read_secret_fd(1, maximum=16)
    with pytest.raises(ElevatedBootstrapError):
        read_secret_fd(2, maximum=16)
    with pytest.raises(ElevatedBootstrapError):
        read_secret_fd(-1, maximum=16)
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


def test_read_secret_fd_empty_and_oversized() -> None:
    read_end, write_end = os.pipe()
    try:
        os.close(write_end)
        write_end = -1
        with pytest.raises(ElevatedBootstrapError) as empty:
            read_secret_fd(read_end, maximum=64)
        assert empty.value.reason == "secret_empty"
    finally:
        os.close(read_end)
        if write_end >= 0:
            os.close(write_end)

    read_end, write_end = os.pipe()
    try:
        os.write(write_end, b"x" * 8)
        os.close(write_end)
        write_end = -1
        with pytest.raises(ElevatedBootstrapError) as oversized:
            read_secret_fd(read_end, maximum=4)
        assert oversized.value.reason == "secret_too_large"
    finally:
        os.close(read_end)
        if write_end >= 0:
            os.close(write_end)


def test_status_includes_catalog(tmp_path: Path) -> None:
    payload = cast(dict[str, Any], status_payload(_state=tmp_path))
    elevated = cast(dict[str, Any], payload["elevated_bootstrap"])
    catalog = cast(dict[str, Any], payload["consent_catalog"])
    assert elevated["required"] is False
    assert catalog["schema"] == "yoetz.consent.catalog/1"
