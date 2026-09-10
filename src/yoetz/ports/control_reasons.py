"""Lightweight closed control-error vocabulary shared by ports and hook diagnostics."""

from typing import Final

__all__ = ["CONTROL_ERROR_REASONS"]

CONTROL_ERROR_REASONS: Final = frozenset(
    {
        "service_unavailable",
        "service_incompatible",
        "peer_untrusted",
        "protocol_mismatch",
        "frame_invalid",
        "frame_too_large",
        "request_cancelled",
        "request_timeout",
        "vault_locked",
        "service_draining",
        "method_forbidden",
        "internal_error",
        "privacy_projection_unavailable",
        "privacy_projection_blocked",
        "response_projection_failed",
        "read_projection_failed",
        "service_generation_changed",
        "endpoint_unsafe",
        "invalid_request",
    }
)
