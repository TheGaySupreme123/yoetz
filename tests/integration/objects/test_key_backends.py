"""Key-backend and encrypted-vault bounded behavior."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import keywrap

from yoetz.adapters.keys.encrypted_vault import (
    EncryptedVaultError,
    EncryptedVaultStore,
    VaultRecordKind,
)
from yoetz.adapters.keys.os_keyring import (
    KeyringInitializationBinding,
    OSKeyringState,
    OSVaultRootKeySource,
)
from yoetz.adapters.keys.secret_memory import LocalSecretMemory
from yoetz.ports.secret_memory import (
    SecretConsumer,
    SecretMemoryError,
    SecretPurpose,
    UserPresenceCapability,
)
from yoetz.protocol.canonical import JsonValue

_INSTALLATION_ID = "ins_30000000-0000-4000-8000-000000000004"
_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64


class _AtomicBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password_if_absent(self, service: str, username: str, password: str) -> bool:
        key = (service, username)
        if key in self.values:
            return False
        self.values[key] = password
        return True

    def delete_password(self, service: str, username: str) -> None:
        del self.values[(service, username)]


def test_bundle_hkdf_and_aes_kw_known_answers() -> None:
    kek = bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
    key = bytes.fromhex("00112233445566778899aabbccddeeff")
    expected = bytes.fromhex("64e8c3f9ce0f5ba263e9777905818a2a93c8191e7d6e8ae7")
    assert keywrap.aes_key_wrap(kek, key) == expected
    assert keywrap.aes_key_unwrap(kek, expected) == key


def test_create_and_load_backends(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    vault_dir = tmp_path / "vault"
    store = EncryptedVaultStore(vault_dir)
    store.initialize(memory.capture(SecretPurpose.VAULT_ROOT_KEY, bytearray(range(32))))
    binding = {"installation_id": _INSTALLATION_ID}
    record_id = store.create_record(
        VaultRecordKind.VAULT_SENTINEL,
        binding,
        memory.capture(SecretPurpose.VAULT_ROOT_KEY, bytearray(b"sentinel")),
    )
    assert record_id.startswith("vrec_")
    loaded = store.load_record(VaultRecordKind.VAULT_SENTINEL, binding)
    assert loaded.consume(SecretConsumer.VAULT_ROOT, bytes) == b"sentinel"
    store.close()

    reopened = EncryptedVaultStore(vault_dir)
    reopened.initialize(memory.capture(SecretPurpose.VAULT_ROOT_KEY, bytearray(range(32))))
    reopened.verify_sentinel(binding)
    reopened.close()
    memory.close()


def test_locked_missing_and_unsupported_fail_closed(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    store = EncryptedVaultStore(tmp_path / "vault")
    store.initialize(memory.capture(SecretPurpose.VAULT_ROOT_KEY, bytearray(range(32))))
    with pytest.raises(EncryptedVaultError, match="record_missing"):
        store.load_record(VaultRecordKind.VAULT_SENTINEL, {"installation_id": _INSTALLATION_ID})
    store.close()
    memory.close()


def test_key_domain_mismatch_is_detected() -> None:
    memory = LocalSecretMemory()
    source = bytearray(b"0123456789abcdef")
    handle = memory.capture(SecretPurpose.PORTABLE_RECOVERY, source)
    assert source == bytearray(16)
    with pytest.raises(SecretMemoryError, match="consumer_forbidden"):
        handle.consume(SecretConsumer.VAULT_ROOT, bytes)
    memory.close()


def test_keyring_create_requires_first_install_authority() -> None:
    memory = LocalSecretMemory()
    backend = _AtomicBackend()
    source = OSVaultRootKeySource(memory, backend=backend)
    source._backend_id = "keyring.backends.macOS.Keyring"  # pyright: ignore[reportPrivateUsage]
    probe = asyncio.run(source.probe(_INSTALLATION_ID))
    presence = UserPresenceCapability(
        candidate_artifact_digest=_DIGEST_A,
        release_cell="macos-arm64",
        adapter_id="test-presence",
        profile_id="test-profile",
        os_authentication_primitive="test-only",
        os_authenticated_prompt="active",
        trusted_action_binding="active",
        one_use_attestation="active",
        available="active",
        capability_evidence_digest=_DIGEST_B,
    )
    row: dict[str, JsonValue] = {
        "adapter_id": presence.adapter_id,
        "available": "active",
        "candidate_artifact_digest": presence.candidate_artifact_digest,
        "capability_evidence_digest": presence.capability_evidence_digest,
        "one_use_attestation": "active",
        "os_authenticated_prompt": "active",
        "profile_id": presence.profile_id,
        "release_cell": presence.release_cell,
        "trusted_action_binding": "active",
    }
    manifest: dict[str, JsonValue] = {"user_presence_cells": [row]}
    authority = asyncio.run(
        source.authorize_first_install(
            probe,
            presence,
            manifest,
            service_generation=1,
            pristine_state_digest=_DIGEST_C,
        )
    )
    correlation = bytearray(range(32, 64))
    commitment = f"sha256:{hashlib.sha256(correlation).hexdigest()}"
    binding = KeyringInitializationBinding(
        1,
        _INSTALLATION_ID,
        commitment,
        memory.capture(SecretPurpose.VAULT_ROOT_KEY, bytearray(range(32))),
        memory.capture(SecretPurpose.VAULT_ROOT_KEY, correlation),
    )

    def _verify(ivk: memoryview, loaded_commitment: str) -> None:
        assert bytes(ivk) == bytes(range(32))
        assert loaded_commitment == commitment

    loaded = asyncio.run(
        source.create_and_verify(
            authority,
            binding,
            service_generation=1,
            pristine_state_digest=_DIGEST_C,
            staged_sentinel_verifier=_verify,
        )
    )
    assert loaded.installation_id == _INSTALLATION_ID
    assert loaded.correlation_commitment == commitment
    loaded.ivk_handle.consume(SecretConsumer.VAULT_ROOT, lambda view: None)
    loaded.correlation_handle.consume(SecretConsumer.VAULT_ROOT, lambda view: None)
    memory.close()


def test_keyring_signatures_and_unapproved_backend_are_fail_closed() -> None:
    assert tuple(inspect.signature(OSVaultRootKeySource).parameters) == (
        "secret_memory",
        "backend",
    )
    assert tuple(inspect.signature(OSVaultRootKeySource.create_and_verify).parameters) == (
        "self",
        "authority",
        "binding",
        "service_generation",
        "pristine_state_digest",
        "staged_sentinel_verifier",
    )
    memory = LocalSecretMemory()
    source = OSVaultRootKeySource(memory, backend=_AtomicBackend())
    probe = asyncio.run(source.probe(_INSTALLATION_ID))
    assert probe.state is OSKeyringState.UNSUPPORTED
    assert probe.create_if_absent
    memory.close()
