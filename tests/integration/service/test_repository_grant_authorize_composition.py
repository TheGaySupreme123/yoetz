"""Positive repository-privacy-grant authorization over the real composition (#519).

The defect this locks against: an exact prepared `repository_privacy_grant` was authorized, the
durable policy transition committed, the real confidential server sent the terminal result — and
a lost close confirmation collapsed the whole ceremony to
`elevated_bootstrap: repository_privacy_grant_failed` while `yoetz provider status` already
reported the grant effective. These tests run real agent attestation, the real ordinary-control
propose path, and the real YZH1/YZS1 decide ceremony against the production daemon composition,
mocking only the OS keyring backend and the configured provider binding.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

import yoetz.service.daemon as daemon_module
from integration.service.test_consent_vault_initialize_composition import (
    _approve_attestation,  # pyright: ignore[reportPrivateUsage]
    _approved_store,  # pyright: ignore[reportPrivateUsage]
    _MemoryKeyring,  # pyright: ignore[reportPrivateUsage]
    _production_daemon,  # pyright: ignore[reportPrivateUsage]
    runtime_directory,  # noqa: F401 - imported pytest fixture  # pyright: ignore[reportUnusedImport]
)
from yoetz.cli import elevated
from yoetz.cli.privacy_setup import (
    PrivacySetupSnapshot,
    build_candidate_policy,
    get_privacy_setup_snapshot,
    recipe_answers,
)
from yoetz.domain.privacy import ProviderBinding
from yoetz.service.confidential_protocol import ServerCloseEnvelope
from yoetz.service.daemon import ServiceDaemon
from yoetz.service.elevated_bootstrap import load_pending, repository_grant_binding


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_EXTERNAL_BINDING = ProviderBinding(
    "fireworks",
    "accounts/fireworks/models/minimax-m3",
    "fireworks-responses",
    "1.0.0",
    "external",
)


async def _authorize_current_pending(consent_state: Path) -> dict[str, Any]:
    pending = load_pending(_state=consent_state)
    assert pending is not None
    return cast(
        dict[str, Any],
        await asyncio.wait_for(
            elevated.authorize_elevated(_approve_attestation(pending)), timeout=30
        ),
    )


async def _initialize_vault(consent_state: Path) -> None:
    elevated.prepare_elevated("vault_initialize")
    result = await _authorize_current_pending(consent_state)
    assert result["result"] == {"state": "ready", "reason": "succeeded"}


async def _prepared_grant_snapshot(consent_state: Path) -> PrivacySetupSnapshot:
    snapshot = await get_privacy_setup_snapshot()
    commitment = snapshot.bound_scope.get("workspace_ref_commitment")
    assert type(commitment) is str
    assert snapshot.grant_state == "missing"
    candidate = build_candidate_policy(
        snapshot.composed_policy,
        recipe_answers("expanded_review", snapshot.composed_policy, _EXTERNAL_BINDING),
        now=datetime.now(UTC),
    )
    elevated.prepare_elevated(
        "repository_privacy_grant",
        grant_binding=repository_grant_binding(
            recipe="expanded_review",
            repository_privacy_commitment=commitment,
            authority_digest=snapshot.authority_digest,
            current_policy=snapshot.composed_policy,
            candidate_policy=candidate,
        ),
    )
    return snapshot


def _audit_lines(consent_state: Path) -> list[str]:
    return (
        (consent_state / "elevated-bootstrap" / "elevated-bootstrap-audit.jsonl")
        .read_text("utf-8")
        .splitlines()
    )


def _assert_granted_once(consent_state: Path, snapshot_after: PrivacySetupSnapshot) -> None:
    assert snapshot_after.grant_state == "granted"
    assert load_pending(_state=consent_state) is None
    audit = _audit_lines(consent_state)
    approved = [line for line in audit if '"outcome":"approved"' in line]
    # Vault initialization plus exactly one grant approval; nothing was applied twice.
    assert len(approved) == 2
    assert not any('"outcome":"failed"' in line for line in audit)


async def _run_grant_flow(
    tmp_path: Path,
    *,
    authorize_patches: Any = None,
) -> None:
    tmp_path.chmod(0o700)
    consent_state = tmp_path / "consent"
    consent_state.mkdir(mode=0o700)
    backend = _MemoryKeyring()
    store = _approved_store(tmp_path / "data", backend)

    daemon: ServiceDaemon = await _production_daemon(tmp_path, pristine=True)
    await daemon.start()
    serving = asyncio.create_task(daemon.serve())
    try:
        with (
            patch("yoetz.service.elevated_bootstrap.state_dir", return_value=consent_state),
            patch("yoetz.cli.elevated._auto_unlock_store", return_value=store),
            patch(
                "yoetz.cli.elevated._load_auto_unlock_passphrase",
                side_effect=lambda: store.load(),
            ),
            patch(
                "yoetz.cli.privacy_setup.configured_bindings",
                return_value=(_EXTERNAL_BINDING, None),
            ),
        ):
            await _initialize_vault(consent_state)
            await _prepared_grant_snapshot(consent_state)
            if authorize_patches is None:
                result = await _authorize_current_pending(consent_state)
            else:
                with authorize_patches:
                    result = await _authorize_current_pending(consent_state)

            assert result["outcome"] == "completed"
            assert result["authority_channel"] == "agent_attested_chat_instruction"
            assert result["result"] == {"recipe": "expanded_review", "outcome": "granted"}
            snapshot_after = await get_privacy_setup_snapshot()
            _assert_granted_once(consent_state, snapshot_after)
    finally:
        await daemon.stop()
        await asyncio.wait_for(serving, timeout=10)


@pytest.mark.anyio
async def test_agent_authorized_repository_grant_completes_with_truthful_result(
    tmp_path: Path,
    runtime_directory: Path,  # noqa: F811 - imported pytest fixture
) -> None:
    """#519 acceptance: prepare, exact agent authorize, real client/server, truthful result."""

    await _run_grant_flow(tmp_path)


@pytest.mark.anyio
async def test_lost_close_confirmation_after_committed_grant_recovers_the_stored_result(
    tmp_path: Path,
    runtime_directory: Path,  # noqa: F811 - imported pytest fixture
) -> None:
    """#519 acceptance: the close frame is lost after the durable commit and the sent result;
    authorize recovers the stored terminal result exactly once instead of reporting failure."""

    original_write = daemon_module._write_human_envelope  # pyright: ignore[reportPrivateUsage]

    async def drop_completed_close(stream: object, envelope: object) -> None:
        # The exact injection: the durable transition committed, the result frame was written,
        # and the correlated "completed" close never reaches the client (the daemon then closes
        # the connection, so the client observes the ambiguous end-of-stream).
        if type(envelope) is ServerCloseEnvelope and envelope.outcome == "completed":
            return
        await original_write(cast(Any, stream), envelope)

    await _run_grant_flow(
        tmp_path,
        authorize_patches=patch.object(
            daemon_module, "_write_human_envelope", drop_completed_close
        ),
    )
