"""Trusted-console review and chat-user authorize for non-default actions (ADR-015/016/#164)."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, Protocol, cast

from yoetz.cli.trusted_console import TrustedConsoleError, TrustedForegroundConsole
from yoetz.cli.unlock import (
    HumanCeremonyCliError,
    overwrite_secret_buffer,
    run_human_ceremony,
    run_human_ceremony_on_terminal,
)
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.chat_user_authority import (
    ChatUserAttestationModel,
    agent_chat_attestation_supported,
)
from yoetz.protocol.consent import (
    ConsentPrepareResultModel,
    ConsentReviewResultModel,
    RepositoryPrivacyRecipe,
)
from yoetz.service.confidential_client import ConfidentialClientError
from yoetz.service.confidential_protocol import (
    EmptyVaultTarget,
    HumanCeremonyKind,
    ProviderCredentialResult,
    ProviderCredentialTarget,
    VaultStateResult,
    human_target_json,
)
from yoetz.service.elevated_bootstrap import (
    ElevatedBootstrapError,
    ElevatedOperation,
    PendingElevatedConsent,
    catalog_payload,
    claim_pending_for_review,
    complete_review,
    grant_target_digest,
    load_pending,
    operation_spec,
    prepare_pending,
    projection_for_status,
    record_import_publication_authorization,
    status_payload,
)

__all__ = [
    "authorize_elevated",
    "catalog_elevated",
    "prepare_elevated",
    "review_elevated",
    "status_elevated",
]


class _AutoUnlockStore(Protocol):
    def stage_for_initialization(self) -> bytearray: ...

    def promote_staged_initialization(self) -> None: ...

    def discard_staged_initialization(self) -> None: ...

    def slot_report(self) -> Mapping[str, str]: ...

    def load(self) -> bytearray | None: ...

    def stage_for_rotation(self) -> bytearray: ...

    def promote_staged_rotation(self) -> None: ...


def status_elevated() -> dict[str, JsonValue]:
    return status_payload()


def catalog_elevated() -> dict[str, JsonValue]:
    return catalog_payload()


def prepare_elevated(
    operation: ElevatedOperation,
    *,
    provider_binding: Mapping[str, str] | None = None,
    grant_binding: Mapping[str, str] | None = None,
    target_digest: str | None = None,
) -> dict[str, JsonValue]:
    digest = _target_digest(operation, provider_binding, grant_binding, target_digest)
    pending = prepare_pending(
        operation,
        target_digest=digest,
        provider_binding=provider_binding,
        grant_binding=grant_binding,
    )
    model = ConsentPrepareResultModel.model_validate(
        {
            "schema": "yoetz.elevated-bootstrap.prepare-result/5",
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
    if pending.import_publication_preview is not None:
        preview = canonical_encode(dict(pending.import_publication_preview)).decode("utf-8")
        detail += f"Import publication preview (structural JSON):\n{preview}\n"
    console.write(detail + pending.danger_text + "\n")


def _review_result(
    pending: PendingElevatedConsent,
    *,
    outcome: str,
    result: Mapping[str, JsonValue],
    authority_channel: Literal[
        "trusted_console_presence", "agent_attested_chat_instruction"
    ] = "trusted_console_presence",
) -> dict[str, JsonValue]:
    try:
        model = ConsentReviewResultModel.model_validate(
            {
                "schema": "yoetz.elevated-bootstrap.result/5",
                "pending_id": pending.pending_id,
                "operation": pending.operation,
                "risk_class": pending.risk_class,
                "outcome": outcome,
                "danger_digest": pending.danger_digest,
                "authority_channel": authority_channel,
                "result": dict(result),
            }
        )
    except (TypeError, ValueError) as exc:
        raise ElevatedBootstrapError("result_invalid") from exc
    return model.model_dump(mode="json", by_alias=True)


def _validated_vault_success(result: object) -> dict[str, JsonValue]:
    """Admit only the exact schema-admitted success; project a bounded failure otherwise."""

    if type(result) is not VaultStateResult:
        raise ElevatedBootstrapError("result_invalid")
    if result.state != "ready" or result.reason != "succeeded":
        raise ElevatedBootstrapError(f"vault_result_{result.reason}")
    return {"state": result.state, "reason": result.reason}


def _require_action_bound_user_presence() -> None:
    """Fail closed until a production action-bound UserPresencePort is installed."""

    # ADR-008 explicitly excludes TTY, same-UID, and unlocked-session signals from human
    # authority.  The packaged runtime currently advertises no verified UserPresencePort cell, so
    # accepting the console decision here would let an automated pseudo-terminal self-authorize.
    raise ElevatedBootstrapError("human_authority_unavailable")


async def review_elevated() -> dict[str, JsonValue]:
    """Review one pending operation after independently verified OS user presence."""

    pending: PendingElevatedConsent | None = None
    consumed = False
    try:
        _require_action_bound_user_presence()
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
            # Validate the projected result before the durable approval record exists: a
            # result the schema does not admit must consume this review as failed.
            payload = _review_result(pending, outcome="completed", result=result)
            complete_review(pending, outcome="approved")
            consumed = True
            return payload
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
    except BaseException as exc:
        if pending is not None and not consumed:
            _consume_failed_review(pending, "failed", _bounded_failure_reason(exc))
        raise


def _bounded_failure_reason(error: BaseException) -> str | None:
    return error.reason if type(error) is ElevatedBootstrapError else None


def _consume_failed_review(
    pending: PendingElevatedConsent, outcome: str, failure_reason: str | None = None
) -> None:
    try:
        complete_review(pending, outcome=outcome, failure_reason=failure_reason)
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
    if pending.operation == "vault_passphrase_rotate":
        return await _complete_vault_passphrase_rotate_generated()
    if pending.operation in {"provider_credential_set", "provider_credential_rotate"}:
        return await _complete_provider_credential(console, pending)
    if pending.operation == "repository_privacy_grant":
        raise ElevatedBootstrapError("repository_privacy_grant_requires_yoetz_privacy")
    if pending.operation == "import_publication":
        authorization = record_import_publication_authorization(pending)
        return {
            "authorization_target_digest": authorization.target_digest,
            "outcome": "authorized",
        }
    raise ElevatedBootstrapError("operation_not_implemented")


async def authorize_elevated(
    attestation: ChatUserAttestationModel | Mapping[str, object],
    *,
    provider_credential: bytearray | None = None,
) -> dict[str, JsonValue]:
    """Complete one prepared consent from an exact agent-attested chat instruction (#164)."""

    try:
        model = (
            attestation
            if type(attestation) is ChatUserAttestationModel
            else ChatUserAttestationModel.model_validate(attestation)
        )
    except (TypeError, ValueError) as exc:
        raise ElevatedBootstrapError("chat_user_attestation_invalid") from exc
    pending: PendingElevatedConsent | None = None
    consumed = False
    try:
        if not agent_chat_attestation_supported(model.client_kind, model.instruction_source):
            raise ElevatedBootstrapError("agent_chat_attestation_unsupported")
        if model.channel != "agent_attested_chat_instruction":
            raise ElevatedBootstrapError("chat_user_attestation_invalid")
        observed = load_pending()
        if observed is None:
            raise ElevatedBootstrapError("pending_absent")
        spec = operation_spec(observed.operation)
        if not spec.agent_chat_authorize_allowed:
            raise ElevatedBootstrapError("chat_user_operation_forbidden")
        if (
            model.pending_id != observed.pending_id
            or model.operation != observed.operation
            or model.danger_digest != observed.danger_digest
            or model.target_digest != observed.target_digest
        ):
            raise ElevatedBootstrapError("chat_user_target_mismatch")
        if model.decision == "approve" and not model.warning_acknowledged:
            raise ElevatedBootstrapError("chat_user_warning_required")
        credential_operation = observed.operation in {
            "provider_credential_set",
            "provider_credential_rotate",
        }
        if model.decision == "approve" and credential_operation:
            if provider_credential is None:
                raise ElevatedBootstrapError("provider_credential_required")
        elif provider_credential is not None:
            raise ElevatedBootstrapError("provider_credential_forbidden")
        pending = claim_pending_for_review()
        if pending != observed:
            raise ElevatedBootstrapError("pending_tampered")
        if pending.expires_at_unix <= int(time.time()):
            complete_review(pending, outcome="expired")
            consumed = True
            raise ElevatedBootstrapError("pending_expired")
        if model.decision == "deny":
            complete_review(pending, outcome="denied")
            consumed = True
            return _review_result(
                pending,
                outcome="denied",
                result={"decision": "denied"},
                authority_channel="agent_attested_chat_instruction",
            )
        if pending.operation == "vault_initialize":
            result = await _complete_vault_initialize_generated()
        elif pending.operation == "vault_passphrase_rotate":
            result = await _complete_vault_passphrase_rotate_generated()
        elif pending.operation in {"provider_credential_set", "provider_credential_rotate"}:
            assert provider_credential is not None
            result = await _complete_provider_credential_supplied(pending, provider_credential)
        elif pending.operation == "repository_privacy_grant":
            result = await _complete_repository_privacy_grant(pending)
        elif pending.operation == "import_publication":
            authorization = record_import_publication_authorization(pending)
            result = {
                "authorization_target_digest": authorization.target_digest,
                "outcome": "authorized",
            }
        else:
            raise ElevatedBootstrapError("chat_user_operation_forbidden")
        # Validate the projected result before the durable approval record exists: a
        # result the schema does not admit must consume this review as failed.
        payload = _review_result(
            pending,
            outcome="completed",
            result=result,
            authority_channel="agent_attested_chat_instruction",
        )
        complete_review(pending, outcome="approved")
        consumed = True
        return payload
    except BaseException as exc:
        if pending is not None and not consumed:
            _consume_failed_review(pending, "failed", _bounded_failure_reason(exc))
        raise
    finally:
        if provider_credential is not None:
            overwrite_secret_buffer(provider_credential)


def _auto_unlock_store() -> _AutoUnlockStore:
    from yoetz.adapters.keys.os_keyring import AutoUnlockPassphraseStore
    from yoetz.config.load import load_config
    from yoetz.config.paths import bundle_root

    config = load_config({}, os.environ, None)
    return AutoUnlockPassphraseStore(bundle_root(_data_dir=config.storage.data_dir))


async def _service_vault_mode() -> str | None:
    """Return the live service vault mode, or ``None`` when it cannot be proven."""

    from yoetz.ports.control import ControlClientKind, ControlError
    from yoetz.service.client import connect_service

    try:
        client = await connect_service(ControlClientKind.CLI, workspace_locator=None)
        try:
            status = await client.service_status()
        finally:
            await client.close()
    except ControlError, OSError, TypeError, ValueError:
        return None
    return status.vault_mode


async def _discard_provably_orphaned_staged_initialization(store: _AutoUnlockStore) -> None:
    """Remove a staged-initialization entry only with proof no vault envelope needs it.

    An uninitialized vault has no envelope the staged secret could unlock, so the exact entry
    created by an earlier authorized attempt is deleted with verified read-back. Any other or
    unprovable service state keeps the entry for proof-based restart reconciliation; deleting
    it there could destroy the only copy of a committed vault's passphrase.
    """

    if store.slot_report().get("staged_initialization") != "present":
        return
    if await _service_vault_mode() == "uninitialized":
        store.discard_staged_initialization()


async def _complete_vault_initialize(
    console: TrustedForegroundConsole,
) -> dict[str, JsonValue]:
    from yoetz.adapters.keys.os_keyring import OSKeyringError

    target = EmptyVaultTarget(expected_mode="uninitialized")
    store = _auto_unlock_store()
    generated: bytearray | None = None
    staged = False
    try:
        try:
            try:
                await _discard_provably_orphaned_staged_initialization(store)
                generated = store.stage_for_initialization()
            except OSKeyringError as exc:
                if exc.reason != "unsupported":
                    raise ElevatedBootstrapError(f"auto_unlock_{exc.reason}") from exc
                # The backend was known unavailable before any write. The existing local-human
                # passphrase ceremony remains the only permitted fallback.
                result = await run_human_ceremony_on_terminal(
                    console,
                    HumanCeremonyKind.VAULT_INITIALIZE,
                    target,
                )
                return _validated_vault_success(result)
            staged = True
            result = await run_human_ceremony_on_terminal(
                console,
                HumanCeremonyKind.VAULT_INITIALIZE,
                target,
                passphrase=bytearray(generated),
            )
            validated = _validated_vault_success(result)
        except ConfidentialClientError as exc:
            raise ElevatedBootstrapError(f"ceremony_{exc.reason}") from exc
        except HumanCeremonyCliError as exc:
            raise ElevatedBootstrapError(exc.reason) from exc
    except BaseException:
        if staged:
            await _cleanup_failed_staged_initialization(store)
        raise
    finally:
        if generated is not None:
            overwrite_secret_buffer(generated)
    _promote_after_committed_initialization(store)
    return validated


async def _complete_vault_initialize_generated() -> dict[str, JsonValue]:
    """Generate and submit a scoped secret locally without any agent-visible secret channel."""

    from yoetz.adapters.keys.os_keyring import OSKeyringError

    store = _auto_unlock_store()
    generated: bytearray | None = None
    staged = False
    try:
        try:
            try:
                await _discard_provably_orphaned_staged_initialization(store)
                generated = store.stage_for_initialization()
            except OSKeyringError as exc:
                raise ElevatedBootstrapError(f"auto_unlock_{exc.reason}") from exc
            staged = True
            result = await run_human_ceremony(
                HumanCeremonyKind.VAULT_INITIALIZE,
                EmptyVaultTarget(expected_mode="uninitialized"),
                passphrase=bytearray(generated),
            )
            validated = _validated_vault_success(result)
        except ConfidentialClientError as exc:
            raise ElevatedBootstrapError(f"ceremony_{exc.reason}") from exc
        except HumanCeremonyCliError as exc:
            raise ElevatedBootstrapError(exc.reason) from exc
    except BaseException:
        if staged:
            await _cleanup_failed_staged_initialization(store)
        raise
    finally:
        if generated is not None:
            overwrite_secret_buffer(generated)
    _promote_after_committed_initialization(store)
    return validated


async def _cleanup_failed_staged_initialization(store: _AutoUnlockStore) -> None:
    """Best-effort failure atomicity for the same-attempt staged credential.

    Runs only under an already-propagating ceremony failure: discard the staged entry when the
    service proves the vault is still uninitialized, and otherwise (ambiguous outcome, service
    unreachable, or discard failure) keep it — the slot is durable, typed in
    ``yoetz service auto-unlock status``, and reconciled by proof at the next unlock or retry.
    Never masks the original failure.
    """

    try:
        await _discard_provably_orphaned_staged_initialization(store)
    except Exception:
        pass


def _promote_after_committed_initialization(store: _AutoUnlockStore) -> None:
    """Promote the staged credential after the validated ready result.

    The vault has committed and activated, so a promotion failure must not fail the completed
    operation: the staged entry remains the sole candidate that unlocks the envelope and restart
    reconciliation promotes it by proof.
    """

    try:
        store.promote_staged_initialization()
    except Exception:
        pass


async def _complete_vault_passphrase_rotate_generated() -> dict[str, JsonValue]:
    """Rotate using only locally loaded/generated keyring bytes and structural output."""

    from yoetz.adapters.keys.os_keyring import OSKeyringError

    current: bytearray | None = None
    replacement: bytearray | None = None
    try:
        store = _auto_unlock_store()
        current = store.load()
        if current is None:
            raise ElevatedBootstrapError("chat_user_reauthentication_unavailable")
        try:
            replacement = store.stage_for_rotation()
        except OSKeyringError as exc:
            raise ElevatedBootstrapError(f"auto_unlock_{exc.reason}") from exc
        result = await run_human_ceremony(
            HumanCeremonyKind.VAULT_PASSPHRASE_ROTATE,
            EmptyVaultTarget(expected_mode="passphrase"),
            passphrase=bytearray(current),
            vault_rewrap_secret=bytearray(replacement),
        )
        # Promote only after the service reports the exact completed rewrap. On any failed or
        # ambiguous outcome the staged slot intentionally remains for restart reconciliation.
        validated = _validated_vault_success(result)
        store.promote_staged_rotation()
        return validated
    except ConfidentialClientError as exc:
        raise ElevatedBootstrapError(f"ceremony_{exc.reason}") from exc
    except HumanCeremonyCliError as exc:
        raise ElevatedBootstrapError(exc.reason) from exc
    finally:
        if current is not None:
            overwrite_secret_buffer(current)
        if replacement is not None:
            overwrite_secret_buffer(replacement)


async def _complete_provider_credential(
    console: TrustedForegroundConsole,
    pending: PendingElevatedConsent,
) -> dict[str, JsonValue]:
    action = "set" if pending.operation == "provider_credential_set" else "rotate"
    kind = (
        HumanCeremonyKind.PROVIDER_CREDENTIAL_SET
        if action == "set"
        else HumanCeremonyKind.PROVIDER_CREDENTIAL_ROTATE
    )
    target = await _provider_credential_target(
        pending,
        # Pending records prepared before repository binding was introduced remain usable only
        # through the stronger trusted-console review. Agent-attested chat always requires the
        # commitment to have been bound at prepare time.
        allow_legacy_repository_binding=True,
    )
    try:
        result = await run_human_ceremony_on_terminal(console, kind, target)
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


async def _complete_provider_credential_supplied(
    pending: PendingElevatedConsent,
    provider_credential: bytearray,
) -> dict[str, JsonValue]:
    if pending.provider_binding is None:
        raise ElevatedBootstrapError("provider_binding_required")
    target = await _provider_credential_target(
        pending,
        allow_legacy_repository_binding=False,
    )
    action = "set" if pending.operation == "provider_credential_set" else "rotate"
    kind = (
        HumanCeremonyKind.PROVIDER_CREDENTIAL_SET
        if action == "set"
        else HumanCeremonyKind.PROVIDER_CREDENTIAL_ROTATE
    )
    reauth = _load_auto_unlock_passphrase()
    if reauth is None:
        raise ElevatedBootstrapError("chat_user_reauthentication_unavailable")
    # Copy: run_human_ceremony overwrites supplied buffers in its finally.
    credential = bytearray(provider_credential)
    try:
        result = await run_human_ceremony(
            kind,
            target,
            provider_credential=credential,
            provider_reauthentication=reauth,
        )
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


async def _provider_credential_target(
    pending: PendingElevatedConsent,
    *,
    allow_legacy_repository_binding: bool,
) -> ProviderCredentialTarget:
    if pending.provider_binding is None:
        raise ElevatedBootstrapError("provider_binding_required")
    binding = pending.provider_binding
    action = "set" if pending.operation == "provider_credential_set" else "rotate"
    from yoetz.cli.privacy_setup import get_privacy_setup_snapshot
    from yoetz.ports.control import ControlError

    try:
        snapshot = await get_privacy_setup_snapshot()
    except (ControlError, OSError, ValueError) as exc:
        raise ElevatedBootstrapError("repository_privacy_scope_unavailable") from exc
    repository_commitment = binding.get("repository_privacy_commitment")
    if repository_commitment is None and allow_legacy_repository_binding:
        repository_commitment = snapshot.bound_scope.get("workspace_ref_commitment")
    if type(repository_commitment) is not str:
        raise ElevatedBootstrapError("repository_privacy_scope_unavailable")
    observed_commitment = snapshot.bound_scope.get("workspace_ref_commitment")
    if observed_commitment != repository_commitment:
        raise ElevatedBootstrapError("chat_user_target_mismatch")
    return ProviderCredentialTarget(
        action=action,
        provider_id=binding["provider_id"],
        model_id=binding["model_id"],
        endpoint_profile_id=binding["endpoint_profile_id"],
        endpoint_profile_version=binding["endpoint_profile_version"],
        purpose=binding["purpose"],
        scope_digest=binding["scope_digest"],
        purpose_digest=binding["purpose_digest"],
        repository_privacy_commitment=repository_commitment,
    )


def _load_auto_unlock_passphrase() -> bytearray | None:
    from yoetz.adapters.keys.os_keyring import AutoUnlockPassphraseStore
    from yoetz.config.load import load_config
    from yoetz.config.paths import bundle_root

    try:
        config = load_config({}, os.environ, None)
        return AutoUnlockPassphraseStore(bundle_root(_data_dir=config.storage.data_dir)).load()
    except Exception:
        return None


async def _complete_repository_privacy_grant(
    pending: PendingElevatedConsent,
) -> dict[str, JsonValue]:
    if pending.grant_binding is None:
        raise ElevatedBootstrapError("grant_binding_required")
    recipe = pending.grant_binding["recipe"]
    expected_commitment = pending.grant_binding["repository_privacy_commitment"]
    expected_authority_digest = pending.grant_binding["authority_digest"]
    from yoetz.cli.privacy_control import decide_policy
    from yoetz.cli.privacy_setup import (
        build_candidate_policy,
        configured_bindings,
        get_privacy_setup_snapshot,
        propose_privacy_candidate,
        recipe_answers,
    )
    from yoetz.ports.control import ControlError

    try:
        snapshot = await get_privacy_setup_snapshot()
    except (ControlError, OSError, ValueError) as exc:
        raise ElevatedBootstrapError("repository_privacy_scope_unavailable") from exc
    observed = snapshot.bound_scope.get("workspace_ref_commitment")
    if type(observed) is not str or observed != expected_commitment:
        raise ElevatedBootstrapError("chat_user_target_mismatch")
    if snapshot.authority_digest != expected_authority_digest:
        raise ElevatedBootstrapError("chat_user_target_mismatch")
    try:
        external, _local = configured_bindings()
        answers = recipe_answers(
            cast(RepositoryPrivacyRecipe, recipe),
            snapshot.composed_policy,
            external,
        )
        candidate = build_candidate_policy(snapshot.composed_policy, answers, now=datetime.now(UTC))
        proposal_id = await propose_privacy_candidate(candidate, snapshot.authority_digest)
    except (ControlError, OSError, TypeError, ValueError) as exc:
        raise ElevatedBootstrapError("repository_privacy_grant_failed") from exc
    if proposal_id is None:
        return {"recipe": recipe, "outcome": "tightened"}
    passphrase = _load_auto_unlock_passphrase()
    if passphrase is None:
        raise ElevatedBootstrapError("chat_user_reauthentication_unavailable")
    try:
        decision_result = await decide_policy(
            proposal_id, decision="approve", passphrase=passphrase
        )
    except (HumanCeremonyCliError, ConfidentialClientError, OSError, ValueError) as exc:
        raise ElevatedBootstrapError("repository_privacy_grant_failed") from exc
    from yoetz.service.confidential_protocol import PrivacyDecisionResult

    if type(decision_result) is not PrivacyDecisionResult or decision_result.status != "committed":
        raise ElevatedBootstrapError("repository_privacy_grant_failed")
    return {"recipe": recipe, "outcome": "granted"}


def _target_digest(
    operation: ElevatedOperation,
    provider_binding: Mapping[str, str] | None,
    grant_binding: Mapping[str, str] | None,
    target_digest: str | None,
) -> str:
    spec = operation_spec(operation)
    if not spec.implemented:
        raise ElevatedBootstrapError("operation_not_implemented")
    if operation == "vault_initialize":
        return canonical_digest({"expected_mode": "uninitialized", "kind": "empty_vault"})
    if operation == "vault_passphrase_rotate":
        return canonical_digest(human_target_json(EmptyVaultTarget(expected_mode="passphrase")))
    if operation in {"provider_credential_set", "provider_credential_rotate"}:
        if provider_binding is None:
            raise ElevatedBootstrapError("provider_binding_required")
        # One digest shape for both bound and legacy unbound bindings, so the digest shown at
        # review time is the digest the ceremony session will actually bind.
        target = ProviderCredentialTarget(
            action="set" if operation == "provider_credential_set" else "rotate",
            provider_id=provider_binding["provider_id"],
            model_id=provider_binding["model_id"],
            endpoint_profile_id=provider_binding["endpoint_profile_id"],
            endpoint_profile_version=provider_binding["endpoint_profile_version"],
            purpose=provider_binding["purpose"],
            scope_digest=provider_binding["scope_digest"],
            purpose_digest=provider_binding["purpose_digest"],
            repository_privacy_commitment=provider_binding.get("repository_privacy_commitment"),
        )
        return target.target_digest()
    if operation == "repository_privacy_grant":
        if grant_binding is None:
            raise ElevatedBootstrapError("grant_binding_required")
        return grant_target_digest(grant_binding)
    if operation == "import_publication":
        raise ElevatedBootstrapError("import_publication_preview_required")
    if spec.requires_target_digest_arg:
        if target_digest is None:
            raise ElevatedBootstrapError("target_digest_required")
        return target_digest
    raise ElevatedBootstrapError("operation_invalid")
