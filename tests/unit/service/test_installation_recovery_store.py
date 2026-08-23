"""Managed recovery-set publication, status, and crash-state tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from yoetz.adapters.keys.installation_recovery import (
    InstallationRecoveryMode,
    InstallationRecoverySecretKind,
    create_installation_recovery_artifact,
)
from yoetz.adapters.keys.secret_memory import LocalSecretMemory
from yoetz.ports.secret_memory import SecretPurpose
from yoetz.service.installation_recovery import (
    InstallationRecoverySetStore,
    InstallationRecoveryState,
)


def _artifact(
    memory: LocalSecretMemory,
    generation: int = 1,
    *,
    mode: InstallationRecoveryMode = InstallationRecoveryMode.COMPACT,
    snapshot_digest: str | None = None,
):
    return create_installation_recovery_artifact(
        memory.capture(SecretPurpose.VAULT_ROOT_KEY, bytearray(b"v" * 32)),
        memory.capture(
            SecretPurpose.INSTALLATION_RECOVERY,
            bytearray(b"correct horse battery staple"),
        ),
        recovery_generation=generation,
        mode=mode,
        secret_kind=InstallationRecoverySecretKind.ARGON2ID_PASSPHRASE,
        snapshot_manifest_digest=snapshot_digest,
    )


def _store(tmp_path: Path) -> InstallationRecoverySetStore:
    bundle = tmp_path / "bundle"
    bundle.mkdir(mode=0o700)
    return InstallationRecoverySetStore(bundle)


def test_stage_is_not_advertised_until_matching_metadata_activates(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    store = _store(tmp_path)
    metadata = store.stage(_artifact(memory))

    before = store.status(
        installation_exists=True,
        vault_ready=False,
        ordinary_unlock_available=False,
        auto_unlock_repairable=False,
    )
    assert before.state is InstallationRecoveryState.TEMPORARILY_LOCKED
    assert before.active_generation is None

    store.activate(metadata)
    after = store.status(
        installation_exists=True,
        vault_ready=False,
        ordinary_unlock_available=False,
        auto_unlock_repairable=False,
    )
    assert after.state is InstallationRecoveryState.RECOVERY_MATERIAL_REQUIRED
    assert after.active_generation == 1
    assert after.available_modes == ("compact",)
    assert after.next_command == "yoetz service recovery restore"


def test_recovery_continuation_is_opaque_restart_state(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    store = _store(tmp_path)
    store.publish(_artifact(memory))

    continuation = store.begin_recovery(1)
    assert len(continuation) == 64
    active = store.status(
        installation_exists=True,
        vault_ready=False,
        ordinary_unlock_available=False,
        auto_unlock_repairable=False,
    )
    assert active.state is InstallationRecoveryState.RECOVERY_IN_PROGRESS
    assert active.continuation_id == continuation

    reopened = InstallationRecoverySetStore(tmp_path / "bundle")
    assert reopened.status(
        installation_exists=True,
        vault_ready=False,
        ordinary_unlock_available=False,
        auto_unlock_repairable=False,
    ).continuation_id == continuation
    reopened.finish_recovery(continuation, success=True)
    finished = reopened.status(
        installation_exists=True,
        vault_ready=True,
        ordinary_unlock_available=True,
        auto_unlock_repairable=False,
    )
    assert finished.state is InstallationRecoveryState.RECOVERED
    assert finished.continuation_id is None


def test_wrong_continuation_cannot_finish_or_replace_owner(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    store = _store(tmp_path)
    store.publish(_artifact(memory))
    continuation = store.begin_recovery(1)

    with pytest.raises(RuntimeError, match="installation_recovery_state_conflict"):
        store.finish_recovery("0" * 64, success=True)
    with pytest.raises(RuntimeError, match="installation_recovery_state_conflict"):
        store.begin_recovery(1)

    store.finish_recovery(continuation, success=False)
    assert store.status(
        installation_exists=True,
        vault_ready=False,
        ordinary_unlock_available=False,
        auto_unlock_repairable=False,
    ).state is InstallationRecoveryState.RECOVERY_MATERIAL_REQUIRED


def test_rotation_advances_active_generation_and_revoke_withdraws_it(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    store = _store(tmp_path)
    store.publish(_artifact(memory, 1))
    second = store.stage(_artifact(memory, 2))
    store.activate(second)
    assert store.status(
        installation_exists=True,
        vault_ready=False,
        ordinary_unlock_available=False,
        auto_unlock_repairable=False,
    ).active_generation == 2
    with pytest.raises(RuntimeError, match="installation_recovery_state_conflict"):
        store.begin_recovery(1)

    store.revoke(2)
    revoked = store.status(
        installation_exists=True,
        vault_ready=False,
        ordinary_unlock_available=False,
        auto_unlock_repairable=False,
    )
    assert revoked.state is InstallationRecoveryState.TEMPORARILY_LOCKED
    assert revoked.reason == "recovery_material_revoked"
    assert revoked.available_modes == ()
    with pytest.raises(RuntimeError, match="installation_recovery_state_conflict"):
        store.begin_recovery(2)


def test_tampered_state_fails_closed_without_permanent_loss_claim(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    store = _store(tmp_path)
    store.publish(_artifact(memory))
    state = tmp_path / "bundle" / "installation-recovery" / "state.json"
    encoded = bytearray(state.read_bytes())
    encoded[-3] = ord("0") if encoded[-3] != ord("0") else ord("1")
    state.write_bytes(encoded)
    state.chmod(0o600)

    with pytest.raises(ValueError, match="recovery_state_invalid"):
        store.status(
            installation_exists=True,
            vault_ready=False,
            ordinary_unlock_available=False,
            auto_unlock_repairable=False,
        )


def test_self_contained_snapshot_uses_sqlite_backup_and_manifest_last(tmp_path: Path) -> None:
    memory = LocalSecretMemory()
    store = _store(tmp_path)
    bundle = tmp_path / "bundle"
    marker = bundle / "installation-state.json"
    marker.write_bytes(b"private structural marker\n")
    marker.chmod(0o600)
    database = bundle / "catalog.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sample(value TEXT NOT NULL)")
    connection.execute("INSERT INTO sample VALUES ('captured')")
    connection.commit()
    connection.close()
    database.chmod(0o600)
    vault = bundle / "vault"
    vault.mkdir(mode=0o700)
    ciphertext = vault / "member.yzv"
    ciphertext.write_bytes(b"ciphertext")
    ciphertext.chmod(0o600)

    snapshot = store.prepare_snapshot(1)
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO sample VALUES ('after-preview')")
    connection.commit()
    connection.close()
    artifact = _artifact(
        memory,
        mode=InstallationRecoveryMode.SELF_CONTAINED,
        snapshot_digest=snapshot.manifest_digest,
    )
    metadata = store.stage(artifact, snapshot)
    store.activate(metadata)

    captured = sqlite3.connect(
        bundle / "installation-recovery" / "sets" / "1" / "snapshot" / "members" / "catalog.sqlite3"
    )
    try:
        assert captured.execute("SELECT value FROM sample").fetchall() == [("captured",)]
    finally:
        captured.close()
    assert store.metadata(1).mode is InstallationRecoveryMode.SELF_CONTAINED

    archive = tmp_path / "recovery.yirs"
    export_digest = store.export_generation(1, archive)
    assert export_digest.startswith("sha256:")
    imported_bundle = tmp_path / "imported"
    imported_bundle.mkdir(mode=0o700)
    imported = InstallationRecoverySetStore(imported_bundle)
    imported_metadata = imported.import_archive(archive)
    assert imported_metadata == metadata
    imported_database = sqlite3.connect(
        imported_bundle
        / "installation-recovery"
        / "sets"
        / "1"
        / "snapshot"
        / "members"
        / "catalog.sqlite3"
    )
    try:
        assert imported_database.execute("SELECT value FROM sample").fetchall() == [("captured",)]
    finally:
        imported_database.close()
    assert imported.install_snapshot_into_pristine(1) == snapshot.manifest_digest
    assert (imported_bundle / "installation-state.json").read_bytes() == marker.read_bytes()
    restored_database = sqlite3.connect(imported_bundle / "catalog.sqlite3")
    try:
        assert restored_database.execute("SELECT value FROM sample").fetchall() == [("captured",)]
    finally:
        restored_database.close()
    assert any(
        path.name.startswith(".imported.pristine-before-recovery.")
        for path in tmp_path.iterdir()
    )


def test_snapshot_uses_quarantined_rotated_vault_instead_of_live_vault(
    tmp_path: Path,
) -> None:
    memory = LocalSecretMemory()
    store = _store(tmp_path)
    bundle = tmp_path / "bundle"
    marker = bundle / "installation-state.json"
    marker.write_bytes(b"private structural marker\n")
    marker.chmod(0o600)
    live_vault = bundle / "vault"
    live_vault.mkdir(mode=0o700)
    live_member = live_vault / "member.yzv"
    live_member.write_bytes(b"old-root-ciphertext")
    live_member.chmod(0o600)
    rotated_vault = bundle / ".vault.root-2.test.tmp"
    rotated_vault.mkdir(mode=0o700)
    rotated_member = rotated_vault / "member.yzv"
    rotated_member.write_bytes(b"new-root-ciphertext")
    rotated_member.chmod(0o600)

    snapshot = store.prepare_snapshot(2, vault_override=rotated_vault)
    artifact = _artifact(
        memory,
        2,
        mode=InstallationRecoveryMode.SELF_CONTAINED,
        snapshot_digest=snapshot.manifest_digest,
    )
    store.stage(artifact, snapshot)

    captured = (
        bundle
        / "installation-recovery"
        / "sets"
        / "2"
        / "snapshot"
        / "members"
        / "vault"
        / "member.yzv"
    )
    assert captured.read_bytes() == b"new-root-ciphertext"
    assert b"old-root-ciphertext" not in b"".join(
        path.read_bytes()
        for path in captured.parent.rglob("*")
        if path.is_file()
    )


def test_snapshot_rejects_unknown_plaintext_member_instead_of_exporting_it(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    bundle = tmp_path / "bundle"
    marker = bundle / "installation-state.json"
    marker.write_bytes(b"private structural marker\n")
    marker.chmod(0o600)
    canary = b"user-content-canary-must-never-enter-recovery-export"
    unknown = bundle / "operator-notes.txt"
    unknown.write_bytes(canary)
    unknown.chmod(0o600)

    with pytest.raises(ValueError, match="installation_snapshot_unknown_member"):
        store.prepare_snapshot(1)

    recovery_root = bundle / "installation-recovery"
    assert not any(
        canary in path.read_bytes()
        for path in recovery_root.rglob("*")
        if path.is_file()
    )
