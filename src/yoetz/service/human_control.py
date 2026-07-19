"""Trusted foreground coordination for structural human-control ceremonies."""

from __future__ import annotations

import math
import secrets
from asyncio import Lock, Task, create_task
from dataclasses import dataclass
from typing import Final, Literal, Protocol, cast

from yoetz.ports.clock import ClockPort
from yoetz.ports.secret_memory import (
    HumanAuthorizationProof,
    SecretHandle,
    SecretPurpose,
    UserPresencePort,
)
from yoetz.service.confidential_protocol import (
    AuthorizationRequiredPhase,
    CancelAction,
    ClientActionEnvelope,
    ClientOpenEnvelope,
    ConfidentialSecretPurpose,
    DecisionAction,
    DecisionRequiredPhase,
    HumanCeremonyBinding,
    HumanCeremonyKind,
    HumanPhase,
    HumanPreview,
    HumanResult,
    IdleRelockPolicyResult,
    IdleRelockPolicyTarget,
    KeyringRetryPhase,
    KeyringRetryResult,
    PortableRecoveryResult,
    PortableRecoveryTarget,
    PrivacyDecisionResult,
    PrivacyPendingTarget,
    ProviderCredentialResult,
    ProviderCredentialTarget,
    RetryAction,
    SecretIngressBinding,
    SecretRequiredPhase,
    SelectAuthorizationSourceAction,
    ServerCloseEnvelope,
    ServerOpenedEnvelope,
    ServerPhaseEnvelope,
    ServerResultEnvelope,
    VaultStateResult,
    monotonic_milliseconds,
    new_binding_expiry_ms,
)
from yoetz.service.unlock import UnlockChallenge, UnlockCoordinator, UnlockError

__all__ = ["HumanControlError", "HumanControlService"]

_HUMAN_CONTROL_REASONS: Final = frozenset(
    {
        "binding_expired",
        "cancelled",
        "closed",
        "internal_error",
        "kind_forbidden",
        "phase_invalid",
        "presence_unavailable",
        "reauthentication_unavailable",
        "replay",
        "secret_rejected",
        "stale_generation",
        "state_forbidden",
        "target_invalid",
    }
)


class HumanControlError(Exception):
    """Bounded human-control failure without preview, proof, or secret detail."""

    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        if type(reason) is not str or reason not in _HUMAN_CONTROL_REASONS:
            raise TypeError("human_control_reason_invalid")
        self.reason = reason
        super().__init__(reason)


class _ServiceInstance(Protocol):
    @property
    def instance_id(self) -> str: ...

    @property
    def generation(self) -> int: ...


class _Lifecycle(Protocol):
    @property
    def instance(self) -> _ServiceInstance: ...


class _VaultView(Protocol):
    @property
    def mode(self) -> object: ...

    @property
    def generation(self) -> int: ...


class _SecretIngress(Protocol):
    async def accept_once(self, expected_binding: SecretIngressBinding) -> SecretHandle: ...

    async def cancel_pending(self) -> None: ...

    async def close(self) -> None: ...


class _HumanEffects(Protocol):
    """Narrow composition seam for maintenance/privacy/provider-owned mutations."""

    async def prepare(
        self, request: ClientOpenEnvelope
    ) -> tuple[HumanPreview, str, int | None]: ...

    async def complete_portable_recovery(
        self, target: PortableRecoveryTarget, secret: SecretHandle
    ) -> PortableRecoveryResult: ...

    async def store_provider_credential(
        self,
        target: ProviderCredentialTarget,
        secret: SecretHandle,
        proof: HumanAuthorizationProof,
        now_monotonic: float,
    ) -> ProviderCredentialResult: ...

    async def decide_privacy(
        self,
        target: PrivacyPendingTarget,
        decision: Literal["approve", "deny"],
        proof: HumanAuthorizationProof | None,
        now_monotonic: float,
    ) -> PrivacyDecisionResult: ...

    async def change_idle_relock_policy(
        self,
        target: IdleRelockPolicyTarget,
        proof: HumanAuthorizationProof,
        now_monotonic: float,
    ) -> IdleRelockPolicyResult: ...

    async def deny_idle_relock_policy(
        self,
        target: IdleRelockPolicyTarget,
        now_monotonic: float,
    ) -> IdleRelockPolicyResult: ...


