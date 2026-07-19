"""Verified OS-keyring source for the installation vault-root key."""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Final, Protocol, cast

import keyring

from yoetz.domain.values import validate_sha256_digest
from yoetz.ports.secret_memory import (
    SecretConsumer,
    SecretHandle,
    SecretMemoryPort,
    SecretPurpose,
    UserPresenceCapability,
)
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.ids import IdKind, validate_id

__all__ = [
    "FirstInstallKeyringAuthority",
    "KeyringInitializationBinding",
    "OSKeyringError",
    "OSKeyringProbe",
    "OSKeyringState",
    "OSVaultRootKeySource",
]

_SERVICE_NAME: Final = "yoetz.vault-root.v1"
_APPROVED_BACKENDS: Final = frozenset(
    {"keyring.backends.macOS.Keyring", "keyring.backends.SecretService.Keyring"}
)
_ERRORS: Final = frozenset(
    {
        "locked",
        "missing",
        "unsupported",
        "unverified",
        "human_authority_unavailable",
        "authority_mismatch",
        "entry_exists",
        "entry_invalid",
        "correlation_mismatch",
        "migration_not_proven",
    }
)


class OSKeyringState(str, Enum):  # noqa: UP042
    AVAILABLE = "available"
    LOCKED = "locked"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"


class OSKeyringError(Exception):
    __slots__ = ("reason",)
    reason: str

    def __init__(self, reason: str) -> None:
        if type(reason) is not str or reason not in _ERRORS:
            raise TypeError("os_keyring_reason_invalid")
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class OSKeyringProbe:
    installation_id: str
    state: OSKeyringState
    backend_id: str
    create_if_absent: bool
    round_trip_load: bool
    probe_digest: str

    def __post_init__(self) -> None:
        validate_id(IdKind.INSTALLATION, self.installation_id)
        if type(self.state) is not OSKeyringState or type(self.backend_id) is not str:
            raise ValueError("keyring_probe_invalid")
        if type(self.create_if_absent) is not bool or type(self.round_trip_load) is not bool:
            raise ValueError("keyring_probe_invalid")
        validate_sha256_digest(self.probe_digest)


@dataclass(frozen=True, slots=True)
class KeyringInitializationBinding:
    version: int
    installation_id: str
    correlation_commitment: str
    ivk_handle: SecretHandle = field(repr=False)
    correlation_handle: SecretHandle = field(repr=False)

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ValueError("keyring_binding_version_invalid")
        validate_id(IdKind.INSTALLATION, self.installation_id)
        validate_sha256_digest(self.correlation_commitment)
        if self.ivk_handle.purpose is not SecretPurpose.VAULT_ROOT_KEY:
            raise ValueError("keyring_ivk_purpose_invalid")
        if self.correlation_handle.purpose is not SecretPurpose.VAULT_ROOT_KEY:
            raise ValueError("keyring_correlation_purpose_invalid")


class _AtomicBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password_if_absent(self, service: str, username: str, password: str) -> bool: ...
    def delete_password(self, service: str, username: str) -> None: ...


