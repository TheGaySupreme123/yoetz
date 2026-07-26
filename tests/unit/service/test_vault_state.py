from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from yoetz.adapters.keys.encrypted_vault import EncryptedVaultStore
from yoetz.adapters.keys.secret_memory import LocalSecretMemory
from yoetz.ports.keys import KeyStoreError, MacKeyPurpose
from yoetz.ports.secret_memory import (
    HumanAuthorizationProof,
    ProviderAttemptAuthBinding,
    SecretMemoryError,
    SecretPurpose,
)
from yoetz.protocol.canonical import canonical_digest
from yoetz.service.vault import (
    ProviderCredentialBinding,
    VaultError,
    VaultMode,
    VaultService,
    VaultState,
)

_INSTALLATION_ID = "ins_00000000-0000-4000-8000-000000000001"
_TASK_ID = "tsk_00000000-0000-4000-8000-000000000002"


@dataclass(slots=True)
class _Clock:
    monotonic: float = 10.0

    def monotonic_seconds(self) -> float:
        return self.monotonic

    def now_utc(self) -> object:
        raise AssertionError("wall clock forbidden")


def _service(tmp_path: Path, memory: LocalSecretMemory, clock: _Clock) -> VaultService:
    vault_dir = tmp_path / "vault"
    return VaultService(
        installation_id=_INSTALLATION_ID,
        service_generation=7,
        mode=VaultMode.UNINITIALIZED,
        secret_memory=memory,
        clock=clock,  # pyright: ignore[reportArgumentType] - wall clock is not sampled by vault
        vault_store_factory=lambda: EncryptedVaultStore(vault_dir),
        pristine_state_digest="sha256:" + "1" * 64,
    )


async def _initialize(service: VaultService, memory: LocalSecretMemory) -> None:
    handle = memory.capture(SecretPurpose.VAULT_INITIALIZE, bytearray(b"correct horse battery"))
    status = await service.initialize_passphrase(handle, "sha256:" + "2" * 64)
    assert status.mode is VaultMode.PASSPHRASE
    assert status.state is VaultState.READY
    assert status.vault_generation == 1


