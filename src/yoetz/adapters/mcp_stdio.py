"""Bounded strict-UTF-8 JSONL transport for the MCP stdio server."""

from __future__ import annotations

import errno
import json
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final, cast

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import types
from mcp.shared.message import SessionMessage
from pydantic import ValidationError

MAX_JSON_FRAME_BYTES: Final = 1_048_576
_MAX_READ_CHUNK: Final = 65_536
_PARSE_REASONS: Final = frozenset(
    {
        "bom_rejected",
        "duplicate_object_key",
        "empty_frame",
        "invalid_json",
        "invalid_utf8",
        "not_jsonrpc",
        "nul_rejected",
    }
)
_FAILURE_REASONS: Final = frozenset(
    {
        *_PARSE_REASONS,
        "broken_pipe",
        "frame_too_large",
        "invalid_limit",
        "outbound_frame_too_large",
        "partial_eof",
        "read_failed",
        "write_failed",
    }
)

__all__ = ["MAX_JSON_FRAME_BYTES", "TransportFailure", "bounded_stdio_server"]


class TransportFailure(Exception):
    """A bounded adapter-private transport termination reason."""

    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        if type(reason) is not str or reason not in _FAILURE_REASONS:
            raise ValueError("invalid_transport_failure")
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class _WriterItem:
    payload: bytes
    done: anyio.Event | None = None


def _parse_frame(frame: bytes) -> SessionMessage:
    if not frame:
        raise TransportFailure("empty_frame")
    if frame.startswith(b"\xef\xbb\xbf"):
        raise TransportFailure("bom_rejected")
    if b"\x00" in frame:
        raise TransportFailure("nul_rejected")
    try:
        text = frame.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise TransportFailure("invalid_utf8") from exc

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise TransportFailure("duplicate_object_key")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except TransportFailure:
        raise
    except (ValueError, json.JSONDecodeError) as exc:
        raise TransportFailure("invalid_json") from exc
    if type(parsed) is not dict:
        raise TransportFailure("not_jsonrpc")
    try:
        _validate_unicode(cast(dict[object, object], parsed))
    except UnicodeEncodeError as exc:
        raise TransportFailure("invalid_json") from exc
    try:
        message = types.JSONRPCMessage.model_validate(parsed)
    except ValidationError as exc:
        raise TransportFailure("not_jsonrpc") from exc
    return SessionMessage(message)


def _validate_unicode(value: object) -> None:
    if type(value) is str:
        value.encode("utf-8", errors="strict")
    elif type(value) is list:
        for item in cast(list[object], value):
            _validate_unicode(item)
    elif type(value) is dict:
        for key, item in cast(dict[object, object], value).items():
            _validate_unicode(key)
            _validate_unicode(item)


def _serialize_session_message(message: SessionMessage) -> bytes:
    if type(message) is not SessionMessage:
        raise TransportFailure("write_failed")
    payload = message.message.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
    if b"\n" in payload or b"\r" in payload:
        raise TransportFailure("write_failed")
    return payload


def _transport_error_frame(reason: str) -> bytes:
    if reason == "frame_too_large":
        code = -32600
        message = "Frame exceeds maximum size"
    elif reason in _PARSE_REASONS:
        code = -32700
        message = "Parse error"
    else:
        raise TransportFailure(reason)
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": code, "message": message, "data": {"reason": reason}},
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")


def _read_fd(fd: int, maximum: int) -> bytes:
    return os.read(fd, maximum)


def _write_fd(fd: int, payload: bytes) -> int:
    return os.write(fd, payload)


async def _write_all(fd: int, payload: bytes) -> None:
    frame = payload + b"\n"
    offset = 0
    while offset < len(frame):
        try:
            await anyio.wait_writable(fd)
            written = _write_fd(fd, frame[offset:])
        except InterruptedError:
            continue
        except BrokenPipeError as exc:
            raise TransportFailure("broken_pipe") from exc
        except OSError as exc:
            reason = "broken_pipe" if exc.errno == errno.EPIPE else "write_failed"
            raise TransportFailure(reason) from exc
        if written <= 0:
            raise TransportFailure("write_failed")
        offset += written


