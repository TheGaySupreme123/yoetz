"""Trusted consent-review CLI driver vectors (ADR-015/016)."""

from __future__ import annotations

import importlib
import io
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import anyio
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from yoetz.cli import elevated
from yoetz.cli.app import app
from yoetz.cli.trusted_console import TrustedForegroundConsole
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.chat_user_authority import ChatUserAttestationModel
from yoetz.protocol.consent import ConsentReviewResultModel
from yoetz.protocol.schemas import SchemaInstanceInvalid, validate_schema_instance
from yoetz.service.confidential_protocol import ProviderCredentialResult, VaultStateResult
from yoetz.service.elevated_bootstrap import (
    ElevatedBootstrapError,
    audit_path,
    consume_import_publication_authorization,
    load_import_publication_authorization,
    load_pending,
    prepare_pending,
)


class _Console:
    def __init__(self, decision: bytes = b"approve") -> None:
        self.decision = decision
        self.output: list[str] = []

    def __enter__(self) -> _Console:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def write(self, value: str) -> None:
        self.output.append(value)

    def read_choice(self, _prompt: str, _allowed: tuple[bytes, ...]) -> bytes:
        return self.decision

    def read_secret(self, _prompt: str, _maximum: int) -> bytearray:
        return bytearray(b"human-entered-secret")


def _patch_state(tmp_path: Path) -> Any:
    return patch("yoetz.service.elevated_bootstrap.state_dir", return_value=tmp_path)


def _patch_verified_presence() -> Any:
    """Admit post-presence behavior in unit tests; production remains fail-closed."""

    return patch("yoetz.cli.elevated._require_action_bound_user_presence", return_value=None)


def _read_stdin_secret_for_test(maximum: int) -> bytearray:
    module = importlib.import_module("yoetz.cli.app")
    reader = cast(Callable[[int], bytearray], getattr(module, "_read_bounded_stdin_secret"))
    return reader(maximum)


_CHAT_PROVIDER_BINDING = {
    "endpoint_profile_id": "ep",
    "endpoint_profile_version": "1",
    "model_id": "model",
    "provider_id": "provider",
    "purpose": "semantic-review",
    "purpose_digest": canonical_digest({"purpose": "semantic-review"}),
    "scope_digest": "sha256:" + ("b" * 64),
    "repository_privacy_commitment": "hmac-sha256:" + ("c" * 64),
}


def _chat_attestation(
    pending: Any, *, decision: str = "approve", warning: bool = True
) -> dict[str, object]:
    return {
        "schema": "yoetz.chat-user-attestation/1",
        "channel": "agent_attested_chat_instruction",
        "client_kind": "codex",
        "instruction_source": "explicit_current_chat_user",
        "pending_id": pending.pending_id,
        "operation": pending.operation,
        "danger_digest": pending.danger_digest,
        "target_digest": pending.target_digest,
        "warning_acknowledged": warning,
        "decision": decision,
    }


def _import_preview(target_digest: str) -> dict[str, object]:
    return {
        "schema": "yoetz.import-publication-preview/1",
        "authorization_target_digest": target_digest,
        "source_identity_digest": "sha256:" + "1" * 64,
        "capture_manifest_commitment": "hmac-sha256:" + "2" * 64,
        "publication_plan_digest": "sha256:" + "3" * 64,
        "task_id": "tsk_10000000-0000-4000-8000-000000000001",
        "session_id": "ses_10000000-0000-4000-8000-000000000002",
        "writer_id": "wri_10000000-0000-4000-8000-000000000003",
        "codex_capability_profile_id": "codex-exec-jsonl/0.139.0/v1",
        "codex_capability_profile_digest": "sha256:" + "4" * 64,
        "codex_version": "0.139.0",
        "mapping_version": "codex-jsonl/1.0.0",
        "source_byte_count": 120,
        "source_line_count": 3,
        "candidate_count_upper_bound": 6,
        "gap_count_upper_bound": 3,
        "batch_count": 1,
        "publication_count": 2,
        "max_source_bytes": 4_194_304,
        "max_line_bytes": 1_048_576,
        "max_lines": 20_000,
        "max_excerpt_bytes": 8_192,
        "max_events_per_batch": 100,
        "max_batches": 1_024,
        "complete_transcript_included": False,
        "reasoning_items_included": False,
        "reviewer_egress_changed": False,
    }


def test_chat_user_attestation_is_closed_and_schema_bound() -> None:
    payload = {
        "schema": "yoetz.chat-user-attestation/1",
        "channel": "agent_attested_chat_instruction",
        "client_kind": "codex",
        "instruction_source": "explicit_current_chat_user",
        "pending_id": "a" * 64,
        "operation": "provider_credential_set",
        "danger_digest": "sha256:" + ("b" * 64),
        "target_digest": "sha256:" + ("c" * 64),
        "warning_acknowledged": True,
        "decision": "approve",
    }
    assert ChatUserAttestationModel.model_validate(payload).client_kind == "codex"
    validate_schema_instance("chat-user-attestation", "1.0.0", payload)
    with pytest.raises(ValidationError):
        ChatUserAttestationModel.model_validate({**payload, "extra": "forbidden"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"provider-secret", b"provider-secret"),
        (b"provider-secret\n", b"provider-secret"),
    ],
)
def test_bounded_stdin_secret_reads_directly_into_mutable_storage(
    raw: bytes, expected: bytes
) -> None:
    with patch("yoetz.cli.app.sys.stdin", SimpleNamespace(buffer=io.BytesIO(raw))):
        secret = _read_stdin_secret_for_test(32)
    assert isinstance(secret, bytearray)
    assert bytes(secret) == expected


@pytest.mark.parametrize("raw", [b"", b"a\nb", b"a\r", b"a\x00b", b"x" * 33])
def test_bounded_stdin_secret_rejects_invalid_or_oversized_input(raw: bytes) -> None:
    with (
        patch("yoetz.cli.app.sys.stdin", SimpleNamespace(buffer=io.BytesIO(raw))),
        pytest.raises(ValueError, match="provider_credential_invalid"),
    ):
        _read_stdin_secret_for_test(32)


def test_prepare_repository_grant_maps_privacy_snapshot_failure() -> None:
    async def fail_snapshot() -> object:
        raise RuntimeError("private internal detail")

    with patch(
        "yoetz.cli.privacy_setup.get_privacy_setup_snapshot",
        side_effect=fail_snapshot,
    ):
        result = CliRunner().invoke(
            app,
            ["consent", "prepare", "repository_privacy_grant", "--recipe", "private"],
        )
    assert result.exit_code == 2
    assert "elevated_bootstrap: repository_privacy_scope_unavailable" in result.stderr
    # The bare token alone left operators with nothing to run; the remediation half must follow it.
    assert "yoetz --privacy" in result.stderr
    assert "private internal detail" not in result.stderr


