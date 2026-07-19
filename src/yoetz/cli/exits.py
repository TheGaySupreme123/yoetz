"""Stable process exits for public Yoetz outcomes."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Literal

from yoetz.protocol.errors import PublicErrorCode

__all__ = ["PUBLIC_EXIT_CODES", "exit_code_for"]

PUBLIC_EXIT_CODES: Final = MappingProxyType(
    {
        PublicErrorCode.INVALID_REQUEST: 2,
        PublicErrorCode.PROTOCOL_VERSION_UNSUPPORTED: 20,
        PublicErrorCode.SESSION_NOT_FOUND: 10,
        PublicErrorCode.SESSION_CONFLICT: 10,
        PublicErrorCode.IDEMPOTENCY_CONFLICT: 10,
        PublicErrorCode.OPERATION_PENDING: 11,
        PublicErrorCode.FRONTIER_CONFLICT: 10,
        PublicErrorCode.EVENT_INVALID: 2,
        PublicErrorCode.LIMIT_EXCEEDED: 2,
        PublicErrorCode.BUNDLE_BUSY: 20,
        PublicErrorCode.STORAGE_UNSAFE: 20,
        PublicErrorCode.STORAGE_CORRUPT: 40,
        PublicErrorCode.MIGRATION_REQUIRED: 20,
        PublicErrorCode.SERVICE_UNAVAILABLE: 20,
        PublicErrorCode.VAULT_LOCKED: 20,
        PublicErrorCode.PRIVACY_AUTHORITY_REQUIRED: 20,
        PublicErrorCode.PROVIDER_UNAVAILABLE: 30,
        PublicErrorCode.PROVIDER_REFUSED: 30,
        PublicErrorCode.PROVIDER_TIMEOUT: 30,
        PublicErrorCode.SEMANTIC_RESULT_INVALID: 30,
        PublicErrorCode.CANCELLED: 130,
        PublicErrorCode.INTERNAL_ERROR: 70,
    }
)

if set(PUBLIC_EXIT_CODES) != set(PublicErrorCode):
    raise RuntimeError("public_exit_codes_not_exhaustive")


def exit_code_for(outcome: PublicErrorCode | Literal["success", "cancelled"]) -> int:
    """Return the approved shell exit for an exact public outcome."""

    if outcome == "success":
        return 0
    if outcome == "cancelled":
        return 130
    if type(outcome) is not PublicErrorCode:
        raise TypeError("public_outcome_invalid")
    return PUBLIC_EXIT_CODES[outcome]
