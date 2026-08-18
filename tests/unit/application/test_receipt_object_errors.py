"""Receipt object-store faults stay classified instead of becoming INTERNAL_ERROR."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from yoetz.application.receipt import (  # noqa: SLF001
    _persist_object,  # pyright: ignore[reportPrivateUsage]
    _read_object,  # pyright: ignore[reportPrivateUsage]
)
from yoetz.observability.diagnostics import lookup_diagnostic_records
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectSource
from yoetz.ports.runtime import TaskRuntime
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

pytestmark = pytest.mark.anyio

_REQUEST_ID = "req_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _diagnostic_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:  # pyright: ignore[reportUnusedFunction]
    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    return tmp_path


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


def _assert_classified(
    error: PublicOperationError,
    *,
    code: PublicErrorCode,
    message: str,
    retryable: bool,
    reason: str,
    operation: str,
    root: Path,
) -> None:
    assert error.code is code
    assert error.message == message
    assert error.retryable is retryable
    assert error.correlation_id is not None
    assert error.correlation_id.startswith("err_")
    found = lookup_diagnostic_records(error.correlation_id, root=root)
    assert len(found) == 1
    assert found[0]["reason"] == reason
    assert found[0]["operation"] == operation
    assert found[0]["component"] == "application.receipt"
    assert found[0]["request_id"] == _REQUEST_ID
    recorded_operation = found[0]["operation"]
    assert type(recorded_operation) is str
    assert "internal_error" not in recorded_operation
    assert "Input/output error" not in error.message
    assert "object_verification_failed" not in error.message


@pytest.mark.parametrize(
    ("error", "code", "message", "retryable", "reason"),
    (
        (
            ValueError("object_verification_failed"),
            PublicErrorCode.STORAGE_CORRUPT,
            "The stored receipt is invalid.",
            False,
            "exception_value_error",
        ),
        (
            KeyError("missing"),
            PublicErrorCode.STORAGE_CORRUPT,
            "The stored receipt is invalid.",
            False,
            "exception_key_error",
        ),
        (
            TypeError("bad chunk"),
            PublicErrorCode.STORAGE_CORRUPT,
            "The stored receipt is invalid.",
            False,
            "exception_type_error",
        ),
        (
            OSError(5, "Input/output error"),
            PublicErrorCode.STORAGE_UNSAFE,
            "Receipt object storage is unavailable.",
            True,
            "exception_os_error",
        ),
    ),
)
async def test_read_object_classifies_store_faults(
    error: Exception,
    code: PublicErrorCode,
    message: str,
    retryable: bool,
    reason: str,
    _diagnostic_dir: Path,
) -> None:
    def open_verified(_ref: ObjectRef) -> AsyncIterator[bytes]:
        raise error

    with pytest.raises(PublicOperationError) as caught:
        await _read_object(
            _runtime_with_open(open_verified), _receipt_ref(), request_id=_REQUEST_ID
        )
    _assert_classified(
        caught.value,
        code=code,
        message=message,
        retryable=retryable,
        reason=reason,
        operation="receipt_object_read",
        root=_diagnostic_dir,
    )


async def test_read_object_maps_size_mismatch_to_storage_corrupt(_diagnostic_dir: Path) -> None:
    async def open_verified(_ref: ObjectRef) -> AsyncIterator[bytes]:
        yield b"x"

    with pytest.raises(PublicOperationError) as caught:
        await _read_object(
            _runtime_with_open(open_verified),
            _receipt_ref(plaintext_size=2),
            request_id=_REQUEST_ID,
        )
    _assert_classified(
        caught.value,
        code=PublicErrorCode.STORAGE_CORRUPT,
        message="The receipt object is invalid.",
        retryable=False,
        reason="exception_value_error",
        operation="receipt_object_read",
        root=_diagnostic_dir,
    )


async def test_persist_object_maps_stage_oserror_to_retryable_storage_unsafe(
    _diagnostic_dir: Path,
) -> None:
    async def stage(_source: ObjectSource, _metadata: ObjectMetadata) -> object:
        raise OSError("No space left on device")

    async def finalize(_staged: object) -> ObjectRef:
        raise AssertionError("finalize must not run after stage failure")

    with pytest.raises(PublicOperationError) as caught:
        await _persist_object(
            _runtime_with_store(stage=stage, finalize=finalize),
            ObjectSource(data=b"{}", declared_size=2),
            _receipt_ref().metadata,
            request_id=_REQUEST_ID,
        )
    _assert_classified(
        caught.value,
        code=PublicErrorCode.STORAGE_UNSAFE,
        message="Receipt object storage is unavailable.",
        retryable=True,
        reason="exception_os_error",
        operation="receipt_object_persist",
        root=_diagnostic_dir,
    )
    assert "No space left on device" not in caught.value.message


async def test_persist_object_maps_finalize_oserror_to_retryable_storage_unsafe(
    _diagnostic_dir: Path,
) -> None:
    async def stage(_source: ObjectSource, _metadata: ObjectMetadata) -> object:
        return object()

    async def finalize(_staged: object) -> ObjectRef:
        raise OSError("object_destination_collision")

    with pytest.raises(PublicOperationError) as caught:
        await _persist_object(
            _runtime_with_store(stage=stage, finalize=finalize),
            ObjectSource(data=b"{}", declared_size=2),
            _receipt_ref().metadata,
            request_id=_REQUEST_ID,
        )
    _assert_classified(
        caught.value,
        code=PublicErrorCode.STORAGE_UNSAFE,
        message="Receipt object storage is unavailable.",
        retryable=True,
        reason="exception_os_error",
        operation="receipt_object_persist",
        root=_diagnostic_dir,
    )
    assert "object_destination_collision" not in caught.value.message