def test_catalog_prepare_hint_names_only_the_caller_supplied_provider_flags() -> None:
    catalog = cast(dict[str, Any], elevated.catalog_elevated())
    hints = {
        cast(str, entry["operation"]): cast(str, entry["prepare_hint"])
        for entry in cast(list[dict[str, Any]], catalog["operations"])
    }

    hint = hints["provider_credential_set"]
    for flag in (
        "--provider-id",
        "--model-id",
        "--endpoint-profile-id",
        "--endpoint-profile-version",
    ):
        assert flag in hint
    # Purpose, digests, and the repository commitment are derived by prepare; naming them sent
    # agents hunting for internals they cannot know.
    for derived in ("--purpose", "--scope-digest", "--purpose-digest"):
        assert derived not in hint
    assert hints["vault_initialize"] == "yoetz consent prepare vault_initialize"


def test_chat_user_authorize_consumes_exact_provider_request_and_wipes_input(
    tmp_path: Path,
) -> None:
    observed: list[bytes] = []

    async def complete(pending: Any, credential: bytearray) -> dict[str, object]:
        observed.append(bytes(credential))
        assert pending.operation == "provider_credential_set"
        return {"action": "set", "generation": 3, "outcome": "stored"}

    async def run() -> dict[str, Any]:
        with _patch_state(tmp_path):
            prepared = cast(
                dict[str, Any],
                elevated.prepare_elevated(
                    "provider_credential_set", provider_binding=_CHAT_PROVIDER_BINDING
                ),
            )
            pending = load_pending(_state=tmp_path)
            assert pending is not None
            secret = bytearray(b"chat-secret")
            with patch(
                "yoetz.cli.elevated._complete_provider_credential_supplied",
                side_effect=complete,
            ):
                result = cast(
                    dict[str, Any],
                    await elevated.authorize_elevated(
                        _chat_attestation(pending), provider_credential=secret
                    ),
                )
            assert bytes(secret) == b"\x00" * len(secret)
            assert prepared["pending"]["pending_id"] == pending.pending_id
            return result

    result = anyio.run(run)
    assert observed == [b"chat-secret"]
    assert result["authority_channel"] == "agent_attested_chat_instruction"
    assert result["outcome"] == "completed"
    validate_schema_instance("review-result", "5.0.0", result)
    assert load_pending(_state=tmp_path) is None
    assert "chat-secret" not in json.dumps(result)


def test_agent_chat_authorize_uses_real_supplied_credential_path(tmp_path: Path) -> None:
    observed: list[tuple[bytes, bytes, object]] = []

    async def privacy_snapshot() -> SimpleNamespace:
        return SimpleNamespace(
            bound_scope={
                "workspace_ref_commitment": _CHAT_PROVIDER_BINDING["repository_privacy_commitment"]
            }
        )

    async def ceremony(kind: object, target: object, **kwargs: object) -> ProviderCredentialResult:
        credential = cast(bytearray, kwargs["provider_credential"])
        reauthentication = cast(bytearray, kwargs["provider_reauthentication"])
        observed.append((bytes(credential), bytes(reauthentication), target))
        elevated.overwrite_secret_buffer(credential)
        elevated.overwrite_secret_buffer(reauthentication)
        return ProviderCredentialResult("set", 7, "stored")

    async def run() -> dict[str, Any]:
        with _patch_state(tmp_path):
            elevated.prepare_elevated(
                "provider_credential_set", provider_binding=_CHAT_PROVIDER_BINDING
            )
            pending = load_pending(_state=tmp_path)
            assert pending is not None
            secret = bytearray(b"chat-secret")
            with (
                patch(
                    "yoetz.cli.privacy_setup.get_privacy_setup_snapshot",
                    side_effect=privacy_snapshot,
                ),
                patch(
                    "yoetz.cli.elevated._load_auto_unlock_passphrase",
                    return_value=bytearray(b"local-reauth"),
                ),
                patch("yoetz.cli.elevated.run_human_ceremony", side_effect=ceremony),
            ):
                result = cast(
                    dict[str, Any],
                    await elevated.authorize_elevated(
                        _chat_attestation(pending), provider_credential=secret
                    ),
                )
            assert bytes(secret) == b"\x00" * len(secret)
            return result

    result = anyio.run(run)
    assert result["result"] == {"action": "set", "generation": 7, "outcome": "stored"}
    assert observed[0][0:2] == (b"chat-secret", b"local-reauth")
    assert (
        getattr(observed[0][2], "repository_privacy_commitment")
        == (_CHAT_PROVIDER_BINDING["repository_privacy_commitment"])
    )


def test_agent_chat_authorize_fails_closed_without_local_reauthentication(
    tmp_path: Path,
) -> None:
    async def privacy_snapshot() -> SimpleNamespace:
        return SimpleNamespace(
            bound_scope={
                "workspace_ref_commitment": _CHAT_PROVIDER_BINDING["repository_privacy_commitment"]
            }
        )

    async def run() -> None:
        with _patch_state(tmp_path):
            elevated.prepare_elevated(
                "provider_credential_set", provider_binding=_CHAT_PROVIDER_BINDING
            )
            pending = load_pending(_state=tmp_path)
            assert pending is not None
            secret = bytearray(b"chat-secret")
            with (
                patch(
                    "yoetz.cli.privacy_setup.get_privacy_setup_snapshot",
                    side_effect=privacy_snapshot,
                ),
                patch("yoetz.cli.elevated._load_auto_unlock_passphrase", return_value=None),
                patch("yoetz.cli.elevated.run_human_ceremony") as ceremony,
            ):
                with pytest.raises(ElevatedBootstrapError) as exc:
                    await elevated.authorize_elevated(
                        _chat_attestation(pending), provider_credential=secret
                    )
                assert exc.value.reason == "chat_user_reauthentication_unavailable"
                ceremony.assert_not_awaited()
            assert bytes(secret) == b"\x00" * len(secret)
            assert load_pending(_state=tmp_path) is None

    anyio.run(run)