@pytest.mark.anyio
async def test_locked_vault_returns_no_handles_and_has_bounded_status(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    service = _service(tmp_path, memory, _Clock())
    with pytest.raises(KeyStoreError, match="vault_locked"):
        await service.load_bundle_keys(_TASK_ID)
    status = service.status
    assert status.mode is VaultMode.UNINITIALIZED
    assert status.state is VaultState.LOCKED
    assert status.reason is None


@pytest.mark.anyio
async def test_passphrase_initialization_is_distinct_and_first_install_only(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    service = _service(tmp_path, memory, _Clock())
    wrong = memory.capture(SecretPurpose.VAULT_UNLOCK, bytearray(b"correct horse battery"))
    with pytest.raises(VaultError, match="secret_purpose_mismatch"):
        await service.initialize_passphrase(wrong, "sha256:" + "2" * 64)

    await _initialize(service, memory)
    another = memory.capture(SecretPurpose.VAULT_INITIALIZE, bytearray(b"another secure phrase"))
    with pytest.raises(VaultError, match="initialization_forbidden"):
        await service.initialize_passphrase(another, "sha256:" + "2" * 64)


@pytest.mark.anyio
async def test_bundle_and_installation_handles_are_generation_fenced(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    service = _service(tmp_path, memory, _Clock())
    await _initialize(service, memory)
    bundle = await service.create_bundle_keys(_TASK_ID)
    mac = bundle.commitment_key.mac(b"yoetz/object/request/v1\x00", b"payload")
    assert mac.startswith("hmac-sha256:")

    catalog = service.installation_mac_handle(MacKeyPurpose.CATALOG_LOOKUP)
    assert catalog.mac(b"yoetz/start-title/v1\x00", b"title").startswith("hmac-sha256:")
    privacy_audit = service.installation_mac_handle(MacKeyPurpose.PRIVACY_AUDIT)
    assert privacy_audit.mac(
        b"yoetz/privacy-audit/authorization/v1\x00", b"authorization"
    ).startswith("hmac-sha256:")
    with pytest.raises(KeyStoreError, match="mac_domain_forbidden"):
        catalog.mac(b"yoetz/session-log-id/v1\x00", b"title")

    await service.lock()
    with pytest.raises(KeyStoreError, match="stale_key_handle"):
        bundle.commitment_key.mac(b"yoetz/object/request/v1\x00", b"payload")
    with pytest.raises(KeyStoreError, match="stale_key_handle"):
        catalog.mac(b"yoetz/start-title/v1\x00", b"title")


@pytest.mark.anyio
async def test_create_bundle_is_once_and_load_derives_equivalent_operations(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    service = _service(tmp_path, memory, _Clock())
    await _initialize(service, memory)
    created = await service.create_bundle_keys(_TASK_ID)
    with pytest.raises(KeyStoreError):
        await service.create_bundle_keys(_TASK_ID)
    loaded = await service.load_bundle_keys(_TASK_ID)
    domain = b"yoetz/object/result/v1\x00"
    assert created.commitment_key.mac(domain, b"same") == loaded.commitment_key.mac(domain, b"same")


@pytest.mark.anyio
async def test_provider_credential_is_exact_attempt_bound_and_one_use(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    clock = _Clock()
    service = _service(tmp_path, memory, clock)
    await _initialize(service, memory)
    purpose_digest = canonical_digest({"purpose": "semantic-review"})
    binding = ProviderCredentialBinding(
        "openai",
        "gpt-5",
        "openai-responses",
        "1",
        "semantic-review",
        "sha256:" + "3" * 64,
        purpose_digest,
    )
    proof = HumanAuthorizationProof(
        "proof-test",
        "provider_credential_set",
        binding.target_digest("set"),
        7,
        service.generation,
        None,
        9.0,
        20.0,
    )
    credential = memory.capture(
        SecretPurpose.PROVIDER_CREDENTIAL, bytearray(b"sk-test-token-value")
    )
    await service.store_provider_credential("set", binding, credential, proof, 10.0)
    replacement_proof = HumanAuthorizationProof(
        "proof-test-replacement",
        "provider_credential_set",
        binding.target_digest("set"),
        7,
        service.generation,
        None,
        10.0,
        20.0,
    )
    replacement = memory.capture(
        SecretPurpose.PROVIDER_CREDENTIAL, bytearray(b"sk-replacement-value")
    )
    await service.store_provider_credential("set", binding, replacement, replacement_proof, 11.0)
    attempt = ProviderAttemptAuthBinding(
        binding.provider_id,
        binding.model_id,
        binding.endpoint_profile_id,
        binding.endpoint_profile_version,
        binding.purpose,
        binding.authorization_scope_digest,
        binding.purpose_digest,
        "dsp_00000000-0000-4000-8000-000000000004",
        "sha256:" + "4" * 64,
        7,
        15.0,
    )
    handle = await service.provider_credential(attempt)

    class Callback:
        async def inject_and_start(self, credential_view: memoryview) -> str:
            assert hashlib.sha256(credential_view).digest()
            assert bytes(credential_view) == b"sk-replacement-value"
            return "started"

    assert await handle.authorize_attempt(attempt, Callback()) == "started"
    with pytest.raises(SecretMemoryError, match="already_consumed"):
        await handle.authorize_attempt(attempt, Callback())


@pytest.mark.anyio
async def test_uninitialized_keyring_has_no_passphrase_fallback(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    service = _service(tmp_path, memory, _Clock())
    status = await service.initialize(None)
    assert status.mode is VaultMode.UNINITIALIZED
    assert status.state is VaultState.LOCKED
    assert status.reason == "keyring_unavailable"
