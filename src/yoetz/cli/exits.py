"""Stable process exits for public Yoetz outcomes."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Literal

from yoetz.protocol.errors import PublicErrorCode

__all__ = [
    "CEREMONY_REFUSAL_MESSAGES",
    "PUBLIC_EXIT_CODES",
    "ceremony_refusal_message",
    "exit_code_for",
]

PUBLIC_EXIT_CODES: Final = MappingProxyType(
    {
        PublicErrorCode.INVALID_REQUEST: 2,
        PublicErrorCode.PROTOCOL_VERSION_UNSUPPORTED: 20,
        PublicErrorCode.SESSION_NOT_FOUND: 10,
        PublicErrorCode.SESSION_CONFLICT: 10,
        PublicErrorCode.IDEMPOTENCY_CONFLICT: 10,
        PublicErrorCode.REQUEST_IDENTITY_CONFLICT: 10,
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


# A confidential ceremony the service answered and *declined* is not an unavailable service.
# Reporting every refusal as service_unavailable sent operators to restart a healthy daemon and
# hid the one fact that told them what to do next.
CEREMONY_REFUSAL_MESSAGES: Final = MappingProxyType(
    {
        "pending_unavailable": (
            "pending_unavailable: that pending decision no longer exists or has expired; "
            "run the check again to get a current one"
        ),
        "pending_not_actionable": (
            "pending_not_actionable: that pending decision cannot be decided as prepared, "
            "usually because the policy changed after it was created; run the check again"
        ),
        "ceremony_unsupported": (
            "ceremony_unsupported: this installation cannot run that confidential ceremony"
        ),
        "kind_forbidden": (
            "kind_forbidden: that ceremony is not permitted for this target in the current "
            "vault mode"
        ),
        "state_forbidden": (
            "state_forbidden: the vault must be unlocked before this ceremony; "
            "run 'yoetz service unlock'"
        ),
    }
)


def ceremony_refusal_message(reason: str) -> str | None:
    """Return the operator-facing line for a structural ceremony refusal, or None."""

    return CEREMONY_REFUSAL_MESSAGES.get(reason)
