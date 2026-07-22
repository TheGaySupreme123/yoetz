"""Service-owned vault state, derivation, and opaque generation-fenced handles."""

from __future__ import annotations

import hashlib
import hmac
import math
import os
from asyncio import Lock
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock as ThreadLock
from typing import Final, Literal, cast

from cryptography.hazmat.primitives import hashes, keywrap
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from yoetz.adapters.keys.encrypted_vault import (
    EncryptedVaultError,
    EncryptedVaultStore,
    VaultRecordKind,
)
from yoetz.adapters.keys.os_keyring import (
    KeyringInitializationBinding,
    OSKeyringError,
    OSKeyringState,
    OSVaultRootKeySource,
)
from yoetz.adapters.keys.passphrase import wrap_recovery_artifact
from yoetz.adapters.keys.vault_passphrase import (
    VaultPassphraseError,
    VaultRootEnvelope,
    create_vault_root_envelope,
    unlock_vault_root_envelope,
)
from yoetz.domain.values import validate_sha256_digest
from yoetz.ports.clock import ClockPort
from yoetz.ports.keys import (
    BundleKeys,
    KeyStoreError,
    KeyStoreReason,
    MacKeyHandle,
    MacKeyPurpose,
    RecoveryArtifact,
    RecoveryKeyMaterialHandle,
    RecoverySecret,
    WrapKeyHandle,
    WrappedDek,
)
from yoetz.ports.secret_memory import (
    HumanAuthorizationProof,
    ProviderAttemptAuthBinding,
    ProviderAuthTransportCallback,
    ProviderCredentialHandle,
    SecretConsumer,
    SecretHandle,
    SecretMemoryCapability,
    SecretMemoryError,
    SecretMemoryPort,
    SecretPurpose,
    UserPresenceAttestation,
    UserPresenceCapability,
    UserPresenceChallenge,
    UserPresencePort,
)
from yoetz.protocol.canonical import JsonValue, canonical_digest
from yoetz.protocol.ids import IdKind, validate_id

__all__ = [
    "ProviderCredentialBinding",
    "provider_credential_profile_binding",
    "VaultError",
    "VaultMode",
    "VaultService",
    "VaultState",
    "VaultStatus",
]

_INSTALLATION_SALT: Final = b"yoetz/installation-mac-root/v1"
_BUNDLE_SALT: Final = b"yoetz/bundle-key-root/v1"
_INSTALLATION_INFO: Final[Mapping[MacKeyPurpose, bytes]] = {
    MacKeyPurpose.CATALOG_LOOKUP: b"yoetz/catalog-lookup/v1",
    MacKeyPurpose.LOG_CORRELATION: b"yoetz/log-correlation/v1",
    MacKeyPurpose.PRIVACY_AUDIT: b"yoetz/privacy-audit/v1",
}
_INSTALLATION_DOMAINS: Final[Mapping[MacKeyPurpose, frozenset[bytes]]] = {
    MacKeyPurpose.CATALOG_LOOKUP: frozenset(
        {
            b"yoetz/start-title/v1\x00",
            b"yoetz/workspace-ref/v1\x00",
            b"yoetz/external-task-ref/v1\x00",
        }
    ),
    MacKeyPurpose.LOG_CORRELATION: frozenset({b"yoetz/session-log-id/v1\x00"}),
    MacKeyPurpose.PRIVACY_AUDIT: frozenset(
        {
            b"yoetz/privacy-audit/control-request/v1\x00",
            b"yoetz/privacy-audit/internal-result/v1\x00",
            b"yoetz/privacy-audit/local-approval/v1\x00",
            b"yoetz/privacy-audit/lookup/v1\x00",
            b"yoetz/privacy-audit/projection/v1\x00",
            b"yoetz/privacy-audit/proposal/v1\x00",
            b"yoetz/privacy-audit/receipt-cursor/v1\x00",
            b"yoetz/privacy-egress-request/v1\x00",
        }
    ),
}
_PROVIDER_TOKEN68 = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~+/"
)
_VAULT_REASONS: Final = frozenset(
    {
        "keyring_locked",
        "keyring_unavailable",
        "human_authority_unavailable",
        "vault_uninitialized",
        "vault_locked",
        "unlock_wrong",
        "vault_tampered",
        "record_missing",
        "record_binding_mismatch",
        "secret_purpose_mismatch",
        "credential_invalid",
        "initialization_forbidden",
        "initialization_ambiguous",
        "already_ready",
        "closed",
    }
)


class VaultMode(str, Enum):  # noqa: UP042 - frozen internal vocabulary
    UNINITIALIZED = "uninitialized"
    OS_KEYRING = "os_keyring"
    PASSPHRASE = "passphrase"


