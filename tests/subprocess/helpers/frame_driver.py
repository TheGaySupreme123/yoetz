"""Independent byte-level LF-delimited JSON-RPC subprocess driver."""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Final, Literal, cast

from helpers.child import ChildHandle, terminate_owned_group

__all__ = [
    "FrameObservation",
    "FrameStimulus",
    "JsonRpcFrame",
    "drive_frames",
    "encode_valid_frame",
    "parse_protocol_output_exact",
    "partial_write_schedule",
    "slow_reader_schedule",
    "split_at_every_boundary",
]

_SAFE_INTEGER: Final = 9_007_199_254_740_991
_READ_CHUNK: Final = 65_536

type JsonRpcScalar = None | bool | int | str
type JsonRpcValue = JsonRpcScalar | list[JsonRpcValue] | dict[str, JsonRpcValue]
type JsonRpcFrame = dict[str, JsonRpcValue]
type ReadPolicy = Literal["normal", "slow", "paused"]


def _validate_string(value: str) -> str:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("frame_unicode_invalid")
    return value


def _validate_json_value(value: object) -> JsonRpcValue:
    if value is None or type(value) is bool:
        return cast(JsonRpcScalar, value)
    if type(value) is str:
        return _validate_string(value)
    if type(value) is int:
        if not -_SAFE_INTEGER <= value <= _SAFE_INTEGER:
            raise ValueError("frame_unsafe_integer")
        return value
    if type(value) is float:
        raise ValueError("frame_float_forbidden")
    if type(value) is list:
        return [_validate_json_value(member) for member in cast(list[object], value)]
    if type(value) is dict:
        source = cast(dict[object, object], value)
        if any(type(key) is not str for key in source):
            raise ValueError("frame_key_invalid")
        return {
            _validate_string(cast(str, key)): _validate_json_value(member)
            for key, member in source.items()
        }
    raise ValueError("frame_value_invalid")


def _validate_frame(value: object) -> JsonRpcFrame:
    validated = _validate_json_value(value)
    if type(validated) is not dict or validated.get("jsonrpc") != "2.0":
        raise ValueError("jsonrpc_shape_invalid")
    has_method = type(validated.get("method")) is str and bool(validated.get("method"))
    has_result = "result" in validated
    has_error = "error" in validated
    if has_method:
        if has_result or has_error:
            raise ValueError("jsonrpc_shape_invalid")
    elif has_result is has_error or "id" not in validated:
        raise ValueError("jsonrpc_shape_invalid")
    if "method" in validated and not has_method:
        raise ValueError("jsonrpc_shape_invalid")
    if "params" in validated and type(validated["params"]) not in {dict, list}:
        raise ValueError("jsonrpc_shape_invalid")
    identifier = validated.get("id")
    if identifier is not None and type(identifier) not in {str, int}:
        raise ValueError("jsonrpc_id_invalid")
    return validated


@dataclass(frozen=True, slots=True)
class FrameStimulus:
    chunks: tuple[bytes, ...]
    delivery_delays: tuple[float, ...] = ()
    eof: bool = True
    read_policy: ReadPolicy = "normal"
    read_delay_seconds: float = 0.0
    max_output_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if type(self.chunks) is not tuple or any(type(chunk) is not bytes for chunk in self.chunks):
            raise TypeError("frame_chunks_invalid")
        if not self.chunks or any(not chunk for chunk in self.chunks):
            raise ValueError("frame_chunks_invalid")
        if self.delivery_delays and len(self.delivery_delays) != len(self.chunks):
            raise ValueError("frame_delays_invalid")
        if any(
            type(delay) is not float or not math.isfinite(delay) or not 0.0 <= delay <= 5.0
            for delay in self.delivery_delays
        ):
            raise ValueError("frame_delays_invalid")
        if type(self.eof) is not bool or self.read_policy not in {"normal", "slow", "paused"}:
            raise ValueError("frame_policy_invalid")
        if (
            type(self.read_delay_seconds) is not float
            or not math.isfinite(self.read_delay_seconds)
            or not 0.0 <= self.read_delay_seconds <= 5.0
        ):
            raise ValueError("frame_read_delay_invalid")
        if self.read_policy in {"slow", "paused"} and self.read_delay_seconds == 0.0:
            raise ValueError("frame_read_delay_invalid")
        if type(self.max_output_bytes) is not int or not 1 <= self.max_output_bytes <= 16_777_216:
            raise ValueError("frame_output_limit_invalid")