def test_chat_user_authorize_denial_is_single_shot_for_repository_grant(tmp_path: Path) -> None:
    async def run() -> dict[str, Any]:
        grant = {
            "recipe": "assisted_review",
            "repository_privacy_commitment": "hmac-sha256:" + ("d" * 64),
            "authority_digest": "sha256:" + ("e" * 64),
        }
        with _patch_state(tmp_path):
            elevated.prepare_elevated("repository_privacy_grant", grant_binding=grant)
            pending = load_pending(_state=tmp_path)
            assert pending is not None
            with patch("yoetz.cli.elevated._complete_repository_privacy_grant") as complete:
                result = cast(
                    dict[str, Any],
                    await elevated.authorize_elevated(
                        _chat_attestation(pending, decision="deny", warning=False)
                    ),
                )
                complete.assert_not_awaited()
            return result

    result = anyio.run(run)
    assert result["outcome"] == "denied"
    assert result["authority_channel"] == "agent_attested_chat_instruction"
    assert load_pending(_state=tmp_path) is None


def test_chat_user_authorize_requires_advertised_capability_before_claim(tmp_path: Path) -> None:
    async def run() -> None:
        with _patch_state(tmp_path):
            elevated.prepare_elevated(
                "provider_credential_set", provider_binding=_CHAT_PROVIDER_BINDING
            )
            pending = load_pending(_state=tmp_path)
            assert pending is not None
            with patch("yoetz.cli.elevated.agent_chat_attestation_supported", return_value=False):
                with pytest.raises(ElevatedBootstrapError) as exc:
                    await elevated.authorize_elevated(_chat_attestation(pending))
                assert exc.value.reason == "agent_chat_attestation_unsupported"
            assert load_pending(_state=tmp_path) == pending

    anyio.run(run)


def test_chat_user_authorize_requires_one_warning_before_secret_ingress(tmp_path: Path) -> None:
    async def run() -> None:
        with _patch_state(tmp_path):
            elevated.prepare_elevated(
                "provider_credential_set", provider_binding=_CHAT_PROVIDER_BINDING
            )
            pending = load_pending(_state=tmp_path)
            assert pending is not None
            secret = bytearray(b"chat-secret")
            with patch("yoetz.cli.elevated._complete_provider_credential_supplied") as complete:
                with pytest.raises(ElevatedBootstrapError) as exc:
                    await elevated.authorize_elevated(
                        _chat_attestation(pending, warning=False), provider_credential=secret
                    )
                assert exc.value.reason == "chat_user_warning_required"
                complete.assert_not_awaited()
            assert bytes(secret) == b"\x00" * len(secret)
            assert load_pending(_state=tmp_path) == pending

    anyio.run(run)