class VaultState(str, Enum):  # noqa: UP042 - frozen internal vocabulary
    LOCKED = "locked"
    UNLOCKING = "unlocking"
    READY = "ready"
    CLOSING = "closing"
    CLOSED = "closed"


class VaultError(Exception):
    """Bounded vault failure with no secret-derived text."""

    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        if type(reason) is not str or reason not in _VAULT_REASONS:
            raise TypeError("vault_reason_invalid")
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ProviderCredentialBinding:
    provider_id: str
    model_id: str
    endpoint_profile_id: str
    endpoint_profile_version: str
    purpose: str
    authorization_scope_digest: str
    purpose_digest: str

    def __post_init__(self) -> None:
        # Reuse the attempt binding's exact structural validators with inert valid attempt fields.
        ProviderAttemptAuthBinding(
            self.provider_id,
            self.model_id,
            self.endpoint_profile_id,
            self.endpoint_profile_version,
            self.purpose,
            self.authorization_scope_digest,
            self.purpose_digest,
            "dsp_00000000-0000-4000-8000-000000000000",
            "sha256:" + "0" * 64,
            1,
            0.0,
        )

    def record_binding(self) -> dict[str, str]:
        return {
            "authorization_scope_digest": self.authorization_scope_digest,
            "endpoint_profile_id": self.endpoint_profile_id,
            "endpoint_profile_version": self.endpoint_profile_version,
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "purpose": self.purpose,
            "purpose_digest": self.purpose_digest,
        }

    def target_digest(self, action: Literal["set", "rotate"]) -> str:
        if action not in {"set", "rotate"}:
            raise ValueError("provider_credential_action_invalid")
        return canonical_digest(
            {
                "action": action,
                "endpoint_profile_id": self.endpoint_profile_id,
                "endpoint_profile_version": self.endpoint_profile_version,
                "kind": "provider_credential",
                "model_id": self.model_id,
                "provider_id": self.provider_id,
                "purpose": self.purpose,
                "purpose_digest": self.purpose_digest,
                "scope_digest": self.authorization_scope_digest,
            }
        )


def provider_credential_profile_binding(
    provider_id: str,
    model_id: str,
    endpoint_profile_id: str,
    endpoint_profile_version: str,
) -> ProviderCredentialBinding:
    """Build the installation-wide credential record binding for one exact provider profile.

    Per-dispatch authorization scope and purpose remain bound to the one-shot credential handle;
    they do not require storing a duplicate copy of the same account credential for every task.
    """

    purpose = "llm-inference"
    scope_digest = canonical_digest(
        {
            "endpoint_profile_id": endpoint_profile_id,
            "endpoint_profile_version": endpoint_profile_version,
            "kind": "provider_credential_profile",
            "model_id": model_id,
            "provider_id": provider_id,
        }
    )
    return ProviderCredentialBinding(
        provider_id,
        model_id,
        endpoint_profile_id,
        endpoint_profile_version,
        purpose,
        scope_digest,
        canonical_digest({"purpose": purpose}),
    )


@dataclass(frozen=True, slots=True)
class VaultStatus:
    mode: VaultMode
    state: VaultState
    format_version: Literal[1]
    vault_generation: int
    reason: str | None
    secret_memory_capability: SecretMemoryCapability

    def __post_init__(self) -> None:
        if type(self.mode) is not VaultMode or type(self.state) is not VaultState:
            raise ValueError("vault_status_invalid")
        if self.format_version != 1:
            raise ValueError("vault_status_invalid")
        if type(self.vault_generation) is not int or self.vault_generation < 0:
            raise ValueError("vault_status_invalid")
        if self.reason is not None and self.reason not in _VAULT_REASONS:
            raise ValueError("vault_status_invalid")