@dataclass(slots=True)
class _Ceremony:
    request: ClientOpenEnvelope
    binding: HumanCeremonyBinding
    preview: HumanPreview
    phase: HumanPhase
    next_step: int
    unlock_challenge: UnlockChallenge | None = None
    secret_binding: SecretIngressBinding | None = None
    secret_task: Task[SecretHandle] | None = None
    proof: HumanAuthorizationProof | None = None
    approved: bool = False


class HumanControlService:
    """One live, generation-bound, structural human ceremony per service."""

    def __init__(
        self,
        *,
        clock: ClockPort,
        lifecycle: _Lifecycle,
        vault: _VaultView,
        unlock: UnlockCoordinator,
        secret_ingress: _SecretIngress,
        effects: _HumanEffects,
        user_presence: UserPresencePort | None,
    ) -> None:
        self._clock = clock
        self._lifecycle = lifecycle
        self._vault = vault
        self._unlock = unlock
        self._secret_ingress = secret_ingress
        self._effects = effects
        self._user_presence = user_presence
        self._active: _Ceremony | None = None
        self._closed = False
        self._mutex = Lock()

    async def open_ceremony(self, request: ClientOpenEnvelope) -> ServerOpenedEnvelope:
        async with self._mutex:
            if self._closed:
                raise HumanControlError("closed")
            if self._active is not None:
                raise HumanControlError("state_forbidden")
            session: _Ceremony | None = None
            try:
                preview, target_digest, policy_generation = await self._effects.prepare(request)
                now = self._sample_monotonic()
                ceremony_id = secrets.token_hex(32)
                expiry_ms = new_binding_expiry_ms(now)
                instance = self._lifecycle.instance
                binding = HumanCeremonyBinding(
                    binding_version=1,
                    ceremony_id=ceremony_id,
                    connection_nonce=request.connection_nonce,
                    ceremony_kind=request.ceremony_kind,
                    service_instance_id=instance.instance_id,
                    service_generation=instance.generation,
                    vault_generation=self._vault.generation,
                    policy_generation=policy_generation,
                    target_digest=target_digest,
                    expires_at_monotonic_ms=expiry_ms,
                )
                session = _Ceremony(request, binding, preview, KeyringRetryPhase(), 2)
                phase = await self._initial_phase(session)
                session.phase = phase
                self._active = session
                return ServerOpenedEnvelope(ceremony_id, 1, binding, preview, phase)
            except HumanControlError:
                if session is not None:
                    await self._consume_failed(session)
                raise
            except (UnlockError, TypeError, ValueError) as exc:
                if session is not None:
                    await self._consume_failed(session)
                raise HumanControlError(self._map_error(exc)) from exc

    async def submit_action(
        self, envelope: ClientActionEnvelope
    ) -> ServerPhaseEnvelope | ServerResultEnvelope | ServerCloseEnvelope:
        async with self._mutex:
            session = self._require_action(envelope)
            if type(envelope.action) is CancelAction:
                return await self._cancel_locked(session)
            try:
                if type(session.phase) is KeyringRetryPhase:
                    if type(envelope.action) is not RetryAction:
                        raise HumanControlError("phase_invalid")
                    presence = self._user_presence
                    capability = (
                        presence.capability()
                        if presence is not None and self._presence_available()
                        else None
                    )
                    result = await self._unlock.retry_keyring(capability)
                    return self._finish(
                        session,
                        KeyringRetryResult(
                            result.state,
                            result.reason or ("succeeded" if result.state == "ready" else "locked"),
                        ),
                    )
                if type(session.phase) is DecisionRequiredPhase:
                    if type(envelope.action) is not DecisionAction:
                        raise HumanControlError("phase_invalid")
                    return await self._handle_decision(session, envelope.action)
                if type(session.phase) is AuthorizationRequiredPhase:
                    if type(envelope.action) is not SelectAuthorizationSourceAction:
                        raise HumanControlError("phase_invalid")
                    return await self._select_authorization(session, envelope.action)
                raise HumanControlError("phase_invalid")
            except HumanControlError:
                await self._consume_failed(session)
                raise
            except (UnlockError, TypeError, ValueError) as exc:
                await self._consume_failed(session)
                raise HumanControlError(self._map_error(exc)) from exc

    async def secret_completed(
        self, ceremony_id: str
    ) -> ServerPhaseEnvelope | ServerResultEnvelope:
        async with self._mutex:
            session = self._require_session(ceremony_id)
            if type(session.phase) is not SecretRequiredPhase or session.secret_task is None:
                raise HumanControlError("phase_invalid")
            self._check_live(session)
            binding = session.secret_binding
            if binding is None or binding != session.phase.binding:
                raise HumanControlError("stale_generation")
            try:
                secret = await session.secret_task
                session.secret_task = None
                purpose = binding.purpose
                if purpose is ConfidentialSecretPurpose.VAULT_INITIALIZE:
                    challenge = self._require_unlock_challenge(session)
                    result = await self._unlock.complete_passphrase_initialization(
                        challenge, secret
                    )
                    return self._finish_after_secret(
                        session,
                        VaultStateResult(
                            result.state,
                            result.reason or ("succeeded" if result.state == "ready" else "locked"),
                        ),
                    )
                if purpose is ConfidentialSecretPurpose.VAULT_UNLOCK:
                    challenge = self._require_unlock_challenge(session)
                    result = await self._unlock.complete_passphrase_unlock(challenge, secret)
                    return self._finish_after_secret(
                        session,
                        VaultStateResult(
                            result.state,
                            result.reason or ("succeeded" if result.state == "ready" else "locked"),
                        ),
                    )
                if purpose is ConfidentialSecretPurpose.PORTABLE_RECOVERY:
                    target = cast(PortableRecoveryTarget, session.request.target)
                    return self._finish_after_secret(
                        session, await self._effects.complete_portable_recovery(target, secret)
                    )
                if purpose in {
                    ConfidentialSecretPurpose.PROVIDER_REAUTHENTICATION,
                    ConfidentialSecretPurpose.PRIVACY_REAUTHENTICATION,
                    ConfidentialSecretPurpose.SECURITY_REAUTHENTICATION,
                }:
                    challenge = self._require_unlock_challenge(session)
                    session.proof = await self._unlock.complete_reauthentication(challenge, secret)
                    if session.request.ceremony_kind in {
                        HumanCeremonyKind.PROVIDER_CREDENTIAL_SET,
                        HumanCeremonyKind.PROVIDER_CREDENTIAL_ROTATE,
                    }:
                        return self._advance_to_secret_after_secret(
                            session, ConfidentialSecretPurpose.PROVIDER_CREDENTIAL
                        )
                    return await self._commit_authorized_change(session, after_secret=True)
                if purpose is ConfidentialSecretPurpose.PROVIDER_CREDENTIAL:
                    target = cast(ProviderCredentialTarget, session.request.target)
                    proof = session.proof
                    if proof is None:
                        raise HumanControlError("reauthentication_unavailable")
                    result = await self._effects.store_provider_credential(
                        target, secret, proof, self._sample_monotonic()
                    )
                    session.proof = None
                    return self._finish_after_secret(session, result)
                raise HumanControlError("phase_invalid")
            except HumanControlError:
                await self._consume_failed(session)
                raise
            except (UnlockError, TypeError, ValueError) as exc:
                await self._consume_failed(session)
                raise HumanControlError(self._map_error(exc)) from exc

    async def cancel(self, ceremony_id: str) -> ServerCloseEnvelope:
        async with self._mutex:
            return await self._cancel_locked(self._require_session(ceremony_id))

    async def close(self) -> None:
        async with self._mutex:
            if self._active is not None:
                await self._consume_failed(self._active)
            await self._secret_ingress.close()
            if self._user_presence is not None:
                self._user_presence.close()
            self._closed = True

    async def _initial_phase(self, session: _Ceremony) -> HumanPhase:
        kind = session.request.ceremony_kind
        if kind is HumanCeremonyKind.VAULT_INITIALIZE:
            session.unlock_challenge = await self._unlock.begin_passphrase_initialization(
                target_digest=session.binding.target_digest
            )
            return self._secret_phase(session, ConfidentialSecretPurpose.VAULT_INITIALIZE)
        if kind is HumanCeremonyKind.VAULT_UNLOCK:
            session.unlock_challenge = await self._unlock.begin_passphrase_unlock(
                target_digest=session.binding.target_digest
            )
            return self._secret_phase(session, ConfidentialSecretPurpose.VAULT_UNLOCK)
        if kind is HumanCeremonyKind.KEYRING_RETRY:
            return KeyringRetryPhase()
        if kind is HumanCeremonyKind.PORTABLE_RECOVERY:
            return self._secret_phase(session, ConfidentialSecretPurpose.PORTABLE_RECOVERY)
        if kind in {
            HumanCeremonyKind.PROVIDER_CREDENTIAL_SET,
            HumanCeremonyKind.PROVIDER_CREDENTIAL_ROTATE,
        }:
            return self._authorization_phase()
        if kind in {
            HumanCeremonyKind.PRIVACY_POLICY_DECISION,
            HumanCeremonyKind.PRIVACY_DISCLOSURE_DECISION,
            HumanCeremonyKind.IDLE_RELOCK_POLICY_CHANGE,
        }:
            return DecisionRequiredPhase()
        raise HumanControlError("kind_forbidden")

    async def _handle_decision(
        self, session: _Ceremony, action: DecisionAction
    ) -> ServerPhaseEnvelope | ServerResultEnvelope:
        kind = session.request.ceremony_kind
        if kind is HumanCeremonyKind.PRIVACY_DISCLOSURE_DECISION:
            target = cast(PrivacyPendingTarget, session.request.target)
            result = await self._effects.decide_privacy(
                target, action.decision, None, self._sample_monotonic()
            )
            return self._finish(session, result)
        if kind is HumanCeremonyKind.PRIVACY_POLICY_DECISION:
            target = cast(PrivacyPendingTarget, session.request.target)
            if action.decision == "deny":
                return self._finish(
                    session,
                    await self._effects.decide_privacy(
                        target, "deny", None, self._sample_monotonic()
                    ),
                )
        elif kind is HumanCeremonyKind.IDLE_RELOCK_POLICY_CHANGE:
            if action.decision == "deny":
                # Denial is exact and secret-free; the owning effect returns the
                # unchanged structural policy result.
                target = cast(IdleRelockPolicyTarget, session.request.target)
                result = await self._effects.deny_idle_relock_policy(
                    target, self._sample_monotonic()
                )
                return self._finish(session, result)
        else:
            raise HumanControlError("phase_invalid")
        session.approved = True
        session.phase = self._authorization_phase()
        return self._phase(session)

    async def _select_authorization(
        self, session: _Ceremony, action: SelectAuthorizationSourceAction
    ) -> ServerPhaseEnvelope | ServerResultEnvelope:
        purpose, secret_purpose = self._authorization_purpose(session.request.ceremony_kind)
        session.unlock_challenge = await self._unlock.begin_reauthentication(
            purpose=purpose,
            target_digest=session.binding.target_digest,
            secret_purpose=secret_purpose,
            policy_generation=session.binding.policy_generation,
        )
        if action.source == "secret_reauthentication":
            if self._vault_mode() != "passphrase":
                raise HumanControlError("reauthentication_unavailable")
            return self._advance_to_secret(
                session, self._wire_reauthentication_purpose(secret_purpose)
            )
        presence = self._user_presence
        if presence is None or not self._presence_available():
            raise HumanControlError("presence_unavailable")
        challenge = self._require_unlock_challenge(session)
        attestation = await presence.assert_presence(
            self._unlock.user_presence_challenge(challenge)
        )
        session.proof = await self._unlock.complete_reauthentication(challenge, attestation)
        if session.request.ceremony_kind in {
            HumanCeremonyKind.PROVIDER_CREDENTIAL_SET,
            HumanCeremonyKind.PROVIDER_CREDENTIAL_ROTATE,
        }:
            return self._advance_to_secret(session, ConfidentialSecretPurpose.PROVIDER_CREDENTIAL)
        return await self._commit_authorized_change(session)

    async def _commit_authorized_change(
        self, session: _Ceremony, *, after_secret: bool = False
    ) -> ServerResultEnvelope:
        proof = session.proof
        if proof is None:
            raise HumanControlError("reauthentication_unavailable")
        if session.request.ceremony_kind is HumanCeremonyKind.PRIVACY_POLICY_DECISION:
            result = await self._effects.decide_privacy(
                cast(PrivacyPendingTarget, session.request.target),
                "approve",
                proof,
                self._sample_monotonic(),
            )
        elif session.request.ceremony_kind is HumanCeremonyKind.IDLE_RELOCK_POLICY_CHANGE:
            result = await self._effects.change_idle_relock_policy(
                cast(IdleRelockPolicyTarget, session.request.target),
                proof,
                self._sample_monotonic(),
            )
        else:
            raise HumanControlError("phase_invalid")
        session.proof = None
        return (
            self._finish_after_secret(session, result)
            if after_secret
            else self._finish(session, result)
        )

    def _authorization_phase(self) -> AuthorizationRequiredPhase:
        sources: list[Literal["os_user_presence", "secret_reauthentication"]] = []
        if self._presence_available():
            sources.append("os_user_presence")
        if self._vault_mode() == "passphrase":
            sources.append("secret_reauthentication")
        if not sources:
            raise HumanControlError("reauthentication_unavailable")
        return AuthorizationRequiredPhase(tuple(sources))

    def _advance_to_secret(
        self, session: _Ceremony, purpose: ConfidentialSecretPurpose
    ) -> ServerPhaseEnvelope:
        session.phase = self._secret_phase(session, purpose)
        return self._phase(session)

    def _advance_to_secret_after_secret(
        self, session: _Ceremony, purpose: ConfidentialSecretPurpose
    ) -> ServerPhaseEnvelope:
        session.phase = self._secret_phase(session, purpose)
        envelope = ServerPhaseEnvelope(
            session.binding.ceremony_id, session.next_step, session.phase
        )
        session.next_step += 1
        return envelope

    def _secret_phase(
        self, session: _Ceremony, purpose: ConfidentialSecretPurpose
    ) -> SecretRequiredPhase:
        challenge = session.unlock_challenge
        secret_challenge = challenge.challenge if challenge is not None else secrets.token_hex(32)
        binding = SecretIngressBinding(
            binding_version=1,
            ceremony_id=session.binding.ceremony_id,
            secret_challenge=secret_challenge,
            purpose=purpose,
            service_instance_id=session.binding.service_instance_id,
            service_generation=session.binding.service_generation,
            vault_generation=session.binding.vault_generation,
            policy_generation=session.binding.policy_generation,
            target_digest=session.binding.target_digest,
            expires_at_monotonic_ms=session.binding.expires_at_monotonic_ms,
        )
        session.secret_binding = binding
        session.secret_task = create_task(self._secret_ingress.accept_once(binding))
        return SecretRequiredPhase(binding)

    def _phase(self, session: _Ceremony) -> ServerPhaseEnvelope:
        envelope = ServerPhaseEnvelope(
            session.binding.ceremony_id, session.next_step + 1, session.phase
        )
        session.next_step += 2
        return envelope

    def _finish(self, session: _Ceremony, result: HumanResult) -> ServerResultEnvelope:
        envelope = ServerResultEnvelope(session.binding.ceremony_id, session.next_step + 1, result)
        self._active = None
        return envelope

    def _finish_after_secret(self, session: _Ceremony, result: HumanResult) -> ServerResultEnvelope:
        envelope = ServerResultEnvelope(session.binding.ceremony_id, session.next_step, result)
        self._active = None
        return envelope

    def _require_action(self, envelope: ClientActionEnvelope) -> _Ceremony:
        session = self._require_session(envelope.ceremony_id)
        if envelope.step != session.next_step:
            raise HumanControlError("replay")
        self._check_live(session)
        return session

    def _require_session(self, ceremony_id: str) -> _Ceremony:
        if self._closed:
            raise HumanControlError("closed")
        session = self._active
        if session is None or session.binding.ceremony_id != ceremony_id:
            raise HumanControlError("replay")
        return session

    def _check_live(self, session: _Ceremony) -> None:
        now_ms = monotonic_milliseconds(self._sample_monotonic())
        instance = self._lifecycle.instance
        if now_ms >= session.binding.expires_at_monotonic_ms:
            raise HumanControlError("binding_expired")
        if (
            instance.instance_id != session.binding.service_instance_id
            or instance.generation != session.binding.service_generation
            or self._vault.generation != session.binding.vault_generation
        ):
            raise HumanControlError("stale_generation")

    async def _cancel_locked(self, session: _Ceremony) -> ServerCloseEnvelope:
        await self._consume_failed(session)
        return ServerCloseEnvelope(session.binding.ceremony_id, session.next_step + 1, "cancelled")

    async def _consume_failed(self, session: _Ceremony) -> None:
        if session.secret_task is not None:
            session.secret_task.cancel()
            session.secret_task = None
        await self._secret_ingress.cancel_pending()
        await self._unlock.cancel()
        session.proof = None
        self._active = None

    def _presence_available(self) -> bool:
        port = self._user_presence
        if port is None:
            return False
        try:
            capability = port.capability()
        except Exception:
            return False
        return all(
            value == "active"
            for value in (
                capability.available,
                capability.os_authenticated_prompt,
                capability.trusted_action_binding,
                capability.one_use_attestation,
            )
        )

    def _vault_mode(self) -> str:
        value = self._vault.mode
        return cast(str, getattr(value, "value", value))

    def _sample_monotonic(self) -> float:
        sample = self._clock.monotonic_seconds()
        if type(sample) is not float or not math.isfinite(sample) or sample < 0.0:
            raise HumanControlError("internal_error")
        return sample

    @staticmethod
    def _require_unlock_challenge(session: _Ceremony) -> UnlockChallenge:
        if session.unlock_challenge is None:
            raise HumanControlError("phase_invalid")
        return session.unlock_challenge

    @staticmethod
    def _authorization_purpose(
        kind: HumanCeremonyKind,
    ) -> tuple[str, SecretPurpose]:
        mapping = {
            HumanCeremonyKind.PROVIDER_CREDENTIAL_SET: (
                "provider_credential_set",
                SecretPurpose.PROVIDER_REAUTHENTICATION,
            ),
            HumanCeremonyKind.PROVIDER_CREDENTIAL_ROTATE: (
                "provider_credential_rotate",
                SecretPurpose.PROVIDER_REAUTHENTICATION,
            ),
            HumanCeremonyKind.PRIVACY_POLICY_DECISION: (
                "privacy_policy_widen",
                SecretPurpose.PRIVACY_REAUTHENTICATION,
            ),
            HumanCeremonyKind.IDLE_RELOCK_POLICY_CHANGE: (
                "idle_relock_policy_change",
                SecretPurpose.SECURITY_REAUTHENTICATION,
            ),
        }
        try:
            return mapping[kind]
        except KeyError as exc:
            raise HumanControlError("phase_invalid") from exc

    @staticmethod
    def _wire_reauthentication_purpose(
        purpose: SecretPurpose,
    ) -> ConfidentialSecretPurpose:
        mapping = {
            SecretPurpose.PROVIDER_REAUTHENTICATION: ConfidentialSecretPurpose.PROVIDER_REAUTHENTICATION,
            SecretPurpose.PRIVACY_REAUTHENTICATION: ConfidentialSecretPurpose.PRIVACY_REAUTHENTICATION,
            SecretPurpose.SECURITY_REAUTHENTICATION: ConfidentialSecretPurpose.SECURITY_REAUTHENTICATION,
        }
        try:
            return mapping[purpose]
        except KeyError as exc:
            raise HumanControlError("phase_invalid") from exc

    @staticmethod
    def _map_error(exc: Exception) -> str:
        reason = getattr(exc, "reason", None)
        mapping = {
            "binding_expired": "binding_expired",
            "challenge_mismatch": "stale_generation",
            "closed": "closed",
            "human_authority_unavailable": "presence_unavailable",
            "invalid_state": "state_forbidden",
            "reauthentication_unavailable": "reauthentication_unavailable",
            "secret_purpose_mismatch": "secret_rejected",
            "stale_generation": "stale_generation",
            "unlock_rate_limited": "state_forbidden",
        }
        return mapping.get(reason, "internal_error") if type(reason) is str else "internal_error"