def test_chat_user_authorize_rejects_invalid_attestation_as_bounded_failure() -> None:
    async def run() -> None:
        with pytest.raises(ElevatedBootstrapError) as exc:
            await elevated.authorize_elevated({"schema": "forged"})
        assert exc.value.reason == "chat_user_attestation_invalid"

    anyio.run(run)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("pending_id", "f" * 64),
        ("operation", "provider_credential_rotate"),
        ("danger_digest", "sha256:" + ("f" * 64)),
        ("target_digest", "sha256:" + ("f" * 64)),
    ],
)
def test_agent_chat_attestation_mismatch_does_not_consume_pending(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    async def run() -> None:
        with _patch_state(tmp_path):
            elevated.prepare_elevated(
                "provider_credential_set", provider_binding=_CHAT_PROVIDER_BINDING
            )
            pending = load_pending(_state=tmp_path)
            assert pending is not None
            attestation = _chat_attestation(pending)
            attestation[field] = replacement
            secret = bytearray(b"chat-secret")
            with pytest.raises(ElevatedBootstrapError) as exc:
                await elevated.authorize_elevated(attestation, provider_credential=secret)
            assert exc.value.reason == "chat_user_target_mismatch"
            assert bytes(secret) == b"\x00" * len(secret)
            assert load_pending(_state=tmp_path) == pending

    anyio.run(run)


def test_agent_chat_authorize_initializes_vault_and_rejects_missing_or_forbidden_credential(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        vault_state = tmp_path / "vault"
        with _patch_state(vault_state):
            elevated.prepare_elevated("vault_initialize")
            vault_pending = load_pending(_state=vault_state)
            assert vault_pending is not None
            with patch(
                "yoetz.cli.elevated._complete_vault_initialize_generated",
                return_value={"state": "ready", "reason": "succeeded"},
            ) as initialize:
                vault_result = await elevated.authorize_elevated(_chat_attestation(vault_pending))
            initialize.assert_awaited_once_with()
            assert vault_result["authority_channel"] == "agent_attested_chat_instruction"
            assert vault_result["result"] == {"state": "ready", "reason": "succeeded"}
            validate_schema_instance("review-result", "5.0.0", vault_result)
            assert load_pending(_state=vault_state) is None

        credential_state = tmp_path / "credential"
        with _patch_state(credential_state):
            elevated.prepare_elevated(
                "provider_credential_set", provider_binding=_CHAT_PROVIDER_BINDING
            )
            credential_pending = load_pending(_state=credential_state)
            assert credential_pending is not None
            with pytest.raises(ElevatedBootstrapError) as missing:
                await elevated.authorize_elevated(_chat_attestation(credential_pending))
            assert missing.value.reason == "provider_credential_required"
            assert load_pending(_state=credential_state) == credential_pending

        grant_state = tmp_path / "grant"
        grant = {
            "recipe": "assisted_review",
            "repository_privacy_commitment": "hmac-sha256:" + ("d" * 64),
            "authority_digest": "sha256:" + ("e" * 64),
        }
        with _patch_state(grant_state):
            elevated.prepare_elevated("repository_privacy_grant", grant_binding=grant)
            grant_pending = load_pending(_state=grant_state)
            assert grant_pending is not None
            secret = bytearray(b"forbidden-secret")
            with pytest.raises(ElevatedBootstrapError) as forbidden:
                await elevated.authorize_elevated(
                    _chat_attestation(grant_pending), provider_credential=secret
                )
            assert forbidden.value.reason == "provider_credential_forbidden"
            assert bytes(secret) == b"\x00" * len(secret)
            assert load_pending(_state=grant_state) == grant_pending

    anyio.run(run)


def test_repository_grant_requires_warning_without_consuming_pending(tmp_path: Path) -> None:
    async def run() -> None:
        grant = {
            "recipe": "assisted_review",
            "repository_privacy_commitment": "hmac-sha256:" + ("d" * 64),
            "authority_digest": "sha256:" + ("e" * 64),
        }
        with _patch_state(tmp_path):
            elevated.prepare_elevated("repository_privacy_grant", grant_binding=grant)
            pending = load_pending(_state=tmp_path)
            assert pending is not None
            with pytest.raises(ElevatedBootstrapError) as exc:
                await elevated.authorize_elevated(_chat_attestation(pending, warning=False))
            assert exc.value.reason == "chat_user_warning_required"
            assert load_pending(_state=tmp_path) == pending

    anyio.run(run)


def test_catalog_and_prepare_are_agent_safe(tmp_path: Path) -> None:
    with _patch_state(tmp_path):
        catalog = cast(dict[str, Any], elevated.catalog_elevated())
        prepared = cast(dict[str, Any], elevated.prepare_elevated("vault_initialize"))
    assert catalog["schema"] == "yoetz.consent.catalog/5"
    assert prepared["schema"] == "yoetz.elevated-bootstrap.prepare-result/5"
    assert prepared["pending"]["review_command"] == ["yoetz", "consent", "review"]
    validate_schema_instance("prepare-result", "5.0.0", prepared)
    rendered = json.dumps({"catalog": catalog, "prepared": prepared})
    for forbidden in (
        "approve_command",
        "confirmation_phrase",
        "passphrase_fd",
        "secret_fds",
    ):
        assert forbidden not in rendered


def test_import_publication_requires_service_prepared_preview(tmp_path: Path) -> None:
    with _patch_state(tmp_path):
        with pytest.raises(ElevatedBootstrapError) as caught:
            elevated.prepare_elevated("import_publication", target_digest="sha256:" + "a" * 64)
    assert caught.value.reason == "import_publication_preview_required"


def test_import_publication_authorization_is_exact_internal_and_one_use(tmp_path: Path) -> None:
    target = "sha256:" + "a" * 64

    async def run() -> dict[str, Any]:
        with _patch_state(tmp_path):
            pending = prepare_pending(
                "import_publication",
                target_digest=target,
                import_publication_preview=cast(Any, _import_preview(target)),
                _state=tmp_path,
            )
            projected = cast(dict[str, Any], elevated.status_elevated())
            assert projected["pending"]["import_publication_preview"] == _import_preview(target)
            result = cast(
                dict[str, Any],
                await elevated.authorize_elevated(_chat_attestation(pending)),
            )
            authorization = load_import_publication_authorization(target, _state=tmp_path)
            assert authorization is not None
            assert (
                load_import_publication_authorization("sha256:" + "b" * 64, _state=tmp_path) is None
            )
            consume_import_publication_authorization(authorization, _state=tmp_path)
            assert load_import_publication_authorization(target, _state=tmp_path) is None
            return result

    result = anyio.run(run)
    assert result["result"] == {
        "authorization_target_digest": target,
        "outcome": "authorized",
    }
    validate_schema_instance("review-result", "5.0.0", result)


def test_import_publication_denial_creates_no_authorization(tmp_path: Path) -> None:
    target = "sha256:" + "c" * 64

    async def run() -> None:
        with _patch_state(tmp_path):
            pending = prepare_pending(
                "import_publication",
                target_digest=target,
                import_publication_preview=cast(Any, _import_preview(target)),
                _state=tmp_path,
            )
            result = await elevated.authorize_elevated(
                _chat_attestation(pending, decision="deny", warning=False)
            )
            assert result["outcome"] == "denied"
            assert load_import_publication_authorization(target, _state=tmp_path) is None

    anyio.run(run)


def test_trusted_import_review_displays_the_structural_preview(tmp_path: Path) -> None:
    target = "sha256:" + "d" * 64
    console = _Console(b"deny")

    async def run() -> None:
        with _patch_state(tmp_path):
            prepare_pending(
                "import_publication",
                target_digest=target,
                import_publication_preview=cast(Any, _import_preview(target)),
                _state=tmp_path,
            )
            with (
                _patch_verified_presence(),
                patch("yoetz.cli.elevated.TrustedForegroundConsole", return_value=console),
            ):
                await elevated.review_elevated()

    anyio.run(run)
    rendered = "".join(console.output)
    assert "Import publication preview (structural JSON):" in rendered
    assert target in rendered
    assert '"complete_transcript_included":false' in rendered
    assert "source_bytes_base64" not in rendered


def test_review_result_rejects_unknown_or_unbounded_result_fields(tmp_path: Path) -> None:
    with _patch_state(tmp_path):
        elevated.prepare_elevated("vault_initialize")
        pending = load_pending(_state=tmp_path)
    assert pending is not None
    with pytest.raises(ElevatedBootstrapError) as exc:
        elevated._review_result(  # pyright: ignore[reportPrivateUsage]
            pending,
            outcome="completed",
            result={"provider_text": "unbounded"},
        )
    assert exc.value.reason == "result_invalid"
    assert "unbounded" not in str(exc.value)


@pytest.mark.parametrize(
    ("operation", "risk_class", "outcome", "result"),
    [
        ("vault_initialize", "secret_ingress", "completed", {"decision": "denied"}),
        (
            "vault_initialize",
            "secret_ingress",
            "completed",
            {"action": "set", "generation": 1, "outcome": "stored"},
        ),
        (
            "provider_credential_set",
            "secret_ingress",
            "completed",
            {"action": "rotate", "generation": 1, "outcome": "stored"},
        ),
        ("backup_execute", "review_only", "denied", {"decision": "denied"}),
        ("vault_initialize", "default_safe", "denied", {"decision": "denied"}),
        (
            "provider_credential_rotate",
            "secret_ingress",
            "denied",
            {"action": "rotate", "generation": 1, "outcome": "stored"},
        ),
    ],
)
def test_review_result_binds_operation_outcome_and_result_in_model_and_schema(
    operation: str,
    risk_class: str,
    outcome: str,
    result: dict[str, object],
) -> None:
    payload = {
        "schema": "yoetz.elevated-bootstrap.result/5",
        "pending_id": "a" * 64,
        "operation": operation,
        "risk_class": risk_class,
        "outcome": outcome,
        "danger_digest": f"sha256:{'b' * 64}",
        "authority_channel": "trusted_console_presence",
        "result": result,
    }
    with pytest.raises(ValidationError):
        ConsentReviewResultModel.model_validate(payload)
    with pytest.raises(SchemaInstanceInvalid):
        validate_schema_instance("review-result", "5.0.0", cast(Any, payload))


def test_review_approval_consumes_pending_and_returns_no_secret(tmp_path: Path) -> None:
    console = _Console()

    async def complete(_console: object, _pending: object) -> dict[str, object]:
        return {"state": "ready", "reason": "succeeded"}

    async def run() -> dict[str, Any]:
        with _patch_state(tmp_path):
            elevated.prepare_elevated("vault_initialize")
            with (
                _patch_verified_presence(),
                patch("yoetz.cli.elevated.TrustedForegroundConsole", return_value=console),
                patch("yoetz.cli.elevated._complete_approved", side_effect=complete),
            ):
                return cast(dict[str, Any], await elevated.review_elevated())

    result = anyio.run(run)
    assert result["schema"] == "yoetz.elevated-bootstrap.result/5"
    assert result["outcome"] == "completed"
    validate_schema_instance("review-result", "5.0.0", result)
    assert load_pending(_state=tmp_path) is None
    assert "human-entered-secret" not in json.dumps(result)


def test_review_denial_and_cancellation_are_single_shot(tmp_path: Path) -> None:
    async def deny() -> dict[str, Any]:
        with _patch_state(tmp_path):
            elevated.prepare_elevated("vault_initialize")
            with (
                _patch_verified_presence(),
                patch(
                    "yoetz.cli.elevated.TrustedForegroundConsole",
                    return_value=_Console(b"deny"),
                ),
            ):
                return cast(dict[str, Any], await elevated.review_elevated())

    denied = anyio.run(deny)
    assert denied["outcome"] == "denied"
    assert load_pending(_state=tmp_path) is None

    async def cancel() -> None:
        with _patch_state(tmp_path):
            elevated.prepare_elevated("vault_initialize")
            console = _Console()

            def interrupt(_prompt: str, _allowed: tuple[bytes, ...]) -> bytes:
                raise KeyboardInterrupt

            console.read_choice = interrupt
            with (
                _patch_verified_presence(),
                patch(
                    "yoetz.cli.elevated.TrustedForegroundConsole",
                    return_value=console,
                ),
            ):
                with pytest.raises(ElevatedBootstrapError) as exc:
                    await elevated.review_elevated()
                assert exc.value.reason == "review_cancelled"

    anyio.run(cancel)
    assert load_pending(_state=tmp_path) is None


def test_pty_like_console_cannot_authorize_without_os_presence(tmp_path: Path) -> None:
    async def run() -> None:
        with _patch_state(tmp_path):
            elevated.prepare_elevated("vault_initialize")
            with (
                patch(
                    "yoetz.cli.elevated.TrustedForegroundConsole",
                    return_value=_Console(b"approve"),
                ) as console,
                patch("yoetz.cli.elevated._complete_approved") as complete,
            ):
                with pytest.raises(ElevatedBootstrapError) as exc:
                    await elevated.review_elevated()
                assert exc.value.reason == "human_authority_unavailable"
                console.assert_not_called()
                complete.assert_not_called()

    anyio.run(run)
    assert load_pending(_state=tmp_path) is not None


@pytest.mark.parametrize(
    "arguments",
    [
        ["consent", "approve"],
        ["consent", "review", "--approve"],
        ["consent", "review", "--confirm", "forged"],
        ["consent", "review", "--danger-digest", "sha256:" + ("0" * 64)],
        ["consent", "review", "--passphrase-fd", "3"],
        ["consent", "review", "--pending-id", "forged"],
    ],
)
def test_forged_approval_arguments_fail_before_mutation(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    with _patch_state(tmp_path):
        elevated.prepare_elevated("vault_initialize")
        result = CliRunner().invoke(app, arguments)
        assert result.exit_code == 2
        assert load_pending(_state=tmp_path) is not None


def test_generated_initialization_secret_is_submitted_then_overwritten() -> None:
    generated = bytearray(b"g" * 64)
    observed: list[bytes] = []

    class _Store:
        def create_for_initialization(self) -> bytearray:
            return generated

    async def ceremony(
        _console: object,
        _kind: object,
        _target: object,
        **kwargs: object,
    ) -> VaultStateResult:
        supplied = cast(bytearray, kwargs["passphrase"])
        observed.append(bytes(supplied))
        return VaultStateResult("ready", "succeeded")

    async def run() -> dict[str, object]:
        with (
            patch("yoetz.cli.elevated._auto_unlock_store", return_value=_Store()),
            patch(
                "yoetz.cli.elevated.run_human_ceremony_on_terminal",
                side_effect=ceremony,
            ),
        ):
            return cast(
                dict[str, object],
                await elevated._complete_vault_initialize(  # pyright: ignore[reportPrivateUsage]
                    cast(TrustedForegroundConsole, _Console())
                ),
            )

    result = anyio.run(run)
    assert observed == [b"g" * 64]
    assert bytes(generated) == b"\x00" * 64
    assert result == {"state": "ready", "reason": "succeeded"}


def test_agent_generated_initialization_secret_never_enters_agent_result() -> None:
    generated = bytearray(b"z" * 64)
    observed: list[bytes] = []

    class _Store:
        def create_for_initialization(self) -> bytearray:
            return generated

    async def ceremony(
        _kind: object,
        _target: object,
        **kwargs: object,
    ) -> VaultStateResult:
        supplied = cast(bytearray, kwargs["passphrase"])
        observed.append(bytes(supplied))
        return VaultStateResult("ready", "succeeded")

    async def run() -> dict[str, object]:
        with (
            patch("yoetz.cli.elevated._auto_unlock_store", return_value=_Store()),
            patch("yoetz.cli.elevated.run_human_ceremony", side_effect=ceremony),
        ):
            return cast(
                dict[str, object],
                await elevated._complete_vault_initialize_generated(),  # pyright: ignore[reportPrivateUsage]
            )

    result = anyio.run(run)
    assert observed == [b"z" * 64]
    assert bytes(generated) == b"\x00" * 64
    assert result == {"state": "ready", "reason": "succeeded"}
    assert b"z" * 16 not in json.dumps(result).encode()


def test_agent_generated_rotation_uses_only_local_store_bytes_and_promotes() -> None:
    current = bytearray(b"c" * 64)
    replacement = bytearray(b"r" * 64)
    observed: list[tuple[bytes, bytes]] = []

    class _Store:
        promoted = False

        def load(self) -> bytearray:
            return current

        def stage_for_rotation(self) -> bytearray:
            return replacement

        def promote_staged_rotation(self) -> None:
            self.promoted = True

    store = _Store()

    async def ceremony(_kind: object, _target: object, **kwargs: object) -> VaultStateResult:
        observed.append(
            (
                bytes(cast(bytearray, kwargs["passphrase"])),
                bytes(cast(bytearray, kwargs["vault_rewrap_secret"])),
            )
        )
        return VaultStateResult("ready", "succeeded")

    async def run() -> dict[str, object]:
        with (
            patch("yoetz.cli.elevated._auto_unlock_store", return_value=store),
            patch("yoetz.cli.elevated.run_human_ceremony", side_effect=ceremony),
        ):
            return cast(
                dict[str, object],
                await elevated._complete_vault_passphrase_rotate_generated(),  # pyright: ignore[reportPrivateUsage]
            )

    result = anyio.run(run)
    assert observed == [(b"c" * 64, b"r" * 64)]
    assert store.promoted is True
    assert bytes(current) == b"\x00" * 64
    assert bytes(replacement) == b"\x00" * 64
    assert result == {"state": "ready", "reason": "succeeded"}
    assert b"c" * 16 not in json.dumps(result).encode()
    assert b"r" * 16 not in json.dumps(result).encode()


def _consumed_audit_events(tmp_path: Path) -> list[dict[str, Any]]:
    lines = audit_path(_state=tmp_path).read_text("utf-8").splitlines()
    events = [cast(dict[str, Any], json.loads(line)) for line in lines]
    return [event for event in events if event["event"] == "review_consumed"]


def test_agent_authorized_non_ready_vault_result_fails_before_approval(tmp_path: Path) -> None:
    """Issue #510: a non-ready ceremony result must never leave an approved consent record."""

    generated = bytearray(b"y" * 64)

    class _Store:
        def create_for_initialization(self) -> bytearray:
            return generated

    async def ceremony(_kind: object, _target: object, **_kwargs: object) -> VaultStateResult:
        return VaultStateResult("locked", "throttle_record_exists")

    async def run() -> None:
        with _patch_state(tmp_path):
            prepared = cast(dict[str, Any], elevated.prepare_elevated("vault_initialize"))
            pending = load_pending(_state=tmp_path)
            assert pending is not None
            with (
                patch("yoetz.cli.elevated._auto_unlock_store", return_value=_Store()),
                patch("yoetz.cli.elevated.run_human_ceremony", side_effect=ceremony),
            ):
                with pytest.raises(ElevatedBootstrapError) as exc:
                    await elevated.authorize_elevated(_chat_attestation(pending))
            assert exc.value.reason == "vault_result_throttle_record_exists"
            assert bytes(generated) == b"\x00" * 64
            assert load_pending(_state=tmp_path) is None
            consumed = _consumed_audit_events(tmp_path)
            assert [event["outcome"] for event in consumed] == ["failed"]
            assert consumed[0]["pending_id"] == pending.pending_id
            assert consumed[0]["failure_reason"] == "vault_result_throttle_record_exists"
            # Retry is explicit and exact: a fresh prepare rebinds the identical target, so
            # nothing about the originally authorized target needs reconstructing.
            reprepared = cast(dict[str, Any], elevated.prepare_elevated("vault_initialize"))
            assert reprepared["pending"]["target_digest"] == prepared["pending"]["target_digest"]

    anyio.run(run)


def test_non_ready_rotation_result_never_promotes_staged_secret() -> None:
    current = bytearray(b"c" * 64)
    replacement = bytearray(b"r" * 64)

    class _Store:
        promoted = False

        def load(self) -> bytearray:
            return current

        def stage_for_rotation(self) -> bytearray:
            return replacement

        def promote_staged_rotation(self) -> None:
            self.promoted = True

    store = _Store()

    async def ceremony(_kind: object, _target: object, **_kwargs: object) -> VaultStateResult:
        return VaultStateResult("locked", "credential_invalid")

    async def run() -> None:
        with (
            patch("yoetz.cli.elevated._auto_unlock_store", return_value=store),
            patch("yoetz.cli.elevated.run_human_ceremony", side_effect=ceremony),
        ):
            with pytest.raises(ElevatedBootstrapError) as exc:
                await elevated._complete_vault_passphrase_rotate_generated()  # pyright: ignore[reportPrivateUsage]
            assert exc.value.reason == "vault_result_credential_invalid"

    anyio.run(run)
    assert store.promoted is False, "a failed rewrap must leave the staged slot for restart"
    assert bytes(current) == b"\x00" * 64
    assert bytes(replacement) == b"\x00" * 64


def test_schema_rejected_result_is_bounded_and_never_recorded_approved(tmp_path: Path) -> None:
    """Result validation runs before the approval record exists, and stays a bounded token."""

    async def run() -> None:
        with _patch_state(tmp_path):
            elevated.prepare_elevated("vault_initialize")
            pending = load_pending(_state=tmp_path)
            assert pending is not None
            with patch(
                "yoetz.cli.elevated._complete_vault_initialize_generated",
                return_value={"state": "locked", "reason": "throttle_record_exists"},
            ):
                with pytest.raises(ElevatedBootstrapError) as exc:
                    await elevated.authorize_elevated(_chat_attestation(pending))
            assert exc.value.reason == "result_invalid"
            assert load_pending(_state=tmp_path) is None
            consumed = _consumed_audit_events(tmp_path)
            assert [event["outcome"] for event in consumed] == ["failed"]
            assert consumed[0]["failure_reason"] == "result_invalid"

    anyio.run(run)


def test_trusted_review_non_ready_vault_result_is_consumed_as_failed(tmp_path: Path) -> None:
    generated = bytearray(b"t" * 64)

    class _Store:
        def create_for_initialization(self) -> bytearray:
            return generated

    async def ceremony(
        _console: object, _kind: object, _target: object, **_kwargs: object
    ) -> VaultStateResult:
        return VaultStateResult("locked", "keyring_unavailable")

    async def run() -> None:
        with _patch_state(tmp_path):
            elevated.prepare_elevated("vault_initialize")
            with (
                _patch_verified_presence(),
                patch("yoetz.cli.elevated.TrustedForegroundConsole", return_value=_Console()),
                patch("yoetz.cli.elevated._auto_unlock_store", return_value=_Store()),
                patch(
                    "yoetz.cli.elevated.run_human_ceremony_on_terminal",
                    side_effect=ceremony,
                ),
            ):
                with pytest.raises(ElevatedBootstrapError) as exc:
                    await elevated.review_elevated()
            assert exc.value.reason == "vault_result_keyring_unavailable"
            assert load_pending(_state=tmp_path) is None
            consumed = _consumed_audit_events(tmp_path)
            assert [event["outcome"] for event in consumed] == ["failed"]
            assert consumed[0]["failure_reason"] == "vault_result_keyring_unavailable"

    anyio.run(run)


def test_authorize_cli_projects_the_bounded_vault_failure_reason(tmp_path: Path) -> None:
    generated = bytearray(b"q" * 64)

    class _Store:
        def create_for_initialization(self) -> bytearray:
            return generated

    async def ceremony(_kind: object, _target: object, **_kwargs: object) -> VaultStateResult:
        return VaultStateResult("locked", "throttle_record_exists")

    with _patch_state(tmp_path):
        elevated.prepare_elevated("vault_initialize")
        pending = load_pending(_state=tmp_path)
        assert pending is not None
        with (
            patch("yoetz.cli.elevated._auto_unlock_store", return_value=_Store()),
            patch("yoetz.cli.elevated.run_human_ceremony", side_effect=ceremony),
        ):
            result = CliRunner().invoke(
                app,
                [
                    "consent",
                    "authorize",
                    "--pending-id",
                    pending.pending_id,
                    "--operation",
                    "vault_initialize",
                    "--danger-digest",
                    pending.danger_digest,
                    "--target-digest",
                    pending.target_digest,
                    "--client-kind",
                    "codex",
                    "--decision",
                    "approve",
                    "--warning-acknowledged",
                ],
            )
    assert result.exit_code == 2
    assert "elevated_bootstrap: vault_result_throttle_record_exists" in result.stderr
    assert "yoetz consent prepare" in result.stderr
    assert "authorize_failed" not in result.stderr


def test_trusted_review_displays_exact_provider_binding(tmp_path: Path) -> None:
    binding = {
        "endpoint_profile_id": "ep",
        "endpoint_profile_version": "1",
        "model_id": "model",
        "provider_id": "provider",
        "purpose": "semantic-review",
        "purpose_digest": canonical_digest({"purpose": "semantic-review"}),
        "scope_digest": "sha256:" + ("b" * 64),
    }
    console = _Console(b"deny")

    async def run() -> None:
        with _patch_state(tmp_path):
            elevated.prepare_elevated("provider_credential_set", provider_binding=binding)
            with (
                _patch_verified_presence(),
                patch("yoetz.cli.elevated.TrustedForegroundConsole", return_value=console),
            ):
                await elevated.review_elevated()

    anyio.run(run)
    rendered = "".join(console.output)
    for key, value in binding.items():
        assert f"{key}: {value}" in rendered


def test_prepare_provider_binding_still_binds_exact_target(tmp_path: Path) -> None:
    binding = {
        "endpoint_profile_id": "ep",
        "endpoint_profile_version": "1",
        "model_id": "model",
        "provider_id": "provider",
        "purpose": "semantic-review",
        "purpose_digest": canonical_digest({"purpose": "semantic-review"}),
        "scope_digest": "sha256:" + ("b" * 64),
    }
    with _patch_state(tmp_path):
        payload = cast(
            dict[str, Any],
            elevated.prepare_elevated("provider_credential_rotate", provider_binding=binding),
        )
    # The pending digest is the exact digest the ceremony session will bind: one shape for
    # bound and unbound bindings, with the absent repository commitment present as null.
    expected = canonical_digest(
        {
            "action": "rotate",
            "endpoint_profile_id": "ep",
            "endpoint_profile_version": "1",
            "kind": "provider_credential",
            "model_id": "model",
            "provider_id": "provider",
            "purpose": "semantic-review",
            "purpose_digest": canonical_digest({"purpose": "semantic-review"}),
            "repository_privacy_commitment": None,
            "scope_digest": "sha256:" + ("b" * 64),
        }
    )
    assert payload["pending"]["target_digest"] == expected


@pytest.mark.parametrize(
    ("operation", "expected_action"),
    [
        ("provider_credential_set", "set"),
        ("provider_credential_rotate", "rotate"),
    ],
)
def test_provider_secret_ingress_uses_only_the_trusted_review_ceremony(
    operation: str,
    expected_action: str,
) -> None:
    binding = {
        "endpoint_profile_id": "ep",
        "endpoint_profile_version": "1",
        "model_id": "model",
        "provider_id": "provider",
        "purpose": "semantic-review",
        "purpose_digest": canonical_digest({"purpose": "semantic-review"}),
        "scope_digest": "sha256:" + ("b" * 64),
    }
    pending = elevated.PendingElevatedConsent(
        pending_id="a" * 64,
        operation=cast(Any, operation),
        risk_class="secret_ingress",
        danger_text="bounded danger",
        danger_digest="sha256:" + ("c" * 64),
        created_at_unix=1,
        expires_at_unix=901,
        target_digest="sha256:" + ("d" * 64),
        provider_binding=binding,
    )
    observed: list[tuple[object, object, dict[str, object]]] = []

    async def ceremony(
        console: object,
        kind: object,
        target: object,
        **kwargs: object,
    ) -> ProviderCredentialResult:
        observed.append((kind, target, kwargs))
        assert console is not None
        return ProviderCredentialResult(cast(Any, expected_action), 2, "stored")

    async def privacy_snapshot() -> SimpleNamespace:
        return SimpleNamespace(bound_scope={"workspace_ref_commitment": "hmac-sha256:" + "7" * 64})

    async def run() -> dict[str, object]:
        with (
            patch(
                "yoetz.cli.elevated.run_human_ceremony_on_terminal",
                side_effect=ceremony,
            ),
            patch(
                "yoetz.cli.privacy_setup.get_privacy_setup_snapshot",
                side_effect=privacy_snapshot,
            ),
        ):
            return cast(
                dict[str, object],
                await elevated._complete_provider_credential(  # pyright: ignore[reportPrivateUsage]
                    cast(TrustedForegroundConsole, _Console()), pending
                ),
            )

    result = anyio.run(run)
    assert result == {"action": expected_action, "generation": 2, "outcome": "stored"}
    assert len(observed) == 1
    assert observed[0][2] == {}


def _configured_provider() -> SimpleNamespace:
    return SimpleNamespace(
        provider=SimpleNamespace(
            provider_id="fireworks",
            model="accounts/fireworks/models/minimax-m3",
            endpoint_profile_id="fireworks-responses",
            endpoint_profile_version="1.0.0",
        )
    )


async def _bound_privacy_snapshot() -> SimpleNamespace:
    return SimpleNamespace(bound_scope={"workspace_ref_commitment": "hmac-sha256:" + "7" * 64})


@pytest.mark.parametrize(
    ("action", "function_name"),
    [("set", "set_provider_credential"), ("rotate", "rotate_provider_credential")],
)
def test_provider_credential_command_needs_no_flags_and_binds_the_installed_profile(
    action: str,
    function_name: str,
) -> None:
    """Seven mandatory options made the documented command unusable by hand."""

    from yoetz.service.vault import provider_credential_profile_binding

    observed: list[tuple[Any, bytearray | None, bytearray | None]] = []

    async def ceremony(
        target: Any,
        credential: bytearray | None = None,
        reauthentication: bytearray | None = None,
    ) -> ProviderCredentialResult:
        observed.append((target, credential, reauthentication))
        return ProviderCredentialResult(cast(Any, action), 4, "stored")

    with (
        patch("yoetz.config.load.load_config", return_value=_configured_provider()),
        patch(
            "yoetz.cli.privacy_setup.get_privacy_setup_snapshot",
            side_effect=_bound_privacy_snapshot,
        ),
        patch(
            "yoetz.cli.unlock.load_auto_unlock_reauthentication",
            return_value=bytearray(b"scoped-reauth"),
        ),
        patch(f"yoetz.cli.unlock.{function_name}", side_effect=ceremony),
    ):
        result = CliRunner().invoke(app, ["provider", "credential", action])

    assert result.exit_code == 0
    assert len(observed) == 1
    target, credential, reauthentication = observed[0]
    expected = provider_credential_profile_binding(
        "fireworks",
        "accounts/fireworks/models/minimax-m3",
        "fireworks-responses",
        "1.0.0",
    )
    assert target.action == action
    assert target.provider_id == expected.provider_id
    assert target.model_id == expected.model_id
    assert target.purpose == expected.purpose
    assert target.scope_digest == expected.authorization_scope_digest
    assert target.purpose_digest == expected.purpose_digest
    # Without this the daemon rejects the ceremony as unbound to the repository's privacy scope.
    assert target.repository_privacy_commitment == "hmac-sha256:" + "7" * 64
    assert credential is None
    # A Keychain-provisioned vault must not prompt for a passphrase the human never saw.
    assert reauthentication is not None
    assert bytes(reauthentication) == b"scoped-reauth"


def test_provider_credential_command_still_accepts_explicit_overrides() -> None:
    observed: list[Any] = []

    async def ceremony(
        target: Any,
        _credential: bytearray | None = None,
        _reauthentication: bytearray | None = None,
    ) -> ProviderCredentialResult:
        observed.append(target)
        return ProviderCredentialResult("set", 1, "stored")

    with (
        patch("yoetz.config.load.load_config", return_value=_configured_provider()),
        patch(
            "yoetz.cli.privacy_setup.get_privacy_setup_snapshot",
            side_effect=_bound_privacy_snapshot,
        ),
        patch("yoetz.cli.unlock.load_auto_unlock_reauthentication", return_value=None),
        patch("yoetz.cli.unlock.set_provider_credential", side_effect=ceremony),
    ):
        result = CliRunner().invoke(
            app,
            [
                "provider",
                "credential",
                "set",
                "--provider-id",
                "provider",
                "--model-id",
                "model",
                "--endpoint-profile-id",
                "ep",
                "--endpoint-profile-version",
                "1",
                "--purpose",
                "semantic-review",
                "--scope-digest",
                "sha256:" + ("b" * 64),
                "--purpose-digest",
                canonical_digest({"purpose": "semantic-review"}),
            ],
        )

    assert result.exit_code == 0
    assert len(observed) == 1
    assert observed[0].provider_id == "provider"
    assert observed[0].model_id == "model"
    assert observed[0].purpose == "semantic-review"
    assert observed[0].scope_digest == "sha256:" + ("b" * 64)


def test_provider_credential_command_without_a_configured_provider_names_the_setup_command() -> (
    None
):
    with (
        patch("yoetz.config.load.load_config", return_value=SimpleNamespace(provider=None)),
        patch("yoetz.cli.unlock.set_provider_credential") as ceremony,
    ):
        result = CliRunner().invoke(app, ["provider", "credential", "set"])

    assert result.exit_code == 2
    assert "provider_not_configured" in result.stderr
    assert "yoetz --set" in result.stderr
    ceremony.assert_not_called()


def test_provider_credential_command_without_privacy_scope_names_the_privacy_command() -> None:
    async def fail_snapshot() -> object:
        raise RuntimeError("private internal detail")

    with (
        patch("yoetz.config.load.load_config", return_value=_configured_provider()),
        patch(
            "yoetz.cli.privacy_setup.get_privacy_setup_snapshot",
            side_effect=fail_snapshot,
        ),
        patch("yoetz.cli.unlock.set_provider_credential") as ceremony,
    ):
        result = CliRunner().invoke(app, ["provider", "credential", "set"])

    assert result.exit_code == 2
    assert "repository_privacy_scope_unavailable" in result.stderr
    assert "yoetz --privacy" in result.stderr
    assert "private internal detail" not in result.stderr
    ceremony.assert_not_called()


def test_menu_credential_ceremony_supplies_the_scoped_reauthentication_secret() -> None:
    """A Keychain-provisioned vault must not ask the menu user for an unseen passphrase."""

    from yoetz.cli import menu

    observed: list[tuple[Any, bytearray | None, bytearray | None]] = []

    async def ceremony(
        target: Any,
        credential: bytearray | None = None,
        reauthentication: bytearray | None = None,
    ) -> ProviderCredentialResult:
        observed.append((target, credential, reauthentication))
        return ProviderCredentialResult("set", 2, "stored")

    with (
        patch("yoetz.cli.menu._ask", return_value="2"),
        patch("yoetz.config.load.load_config", return_value=_configured_provider()),
        patch(
            "yoetz.cli.privacy_setup.get_privacy_setup_snapshot",
            side_effect=_bound_privacy_snapshot,
        ),
        patch(
            "yoetz.cli.unlock.load_auto_unlock_reauthentication",
            return_value=bytearray(b"scoped-reauth"),
        ),
        patch("yoetz.cli.unlock.set_provider_credential", side_effect=ceremony),
    ):
        menu._provider_menu()  # pyright: ignore[reportPrivateUsage]

    assert len(observed) == 1
    _target, credential, reauthentication = observed[0]
    assert credential is None
    assert reauthentication is not None
    assert bytes(reauthentication) == b"scoped-reauth"
