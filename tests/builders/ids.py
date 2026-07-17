"""Deterministic, explicit identifier helpers for tests."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

type IdFamily = Literal[
    "request",
    "task",
    "session",
    "writer",
    "event",
    "obligation",
    "claim",
    "action",
    "result",
    "evidence",
    "finding",
    "object",
    "receipt",
]
type Seed = str | bytes

_PREFIX_BY_FAMILY_MUTABLE: dict[IdFamily, str] = {
    "request": "req_",
    "task": "tsk_",
    "session": "ses_",
    "writer": "wri_",
    "event": "evt_",
    "obligation": "obl_",
    "claim": "clm_",
    "action": "act_",
    "result": "res_",
    "evidence": "evd_",
    "finding": "fnd_",
    "object": "obj_",
    "receipt": "rcp_",
}
PREFIX_BY_FAMILY: Final[Mapping[IdFamily, str]] = MappingProxyType(_PREFIX_BY_FAMILY_MUTABLE)
_UUID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ID_DOMAIN: Final[bytes] = b"yoetz/test-id/v1\x00"


def _seed_bytes(seed: object) -> bytes:
    if isinstance(seed, str):
        try:
            value = seed.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("seed_invalid_utf8") from exc
    elif isinstance(seed, bytes):
        value = seed
    else:
        raise TypeError("seed_wrong_type")
    if not value:
        raise ValueError("seed_empty")
    return value


def build_id(family: IdFamily, seed: Seed, /) -> str:
    """Derive a canonical UUIDv4-shaped ID from an explicit test seed."""

    try:
        prefix = PREFIX_BY_FAMILY[family]
    except KeyError as exc:
        raise ValueError("id_family_unknown") from exc

    digest = hashlib.sha256(_ID_DOMAIN + family.encode("ascii") + b"\x00" + _seed_bytes(seed))
    raw = bytearray(digest.digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return prefix + str(uuid.UUID(bytes=bytes(raw)))


def validate_test_id(family: IdFamily, value: object, /) -> str:
    """Validate a test ID without repairing or normalizing hostile input."""

    if not isinstance(value, str):
        raise TypeError("id_wrong_type")
    if len(value) != 40:
        raise ValueError("id_wrong_length")
    try:
        prefix = PREFIX_BY_FAMILY[family]
    except KeyError as exc:
        raise ValueError("id_family_unknown") from exc
    if not value.startswith(prefix):
        raise ValueError("id_wrong_prefix")
    if _UUID_PATTERN.fullmatch(value[4:]) is None:
        raise ValueError("id_malformed_uuid")
    return value


def request_id(seed: Seed, /) -> str:
    return build_id("request", seed)


def operation_id(seed: Seed, /) -> str:
    """Build the request ID reused as the durable operation ID."""

    return request_id(seed)


def task_id(seed: Seed, /) -> str:
    return build_id("task", seed)


def session_id(seed: Seed, /) -> str:
    return build_id("session", seed)


def writer_id(seed: Seed, /) -> str:
    return build_id("writer", seed)


def event_id(seed: Seed, /) -> str:
    return build_id("event", seed)


def obligation_id(seed: Seed, /) -> str:
    return build_id("obligation", seed)


def claim_id(seed: Seed, /) -> str:
    return build_id("claim", seed)


def action_id(seed: Seed, /) -> str:
    return build_id("action", seed)


def result_id(seed: Seed, /) -> str:
    return build_id("result", seed)


def evidence_id(seed: Seed, /) -> str:
    return build_id("evidence", seed)


def finding_id(seed: Seed, /) -> str:
    return build_id("finding", seed)


def object_id(seed: Seed, /) -> str:
    return build_id("object", seed)


def receipt_id(seed: Seed, /) -> str:
    return build_id("receipt", seed)


def entry_digest(canonical_envelope: bytes, /) -> str:
    """Digest explicit canonical envelope bytes; ledger entries have no ID family."""

    envelope = _require_bytes(canonical_envelope)
    if not envelope:
        raise ValueError("canonical_envelope_empty")
    return f"sha256:{hashlib.sha256(envelope).hexdigest()}"


def _require_bytes(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError("canonical_envelope_wrong_type")
    return value
