"""Trusted-console review for non-default actions (ADR-015 / ADR-016)."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Protocol

from yoetz.cli.trusted_console import TrustedConsoleError, TrustedForegroundConsole
from yoetz.cli.unlock import (
    HumanCeremonyCliError,
    _overwrite,  # pyright: ignore[reportPrivateUsage]
    _run_human_ceremony_on_terminal,  # pyright: ignore[reportPrivateUsage]
)
from yoetz.protocol.canonical import JsonValue, canonical_digest
from yoetz.protocol.consent import ConsentPrepareResultModel, ConsentReviewResultModel
from yoetz.service.confidential_client import ConfidentialClientError
from yoetz.service.confidential_protocol import (
    EmptyVaultTarget,
    HumanCeremonyKind,
    ProviderCredentialResult,
    ProviderCredentialTarget,
    VaultStateResult,
)
from yoetz.service.elevated_bootstrap import (
    ElevatedBootstrapError,
    ElevatedOperation,
    PendingElevatedConsent,
    catalog_payload,
    claim_pending_for_review,
    complete_review,
    operation_spec,
    prepare_pending,
    projection_for_status,
    status_payload,
)

__all__ = [
    "catalog_elevated",
    "prepare_elevated",
    "review_elevated",
    "status_elevated",
]


class _AutoUnlockStore(Protocol):
    def create_for_initialization(self) -> bytearray: ...


def status_elevated() -> dict[str, JsonValue]:
    return status_payload()


def catalog_elevated() -> dict[str, JsonValue]:
    return catalog_payload()


def prepare_elevated(
    operation: ElevatedOperation,
    *,
    provider_binding: Mapping[str, str] | None = None,
    target_digest: str | None = None,
) -> dict[str, JsonValue]:
    digest = _target_digest(operation, provider_binding, target_digest)
    pending = prepare_pending(operation, target_digest=digest, provider_binding=provider_binding)
    model = ConsentPrepareResultModel.model_validate(
        {
            "schema": "yoetz.elevated-bootstrap.prepare-result/2",
            "pending": projection_for_status(pending),
        }
    )
    return model.model_dump(mode="json", by_alias=True)


def _render_review(console: TrustedForegroundConsole, pending: PendingElevatedConsent) -> None:
    detail = (
        "Yoetz trusted consent review\n"
        f"Operation: {pending.operation}\n"
        f"Risk class: {pending.risk_class}\n"
        f"Pending ID: {pending.pending_id}\n"
        f"Danger digest: {pending.danger_digest}\n"
        f"Target digest: {pending.target_digest}\n"
        f"Expires at (Unix): {pending.expires_at_unix}\n"
    )
    if pending.provider_binding is not None:
        detail += "Provider binding:\n"
        for key in sorted(pending.provider_binding, key=str.encode):
            detail += f"  {key}: {pending.provider_binding[key]}\n"
    console.write(detail + pending.danger_text + "\n")


def _review_result(
    pending: PendingElevatedConsent,
    *,
    outcome: str,
    result: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    model = ConsentReviewResultModel.model_validate(
        {
            "schema": "yoetz.elevated-bootstrap.result/2",
            "pending_id": pending.pending_id,
            "operation": pending.operation,
            "risk_class": pending.risk_class,
            "outcome": outcome,
            "danger_digest": pending.danger_digest,
            "result": dict(result),
        }
    )
    return model.model_dump(mode="json", by_alias=True)


async def review_elevated() -> dict[str, JsonValue]:
    """Review and consume one pending operation solely on a trusted foreground console."""

    pending: PendingElevatedConsent | None = None
    consumed = False
    try:
        with TrustedForegroundConsole() as console:
            pending = claim_pending_for_review()
            _render_review(console, pending)
            selected = console.read_choice("Decision [approve/deny]: ", (b"approve", b"deny"))
            if selected == b"deny":
                complete_review(pending, outcome="denied")
                consumed = True
                return _review_result(
                    pending,
                    outcome="denied",
                    result={"decision": "denied"},
                )
            if pending.expires_at_unix <= int(time.time()):
                complete_review(pending, outcome="expired")
                consumed = True
                raise ElevatedBootstrapError("pending_expired")
            result = await _complete_approved(console, pending)
            complete_review(pending, outcome="approved")
            consumed = True
            return _review_result(pending, outcome="completed", result=result)
    except TrustedConsoleError as exc:
        if pending is not None and not consumed:
            _consume_failed_review(pending, "cancelled")
        if exc.reason == "trusted_console_required":
            raise ElevatedBootstrapError("trusted_console_required") from exc
        raise ElevatedBootstrapError("review_cancelled") from exc
    except KeyboardInterrupt as exc:
        if pending is not None and not consumed:
            _consume_failed_review(pending, "cancelled")
        raise ElevatedBootstrapError("review_cancelled") from exc
    except BaseException:
        if pending is not None and not consumed:
            _consume_failed_review(pending, "failed")
        raise


def _consume_failed_review(pending: PendingElevatedConsent, outcome: str) -> None:
    try:
        complete_review(pending, outcome=outcome)
    except ElevatedBootstrapError:
        # Preserve the original bounded failure. A claim that could not be removed remains
        # fail-closed and prevents reuse or a second pending request.
        pass


async def _complete_approved(
    console: TrustedForegroundConsole,
    pending: PendingElevatedConsent,
) -> dict[str, JsonValue]:
    if pending.operation == "vault_initialize":
        return await _complete_vault_initialize(console)
    if pending.operation in {"provider_credential_set", "provider_credential_rotate"}:
        return await _complete_provider_credential(console, pending)
    raise ElevatedBootstrapError("operation_not_implemented")


def _auto_unlock_store() -> _AutoUnlockStore:
    from yoetz.adapters.keys.os_keyring import AutoUnlockPassphraseStore
    from yoetz.config.load import load_config
    from yoetz.config.paths import bundle_root

    config = load_config({}, os.environ, None)
    return AutoUnlockPassphraseStore(bundle_root(_data_dir=config.storage.data_dir))


async def _complete_vault_initialize(
    console: TrustedForegroundConsole,
) -> dict[str, JsonValue]:
    from yoetz.adapters.keys.os_keyring import OSKeyringError

    target = EmptyVaultTarget(expected_mode="uninitialized")
    generated: bytearray | None = None
    try:
        store = _auto_unlock_store()
        try:
            generated = store.create_for_initialization()
        except OSKeyringError as exc:
            if exc.reason != "unsupported":
                raise ElevatedBootstrapError(f"auto_unlock_{exc.reason}") from exc
            # The backend was known unavailable before any write. The existing local-human
            # passphrase ceremony remains the only permitted fallback.
            result = await _run_human_ceremony_on_terminal(
                console,
                HumanCeremonyKind.VAULT_INITIALIZE,
                target,
            )
        else:
            result = await _run_human_ceremony_on_terminal(
                console,
                HumanCeremonyKind.VAULT_INITIALIZE,
                target,
                passphrase=generated,
            )
    except ConfidentialClientError as exc:
        raise ElevatedBootstrapError(f"ceremony_{exc.reason}") from exc
    except HumanCeremonyCliError as exc:
        raise ElevatedBootstrapError(exc.reason) from exc
    finally:
        if generated is not None:
            _overwrite(generated)
    if type(result) is not VaultStateResult:
        raise ElevatedBootstrapError("result_invalid")
    return {"state": result.state, "reason": result.reason}


async def _complete_provider_credential(
    console: TrustedForegroundConsole,
    pending: PendingElevatedConsent,
) -> dict[str, JsonValue]:
    if pending.provider_binding is None:
        raise ElevatedBootstrapError("provider_binding_required")
    binding = pending.provider_binding
    action = "set" if pending.operation == "provider_credential_set" else "rotate"
    kind = (
        HumanCeremonyKind.PROVIDER_CREDENTIAL_SET
        if action == "set"
        else HumanCeremonyKind.PROVIDER_CREDENTIAL_ROTATE
    )
    target = ProviderCredentialTarget(
        action=action,
        provider_id=binding["provider_id"],
        model_id=binding["model_id"],
        endpoint_profile_id=binding["endpoint_profile_id"],
        endpoint_profile_version=binding["endpoint_profile_version"],
        purpose=binding["purpose"],
        scope_digest=binding["scope_digest"],
        purpose_digest=binding["purpose_digest"],
    )
    try:
        result = await _run_human_ceremony_on_terminal(console, kind, target)
    except ConfidentialClientError as exc:
        raise ElevatedBootstrapError(f"ceremony_{exc.reason}") from exc
    except HumanCeremonyCliError as exc:
        raise ElevatedBootstrapError(exc.reason) from exc
    if type(result) is not ProviderCredentialResult:
        raise ElevatedBootstrapError("result_invalid")
    return {
        "action": result.action,
        "generation": result.stored_generation,
        "outcome": result.activation_status,
    }


def _target_digest(
    operation: ElevatedOperation,
    provider_binding: Mapping[str, str] | None,
    target_digest: str | None,
) -> str:
    spec = operation_spec(operation)
    if not spec.implemented:
        raise ElevatedBootstrapError("operation_not_implemented")
    if operation == "vault_initialize":
        return canonical_digest({"expected_mode": "uninitialized", "kind": "empty_vault"})
    if operation in {"provider_credential_set", "provider_credential_rotate"}:
        if provider_binding is None:
            raise ElevatedBootstrapError("provider_binding_required")
        action = "set" if operation == "provider_credential_set" else "rotate"
        return canonical_digest(
            {
                "action": action,
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
    if spec.requires_target_digest_arg:
        if target_digest is None:
            raise ElevatedBootstrapError("target_digest_required")
        return target_digest
    raise ElevatedBootstrapError("operation_invalid")
