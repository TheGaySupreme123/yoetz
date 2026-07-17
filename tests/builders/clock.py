"""Explicit UTC and monotonic-time helpers for tests."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

_TIMESTAMP_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)


def _require_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("timestamp_wrong_type")
    return value


def _require_string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("timestamp_wrong_type")
    return value


def _require_number(value: object, *, reason: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(reason)
    return float(value)


def _require_integer(value: object, *, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(reason)
    return value


def format_utc_millis(value: datetime, /) -> str:
    """Render an explicit UTC datetime with exactly millisecond precision."""

    value = _require_datetime(value)
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp_not_utc")
    truncated = value.replace(microsecond=(value.microsecond // 1_000) * 1_000)
    return truncated.strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + "Z"


def parse_utc_millis(value: str, /) -> datetime:
    """Parse only the canonical persisted timestamp spelling."""

    value = _require_string(value)
    if _TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid_timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ValueError("invalid_timestamp") from exc
    result = parsed.replace(tzinfo=UTC)
    if format_utc_millis(result) != value:
        raise ValueError("invalid_timestamp")
    return result


def monotonic_timestamp(value: float, /) -> float:
    """Validate an explicit finite, nonnegative monotonic reading."""

    result = _require_number(value, reason="monotonic_wrong_type")
    if not math.isfinite(result) or result < 0:
        raise ValueError("monotonic_invalid")
    return result


@dataclass(frozen=True, slots=True)
class FrozenClock:
    """An immutable clock snapshot; progression returns a new explicit snapshot."""

    utc: datetime
    monotonic: float

    def __post_init__(self) -> None:
        format_utc_millis(self.utc)
        object.__setattr__(self, "monotonic", monotonic_timestamp(self.monotonic))

    def now_utc(self) -> datetime:
        return self.utc

    def monotonic_seconds(self) -> float:
        return self.monotonic

    def advanced(self, *, utc_milliseconds: int, monotonic_seconds: float) -> FrozenClock:
        """Return a new snapshot using caller-owned progression values."""

        utc_delta = _require_integer(utc_milliseconds, reason="utc_milliseconds_wrong_type")
        monotonic_delta = _require_number(monotonic_seconds, reason="monotonic_delta_wrong_type")
        if not math.isfinite(monotonic_delta):
            raise ValueError("monotonic_delta_invalid")
        return FrozenClock(
            utc=self.utc + timedelta(milliseconds=utc_delta),
            monotonic=self.monotonic + monotonic_delta,
        )


def frozen_clock(*, utc: datetime, monotonic: float) -> FrozenClock:
    """Construct a clock only from explicit caller-supplied readings."""

    return FrozenClock(utc=utc, monotonic=monotonic)
