"""Trusted-review pending and catalog contract (ADR-015/016)."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.schemas import validate_schema_instance
from yoetz.service.elevated_bootstrap import (
    ElevatedBootstrapError,
    catalog_payload,
    claim_pending_for_review,
    complete_review,
    grant_target_digest,
    load_pending,
    prepare_pending,
    projection_for_status,
    status_payload,
)

_TARGET = canonical_digest({"expected_mode": "uninitialized", "kind": "empty_vault"})
_REPOSITORY_COMMITMENT = "hmac-sha256:" + ("c" * 64)
_AUTHORITY_DIGEST = "sha256:" + ("d" * 64)
_BINDING = {
    "endpoint_profile_id": "ep_fireworks",
    "endpoint_profile_version": "1",
    "model_id": "accounts/fireworks/models/minimax-m3",
    "provider_id": "fireworks",
    "purpose": "semantic-review",
    "purpose_digest": canonical_digest({"purpose": "semantic-review"}),
    "scope_digest": "sha256:" + ("b" * 64),
}
_AGENT_FORBIDDEN = {
    "approve_command",
    "confirmation_phrase",
    "credential_fd",
    "passphrase_fd",
    "reauth_fd",
    "secret_fds",
}


def _assert_agent_safe(value: object) -> None:
    rendered = json.dumps(value, sort_keys=True)
    for forbidden in _AGENT_FORBIDDEN:
        assert forbidden not in rendered


def test_catalog_is_review_only_and_agent_safe() -> None:
    catalog = cast(dict[str, Any], catalog_payload())
    assert catalog["schema"] == "yoetz.consent.catalog/2"
    assert "mcp.start" in catalog["default_safe"]
    assert catalog["rules"]["no_standing_yolo"] is True
    assert catalog["rules"]["verified_user_presence_required"] is True
    assert catalog["rules"]["trusted_console_is_not_authority"] is True
    assert catalog["rules"]["agent_selected_initialization_secret_forbidden"] is True
    assert catalog["rules"]["chat_user_host_tool_approval_permitted"] is True
    assert catalog["rules"]["unattested_chat_assent_forbidden"] is True
    by_name = {item["operation"]: item for item in catalog["operations"]}
    assert by_name["vault_initialize"]["implemented"] is True
    assert by_name["provider_credential_rotate"]["implemented"] is True
    assert by_name["provider_credential_set"]["chat_user_authorize_allowed"] is True
    assert by_name["repository_privacy_grant"]["requires_grant_binding"] is True
    assert by_name["repository_privacy_grant"]["chat_user_authorize_allowed"] is True
    assert by_name["backup_execute"]["implemented"] is False
    assert by_name["backup_execute"]["risk_class"] == "review_only"
    _assert_agent_safe(catalog)


def test_prepare_projection_contains_only_agent_safe_review_fields(tmp_path: Path) -> None:
    pending = prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    projection = cast(dict[str, Any], projection_for_status(pending))

    assert set(projection) == {
        "danger_digest",
        "danger_text",
        "expires_at_unix",
        "operation",
        "pending_id",
        "authorize_command",
        "review_command",
        "risk_class",
        "schema",
        "target_digest",
    }
    assert projection["schema"] == "yoetz.consent.pending-agent/2"
    assert projection["review_command"] == ["yoetz", "consent", "review"]
    assert projection["authorize_command"] == ["yoetz", "consent", "authorize"]
    assert pending.expires_at_unix - pending.created_at_unix == 15 * 60
    stored = json.loads(
        (tmp_path / "elevated-bootstrap" / "elevated-bootstrap-pending.json").read_text()
    )
    assert stored["schema"] == "yoetz.elevated-bootstrap.pending/2"
    _assert_agent_safe(projection)
    _assert_agent_safe(stored)


def test_review_claim_is_single_shot_for_approval_and_duplicate(tmp_path: Path) -> None:
    prepared = prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    claimed = claim_pending_for_review(_state=tmp_path)
    assert claimed == prepared
    assert load_pending(_state=tmp_path) is None

    with pytest.raises(ElevatedBootstrapError) as duplicate:
        claim_pending_for_review(_state=tmp_path)
    assert duplicate.value.reason == "pending_absent"

    complete_review(claimed, outcome="approved", _state=tmp_path)
    with pytest.raises(ElevatedBootstrapError) as reused:
        complete_review(claimed, outcome="approved", _state=tmp_path)
    assert reused.value.reason == "pending_absent"


@pytest.mark.parametrize("outcome", ["denied", "cancelled", "failed"])
def test_review_consumes_denial_cancellation_and_failure(tmp_path: Path, outcome: str) -> None:
    prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    claimed = claim_pending_for_review(_state=tmp_path)
    complete_review(claimed, outcome=outcome, _state=tmp_path)
    assert load_pending(_state=tmp_path) is None


def test_concurrent_review_has_one_winner(tmp_path: Path) -> None:
    prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)

    def claim() -> tuple[str, object]:
        try:
            return "ok", claim_pending_for_review(_state=tmp_path)
        except ElevatedBootstrapError as exc:
            return "error", exc.reason

    def claim_index(_index: int) -> tuple[str, object]:
        return claim()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim_index, range(2)))

    winners = [value for status, value in outcomes if status == "ok"]
    losers = [value for status, value in outcomes if status == "error"]
    assert len(winners) == 1
    assert losers[0] in {"pending_absent", "review_in_progress"}
    complete_review(cast(Any, winners[0]), outcome="cancelled", _state=tmp_path)


def test_interrupted_review_marker_blocks_reuse_and_new_prepare(tmp_path: Path) -> None:
    prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    claimed = claim_pending_for_review(_state=tmp_path)
    assert load_pending(_state=tmp_path) is None
    with pytest.raises(ElevatedBootstrapError) as duplicate:
        claim_pending_for_review(_state=tmp_path)
    assert duplicate.value.reason == "pending_absent"
    with pytest.raises(ElevatedBootstrapError) as replacement:
        prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    assert replacement.value.reason == "review_in_progress"
    complete_review(claimed, outcome="failed", _state=tmp_path)


def test_legacy_v1_record_is_invalidated_not_migrated(tmp_path: Path) -> None:
    root = tmp_path / "elevated-bootstrap"
    root.mkdir(mode=0o700)
    path = root / "elevated-bootstrap-pending.json"
    path.write_text(
        json.dumps(
            {
                "schema": "yoetz.elevated-bootstrap.pending/1",
                "confirmation_phrase": "reusable value",
            }
        )
    )

    with pytest.raises(ElevatedBootstrapError) as exc:
        load_pending(_state=tmp_path)
    assert exc.value.reason == "legacy_pending_invalidated"
    assert not path.exists()
    audit = (root / "elevated-bootstrap-audit.jsonl").read_text()
    assert "legacy_pending_invalidated" in audit
    assert "reusable value" not in audit


def test_expiry_consumes_before_review(tmp_path: Path) -> None:
    with patch("yoetz.service.elevated_bootstrap.time.time", return_value=1_000):
        prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    with patch("yoetz.service.elevated_bootstrap.time.time", return_value=1_901):
        with pytest.raises(ElevatedBootstrapError) as exc:
            claim_pending_for_review(_state=tmp_path)
    assert exc.value.reason == "pending_absent"
    assert load_pending(_state=tmp_path) is None


def test_tampered_digest_or_operation_is_rejected_before_claim(tmp_path: Path) -> None:
    prepare_pending("vault_initialize", target_digest=_TARGET, _state=tmp_path)
    path = tmp_path / "elevated-bootstrap" / "elevated-bootstrap-pending.json"
    payload = json.loads(path.read_text())
    payload["danger_digest"] = "sha256:" + ("0" * 64)
    path.write_text(json.dumps(payload))
    with pytest.raises(ElevatedBootstrapError) as digest:
        claim_pending_for_review(_state=tmp_path)
    assert digest.value.reason == "pending_tampered"

    payload["operation"] = "provider_credential_set"
    path.write_text(json.dumps(payload))
    with pytest.raises(ElevatedBootstrapError) as operation:
        claim_pending_for_review(_state=tmp_path)
    assert operation.value.reason in {"pending_corrupt", "pending_tampered"}


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


def test_repository_grant_and_provider_bindings_are_repository_bound(tmp_path: Path) -> None:
    grant = {
        "recipe": "assisted_review",
        "repository_privacy_commitment": _REPOSITORY_COMMITMENT,
        "authority_digest": _AUTHORITY_DIGEST,
    }
    pending = prepare_pending(
        "repository_privacy_grant",
        target_digest=grant_target_digest(grant),
        grant_binding=grant,
        _state=tmp_path,
    )
    assert pending.grant_binding == grant
    assert load_pending(_state=tmp_path) == pending
    assert (
        grant_target_digest({**grant, "authority_digest": "sha256:" + ("e" * 64)})
        != pending.target_digest
    )

    provider = {**_BINDING, "repository_privacy_commitment": _REPOSITORY_COMMITMENT}
    tmp_path_provider = tmp_path / "provider"
    provider_pending = prepare_pending(
        "provider_credential_set",
        target_digest=_TARGET,
        provider_binding=provider,
        _state=tmp_path_provider,
    )
    assert provider_pending.provider_binding == provider


def test_target_digest_and_unimplemented_operations_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ElevatedBootstrapError) as digest:
        prepare_pending("vault_initialize", target_digest="sha256:not-hex", _state=tmp_path)
    assert digest.value.reason == "target_digest_invalid"
    with pytest.raises(ElevatedBootstrapError) as unimplemented:
        prepare_pending(
            "privacy_policy_widen",
            target_digest=canonical_digest({"pending": "x"}),
            _state=tmp_path,
        )
    assert unimplemented.value.reason == "operation_not_implemented"


def test_status_contains_nullable_pending_and_catalog(tmp_path: Path) -> None:
    empty = cast(dict[str, Any], status_payload(_state=tmp_path))
    assert empty["schema"] == "yoetz.elevated-bootstrap.status/2"
    assert empty["pending"] is None
    assert empty["consent_catalog"]["schema"] == "yoetz.consent.catalog/2"
    _assert_agent_safe(empty)

    prepare_pending(
        "provider_credential_set", target_digest=_TARGET, provider_binding=_BINDING, _state=tmp_path
    )
    prepared = cast(dict[str, Any], status_payload(_state=tmp_path))
    assert prepared["pending"]["operation"] == "provider_credential_set"
    _assert_agent_safe(prepared)
    validate_schema_instance("catalog", "2.0.0", prepared["consent_catalog"])
    validate_schema_instance("pending-agent", "2.0.0", prepared["pending"])
    validate_schema_instance("status", "2.0.0", prepared)
