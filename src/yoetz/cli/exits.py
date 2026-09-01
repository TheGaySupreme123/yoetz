"""Stable process exits for public Yoetz outcomes."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Literal

from yoetz.protocol.errors import PublicErrorCode

__all__ = [
    "CEREMONY_REFUSAL_MESSAGES",
    "LIFECYCLE_PUBLIC_CODES",
    "PUBLIC_EXIT_CODES",
    "REMEDIATION_MESSAGES",
    "ceremony_refusal_message",
    "exit_code_for",
    "lifecycle_public_code",
    "remediation_message",
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


# Each closed lifecycle reason names a true operating condition. Collapsing them into
# internal_error told an operator the service had died when it was alive and holding its
# singleton, and sent the whole incident down the wrong diagnostic path (#237).
# ``BUNDLE_BUSY`` is the only member of the frozen public enum whose meaning is "a resource is
# already held by another owner"; it exits 20, like every other entry here.
# ``invalid_transition`` is deliberately absent: it is a genuine internal defect and stays
# INTERNAL_ERROR / 70.
LIFECYCLE_PUBLIC_CODES: Final = MappingProxyType(
    {
        "service_already_running": PublicErrorCode.BUNDLE_BUSY,
        "service_draining": PublicErrorCode.SERVICE_UNAVAILABLE,
        "vault_locked": PublicErrorCode.VAULT_LOCKED,
        "session_monitor_unavailable": PublicErrorCode.SERVICE_UNAVAILABLE,
        "human_authorization_required": PublicErrorCode.PRIVACY_AUTHORITY_REQUIRED,
        "human_authorization_stale": PublicErrorCode.PRIVACY_AUTHORITY_REQUIRED,
    }
)


def lifecycle_public_code(reason: str) -> PublicErrorCode | None:
    """Return the public outcome for a bounded lifecycle reason, or None when unmapped."""

    return LIFECYCLE_PUBLIC_CODES.get(reason)


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
            "run 'yoetz service unlock', or 'yoetz service recovery status' if ordinary "
            "unlock authority may be lost"
        ),
        # Covers both a malformed paste and a provider that refused the key. Either way nothing
        # durable should look "configured" when the credential cannot be used.
        "secret_rejected": (
            "secret_rejected: that credential was not accepted, so nothing was saved. "
            "Check the key is complete, current, and belongs to the configured provider, "
            "then run 'yoetz provider credential set' again"
        ),
    }
)


def ceremony_refusal_message(reason: str) -> str | None:
    """Return the operator-facing line for a structural ceremony refusal, or None."""

    return CEREMONY_REFUSAL_MESSAGES.get(reason)


# Bounded elevated-bootstrap and human-ceremony tokens are stable contract, but a bare token on
# stderr tells an operator nothing about what to do next. These are the remediation halves; the
# caller keeps printing the token so existing machine-readable expectations stay intact.
REMEDIATION_MESSAGES: Final = MappingProxyType(
    {
        "support_resource_set_mismatch": (
            "the runtime support document describes a different resource set; in a source "
            "checkout run 'python scripts/sync_resource_ripple.py --write', which owns that "
            "regeneration order"
        ),
        "support_digest_mismatch": (
            "support/runtime-support.json is not self-consistent; in a source checkout run "
            "'python scripts/sync_resource_ripple.py --write' to rebuild its digests"
        ),
        "resource_digest_mismatch": (
            "an installed resource does not match the reviewed manifest; reinstall Yoetz from a "
            "verified artifact, or regenerate the resource manifest in a source checkout"
        ),
        "manifest_digest_mismatch": (
            "the installed resource manifest is not self-consistent; reinstall Yoetz from a "
            "verified artifact, or in a source checkout run "
            "'python scripts/sync_resource_ripple.py --write'"
        ),
        "resource_missing": (
            "a reviewed installed resource is absent; reinstall Yoetz from a verified artifact"
        ),
        "resource_counts_invalid": (
            "the compiled resource counts do not match the reviewed manifest; after an "
            "intentional inventory change, update REVIEWED_RESOURCE_COUNT and run "
            "'python scripts/sync_resource_ripple.py --write' in a source checkout"
        ),
        "trusted_console_required": (
            "this ceremony needs a foreground terminal Yoetz owns (stdin and stderr on the same "
            "tty, in the foreground process group). From an agent session run "
            "'yoetz consent prepare <operation>' then 'yoetz consent authorize' instead"
        ),
        "human_authority_unavailable": (
            "no verified foreground console was found for this review; run "
            "'yoetz consent review' directly on a local terminal"
        ),
        "chat_user_attestation_invalid": (
            "the relayed attestation was not accepted; --client-kind must name an allowlisted "
            "first-party client (currently 'codex')"
        ),
        "chat_user_target_mismatch": (
            "the supplied pending id or digests do not match the pending decision; the pending "
            "record is stale, or prepare and authorize ran from different working directories. "
            "Run 'yoetz consent prepare' again and pass its exact digests"
        ),
        "chat_user_warning_required": (
            "a credential-bearing approve requires --warning-acknowledged after showing the "
            "danger text to the person instructing you"
        ),
        "chat_user_reauthentication_unavailable": (
            "this installation has no keyring auto-unlock secret, so the credential cannot be "
            "stored from an agent session; run the ceremony on a local terminal with "
            "'yoetz provider credential set'"
        ),
        "repository_privacy_scope_unavailable": (
            "this repository is not bound to privacy authority yet; run 'yoetz --privacy' "
            "(or prepare and authorize 'repository_privacy_grant') with the service running"
        ),
        "provider_credential_required": (
            "this operation stores a provider credential; pipe exactly one into "
            "'yoetz consent authorize --provider-credential-stdin'"
        ),
        "provider_credential_invalid": (
            "the piped credential must be 1..8192 bytes with no NUL, carriage return, or newline"
        ),
        "provider_not_configured": (
            "no provider and model are configured for this installation; run 'yoetz --set' first"
        ),
        "unsafe_root": (
            "the workspace or Git metadata has a symlinked, foreign-owned, writable, or otherwise "
            "unsafe ancestor; pass the fully resolved path to a repository owned by the current user"
        ),
        "git_config_limit_exceeded": (
            "the repository's .git/config exceeds the bounded 1 MiB safety scan; remove stale local "
            "branch configuration or use a fresh clone"
        ),
        "pending_expired": (
            "that pending decision expired before it was authorized; run "
            "'yoetz consent prepare <operation>' again"
        ),
        "pending_already_active": (
            "another pending decision is already active; authorize or deny it with "
            "'yoetz consent authorize', or wait for it to expire"
        ),
        "service_already_running": (
            "another local service process already holds the singleton lock, so a second one "
            "cannot start. Inspect it with 'yoetz service status'; if it stays unresponsive, "
            "stop it with 'yoetz service stop' or end the holding process, then start again"
        ),
        "service_draining": (
            "the local service is shutting down; wait for it to exit, then start it again"
        ),
        "storage_unsafe": (
            "the local Yoetz observation store has an unsafe file or path shape; repair its "
            "owner-only files, permissions, or symlinks and retry"
        ),
        "storage_unavailable": (
            "the local Yoetz observation store could not be opened or locked from this process; "
            "make the owner-only state directory accessible and writable from the supported host "
            "surface, then retry"
        ),
        "result_invalid": (
            "the operation produced a result the consent schema does not admit, so the "
            "approval was recorded as failed and nothing was approved; run "
            "'yoetz consent prepare <operation>' again, and report a defect if this recurs"
        ),
        # Canonical config-loading tokens surfaced by provider lifecycle commands (#520). Each
        # names the failing condition without echoing file content or private paths.
        "config_toml_invalid": (
            "the selected Yoetz config file is not valid TOML; fix the syntax error and retry"
        ),
        "config_value_invalid": (
            "a value in the selected Yoetz config file (or a YOETZ_ override) is outside the "
            "reviewed configuration model; correct it and retry"
        ),
        "unknown_config_key": (
            "the selected Yoetz config file names a key the reviewed configuration model does "
            "not admit; remove or correct that key and retry"
        ),
        "config_file_unreadable": (
            "the selected Yoetz config file exists but could not be read; check its permissions "
            "and retry"
        ),
        "config_file_too_large": (
            "the selected Yoetz config file exceeds the bounded 64 KiB limit; remove unrelated "
            "content and retry"
        ),
        "config_schema_unsupported": (
            "the selected Yoetz config file declares a schema_version this installation does "
            'not support; set schema_version = "1" or upgrade Yoetz'
        ),
        "secret_in_config": (
            "the selected Yoetz config file contains a secret-named key; config.toml is "
            "nonsecret — provision credentials with 'yoetz provider credential set' instead"
        ),
        "secret_env_forbidden": (
            "a secret-named YOETZ_ environment variable is set; Yoetz never reads secrets from "
            "the environment — unset it and provision credentials with "
            "'yoetz provider credential set'"
        ),
        "unknown_config_env_var": (
            "an unrecognized YOETZ_-prefixed environment variable is set; unset it or use a "
            "documented configuration variable"
        ),
        "workspace_unresolvable": (
            "the --workspace locator is empty, missing, symlinked, foreign-owned, or otherwise "
            "unsafe; pass the resolved path of a directory owned by the current user (host hooks "
            "render it from CLAUDE_PROJECT_DIR or CURSOR_PROJECT_DIR, so check that variable "
            "when a hook reports this)"
        ),
    }
)


def remediation_message(reason: str) -> str | None:
    """Return the next-step half of an operator-facing line for a bounded token, or None."""

    message = REMEDIATION_MESSAGES.get(reason)
    if message is None and reason.startswith("vault_result_"):
        # One remediation for the whole family: the token names the exact service condition,
        # and the consent approval was consumed as failed, never recorded as approved.
        return (
            "the vault ceremony finished without reaching its exact successful state, so the "
            "approval was recorded as failed; inspect 'yoetz service status', resolve the "
            "named condition, then run 'yoetz consent prepare <operation>' and authorize again"
        )
    return message
