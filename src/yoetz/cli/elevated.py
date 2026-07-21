"""CLI for founder-authorized elevated bootstrap (ADR-015)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, cast

from yoetz.cli.unlock import HumanCeremonyCliError, _overwrite, _verify_preview
from yoetz.protocol.canonical import JsonValue, canonical_digest
from yoetz.service.confidential_client import (
    ConfidentialClientError,
    HumanControlClient,
    HumanControlSession,
)
from yoetz.service.confidential_protocol import (
    AuthorizationRequiredPhase,
    ConfidentialSecretPurpose,
    EmptyVaultTarget,
    HumanCeremonyKind,
    HumanOpenTarget,
    HumanPhase,
    HumanResult,
    ProviderCredentialResult,
    ProviderCredentialTarget,
    SecretRequiredPhase,
    SelectAuthorizationSourceAction,
    VaultStateResult,
    validate_passphrase_buffer,
    validate_provider_credential_buffer,
)
from yoetz.service.elevated_bootstrap import (
    ElevatedBootstrapError,
    ElevatedOperation,
    PendingElevatedConsent,
    approve_pending,
    clear_pending,
    prepare_pending,
    projection_for_status,
    read_secret_fd,
    status_payload,
)

__all__ = [
    "approve_elevated",
    "prepare_elevated",
    "status_elevated",
]

_PASSPHRASE_MAX: Final = 1_024
_CREDENTIAL_MAX: Final = 8_192


def status_elevated() -> dict[str, JsonValue]:
    return status_payload()


def prepare_elevated(
    operation: ElevatedOperation,
    *,
    provider_binding: Mapping[str, str] | None = None,
) -> dict[str, JsonValue]:
    target_digest = _target_digest(operation, provider_binding)
    pending = prepare_pending(
        operation, target_digest=target_digest, provider_binding=provider_binding
    )
    return {
        "schema": "yoetz.elevated-bootstrap.prepare-result/1",
        "elevated_bootstrap": projection_for_status(pending),
    }


async def approve_elevated(
    *,
    pending_id: str,
    danger_digest: str,
    confirm: str,
    passphrase_fd: int | None = None,
    reauth_fd: int | None = None,
    credential_fd: int | None = None,
) -> dict[str, JsonValue]:
    pending = approve_pending(
        pending_id=pending_id, danger_digest=danger_digest, confirm=confirm
    )
    try:
        if pending.operation == "vault_initialize":
            if passphrase_fd is None:
                raise ElevatedBootstrapError("passphrase_fd_required")
            result = await _complete_vault_initialize(pending, passphrase_fd)
        else:
            if reauth_fd is None or credential_fd is None:
                raise ElevatedBootstrapError("credential_fds_required")
            result = await _complete_provider_credential(pending, reauth_fd, credential_fd)
        clear_pending()
        return {
            "schema": "yoetz.elevated-bootstrap.result/1",
            "pending_id": pending.pending_id,
            "operation": pending.operation,
            "outcome": "completed",
            "danger_digest": pending.danger_digest,
            "result": result,
        }
    except Exception:
        clear_pending()
        raise


def _target_digest(
    operation: ElevatedOperation, provider_binding: Mapping[str, str] | None
) -> str:
    if operation == "vault_initialize":
        return canonical_digest({"expected_mode": "uninitialized", "kind": "empty_vault"})
    assert provider_binding is not None
    return canonical_digest(
        {
            "action": "set",
            "endpoint_profile_id": provider_binding["endpoint_profile_id"],
            "endpoint_profile_version": provider_binding["endpoint_profile_version"],
            "kind": "provider_credential",
            "model_id": provider_binding["model_id"],
            "provider_id": provider_binding["provider_id"],
            "purpose": provider_binding["purpose"],
            "purpose_digest": provider_binding["purpose_digest"],
            "scope_digest": provider_binding["scope_digest"],
        }
    )


async def _complete_vault_initialize(
    pending: PendingElevatedConsent, passphrase_fd: int
) -> dict[str, JsonValue]:
    del pending
    secret = read_secret_fd(passphrase_fd, maximum=_PASSPHRASE_MAX)
    try:
        validate_passphrase_buffer(memoryview(secret))
    except Exception as exc:
        _overwrite(secret)
        raise ElevatedBootstrapError("secret_rejected") from exc
    target = EmptyVaultTarget(expected_mode="uninitialized")
    client = HumanControlClient()
    try:
        session = await client.open(HumanCeremonyKind.VAULT_INITIALIZE, target)
        async with session:
            try:
                result = await _drive_with_fd_secrets(
                    session,
                    HumanCeremonyKind.VAULT_INITIALIZE,
                    target,
                    {ConfidentialSecretPurpose.VAULT_INITIALIZE: secret},
                )
            except BaseException:
                await _cancel_quietly(session)
                raise
    except ConfidentialClientError as exc:
        raise ElevatedBootstrapError(f"ceremony_{exc.reason}") from exc
    except HumanCeremonyCliError as exc:
        raise ElevatedBootstrapError(exc.reason) from exc
    finally:
        await client.close()
        _overwrite(secret)
    if type(result) is not VaultStateResult:
        raise ElevatedBootstrapError("result_invalid")
    return {"state": result.state, "reason": result.reason}


async def _complete_provider_credential(
    pending: PendingElevatedConsent, reauth_fd: int, credential_fd: int
) -> dict[str, JsonValue]:
    if pending.provider_binding is None:
        raise ElevatedBootstrapError("provider_binding_required")
    binding = pending.provider_binding
    reauth = read_secret_fd(reauth_fd, maximum=_PASSPHRASE_MAX)
    credential = read_secret_fd(credential_fd, maximum=_CREDENTIAL_MAX)
    try:
        validate_passphrase_buffer(memoryview(reauth))
        validate_provider_credential_buffer(memoryview(credential))
    except Exception as exc:
        _overwrite(reauth)
        _overwrite(credential)
        raise ElevatedBootstrapError("secret_rejected") from exc
    target = ProviderCredentialTarget(
        action="set",
        provider_id=binding["provider_id"],
        model_id=binding["model_id"],
        endpoint_profile_id=binding["endpoint_profile_id"],
        endpoint_profile_version=binding["endpoint_profile_version"],
        purpose=binding["purpose"],
        scope_digest=binding["scope_digest"],
        purpose_digest=binding["purpose_digest"],
    )
    client = HumanControlClient()
    try:
        session = await client.open(HumanCeremonyKind.PROVIDER_CREDENTIAL_SET, target)
        async with session:
            try:
                result = await _drive_with_fd_secrets(
                    session,
                    HumanCeremonyKind.PROVIDER_CREDENTIAL_SET,
                    target,
                    {
                        ConfidentialSecretPurpose.PROVIDER_REAUTHENTICATION: reauth,
                        ConfidentialSecretPurpose.PROVIDER_CREDENTIAL: credential,
                    },
                )
            except BaseException:
                await _cancel_quietly(session)
                raise
    except ConfidentialClientError as exc:
        raise ElevatedBootstrapError(f"ceremony_{exc.reason}") from exc
    finally:
        await client.close()
        _overwrite(reauth)
        _overwrite(credential)
    if type(result) is not ProviderCredentialResult:
        raise ElevatedBootstrapError("result_invalid")
    return {
        "action": result.action,
        "generation": result.stored_generation,
        "outcome": result.activation_status,
    }


async def _drive_with_fd_secrets(
    session: HumanControlSession,
    kind: HumanCeremonyKind,
    target: HumanOpenTarget,
    secrets_by_purpose: Mapping[ConfidentialSecretPurpose, bytearray],
) -> HumanResult:
    current: HumanPhase | HumanResult = session.opened.phase
    _verify_preview(kind, target, session)
    for _ in range(8):
        if type(current) in {VaultStateResult, ProviderCredentialResult}:
            return cast(HumanResult, current)
        if type(current) is SecretRequiredPhase:
            purpose = current.binding.purpose
            source = secrets_by_purpose.get(purpose)
            if source is None:
                raise ElevatedBootstrapError("secret_purpose_unsatisfied")
            secret_client = session._secret_client()  # pyright: ignore[reportPrivateUsage]
            token = session._session_token()  # pyright: ignore[reportPrivateUsage]
            buffer = bytearray(source)
            try:
                await secret_client.send_once(current.binding, buffer, token)
            finally:
                _overwrite(buffer)
            current = await session.wait_phase_or_result()
            continue
        if type(current) is AuthorizationRequiredPhase:
            if "secret_reauthentication" not in current.available_sources:
                raise ElevatedBootstrapError("reauthentication_unavailable")
            await session.send_action(SelectAuthorizationSourceAction("secret_reauthentication"))
            current = await session.wait_phase_or_result()
            continue
        raise ElevatedBootstrapError("phase_unsupported")
    raise ElevatedBootstrapError("ceremony_steps_exhausted")


async def _cancel_quietly(session: HumanControlSession) -> None:
    try:
        await session.cancel()
    except (ConfidentialClientError, OSError, RuntimeError):
        pass
