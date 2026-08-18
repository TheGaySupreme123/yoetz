"""Receipt object-store faults stay classified instead of becoming INTERNAL_ERROR."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from yoetz.application.receipt import (  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    _persist_object,
    _read_object,
)
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectSource
from yoetz.ports.runtime import TaskRuntime
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

pytestmark = pytest.mark.anyio


def _receipt_ref(*, plaintext_size: int = 2) -> ObjectRef:
    return ObjectRef(
        "obj_ffffffff-0000-4000-8000-000000000001",
        plaintext_size,
        "hmac-sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
        "yoetz-object/1",
        "test-key-1",
        ObjectMetadata(
            ObjectKind.RECEIPT,
            "application/vnd.yoetz.receipt+json",
            "tsk_ffffffff-0000-4000-8000-000000000001",
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )


def _runtime_with_open(open_verified: object) -> TaskRuntime:
    return cast(TaskRuntime, SimpleNamespace(objects=SimpleNamespace(open_verified=open_verified)))


def _runtime_with_store(*, stage: object, finalize: object) -> TaskRuntime:
    return cast(
        TaskRuntime,
        SimpleNamespace(objects=SimpleNamespace(stage=stage, finalize=finalize)),
    )


@pytest.mark.parametrize(
    "error",
    (
        ValueError("object_verification_failed"),
        OSError("read failed"),
        KeyError("missing"),
        TypeError("bad chunk"),
    ),
)
async def test_read_object_maps_store_faults_to_storage_corrupt(error: Exception) -> None:
    def open_verified(_ref: ObjectRef) -> AsyncIterator[bytes]:
        raise error

    with pytest.raises(PublicOperationError) as caught:
        await _read_object(_runtime_with_open(open_verified), _receipt_ref())
    assert caught.value.code is PublicErrorCode.STORAGE_CORRUPT
    assert caught.value.message == "The stored receipt is invalid."
    assert caught.value.retryable is False


async def test_read_object_maps_size_mismatch_to_storage_corrupt() -> None:
    async def open_verified(_ref: ObjectRef) -> AsyncIterator[bytes]:
        yield b"x"

    with pytest.raises(PublicOperationError) as caught:
        await _read_object(_runtime_with_open(open_verified), _receipt_ref(plaintext_size=2))
    assert caught.value.code is PublicErrorCode.STORAGE_CORRUPT
    assert caught.value.message == "The receipt object is invalid."
    assert caught.value.retryable is False


async def test_persist_object_maps_stage_oserror_to_retryable_storage_unsafe() -> None:
    async def stage(_source: ObjectSource, _metadata: ObjectMetadata) -> object:
        raise OSError("No space left on device")

    async def finalize(_staged: object) -> ObjectRef:
        raise AssertionError("finalize must not run after stage failure")

    with pytest.raises(PublicOperationError) as caught:
        await _persist_object(
            _runtime_with_store(stage=stage, finalize=finalize),
            ObjectSource(data=b"{}", declared_size=2),
            _receipt_ref().metadata,
        )
    assert caught.value.code is PublicErrorCode.STORAGE_UNSAFE
    assert caught.value.message == "Receipt object storage is unavailable."
    assert caught.value.retryable is True


async def test_persist_object_maps_finalize_oserror_to_retryable_storage_unsafe() -> None:
    async def stage(_source: ObjectSource, _metadata: ObjectMetadata) -> object:
        return object()

    async def finalize(_staged: object) -> ObjectRef:
        raise OSError("object_destination_collision")

    with pytest.raises(PublicOperationError) as caught:
        await _persist_object(
            _runtime_with_store(stage=stage, finalize=finalize),
            ObjectSource(data=b"{}", declared_size=2),
            _receipt_ref().metadata,
        )
    assert caught.value.code is PublicErrorCode.STORAGE_UNSAFE
    assert caught.value.retryable is True
