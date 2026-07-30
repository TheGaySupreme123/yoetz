"""Trusted consent-review CLI driver vectors (ADR-015/016)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import anyio
import pytest
from typer.testing import CliRunner

from yoetz.cli import elevated
from yoetz.cli.app import app
from yoetz.cli.trusted_console import TrustedConsoleError, TrustedForegroundConsole
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.schemas import validate_schema_instance
from yoetz.service.confidential_protocol import ProviderCredentialResult, VaultStateResult
from yoetz.service.elevated_bootstrap import ElevatedBootstrapError, load_pending


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


def test_catalog_and_prepare_are_agent_safe(tmp_path: Path) -> None:
    with _patch_state(tmp_path):
        catalog = cast(dict[str, Any], elevated.catalog_elevated())
        prepared = cast(dict[str, Any], elevated.prepare_elevated("vault_initialize"))
    assert catalog["schema"] == "yoetz.consent.catalog/2"
    assert prepared["schema"] == "yoetz.elevated-bootstrap.prepare-result/2"
    assert prepared["pending"]["review_command"] == ["yoetz", "consent", "review"]
    validate_schema_instance("prepare-result", "2.0.0", prepared)
    rendered = json.dumps({"catalog": catalog, "prepared": prepared})
    for forbidden in (
        "approve_command",
        "confirmation_phrase",
        "passphrase_fd",
        "secret_fds",
    ):
        assert forbidden not in rendered


def test_review_approval_consumes_pending_and_returns_no_secret(tmp_path: Path) -> None:
    console = _Console()

    async def complete(_console: object, _pending: object) -> dict[str, object]:
        return {"state": "ready", "reason": "succeeded"}

    async def run() -> dict[str, Any]:
        with _patch_state(tmp_path):
            elevated.prepare_elevated("vault_initialize")
            with (
                patch("yoetz.cli.elevated.TrustedForegroundConsole", return_value=console),
                patch("yoetz.cli.elevated._complete_approved", side_effect=complete),
            ):
                return cast(dict[str, Any], await elevated.review_elevated())

    result = anyio.run(run)
    assert result["schema"] == "yoetz.elevated-bootstrap.result/2"
    assert result["outcome"] == "completed"
    validate_schema_instance("review-result", "2.0.0", result)
    assert load_pending(_state=tmp_path) is None
    assert "human-entered-secret" not in json.dumps(result)


def test_review_denial_and_cancellation_are_single_shot(tmp_path: Path) -> None:
    async def deny() -> dict[str, Any]:
        with _patch_state(tmp_path):
            elevated.prepare_elevated("vault_initialize")
            with patch(
                "yoetz.cli.elevated.TrustedForegroundConsole",
                return_value=_Console(b"deny"),
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
            with patch(
                "yoetz.cli.elevated.TrustedForegroundConsole",
                return_value=console,
            ):
                with pytest.raises(ElevatedBootstrapError) as exc:
                    await elevated.review_elevated()
                assert exc.value.reason == "review_cancelled"

    anyio.run(cancel)
    assert load_pending(_state=tmp_path) is None


def test_untrusted_or_headless_console_fails_without_consuming_pending(tmp_path: Path) -> None:
    class _Headless:
        def __enter__(self) -> None:
            raise TrustedConsoleError("trusted_console_required")

        def __exit__(self, *_args: object) -> None:
            pass

    async def run() -> None:
        with _patch_state(tmp_path):
            elevated.prepare_elevated("vault_initialize")
            with patch("yoetz.cli.elevated.TrustedForegroundConsole", return_value=_Headless()):
                with pytest.raises(ElevatedBootstrapError) as exc:
                    await elevated.review_elevated()
                assert exc.value.reason == "trusted_console_required"

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
                "yoetz.cli.elevated._run_human_ceremony_on_terminal",
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
            with patch("yoetz.cli.elevated.TrustedForegroundConsole", return_value=console):
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

    async def run() -> dict[str, object]:
        with patch(
            "yoetz.cli.elevated._run_human_ceremony_on_terminal",
            side_effect=ceremony,
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