@dataclass(slots=True)
class FirstInstallKeyringAuthority:
    service_generation: int
    pristine_state_digest: str
    probe_digest: str
    candidate_artifact_digest: str
    release_cell: str
    presence_evidence_digest: str
    _token: object = field(repr=False)
    _used: bool = field(default=False, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self._token is not _AUTHORITY_TOKEN:
            raise TypeError("first_install_authority_opaque")
        if type(self.service_generation) is not int or self.service_generation <= 0:
            raise ValueError("service_generation_invalid")
        validate_sha256_digest(self.pristine_state_digest)
        validate_sha256_digest(self.probe_digest)
        validate_sha256_digest(self.candidate_artifact_digest)
        validate_sha256_digest(self.presence_evidence_digest)
        if type(self.release_cell) is not str or not self.release_cell:
            raise ValueError("release_cell_invalid")

    def _consume(self) -> None:
        with self._lock:
            if self._used:
                raise OSKeyringError("authority_mismatch")
            self._used = True

    def __copy__(self) -> FirstInstallKeyringAuthority:
        raise TypeError("first_install_authority_not_copyable")

    def __deepcopy__(self, memo: Mapping[int, object]) -> FirstInstallKeyringAuthority:
        del memo
        raise TypeError("first_install_authority_not_copyable")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("first_install_authority_not_serializable")


_AUTHORITY_TOKEN: Final = object()


class OSVaultRootKeySource:
    def __init__(
        self,
        secret_memory: SecretMemoryPort,
        *,
        backend: object | None = None,
    ) -> None:
        self._secret_memory = secret_memory
        self._backend = backend if backend is not None else keyring.get_keyring()
        self._backend_id = f"{type(self._backend).__module__}.{type(self._backend).__qualname__}"

    async def probe(self, installation_id: str) -> OSKeyringProbe:
        validate_id(IdKind.INSTALLATION, installation_id)
        state = OSKeyringState.UNSUPPORTED
        atomic = callable(getattr(self._backend, "set_password_if_absent", None))
        if self._backend_id not in _APPROVED_BACKENDS:
            state = OSKeyringState.UNSUPPORTED
        else:
            try:
                existing = cast(_AtomicBackend, self._backend).get_password(
                    _SERVICE_NAME, installation_id
                )
                state = OSKeyringState.MISSING if existing is None else OSKeyringState.AVAILABLE
            except Exception:
                state = OSKeyringState.LOCKED
        value: dict[str, JsonValue] = {
            "backend_id": self._backend_id,
            "create_if_absent": atomic,
            "installation_id": installation_id,
            "round_trip_load": state in {OSKeyringState.AVAILABLE, OSKeyringState.MISSING},
            "state": state.value,
        }
        return OSKeyringProbe(
            installation_id,
            state,
            self._backend_id,
            atomic,
            state in {OSKeyringState.AVAILABLE, OSKeyringState.MISSING},
            canonical_digest(value),
        )

    async def authorize_first_install(
        self,
        probe: OSKeyringProbe,
        user_presence: UserPresenceCapability | None,
        runtime_support: Mapping[str, JsonValue],
        *,
        service_generation: int,
        pristine_state_digest: str,
    ) -> FirstInstallKeyringAuthority:
        current = await self.probe(probe.installation_id)
        if (
            probe.state is not OSKeyringState.MISSING
            or not probe.create_if_absent
            or not probe.round_trip_load
            or current.probe_digest != probe.probe_digest
            or user_presence is None
            or not _presence_allowed(user_presence, runtime_support)
        ):
            raise OSKeyringError("human_authority_unavailable")
        return FirstInstallKeyringAuthority(
            service_generation,
            pristine_state_digest,
            probe.probe_digest,
            user_presence.candidate_artifact_digest,
            user_presence.release_cell,
            user_presence.capability_evidence_digest,
            _AUTHORITY_TOKEN,
        )

    async def load(self, installation_id: str) -> KeyringInitializationBinding:
        validate_id(IdKind.INSTALLATION, installation_id)
        if self._backend_id not in _APPROVED_BACKENDS:
            raise OSKeyringError("unsupported")
        try:
            encoded = cast(_AtomicBackend, self._backend).get_password(
                _SERVICE_NAME, installation_id
            )
        except Exception:
            raise OSKeyringError("locked") from None
        if encoded is None:
            raise OSKeyringError("missing")
        return self._decode_entry(encoded, installation_id)

    async def create_and_verify(
        self,
        authority: FirstInstallKeyringAuthority,
        binding: KeyringInitializationBinding,
        *,
        service_generation: int,
        pristine_state_digest: str,
        staged_sentinel_verifier: Callable[[memoryview, str], None],
    ) -> KeyringInitializationBinding:
        authority._consume()  # pyright: ignore[reportPrivateUsage]
        if (
            authority.service_generation != service_generation
            or authority.pristine_state_digest != pristine_state_digest
            or self._backend_id not in _APPROVED_BACKENDS
        ):
            raise OSKeyringError("authority_mismatch")
        payload = _encode_binding_payload(binding)
        backend = cast(_AtomicBackend, self._backend)
        try:
            if not backend.set_password_if_absent(_SERVICE_NAME, binding.installation_id, payload):
                raise OSKeyringError("entry_exists")
        except OSKeyringError:
            raise
        except Exception:
            raise OSKeyringError("unsupported") from None
        loaded = await self.load(binding.installation_id)

        def _verify(view: memoryview) -> None:
            staged_sentinel_verifier(view, loaded.correlation_commitment)

        loaded.ivk_handle.consume(SecretConsumer.VAULT_ROOT, _verify)
        return await self.load(binding.installation_id)

    async def delete_after_proven_migration(
        self,
        installation_id: str,
        migration_verifier: Callable[[KeyringInitializationBinding], None],
    ) -> None:
        loaded = await self.load(installation_id)
        try:
            migration_verifier(loaded)
        except Exception:
            raise OSKeyringError("migration_not_proven") from None
        try:
            cast(_AtomicBackend, self._backend).delete_password(_SERVICE_NAME, installation_id)
        except Exception:
            raise OSKeyringError("locked") from None

    def _decode_entry(
        self, encoded: str, expected_installation_id: str
    ) -> KeyringInitializationBinding:
        try:
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            value = strict_json_parse(raw)
            if canonical_encode(value) != raw or type(value) is not dict:
                raise ValueError
            source = cast(dict[str, JsonValue], value)
            if set(source) != {"correlation", "format", "installation_id", "ivk"}:
                raise ValueError
            if (
                source["format"] != "yoetz-keyring-vault-root/1"
                or source["installation_id"] != expected_installation_id
            ):
                raise ValueError
            ivk = bytearray(base64.urlsafe_b64decode(cast(str, source["ivk"]) + "=="))
            correlation = bytearray(
                base64.urlsafe_b64decode(cast(str, source["correlation"]) + "==")
            )
            if len(ivk) != 32 or len(correlation) != 32:
                raise ValueError
        except (ValueError, TypeError, ProtocolValueError) as exc:
            raise OSKeyringError("entry_invalid") from exc
        commitment = f"sha256:{hashlib.sha256(correlation).hexdigest()}"
        return KeyringInitializationBinding(
            1,
            expected_installation_id,
            commitment,
            self._secret_memory.capture(SecretPurpose.VAULT_ROOT_KEY, ivk),
            self._secret_memory.capture(SecretPurpose.VAULT_ROOT_KEY, correlation),
        )


def _encode_binding_payload(binding: KeyringInitializationBinding) -> str:
    def _with_ivk(ivk: memoryview) -> str:
        def _with_correlation(correlation: memoryview) -> str:
            if len(ivk) != 32 or len(correlation) != 32:
                raise OSKeyringError("entry_invalid")
            commitment = f"sha256:{hashlib.sha256(correlation).hexdigest()}"
            if not hmac.compare_digest(commitment, binding.correlation_commitment):
                raise OSKeyringError("correlation_mismatch")
            value: dict[str, JsonValue] = {
                "correlation": base64.urlsafe_b64encode(correlation).rstrip(b"=").decode("ascii"),
                "format": "yoetz-keyring-vault-root/1",
                "installation_id": binding.installation_id,
                "ivk": base64.urlsafe_b64encode(ivk).rstrip(b"=").decode("ascii"),
            }
            return base64.urlsafe_b64encode(canonical_encode(value)).rstrip(b"=").decode("ascii")

        return binding.correlation_handle.consume(SecretConsumer.VAULT_ROOT, _with_correlation)

    return binding.ivk_handle.consume(SecretConsumer.VAULT_ROOT, _with_ivk)


def _presence_allowed(
    capability: UserPresenceCapability, manifest: Mapping[str, JsonValue]
) -> bool:
    if any(
        state != "active"
        for state in (
            capability.os_authenticated_prompt,
            capability.trusted_action_binding,
            capability.one_use_attestation,
            capability.available,
        )
    ):
        return False
    rows = manifest.get("user_presence_cells")
    if type(rows) is not list:
        return False
    for row_value in rows:
        if type(row_value) is not dict:
            continue
        row = cast(dict[str, JsonValue], row_value)
        if (
            row.get("candidate_artifact_digest") == capability.candidate_artifact_digest
            and row.get("release_cell") == capability.release_cell
            and row.get("adapter_id") == capability.adapter_id
            and row.get("profile_id") == capability.profile_id
            and row.get("capability_evidence_digest") == capability.capability_evidence_digest
            and all(
                row.get(name) == "active"
                for name in (
                    "os_authenticated_prompt",
                    "trusted_action_binding",
                    "one_use_attestation",
                    "available",
                )
            )
        ):
            return True
    return False
