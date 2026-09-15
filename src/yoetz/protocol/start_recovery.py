"""Fixed first-start recovery wording selected only by service-owned reason tokens."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from yoetz.protocol.errors import PublicErrorCode


def start_recovery_guidance(code: object, retryable: object, safe_details: object) -> str:
    """Never render an error message, arbitrary detail, or caller-supplied identity."""

    if retryable is not True or not isinstance(safe_details, Mapping):
        return ""
    reason = cast(Mapping[str, object], safe_details).get("reason_code")
    if type(reason) is not str:
        return ""
    if code == PublicErrorCode.BUNDLE_BUSY and reason in {
        "start_runtime_rebind_retry_ready",
        "start_catalog_retry_ready",
        "start_busy_retry_ready",
    }:
        return (
            " The start reservation is retained and its lease was released. Replay the exact "
            "start body and request_id once; no session or writer IDs are needed. If contention "
            "persists, retain that request and report the unresolved start."
        )
    if code == PublicErrorCode.OPERATION_PENDING and reason == "start_lease_pending":
        return (
            " Start still has a live lease. Wait up to 60 seconds, then replay the exact start "
            "body and request_id once. Do not invent session or writer IDs. If still pending, "
            "retain the request and report the unresolved start."
        )
    return ""
