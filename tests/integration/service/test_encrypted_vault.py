from __future__ import annotations

from pathlib import Path

import pytest

from yoetz.adapters.keys.encrypted_vault import (
    EncryptedVaultError,
    EncryptedVaultStore,
    VaultRecordKind,
)
from yoetz.adapters.keys.secret_memory import LocalSecretMemory
from yoetz.ports.secret_memory import SecretConsumer, SecretPurpose

_INSTALLATION_ID = "ins_00000000-0000-4000-8000-000000000001"
_TASK_ID = "tsk_00000000-0000-4000-8000-000000000002"


def _capture(memory: LocalSecretMemory, value: bytes, purpose: SecretPurpose):
    return memory.capture(purpose, bytearray(value))


def test_records_round_trip_without_plaintext_or_independent_mac_key_records(
    tmp_path: Path,
) -> None:
    memory = LocalSecretMemory()
    root = b"r" * 32
    store = EncryptedVaultStore(tmp_path / "vault")
    store.initialize(_capture(memory, root, SecretPurpose.VAULT_ROOT_KEY))
    store.create_record(
        VaultRecordKind.VAULT_SENTINEL,
        {"installation_id": _INSTALLATION_ID},
        _capture(memory, b"sentinel-canary", SecretPurpose.VAULT_ROOT_KEY),
    )
    store.create_record(
        VaultRecordKind.BUNDLE_KEY,
        {"task_id": _TASK_ID, "key_slot": "bmk-test"},
        _capture(memory, b"bundle-master-key-canary-32byt!", SecretPurpose.VAULT_ROOT_KEY),
    )
    loaded = store.load_record(
        VaultRecordKind.BUNDLE_KEY,
        {"task_id": _TASK_ID, "key_slot": "bmk-test"},
    )
    assert loaded.consume(SecretConsumer.VAULT_ROOT, bytes) == b"bundle-master-key-canary-32byt!"

    disk = b"".join(path.read_bytes() for path in (tmp_path / "vault").iterdir())
    assert b"sentinel-canary" not in disk
    assert b"bundle-master-key-canary" not in disk
    assert b"catalog_lookup" not in disk
    assert b"log_correlation" not in disk
    assert b"privacy_audit" not in disk


def test_wrong_root_and_frame_tamper_fail_closed(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    vault_dir = tmp_path / "vault"
    store = EncryptedVaultStore(vault_dir)
    store.initialize(_capture(memory, b"a" * 32, SecretPurpose.VAULT_ROOT_KEY))
    store.create_record(
        VaultRecordKind.VAULT_SENTINEL,
        {"installation_id": _INSTALLATION_ID},
        _capture(memory, b"sentinel", SecretPurpose.VAULT_ROOT_KEY),
    )
    store.close()

    wrong = EncryptedVaultStore(vault_dir)
    with pytest.raises(EncryptedVaultError):
        wrong.initialize(_capture(memory, b"b" * 32, SecretPurpose.VAULT_ROOT_KEY))

    frame = next(vault_dir.glob("vrec_*.1.yzv"))
    data = bytearray(frame.read_bytes())
    data[-1] ^= 1
    frame.write_bytes(data)
    reopened = EncryptedVaultStore(vault_dir)
    with pytest.raises(EncryptedVaultError):
        reopened.initialize(_capture(memory, b"a" * 32, SecretPurpose.VAULT_ROOT_KEY))
