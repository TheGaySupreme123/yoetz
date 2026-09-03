"""Codex lifecycle mapping store unit tests."""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from yoetz.adapters.integrations.codex_lifecycle import (
    MAPPING_VERSION,
    LifecycleMapping,
    acquire_session_lock,
    clear_mapping,
    encode_frontier_token,
    load_latest_mapping,
    load_mapping,
    mapping_from_start_ids,
    store_mapping,
    validate_codex_session_id,
)
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.ids import IdKind, new_id


def _ids() -> tuple[str, str, str, str]:
    return (
        "codex-session-abc123",
        new_id(IdKind.TASK),
        new_id(IdKind.SESSION),
        new_id(IdKind.WRITER),
    )


def test_mapping_round_trip(tmp_path: Path) -> None:
    codex_session_id, task_id, session_id, writer_id = _ids()
    frontier = encode_frontier_token(sequence="0", head_digest="genesis")
    mapping = mapping_from_start_ids(
        codex_session_id=codex_session_id,
        yoetz_task_id=task_id,
        yoetz_session_id=session_id,
        yoetz_writer_id=writer_id,
        last_frontier=frontier,
    )
    store_mapping(mapping, _state=tmp_path)
    loaded = load_mapping(codex_session_id, _state=tmp_path)
    assert loaded == mapping
    path = tmp_path / "codex-lifecycle" / f"{codex_session_id}.json"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_rejects_extra_keys_and_wrong_version(tmp_path: Path) -> None:
    codex_session_id, task_id, session_id, writer_id = _ids()
    store_mapping(
        mapping_from_start_ids(
            codex_session_id=codex_session_id,
            yoetz_task_id=task_id,
            yoetz_session_id=session_id,
            yoetz_writer_id=writer_id,
            last_frontier=None,
        ),
        _state=tmp_path,
    )
    path = tmp_path / "codex-lifecycle" / f"{codex_session_id}.json"
    path.write_text(
        json.dumps(
            {
                "mapping_version": MAPPING_VERSION,
                "codex_session_id": codex_session_id,
                "yoetz_task_id": task_id,
                "yoetz_session_id": session_id,
                "yoetz_writer_id": writer_id,
                "last_frontier": None,
                "title": "secret prose",
            }
        ),
        encoding="utf-8",
    )
    assert load_mapping(codex_session_id, _state=tmp_path) is None
    path.write_text(
        json.dumps(
            {
                "mapping_version": 99,
                "codex_session_id": codex_session_id,
                "yoetz_task_id": task_id,
                "yoetz_session_id": session_id,
                "yoetz_writer_id": writer_id,
                "last_frontier": None,
            }
        ),
        encoding="utf-8",
    )
    assert load_mapping(codex_session_id, _state=tmp_path) is None


def test_rejects_oversized_file(tmp_path: Path) -> None:
    codex_session_id = "session-oversize"
    directory = tmp_path / "codex-lifecycle"
    directory.mkdir(parents=True, mode=0o700)
    path = directory / f"{codex_session_id}.json"
    path.write_bytes(b"{" + (b"x" * 5000) + b"}")
    assert load_mapping(codex_session_id, _state=tmp_path) is None


def test_forbidden_codex_session_tokens() -> None:
    with pytest.raises(ProtocolValueError):
        validate_codex_session_id("../escape")
    with pytest.raises(ProtocolValueError):
        validate_codex_session_id("has/slash")
    with pytest.raises(ProtocolValueError):
        validate_codex_session_id("x" * 200)
    with pytest.raises(ProtocolValueError):
        validate_codex_session_id("has space")


def test_store_rejects_bad_ids(tmp_path: Path) -> None:
    with pytest.raises(ProtocolValueError):
        store_mapping(
            LifecycleMapping(
                mapping_version=1,
                codex_session_id="ok-session",
                yoetz_task_id="not-a-task",
                yoetz_session_id=new_id(IdKind.SESSION),
                yoetz_writer_id=new_id(IdKind.WRITER),
                last_frontier=None,
            ),
            _state=tmp_path,
        )


def test_clear_mapping(tmp_path: Path) -> None:
    codex_session_id, task_id, session_id, writer_id = _ids()
    store_mapping(
        mapping_from_start_ids(
            codex_session_id=codex_session_id,
            yoetz_task_id=task_id,
            yoetz_session_id=session_id,
            yoetz_writer_id=writer_id,
            last_frontier=None,
        ),
        _state=tmp_path,
    )
    clear_mapping(codex_session_id, _state=tmp_path)
    assert load_mapping(codex_session_id, _state=tmp_path) is None


def test_load_latest_mapping_uses_valid_mapping_write_recency(tmp_path: Path) -> None:
    first_session, first_task, first_yoetz_session, first_writer = _ids()
    second_session = "codex-session-def456"
    second = mapping_from_start_ids(
        codex_session_id=second_session,
        yoetz_task_id=new_id(IdKind.TASK),
        yoetz_session_id=new_id(IdKind.SESSION),
        yoetz_writer_id=new_id(IdKind.WRITER),
        last_frontier=None,
    )
    first = mapping_from_start_ids(
        codex_session_id=first_session,
        yoetz_task_id=first_task,
        yoetz_session_id=first_yoetz_session,
        yoetz_writer_id=first_writer,
        last_frontier=None,
    )
    store_mapping(first, _state=tmp_path)
    store_mapping(second, _state=tmp_path)
    directory = tmp_path / "codex-lifecycle"
    os.utime(directory / f"{first_session}.json", ns=(1_000_000_000, 1_000_000_000))
    os.utime(directory / f"{second_session}.json", ns=(2_000_000_000, 2_000_000_000))

    assert load_latest_mapping((first_session, second_session), _state=tmp_path) == second

    (directory / f"{second_session}.json").write_text("invalid", encoding="ascii")
    assert load_latest_mapping((first_session, second_session), _state=tmp_path) == first


def test_session_lock_coalesces_concurrent_acquirers(tmp_path: Path) -> None:
    session_id = "lock-session-1"
    results: list[bool] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        with acquire_session_lock(session_id, _state=tmp_path, stale_seconds=30.0) as owned:
            results.append(owned)
            if owned:
                # Hold briefly so the other contender observes the lock.
                import time

                time.sleep(0.05)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1
    assert results.count(False) == 1


def test_stale_lock_can_be_broken(tmp_path: Path) -> None:
    session_id = "stale-lock"
    lock = tmp_path / "codex-lifecycle"
    lock.mkdir(parents=True, mode=0o700)
    path = lock / f".{session_id}.lock"
    path.write_text("old\n", encoding="ascii")
    old = path.stat().st_mtime - 120
    os.utime(path, (old, old))
    with acquire_session_lock(session_id, _state=tmp_path, stale_seconds=1.0) as owned:
        assert owned is True
