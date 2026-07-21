"""Elevated-bootstrap pending consent contract (ADR-015)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from yoetz.protocol.canonical import canonical_digest
from yoetz.service.elevated_bootstrap import (
    ElevatedBootstrapError,
    approve_pending,
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


def test_prepare_vault_initialize_projection_and_approve(tmp_path: Path) -> None:
    pending = prepare_pending(
        "vault_initialize", target_digest=_TARGET, _state=tmp_path
    )
    assert pending.operation == "vault_initialize"
    assert pending.provider_binding is None
    assert pending.expires_at_unix - pending.created_at_unix == 15 * 60
    assert pending.danger_text.startswith("DANGER — elevated vault initialize")
    assert pending.confirmation_phrase.startswith("YOETZ APPROVE ")
    assert pending.danger_digest.startswith("sha256:")

    loaded = load_pending(_state=tmp_path)
    assert loaded == pending

    projection = projection_for_status(pending)
    assert projection["required"] is True
    assert projection["state"] == "pending"
    assert projection["danger_text"] == pending.danger_text
    assert projection["confirmation_phrase"] == pending.confirmation_phrase
    assert projection["approve_command"][-2:] == ["--passphrase-fd", "3"]
    assert projection["forbidden_channels"] == [
        "mcp",
        "argv",
        "env",
        "stdin",
        "config",
        "transcript",
    ]
    payload = json.loads((tmp_path / "elevated-bootstrap" / "elevated-bootstrap-pending.json").read_text())
    assert "schema" in payload
    for key in payload:
        assert key not in {"passphrase", "credential", "token", "secret", "proof", "reauth"}
    encoded = json.dumps(payload)
    assert "api_key" not in encoded.lower()
    assert "sk-" not in encoded.lower()

    approved = approve_pending(
        pending_id=pending.pending_id,
        danger_digest=pending.danger_digest,
        confirm=pending.confirmation_phrase,
        _state=tmp_path,
    )
    assert approved == pending
    clear_pending(_state=tmp_path)
    assert load_pending(_state=tmp_path) is None
    assert status_payload(_state=tmp_path)["elevated_bootstrap"]["required"] is False


def test_prepare_provider_binding_rules_and_command(tmp_path: Path) -> None:
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

    target = canonical_digest(
        {
            "action": "set",
            "endpoint_profile_id": _BINDING["endpoint_profile_id"],
            "endpoint_profile_version": _BINDING["endpoint_profile_version"],
            "kind": "provider_credential",
            "model_id": _BINDING["model_id"],
            "provider_id": _BINDING["provider_id"],
            "purpose": _BINDING["purpose"],
            "purpose_digest": _BINDING["purpose_digest"],
            "scope_digest": _BINDING["scope_digest"],
        }
    )
    pending = prepare_pending(
        "provider_credential_set",
        target_digest=target,
        provider_binding=_BINDING,
        _state=tmp_path,
    )
    assert pending.provider_binding == _BINDING
    command = projection_for_status(pending)["approve_command"]
    assert command[-4:] == ["--reauth-fd", "3", "--credential-fd", "4"]


def test_singleton_pending_and_exact_approve_checks(tmp_path: Path) -> None:
    first = prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    with pytest.raises(ElevatedBootstrapError) as duplicate:
        prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    assert duplicate.value.reason == "pending_already_active"

    with pytest.raises(ElevatedBootstrapError) as bad_id:
        approve_pending(
            pending_id="nope",
            danger_digest=first.danger_digest,
            confirm=first.confirmation_phrase,
            _state=tmp_path,
        )
    assert bad_id.value.reason == "pending_id_mismatch"
    with pytest.raises(ElevatedBootstrapError) as bad_digest:
        approve_pending(
            pending_id=first.pending_id,
            danger_digest="sha256:" + ("0" * 64),
            confirm=first.confirmation_phrase,
            _state=tmp_path,
        )
    assert bad_digest.value.reason == "danger_digest_mismatch"
    with pytest.raises(ElevatedBootstrapError) as bad_phrase:
        approve_pending(
            pending_id=first.pending_id,
            danger_digest=first.danger_digest,
            confirm=first.confirmation_phrase.lower(),
            _state=tmp_path,
        )
    assert bad_phrase.value.reason == "confirmation_mismatch"


def test_read_secret_fd_bounds_and_reserved_descriptors() -> None:
    with pytest.raises(ElevatedBootstrapError) as reserved:
        read_secret_fd(0, maximum=16)
    assert reserved.value.reason == "secret_fd_invalid"
    with pytest.raises(ElevatedBootstrapError):
        read_secret_fd(1, maximum=16)
    with pytest.raises(ElevatedBootstrapError):
        read_secret_fd(2, maximum=16)

    read_end, write_end = os.pipe()
    try:
        os.write(write_end, b"secret-value\n")
        os.close(write_end)
        write_end = -1
        value = read_secret_fd(read_end, maximum=64)
        assert bytes(value) == b"secret-value"
        assert isinstance(value, bytearray)
    finally:
        os.close(read_end)
        if write_end >= 0:
            os.close(write_end)

    read_end, write_end = os.pipe()
    try:
        os.write(write_end, b"x" * 5)
        os.close(write_end)
        write_end = -1
        with pytest.raises(ElevatedBootstrapError) as oversized:
            read_secret_fd(read_end, maximum=4)
        assert oversized.value.reason == "secret_too_large"
    finally:
        os.close(read_end)
        if write_end >= 0:
            os.close(write_end)


def test_empty_projection_forbidden_channels() -> None:
    projection = projection_for_status(None)
    assert projection == {
        "required": False,
        "state": "not_prepared",
        "operation": None,
        "pending_id": None,
        "danger_digest": None,
        "confirmation_phrase": None,
        "forbidden_channels": ["mcp", "argv", "env", "stdin", "config", "transcript"],
    }