@dataclass(frozen=True, slots=True)
class FrameObservation:
    raw_output_chunks: tuple[bytes, ...]
    raw_output: bytes
    frames: tuple[JsonRpcFrame, ...]
    stderr: bytes
    exit_code: int | None
    signal: int | None
    write_count: int
    read_count: int
    timing_buckets_ms: tuple[int, ...]
    buffer_watermark: int


def encode_valid_frame(value: object) -> bytes:
    frame = _validate_frame(value)
    return (
        json.dumps(
            frame,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _reject_constant(_value: str) -> object:
    raise ValueError("jsonrpc_constant_invalid")


def _parse_integer(value: str) -> int:
    parsed = int(value)
    if not -_SAFE_INTEGER <= parsed <= _SAFE_INTEGER:
        raise ValueError("frame_unsafe_integer")
    return parsed


def _reject_float(_value: str) -> object:
    raise ValueError("frame_float_forbidden")


def _object_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("frame_duplicate_key")
        result[key] = value
    return result


def parse_protocol_output_exact(encoded: bytes) -> tuple[JsonRpcFrame, ...]:
    if type(encoded) is not bytes:
        raise TypeError("protocol_output_not_bytes")
    if not encoded or not encoded.endswith(b"\n"):
        raise ValueError("protocol_output_partial")
    if encoded.startswith(b"\xef\xbb\xbf") or b"\r" in encoded or b"\x00" in encoded:
        raise ValueError("protocol_output_invalid_bytes")
    frames: list[JsonRpcFrame] = []
    for raw in encoded[:-1].split(b"\n"):
        if not raw:
            raise ValueError("protocol_output_blank_frame")
        try:
            text = raw.decode("utf-8", errors="strict")
            decoded = json.loads(
                text,
                object_pairs_hook=_object_no_duplicates,
                parse_constant=_reject_constant,
                parse_float=_reject_float,
                parse_int=_parse_integer,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("protocol_output_invalid_json") from exc
        frames.append(_validate_frame(decoded))
    return tuple(frames)


def split_at_every_boundary(frame: bytes) -> tuple[FrameStimulus, ...]:
    if type(frame) is not bytes or len(frame) < 2:
        raise ValueError("frame_split_invalid")
    return tuple(
        FrameStimulus((frame[:boundary], frame[boundary:])) for boundary in range(1, len(frame))
    )


def partial_write_schedule(frame: bytes, maximum_chunk_bytes: int = 1) -> FrameStimulus:
    if type(frame) is not bytes or not frame:
        raise ValueError("frame_schedule_invalid")
    if type(maximum_chunk_bytes) is not int or not 1 <= maximum_chunk_bytes <= _READ_CHUNK:
        raise ValueError("frame_schedule_invalid")
    chunks = tuple(
        frame[offset : offset + maximum_chunk_bytes]
        for offset in range(0, len(frame), maximum_chunk_bytes)
    )
    return FrameStimulus(chunks)


def slow_reader_schedule(frame: bytes, delay_seconds: float = 0.01) -> FrameStimulus:
    if type(delay_seconds) is not float or not 0.0 < delay_seconds <= 5.0:
        raise ValueError("frame_schedule_invalid")
    return FrameStimulus((frame,), read_policy="slow", read_delay_seconds=delay_seconds)


@dataclass(slots=True)
class _Capture:
    maximum: int
    chunks: list[bytes]
    exceeded: threading.Event

    def append(self, chunk: bytes) -> None:
        if sum(map(len, self.chunks)) + len(chunk) > self.maximum:
            self.exceeded.set()
            return
        self.chunks.append(chunk)


@dataclass(slots=True)
class _WriteState:
    count: int = 0
    error: BaseException | None = None
    done: threading.Event = field(default_factory=threading.Event)


def _write_stimulus(handle: ChildHandle, stimulus: FrameStimulus, state: _WriteState) -> None:
    stream = handle.process.stdin
    if stream is None:
        state.error = RuntimeError("frame_driver_stdin_missing")
        state.done.set()
        return
    delays = stimulus.delivery_delays or (0.0,) * len(stimulus.chunks)
    try:
        for chunk, delay in zip(stimulus.chunks, delays, strict=True):
            if delay:
                time.sleep(delay)
            stream.write(chunk)
            stream.flush()
            state.count += 1
    except (BrokenPipeError, OSError) as exc:
        state.error = exc
    except BaseException as exc:
        state.error = exc
    finally:
        if stimulus.eof:
            try:
                stream.close()
            except OSError:
                pass
        state.done.set()


def _drain_stdout(
    handle: ChildHandle,
    stimulus: FrameStimulus,
    capture: _Capture,
    timing: list[int],
) -> None:
    stream = handle.process.stdout
    if stream is None:
        return
    if stimulus.read_policy == "paused":
        time.sleep(stimulus.read_delay_seconds)
    start = time.monotonic()
    while True:
        chunk = stream.read(_READ_CHUNK)
        if not chunk:
            return
        capture.append(chunk)
        timing.append(int((time.monotonic() - start) * 1_000))
        if stimulus.read_policy == "slow":
            time.sleep(stimulus.read_delay_seconds)


def _drain_stderr(handle: ChildHandle, capture: _Capture) -> None:
    stream = handle.process.stderr
    if stream is None:
        return
    while True:
        chunk = stream.read(_READ_CHUNK)
        if not chunk:
            return
        capture.append(chunk)


def drive_frames(child: ChildHandle, stimulus: FrameStimulus) -> FrameObservation:
    """Deliver exact chunks while independently draining both bounded child streams."""

    if type(child) is not ChildHandle or type(stimulus) is not FrameStimulus:
        raise TypeError("frame_driver_input_invalid")
    process = child.process
    if process.stdin is None:
        raise RuntimeError("frame_driver_stdin_missing")
    stdout = _Capture(stimulus.max_output_bytes, [], threading.Event())
    stderr = _Capture(stimulus.max_output_bytes, [], threading.Event())
    writer_state = _WriteState()
    timing: list[int] = []
    drain_threads = (
        threading.Thread(target=_drain_stdout, args=(child, stimulus, stdout, timing), daemon=True),
        threading.Thread(target=_drain_stderr, args=(child, stderr), daemon=True),
    )
    writer = threading.Thread(
        target=_write_stimulus,
        args=(child, stimulus, writer_state),
        daemon=True,
    )
    for thread in (*drain_threads, writer):
        thread.start()
    try:
        deadline = child.start_monotonic + child.limits.wall_time_seconds
        while process.poll() is None:
            if stdout.exceeded.is_set() or stderr.exceeded.is_set() or time.monotonic() >= deadline:
                terminate_owned_group(child)
                raise RuntimeError("frame_driver_limit_exceeded")
            time.sleep(0.005)
        writer.join(timeout=1.0)
        if writer.is_alive():
            raise RuntimeError("frame_driver_writer_stalled")
        if writer_state.error is not None and not isinstance(
            writer_state.error, (BrokenPipeError, OSError)
        ):
            raise RuntimeError("frame_driver_write_failed") from writer_state.error
        for thread in drain_threads:
            thread.join(timeout=1.0)
        raw_output = b"".join(stdout.chunks)
        frames = () if not raw_output else parse_protocol_output_exact(raw_output)
        return_code = process.returncode
        return FrameObservation(
            raw_output_chunks=tuple(stdout.chunks),
            raw_output=raw_output,
            frames=frames,
            stderr=b"".join(stderr.chunks),
            exit_code=return_code if return_code is not None and return_code >= 0 else None,
            signal=-return_code if return_code is not None and return_code < 0 else None,
            write_count=writer_state.count,
            read_count=len(stdout.chunks),
            timing_buckets_ms=tuple(timing),
            buffer_watermark=max((len(chunk) for chunk in stdout.chunks), default=0),
        )
    except BaseException:
        terminate_owned_group(child)
        raise