class VaultService:
    """The only owner of IVK-derived operations and encrypted vault records.

    Mode-marker publication remains an injected same-directory durability operation. This keeps
    secret state here while letting the daemon own installation-path discovery and recovery.
    """

    def __init__(
        self,
        *,
        installation_id: str,
        service_generation: int,
        mode: VaultMode,
        secret_memory: SecretMemoryPort,
        clock: ClockPort,
        vault_store_factory: Callable[[], EncryptedVaultStore],
        keyring_source: OSVaultRootKeySource | None = None,
        root_envelope: VaultRootEnvelope | None = None,
        user_presence_port: UserPresencePort | None = None,
        runtime_support: Mapping[str, JsonValue] | None = None,
        pristine_state_digest: str | None = None,
        publish_mode: Callable[[VaultMode, VaultRootEnvelope | None, str], None] | None = None,
    ) -> None:
        self._installation_id = validate_id(IdKind.INSTALLATION, installation_id)
        if type(service_generation) is not int or service_generation <= 0:
            raise ValueError("service_generation_invalid")
        if type(mode) is not VaultMode:
            raise TypeError("vault_mode_invalid")
        if pristine_state_digest is not None:
            validate_sha256_digest(pristine_state_digest)
        if mode is VaultMode.PASSPHRASE and root_envelope is None:
            raise ValueError("passphrase_envelope_missing")
        if mode is not VaultMode.PASSPHRASE and root_envelope is not None:
            raise ValueError("passphrase_envelope_forbidden")
        self._service_generation = service_generation
        self._mode = mode
        self._state = VaultState.LOCKED
        self._vault_generation = 0
        self._reason: str | None = None
        self._secret_memory = secret_memory
        self._clock = clock
        self._vault_store_factory = vault_store_factory
        self._keyring_source = keyring_source
        self._root_envelope = root_envelope
        self._user_presence_port = user_presence_port
        self._runtime_support = dict(runtime_support or {})
        self._pristine_state_digest = pristine_state_digest
        self._publish_mode = publish_mode
        self._store: EncryptedVaultStore | None = None
        self._bundle_handles: list[_GenerationHandle] = []
        self._installation_handles: dict[MacKeyPurpose, _MacHandle] = {}
        self._provider_handles: list[_ProviderHandle] = []
        self._provider_generations: dict[ProviderCredentialBinding, int] = {}
        self._mutex = Lock()

    @property
    def mode(self) -> VaultMode:
        return self._mode

    @property
    def state(self) -> VaultState:
        return self._state

    @property
    def generation(self) -> int:
        return self._vault_generation

    @property
    def ready(self) -> bool:
        return self._state is VaultState.READY

    @property
    def status(self) -> VaultStatus:
        return VaultStatus(
            self._mode,
            self._state,
            1,
            self._vault_generation,
            self._reason,
            self._secret_memory.capability(),
        )

    async def initialize(
        self, user_presence_capability: UserPresenceCapability | None
    ) -> VaultStatus:
        async with self._mutex:
            self._require_not_closed()
            if self.ready:
                raise VaultError("already_ready")
            if self._mode is VaultMode.PASSPHRASE:
                self._reason = "vault_locked"
                return self.status
            self._state = VaultState.UNLOCKING
            try:
                if self._mode is VaultMode.OS_KEYRING:
                    await self._load_keyring_ready()
                else:
                    await self._create_keyring_ready(user_presence_capability)
            except VaultError as exc:
                self._state = VaultState.LOCKED
                self._reason = exc.reason
            return self.status

    async def retry_keyring(
        self, user_presence_capability: UserPresenceCapability | None
    ) -> VaultStatus:
        if self._mode is VaultMode.PASSPHRASE:
            raise VaultError("initialization_forbidden")
        return await self.initialize(user_presence_capability)

    async def initialize_passphrase(
        self,
        handle: SecretHandle,
        throttle_record_digest: str,
    ) -> VaultStatus:
        validate_sha256_digest(throttle_record_digest)
        async with self._mutex:
            self._require_not_closed()
            if self._mode is not VaultMode.UNINITIALIZED or self._state is not VaultState.LOCKED:
                raise VaultError("initialization_forbidden")
            if self._pristine_state_digest is None:
                raise VaultError("initialization_ambiguous")
            if handle.purpose is not SecretPurpose.VAULT_INITIALIZE:
                raise VaultError("secret_purpose_mismatch")
            self._state = VaultState.UNLOCKING
            ivk = bytearray(os.urandom(32))
            try:
                envelope = create_vault_root_envelope(
                    self._root_handle(bytearray(ivk)),
                    handle,
                    installation_id=self._installation_id,
                )
                self._open_store_and_sentinel(ivk)
                if self._publish_mode is not None:
                    self._publish_mode(VaultMode.PASSPHRASE, envelope, throttle_record_digest)
                self._mode = VaultMode.PASSPHRASE
                self._root_envelope = envelope
                self._become_ready(ivk)
            except (EncryptedVaultError, VaultPassphraseError, OSError) as exc:
                self._close_store()
                self._state = VaultState.LOCKED
                self._reason = "vault_tampered"
                raise VaultError("vault_tampered") from exc
            finally:
                _overwrite(ivk)
            return self.status

    async def unlock(self, handle: SecretHandle) -> VaultStatus:
        async with self._mutex:
            self._require_not_closed()
            if self._mode is not VaultMode.PASSPHRASE:
                raise VaultError("initialization_forbidden")
            if self.ready:
                raise VaultError("already_ready")
            if handle.purpose is not SecretPurpose.VAULT_UNLOCK:
                raise VaultError("secret_purpose_mismatch")
            envelope = self._root_envelope
            if envelope is None:
                raise VaultError("vault_tampered")
            self._state = VaultState.UNLOCKING
            try:
                root = unlock_vault_root_envelope(envelope, handle)
                ivk = root.consume(SecretConsumer.VAULT_ROOT, lambda view: bytearray(view))
                try:
                    self._open_existing_store(ivk)
                    self._become_ready(ivk)
                finally:
                    _overwrite(ivk)
            except (VaultPassphraseError, EncryptedVaultError, SecretMemoryError) as exc:
                self._close_store()
                self._state = VaultState.LOCKED
                self._reason = "unlock_wrong"
                raise VaultError("unlock_wrong") from exc
            return self.status

    async def lock(self) -> VaultStatus:
        async with self._mutex:
            self._require_not_closed()
            if self._state is VaultState.LOCKED:
                return self.status
            self._state = VaultState.CLOSING
            self._invalidate_handles()
            self._close_store()
            self._vault_generation += 1
            self._state = VaultState.LOCKED
            self._reason = "vault_locked"
            return self.status

    async def close(self) -> None:
        async with self._mutex:
            if self._state is VaultState.CLOSED:
                return
            self._state = VaultState.CLOSING
            self._invalidate_handles()
            self._close_store()
            self._vault_generation += 1
            self._state = VaultState.CLOSED
            self._reason = "closed"

    async def load_bundle_keys(self, bundle_id: str) -> BundleKeys:
        validate_id(IdKind.TASK, bundle_id)
        async with self._mutex:
            store, generation = self._ready_store()
            key_slot = _bundle_key_slot(bundle_id)
            try:
                handle = store.load_record(
                    VaultRecordKind.BUNDLE_KEY,
                    {"task_id": bundle_id, "key_slot": key_slot},
                )
            except EncryptedVaultError as exc:
                reason = (
                    KeyStoreReason.KEY_MISSING
                    if exc.reason == "record_missing"
                    else KeyStoreReason.KEY_ID_MISMATCH
                )
                raise KeyStoreError(reason) from exc
            return self._derive_bundle_handles(handle, key_slot, generation)

    async def create_bundle_keys(self, bundle_id: str) -> BundleKeys:
        validate_id(IdKind.TASK, bundle_id)
        async with self._mutex:
            store, generation = self._ready_store()
            key_slot = _bundle_key_slot(bundle_id)
            source = bytearray(os.urandom(32))
            try:
                store.create_record(
                    VaultRecordKind.BUNDLE_KEY,
                    {"task_id": bundle_id, "key_slot": key_slot},
                    self._root_handle(bytearray(source)),
                )
                return self._derive_bundle_bytes(source, key_slot, generation)
            except EncryptedVaultError as exc:
                raise KeyStoreError(KeyStoreReason.KEY_ID_MISMATCH) from exc
            finally:
                _overwrite(source)

    async def wrap_recovery(
        self,
        bundle_id: str,
        recovery_secret: RecoverySecret,
    ) -> RecoveryArtifact:
        validate_id(IdKind.TASK, bundle_id)
        async with self._mutex:
            store, _ = self._ready_store()
            key_slot = _bundle_key_slot(bundle_id)
            try:
                record = store.load_record(
                    VaultRecordKind.BUNDLE_KEY,
                    {"task_id": bundle_id, "key_slot": key_slot},
                )
                material = _RecoveryMaterial(
                    record.consume(SecretConsumer.VAULT_ROOT, lambda view: bytearray(view))
                )
                return wrap_recovery_artifact(
                    material,
                    recovery_secret,
                    task_id=bundle_id,
                    key_slot=key_slot,
                )
            except EncryptedVaultError as exc:
                raise KeyStoreError(KeyStoreReason.KEY_MISSING) from exc

    def installation_mac_handle(self, purpose: MacKeyPurpose) -> MacKeyHandle:
        if purpose not in _INSTALLATION_INFO:
            raise KeyStoreError(KeyStoreReason.MAC_PURPOSE_MISMATCH)
        if not self.ready:
            raise KeyStoreError(KeyStoreReason.VAULT_LOCKED)
        handle = self._installation_handles.get(purpose)
        if handle is None:
            raise KeyStoreError(KeyStoreReason.STALE_KEY_HANDLE)
        return handle

    async def store_provider_credential(
        self,
        action: Literal["set", "rotate"],
        binding: ProviderCredentialBinding,
        secret: SecretHandle,
        proof: HumanAuthorizationProof,
        now_monotonic: float,
    ) -> None:
        if action not in {"set", "rotate"}:
            raise VaultError("record_binding_mismatch")
        if secret.purpose is not SecretPurpose.PROVIDER_CREDENTIAL:
            raise VaultError("secret_purpose_mismatch")
        async with self._mutex:
            store, generation = self._ready_store()
            expected_purpose = f"provider_credential_{action}"
            try:
                validated = secret.consume(
                    SecretConsumer.PROVIDER_AUTHORIZER,
                    _validated_provider_credential,
                )
            except (SecretMemoryError, ValueError) as exc:
                raise VaultError("credential_invalid") from exc
            payload = self._secret_memory.capture(SecretPurpose.PROVIDER_CREDENTIAL, validated)
            try:
                proof.consume(
                    expected_purpose,
                    binding.target_digest(action),
                    self._service_generation,
                    generation,
                    None,
                    now_monotonic,
                )
                current = store.record_generation(
                    VaultRecordKind.PROVIDER_CREDENTIAL,
                    binding.record_binding(),
                )
                if action == "set":
                    if current is None:
                        store.create_record(
                            VaultRecordKind.PROVIDER_CREDENTIAL,
                            binding.record_binding(),
                            payload,
                        )
                        self._provider_generations[binding] = 1
                    else:
                        store.replace_credential_record(
                            binding.record_binding(), payload, expected_generation=current
                        )
                        self._provider_generations[binding] = current + 1
                else:
                    if current is None:
                        raise VaultError("record_missing")
                    store.replace_credential_record(
                        binding.record_binding(), payload, expected_generation=current
                    )
                    self._provider_generations[binding] = current + 1
            except SecretMemoryError as exc:
                raise VaultError("record_binding_mismatch") from exc
            except EncryptedVaultError as exc:
                reason = "record_missing" if exc.reason == "record_missing" else "vault_tampered"
                raise VaultError(reason) from exc

    async def provider_credential(
        self, binding: ProviderAttemptAuthBinding
    ) -> ProviderCredentialHandle:
        async with self._mutex:
            store, generation = self._ready_store()
            stored = provider_credential_profile_binding(
                binding.provider_id,
                binding.model_id,
                binding.endpoint_profile_id,
                binding.endpoint_profile_version,
            )
            if binding.service_generation != self._service_generation:
                raise VaultError("record_binding_mismatch")
            try:
                record = store.load_record(VaultRecordKind.PROVIDER_CREDENTIAL, stored.record_binding())
            except EncryptedVaultError:
                # Backward-read support for pre-profile-binding development vaults.
                legacy = ProviderCredentialBinding(
                    binding.provider_id,
                    binding.model_id,
                    binding.endpoint_profile_id,
                    binding.endpoint_profile_version,
                    binding.purpose,
                    binding.authorization_scope_digest,
                    binding.purpose_digest,
                )
                try:
                    record = store.load_record(
                        VaultRecordKind.PROVIDER_CREDENTIAL, legacy.record_binding()
                    )
                except EncryptedVaultError as exc:
                    raise VaultError("record_missing") from exc
            plaintext = record.consume(SecretConsumer.VAULT_ROOT, lambda view: bytearray(view))
            credential = self._secret_memory.capture(SecretPurpose.PROVIDER_CREDENTIAL, plaintext)
            handle = _ProviderHandle(self, generation, binding, credential, self._clock)
            self._provider_handles.append(handle)
            return handle

    async def mint_human_authorization(
        self,
        source: UserPresenceAttestation | SecretHandle,
        challenge: UserPresenceChallenge,
    ) -> HumanAuthorizationProof:
        async with self._mutex:
            _, generation = self._ready_store()
            if (
                challenge.service_generation != self._service_generation
                or challenge.vault_generation != generation
            ):
                raise VaultError("record_binding_mismatch")
            now = self._clock.monotonic_seconds()
            if not math.isfinite(now) or now < 0.0 or now >= challenge.expires_at_monotonic:
                raise VaultError("record_binding_mismatch")
            if hasattr(source, "purpose"):
                self._verify_reauthentication(cast(SecretHandle, source), challenge)
            else:
                presence = self._user_presence_port
                if presence is None:
                    raise VaultError("human_authority_unavailable")
                presence.consume(cast(UserPresenceAttestation, source), challenge)
            return HumanAuthorizationProof(
                proof_id=f"proof_{os.urandom(16).hex()}",
                purpose=challenge.purpose,
                target_digest=challenge.target_digest,
                service_generation=self._service_generation,
                vault_generation=generation,
                policy_generation=challenge.policy_generation,
                issued_at_monotonic=now,
                expires_at_monotonic=challenge.expires_at_monotonic,
            )

    async def _load_keyring_ready(self) -> None:
        source = self._keyring_source
        if source is None:
            raise VaultError("keyring_unavailable")
        try:
            binding = await source.load(self._installation_id)
            ivk = binding.ivk_handle.consume(
                SecretConsumer.VAULT_ROOT, lambda view: bytearray(view)
            )
            try:
                self._open_existing_store(ivk)
                self._become_ready(ivk)
            finally:
                _overwrite(ivk)
        except OSKeyringError as exc:
            reason = "keyring_locked" if exc.reason == "locked" else "keyring_unavailable"
            raise VaultError(reason) from exc
        except EncryptedVaultError as exc:
            raise VaultError("vault_tampered") from exc

    async def _create_keyring_ready(
        self, user_presence_capability: UserPresenceCapability | None
    ) -> None:
        source = self._keyring_source
        pristine = self._pristine_state_digest
        if source is None:
            raise VaultError("keyring_unavailable")
        if pristine is None:
            raise VaultError("initialization_ambiguous")
        try:
            probe = await source.probe(self._installation_id)
            if probe.state is OSKeyringState.LOCKED:
                raise VaultError("keyring_locked")
            if probe.state is not OSKeyringState.MISSING:
                raise VaultError("initialization_ambiguous")
            authority = await source.authorize_first_install(
                probe,
                user_presence_capability,
                self._runtime_support,
                service_generation=self._service_generation,
                pristine_state_digest=pristine,
            )
            ivk = bytearray(os.urandom(32))
            correlation = bytearray(os.urandom(32))
            commitment = f"sha256:{hashlib.sha256(correlation).hexdigest()}"
            try:
                self._open_store_and_sentinel(ivk)
                initial = KeyringInitializationBinding(
                    1,
                    self._installation_id,
                    commitment,
                    self._root_handle(bytearray(ivk)),
                    self._root_handle(bytearray(correlation)),
                )

                def _verify(candidate: memoryview, loaded_commitment: str) -> None:
                    if loaded_commitment != commitment or not hmac.compare_digest(
                        candidate, memoryview(ivk)
                    ):
                        raise OSKeyringError("correlation_mismatch")

                verified = await source.create_and_verify(
                    authority,
                    initial,
                    service_generation=self._service_generation,
                    pristine_state_digest=pristine,
                    staged_sentinel_verifier=_verify,
                )
                verified.ivk_handle.consume(SecretConsumer.VAULT_ROOT, lambda view: None)
                verified.correlation_handle.consume(SecretConsumer.VAULT_ROOT, lambda view: None)
                if self._publish_mode is not None:
                    self._publish_mode(VaultMode.OS_KEYRING, None, commitment)
                self._mode = VaultMode.OS_KEYRING
                self._become_ready(ivk)
            finally:
                _overwrite(ivk)
                _overwrite(correlation)
        except VaultError:
            raise
        except OSKeyringError as exc:
            reason = {
                "locked": "keyring_locked",
                "human_authority_unavailable": "human_authority_unavailable",
            }.get(exc.reason, "initialization_ambiguous")
            raise VaultError(reason) from exc

    def _open_store_and_sentinel(self, ivk: bytearray) -> None:
        store = self._vault_store_factory()
        store.initialize(self._root_handle(bytearray(ivk)))
        sentinel = self._root_handle(bytearray(os.urandom(32)))
        store.create_record(
            VaultRecordKind.VAULT_SENTINEL,
            {"installation_id": self._installation_id},
            sentinel,
        )
        self._store = store

    def _open_existing_store(self, ivk: bytearray) -> None:
        store = self._vault_store_factory()
        store.initialize(self._root_handle(bytearray(ivk)))
        store.verify_sentinel({"installation_id": self._installation_id})
        self._store = store

    def _become_ready(self, ivk: bytearray) -> None:
        self._vault_generation += 1
        generation = self._vault_generation
        handles: dict[MacKeyPurpose, _MacHandle] = {}
        for purpose, info in _INSTALLATION_INFO.items():
            key = _derive(ivk, _INSTALLATION_SALT, info)
            handles[purpose] = _MacHandle(
                self,
                generation,
                purpose,
                key,
                _INSTALLATION_DOMAINS[purpose],
                bundle_domains=False,
            )
        self._installation_handles = handles
        self._state = VaultState.READY
        self._reason = None

    def _derive_bundle_handles(
        self, handle: SecretHandle, key_slot: str, generation: int
    ) -> BundleKeys:
        secret = handle.consume(SecretConsumer.VAULT_ROOT, lambda view: bytearray(view))
        try:
            return self._derive_bundle_bytes(secret, key_slot, generation)
        finally:
            _overwrite(secret)

    def _derive_bundle_bytes(self, secret: bytearray, key_slot: str, generation: int) -> BundleKeys:
        wrap_key = _WrapHandle(self, generation, _derive(secret, _BUNDLE_SALT, b"yoetz/kek/v1"))
        commitment_key = _MacHandle(
            self,
            generation,
            MacKeyPurpose.BUNDLE_COMMITMENT,
            _derive(secret, _BUNDLE_SALT, b"yoetz/commitment/v1"),
            frozenset(),
            bundle_domains=True,
        )
        self._bundle_handles.extend((wrap_key, commitment_key))
        return BundleKeys(key_slot, wrap_key, commitment_key)

    def _verify_reauthentication(
        self, source: SecretHandle, challenge: UserPresenceChallenge
    ) -> None:
        expected = {
            "provider_credential_set": SecretPurpose.PROVIDER_REAUTHENTICATION,
            "provider_credential_rotate": SecretPurpose.PROVIDER_REAUTHENTICATION,
            "privacy_policy_change": SecretPurpose.PRIVACY_REAUTHENTICATION,
            "idle_relock_policy_change": SecretPurpose.SECURITY_REAUTHENTICATION,
        }.get(challenge.purpose)
        if (
            expected is None
            or source.purpose is not expected
            or self._mode is not VaultMode.PASSPHRASE
        ):
            raise VaultError("secret_purpose_mismatch")
        envelope = self._root_envelope
        if envelope is None:
            raise VaultError("vault_tampered")
        copied = source.consume(SecretConsumer.VAULT_ROOT, lambda view: bytearray(view))
        candidate = self._secret_memory.capture(SecretPurpose.VAULT_UNLOCK, copied)
        try:
            root = unlock_vault_root_envelope(envelope, candidate)
            store = self._store
            if store is None:
                raise VaultError("vault_locked")
            # The candidate is authenticated by the immutable envelope. Consuming it here also
            # proves exact key length; the live store's sentinel was verified on ready admission.
            root.consume(
                SecretConsumer.VAULT_ROOT,
                lambda view: None if view.nbytes == 32 else _raise_tampered(),
            )
        except (VaultPassphraseError, SecretMemoryError) as exc:
            raise VaultError("unlock_wrong") from exc

    def _root_handle(self, source: bytearray) -> SecretHandle:
        return self._secret_memory.capture(SecretPurpose.VAULT_ROOT_KEY, source)

    def _ready_store(self) -> tuple[EncryptedVaultStore, int]:
        if self._state is not VaultState.READY or self._store is None:
            raise KeyStoreError(KeyStoreReason.VAULT_LOCKED)
        return self._store, self._vault_generation

    def is_generation_current(self, generation: int) -> bool:
        """Return whether an opaque handle still belongs to the live ready generation."""

        return self.ready and generation == self._vault_generation

    def _invalidate_handles(self) -> None:
        for handle in (*self._bundle_handles, *self._installation_handles.values()):
            handle.invalidate()
        for handle in self._provider_handles:
            handle.invalidate()
        self._bundle_handles.clear()
        self._installation_handles.clear()
        self._provider_handles.clear()

    def _close_store(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    def _require_not_closed(self) -> None:
        if self._state in {VaultState.CLOSING, VaultState.CLOSED}:
            raise VaultError("closed")


class _GenerationHandle:
    __slots__ = ("_generation", "_invalid", "_owner")

    def __init__(self, owner: VaultService, generation: int) -> None:
        self._owner = owner
        self._generation = generation
        self._invalid = False

    def _require_current(self) -> None:
        if self._invalid or not self._owner.is_generation_current(self._generation):
            raise KeyStoreError(KeyStoreReason.STALE_KEY_HANDLE)

    def invalidate(self) -> None:
        self._invalid = True


class _WrapHandle(_GenerationHandle, WrapKeyHandle):
    __slots__ = ("_key", "_lock")

    def __init__(self, owner: VaultService, generation: int, key: bytearray) -> None:
        super().__init__(owner, generation)
        self._key = key
        self._lock = ThreadLock()

    def wrap_dek(self, dek: SecretHandle) -> WrappedDek:
        with self._lock:
            self._require_current()
            wrapped = dek.consume(
                SecretConsumer.OBJECT_CRYPTO,
                lambda view: keywrap.aes_key_wrap(bytes(self._key), bytes(view)),
            )
            return WrappedDek("aes-256-kw-rfc3394", wrapped)

    def unwrap_dek(self, wrapped: WrappedDek) -> SecretHandle:
        with self._lock:
            self._require_current()
            try:
                value = keywrap.aes_key_unwrap(bytes(self._key), wrapped.wrapped)
            except keywrap.InvalidUnwrap as exc:
                raise KeyStoreError(KeyStoreReason.KEY_ID_MISMATCH) from exc
            return _EphemeralSecret(bytearray(value), SecretPurpose.OBJECT_PAYLOAD)

    def invalidate(self) -> None:
        with self._lock:
            super().invalidate()
            _overwrite(self._key)


class _MacHandle(_GenerationHandle, MacKeyHandle):
    __slots__ = ("_bundle_domains", "_domains", "_key", "_lock", "_purpose")

    def __init__(
        self,
        owner: VaultService,
        generation: int,
        purpose: MacKeyPurpose,
        key: bytearray,
        domains: frozenset[bytes],
        *,
        bundle_domains: bool,
    ) -> None:
        super().__init__(owner, generation)
        self._purpose = purpose
        self._key = key
        self._domains = domains
        self._bundle_domains = bundle_domains
        self._lock = ThreadLock()

    def mac(self, domain: bytes, message: bytes) -> str:
        if type(domain) is not bytes or type(message) is not bytes:
            raise KeyStoreError(KeyStoreReason.MAC_DOMAIN_FORBIDDEN)
        allowed = domain in self._domains or (
            self._bundle_domains
            and domain.startswith(b"yoetz/object/")
            and domain.endswith(b"/v1\x00")
        )
        if not allowed:
            raise KeyStoreError(KeyStoreReason.MAC_DOMAIN_FORBIDDEN)
        with self._lock:
            self._require_current()
            return f"hmac-sha256:{hmac.digest(self._key, domain + message, 'sha256').hex()}"

    def invalidate(self) -> None:
        with self._lock:
            super().invalidate()
            _overwrite(self._key)


@dataclass(slots=True)
class _EphemeralSecret:
    _secret: bytearray = field(repr=False)
    _purpose: SecretPurpose
    _consumed: bool = field(default=False, init=False, repr=False)
    _lock: ThreadLock = field(default_factory=ThreadLock, init=False, repr=False)

    @property
    def purpose(self) -> SecretPurpose:
        return self._purpose

    def consume[T](self, consumer: SecretConsumer, fn: Callable[[memoryview], T]) -> T:
        expected = {
            SecretPurpose.OBJECT_PAYLOAD: SecretConsumer.OBJECT_CRYPTO,
            SecretPurpose.VAULT_ROOT_KEY: SecretConsumer.VAULT_ROOT,
        }.get(self._purpose)
        if consumer is not expected:
            raise SecretMemoryError("consumer_forbidden")
        with self._lock:
            if self._consumed:
                raise SecretMemoryError("already_consumed")
            self._consumed = True
            try:
                return fn(memoryview(self._secret))
            finally:
                _overwrite(self._secret)


@dataclass(slots=True)
class _RecoveryMaterial(RecoveryKeyMaterialHandle):
    _secret: bytearray = field(repr=False)
    _consumed: bool = field(default=False, init=False, repr=False)
    _lock: ThreadLock = field(default_factory=ThreadLock, init=False, repr=False)

    def consume[T](self, fn: Callable[[memoryview], T]) -> T:
        with self._lock:
            if self._consumed:
                raise KeyStoreError(KeyStoreReason.STALE_KEY_HANDLE)
            self._consumed = True
            try:
                return fn(memoryview(self._secret))
            finally:
                _overwrite(self._secret)


@dataclass(slots=True)
class _ProviderHandle(ProviderCredentialHandle):
    _owner: VaultService
    _generation: int
    _binding: ProviderAttemptAuthBinding
    _credential: SecretHandle = field(repr=False)
    _clock: ClockPort
    _used: bool = field(default=False, init=False, repr=False)
    _lock: ThreadLock = field(default_factory=ThreadLock, init=False, repr=False)

    async def authorize_attempt[T](
        self,
        binding: ProviderAttemptAuthBinding,
        inject_and_start: ProviderAuthTransportCallback[T],
    ) -> T:
        with self._lock:
            if self._used:
                raise SecretMemoryError("already_consumed")
            self._used = True
            if binding != self._binding or not self._owner.is_generation_current(self._generation):
                raise SecretMemoryError("provider_binding_mismatch")
            now = self._clock.monotonic_seconds()
            if not math.isfinite(now) or now < 0.0 or now >= binding.monotonic_deadline:
                raise SecretMemoryError("provider_deadline_expired")
            secret = self._credential.consume(
                SecretConsumer.PROVIDER_AUTHORIZER,
                lambda view: bytearray(view),
            )
        view = memoryview(secret)
        try:
            return await inject_and_start.inject_and_start(view)
        finally:
            view.release()
            _overwrite(secret)

    def invalidate(self) -> None:
        with self._lock:
            if self._used:
                return
            self._used = True
            try:
                discarded = self._credential.consume(
                    SecretConsumer.PROVIDER_AUTHORIZER,
                    lambda view: bytearray(view),
                )
            except SecretMemoryError, EncryptedVaultError:
                return
            _overwrite(discarded)


def _derive(source: bytes | bytearray, salt: bytes, info: bytes) -> bytearray:
    return bytearray(
        HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(bytes(source))
    )


def _bundle_key_slot(bundle_id: str) -> str:
    return f"bmk-{hashlib.sha256(bundle_id.encode('ascii')).hexdigest()}"


def _validated_provider_credential(view: memoryview) -> bytearray:
    value = bytearray(view)
    if not 16 <= len(value) <= 512 or any(byte not in _PROVIDER_TOKEN68 for byte in value):
        _overwrite(value)
        raise ValueError("credential_invalid")
    return value


def _overwrite(value: bytearray) -> None:
    value[:] = b"\x00" * len(value)


def _raise_tampered() -> None:
    raise VaultError("vault_tampered")