async def _writer(
    fd: int,
    maximum: int,
    items: MemoryObjectReceiveStream[_WriterItem],
) -> None:
    try:
        async with items:
            async for item in items:
                if len(item.payload) > maximum:
                    raise TransportFailure("outbound_frame_too_large")
                await _write_all(fd, item.payload)
                if item.done is not None:
                    item.done.set()
    except anyio.ClosedResourceError, anyio.EndOfStream:
        await anyio.sleep(0)


async def _outbound_forwarder(
    outbound: MemoryObjectReceiveStream[SessionMessage],
    items: MemoryObjectSendStream[_WriterItem],
) -> None:
    try:
        async with outbound, items:
            async for message in outbound:
                done = anyio.Event()
                await items.send(_WriterItem(_serialize_session_message(message), done))
                await done.wait()
    except anyio.BrokenResourceError, anyio.ClosedResourceError:
        await anyio.sleep(0)


async def _emit_transport_error(items: MemoryObjectSendStream[_WriterItem], reason: str) -> None:
    done = anyio.Event()
    await items.send(_WriterItem(_transport_error_frame(reason), done))
    await done.wait()


async def _reader(
    fd: int,
    maximum: int,
    inbound: MemoryObjectSendStream[SessionMessage],
    items: MemoryObjectSendStream[_WriterItem],
) -> None:
    buffer = bytearray()
    try:
        async with inbound, items:
            while True:
                remaining = maximum + 1 - len(buffer)
                if remaining <= 0:
                    await _emit_transport_error(items, "frame_too_large")
                    return
                try:
                    await anyio.wait_readable(fd)
                    chunk = _read_fd(fd, min(_MAX_READ_CHUNK, remaining))
                except InterruptedError:
                    continue
                except OSError as exc:
                    raise TransportFailure("read_failed") from exc
                if not chunk:
                    if buffer:
                        raise TransportFailure("partial_eof")
                    return
                buffer.extend(chunk)
                while True:
                    delimiter = buffer.find(b"\n")
                    if delimiter < 0:
                        if len(buffer) > maximum:
                            await _emit_transport_error(items, "frame_too_large")
                            return
                        break
                    frame = bytes(buffer[:delimiter])
                    del buffer[: delimiter + 1]
                    try:
                        message = _parse_frame(frame)
                    except TransportFailure as exc:
                        await _emit_transport_error(items, exc.reason)
                        continue
                    await inbound.send(message)
    except anyio.BrokenResourceError, anyio.ClosedResourceError:
        await anyio.sleep(0)


@asynccontextmanager
async def bounded_stdio_server(
    max_json_bytes: int = MAX_JSON_FRAME_BYTES,
) -> AsyncGenerator[
    tuple[
        MemoryObjectReceiveStream[SessionMessage],
        MemoryObjectSendStream[SessionMessage],
    ]
]:
    """Own stdin/stdout and expose zero-capacity validated MCP message streams."""

    minimum = max(
        len(_transport_error_frame(reason))
        for reason in (*sorted(_PARSE_REASONS), "frame_too_large")
    )
    if type(max_json_bytes) is not int or not minimum <= max_json_bytes <= MAX_JSON_FRAME_BYTES:
        raise TransportFailure("invalid_limit")

    stdin_fd = os.dup(0)
    stdout_fd = os.dup(1)
    os.set_blocking(stdin_fd, False)
    os.set_blocking(stdout_fd, False)
    original_stdout = sys.stdout
    sys.stdout = sys.stderr

    inbound_send, inbound_receive = anyio.create_memory_object_stream[SessionMessage](0)
    outbound_send, outbound_receive = anyio.create_memory_object_stream[SessionMessage](0)
    item_send, item_receive = anyio.create_memory_object_stream[_WriterItem](0)
    reader_items = item_send.clone()
    try:
        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(_writer, stdout_fd, max_json_bytes, item_receive)
                tasks.start_soon(_outbound_forwarder, outbound_receive, item_send)
                tasks.start_soon(_reader, stdin_fd, max_json_bytes, inbound_send, reader_items)
                try:
                    yield inbound_receive, outbound_send
                except BaseException:
                    tasks.cancel_scope.cancel()
                    raise
                finally:
                    await inbound_receive.aclose()
                    await outbound_send.aclose()
        except* TransportFailure:
            # Raw I/O failures are adapter-private bounded shutdown reasons. The
            # SDK sees stream closure, never an exception group or OS detail.
            pass
    finally:
        sys.stdout = original_stdout
        for descriptor in (stdin_fd, stdout_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass
