"""A control frame larger than one bounded receive is read in chunks (issue #921).

The authenticated Unix stream answers at most 64 KiB per ``receive`` and refuses a larger
request outright (``receive_size_invalid``). The frame reader asked it for the whole remaining
frame at once, so every frame over 64 KiB -- a privacy receipt page of about 45 receipts, a large
status page, a large import request -- failed as ``frame_invalid`` and reached the operator as the
caller's ``invalid_request``, although the peer had sent a valid frame.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Buffer
from datetime import timedelta
from typing import cast

from tests.builders.privacy_receipts import NOW, local_receipt_view, network_receipt_view

from yoetz.adapters.control.unix_socket import AuthenticatedUnixStream, authenticate_peer
from yoetz.application.privacy_control import encode_privacy_receipt_page
from yoetz.domain.values import JsonObject
from yoetz.ports.control import ControlMethod, ControlResult
from yoetz.ports.privacy import PrivacyReceiptPage, PrivacyReceiptView
from yoetz.protocol.ids import IdKind, new_id
from yoetz.service.control_protocol import (
    MAX_CONTROL_RECEIVE_CHUNK_BYTES,
    decode_control_frame,
    encode_control_frame,
    parse_control_result,
    read_control_frame,
    write_control_frame,
)

# The transport's own per-receive ceiling; the reader's bound must never exceed it.
_UNIX_STREAM_RECEIVE_CEILING = 65_536


def _receipt_page(count: int) -> PrivacyReceiptPage:
    views: list[PrivacyReceiptView] = [network_receipt_view()]
    views.extend(
        local_receipt_view(
            receipt_id=new_id(IdKind.EGRESS_RECEIPT),
            finished_at=NOW - timedelta(seconds=index + 1),
        )
        for index in range(count - 1)
    )
    return PrivacyReceiptPage(count + 1, tuple(views), None)


def _receipt_page_frame(count: int) -> bytes:
    return encode_control_frame(
        ControlResult(
            protocol_version="1.0",
            rpc_id=new_id(IdKind.CONTROL_RPC),
            service_instance_id="svc_00000000-0000-4000-8000-000000000002",
            service_generation="1",
            method=ControlMethod.PRIVACY_RECEIPTS_LIST,
            outcome="ok",
            body=encode_privacy_receipt_page(_receipt_page(count)),
        )
    )


class _BoundedStream:
    """Answers exactly like ``AuthenticatedUnixStream.receive`` and records what was asked."""

    peer_identity = object()

    def __init__(self, data: bytes) -> None:
        self._data = bytearray(data)
        self.requested: list[int] = []

    async def receive(self, max_bytes: int) -> bytes:
        self.requested.append(max_bytes)
        if not 1 <= max_bytes <= _UNIX_STREAM_RECEIVE_CEILING:
            raise ValueError("receive_size_invalid")
        chunk = bytes(self._data[:max_bytes])
        del self._data[:max_bytes]
        return chunk

    async def send_all(self, data: Buffer) -> None:
        raise AssertionError(bytes(data))

    async def aclose(self) -> None:
        return None


def test_the_reader_never_asks_for_more_than_one_bounded_receive() -> None:
    assert MAX_CONTROL_RECEIVE_CHUNK_BYTES <= _UNIX_STREAM_RECEIVE_CEILING


# Fifty builder receipts encode to just over one 64 KiB receive; the benchmark's real receipts,
# slightly larger, crossed it at 45. Every receipt schema-validates on encode and decode, so the
# frame is kept to the smallest size that proves the bound.
_JUST_OVER_ONE_RECEIVE = 50


def test_a_receipt_page_over_64_kib_is_read_in_bounded_chunks() -> None:
    receipts = _JUST_OVER_ONE_RECEIVE
    frame = _receipt_page_frame(receipts)
    assert len(frame) - 4 > _UNIX_STREAM_RECEIVE_CEILING

    async def exercise() -> None:
        stream = _BoundedStream(frame)
        decoded = parse_control_result(await read_control_frame(stream))

        body = cast(JsonObject, decoded.body)
        assert len(cast(tuple[object, ...], body["receipts"])) == receipts
        assert max(stream.requested) <= _UNIX_STREAM_RECEIVE_CEILING
        assert len(stream.requested) >= 3

    asyncio.run(exercise())


def test_a_large_frame_crosses_a_real_authenticated_unix_stream() -> None:
    """The product transport itself: a socket pair authenticated as the same user."""

    frame = _receipt_page_frame(_JUST_OVER_ONE_RECEIVE)
    assert len(frame) - 4 > _UNIX_STREAM_RECEIVE_CEILING
    result = parse_control_result(decode_control_frame(frame))

    async def exercise() -> None:
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        writer = AuthenticatedUnixStream(left, authenticate_peer(left))
        reader = AuthenticatedUnixStream(right, authenticate_peer(right))
        try:
            sending = asyncio.create_task(write_control_frame(writer, result))
            received = await asyncio.wait_for(read_control_frame(reader), 30)
            await sending
            assert encode_control_frame(parse_control_result(received)) == frame
        finally:
            await writer.aclose()
            await reader.aclose()

    asyncio.run(exercise())
