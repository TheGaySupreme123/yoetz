"""Human-review consent for non-default actions (ADR-015 / ADR-016).

Pending consent is owner-only file state. Console input never substitutes for OS user presence.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, NoReturn, cast

from yoetz.config.paths import ensure_owner_only_dir, state_dir
from yoetz.domain.values import ProtocolValueError, validate_commitment, validate_sha256_digest
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.consent import (
    AgentSafePendingModel,
    ConsentCatalogModel,
    ConsentStatusModel,
    RepositoryPrivacyRecipe,
)
from yoetz.service.confidential_protocol import ProviderCredentialTarget

__all__ = [
    "CONSENT_OPERATIONS",
    "ConsentOperationSpec",
    "ElevatedBootstrapError",
    "ElevatedOperation",
    "PendingElevatedConsent",
    "RiskClass",
    "catalog_payload",
    "claim_pending_for_review",
    "clear_pending",
    "complete_review",
    "grant_target_digest",
    "load_pending",
    "operation_spec",
    "prepare_pending",
    "projection_for_status",
    "status_payload",
]

RiskClass = Literal[
    "default_safe",
    "secret_ingress",
    "secret_reauth",
    "review_only",
    "privacy_widen",
]

ElevatedOperation = Literal[
    "vault_initialize",
    "provider_credential_set",
    "provider_credential_rotate",
    "repository_privacy_grant",
    "idle_relock_disable",
    "privacy_policy_widen",
    "backup_execute",
    "restore_execute",
    "migrate_execute",
    "skill_install",
    "harness_mcp_register",
]

_SCHEMA: Final = "yoetz.elevated-bootstrap.pending/2"
_LEGACY_SCHEMA: Final = "yoetz.elevated-bootstrap.pending/1"
_TTL_SECONDS: Final = 15 * 60
_PENDING_NAME: Final = "elevated-bootstrap-pending.json"
_REVIEW_NAME: Final = "elevated-bootstrap-reviewing.json"
_AUDIT_NAME: Final = "elevated-bootstrap-audit.jsonl"
_FORBIDDEN: Final = ("mcp", "argv", "env", "stdin", "config", "transcript")
_PROVIDER_BINDING_KEYS: Final = (
    "provider_id",
    "model_id",
    "endpoint_profile_id",
    "endpoint_profile_version",
    "purpose",
    "scope_digest",
    "purpose_digest",
)
_PROVIDER_REPOSITORY_KEY: Final = "repository_privacy_commitment"
_PROVIDER_BINDING_OPTIONAL_KEYS: Final = frozenset({_PROVIDER_REPOSITORY_KEY})
_GRANT_BINDING_KEYS: Final = (
    "recipe",
    "repository_privacy_commitment",
    "authority_digest",
)
_GRANT_RECIPES: Final[frozenset[RepositoryPrivacyRecipe]] = frozenset(
    {"assisted_review", "private", "metadata_only"}
)


@dataclass(frozen=True, slots=True)
class ConsentOperationSpec:
    """One catalogued non-default (or default-safe reference) operation."""

    operation: str
    risk_class: RiskClass
    summary: str
    danger_text: str
    requires_provider_binding: bool
    requires_grant_binding: bool
    requires_target_digest_arg: bool
    implemented: bool
    agent_chat_authorize_allowed: bool


CONSENT_OPERATIONS: Final[tuple[ConsentOperationSpec, ...]] = (
    ConsentOperationSpec(
        operation="vault_initialize",
        risk_class="secret_ingress",
        summary="Create the local vault passphrase (cloud/no-TTY).",
        danger_text=(
            "DANGER — vault initialize. Creates this installation's encrypted vault and stores a "
            "helper-generated auto-unlock secret in the scoped platform credential store. Review "
            "requires independent action-bound OS user presence; a foreground console alone is "
            "never authorization. Chat-user authorize cannot supply the vault root secret."
        ),
        requires_provider_binding=False,
        requires_grant_binding=False,
        requires_target_digest_arg=False,
        implemented=True,
        agent_chat_authorize_allowed=False,
    ),
    ConsentOperationSpec(
        operation="provider_credential_set",
        risk_class="secret_ingress",
        summary="Store an LLM API credential in the vault.",
        danger_text=(
            "DANGER — provider credential set. Stores an API credential in the local vault for the "
            "exact provider binding. Ordinary chat may retain or expose the value — prefer a "
            "confidential input surface or a limited/rotatable credential, or run "
            "`yoetz consent review` locally. After one clear warning and an explicit current-chat "
            "instruction, the agent may attest and complete this exact action without a second "
            "terminal. Yoetz cannot independently authenticate the chat provenance."
        ),
        requires_provider_binding=True,
        requires_grant_binding=False,
        requires_target_digest_arg=False,
        implemented=True,
        agent_chat_authorize_allowed=True,
    ),
    ConsentOperationSpec(
        operation="provider_credential_rotate",
        risk_class="secret_ingress",
        summary="Rotate a stored LLM API credential.",
        danger_text=(
            "DANGER — provider credential rotate. Replaces a stored API credential for the exact "
            "provider binding. Ordinary chat may retain or expose the value — prefer confidential "
            "input or a limited/rotatable credential, or run `yoetz consent review` locally. "
            "After one clear warning and an explicit current-chat instruction, the agent may "
            "attest and complete this exact action without a second terminal. Yoetz cannot "
            "independently authenticate the chat provenance."
        ),
        requires_provider_binding=True,
        requires_grant_binding=False,
        requires_target_digest_arg=False,
        implemented=True,
        agent_chat_authorize_allowed=True,
    ),
    ConsentOperationSpec(
        operation="repository_privacy_grant",
        risk_class="privacy_widen",
        summary="Grant exact repository privacy recipe (e.g. assisted_review).",
        danger_text=(
            "DANGER — repository privacy grant. Widens or sets external-review permission for one "
            "exact repository recipe. After one warning, an agent attesting an explicit "
            "current-chat instruction may complete this exact prepared grant. Yoetz cannot "
            "independently authenticate that provenance; the stronger local path remains "
            "`yoetz --privacy`."
        ),
        requires_provider_binding=False,
        requires_grant_binding=True,
        requires_target_digest_arg=False,
        implemented=True,
        agent_chat_authorize_allowed=True,
    ),
    ConsentOperationSpec(
        operation="idle_relock_disable",
        risk_class="secret_reauth",
        summary="Disable idle vault relock (weakens locked-session protection).",
        danger_text=(
            "DANGER — disable idle relock. Weakens automatic vault locking. Approve only from the "
            "verified foreground console after reviewing the exact change."
        ),
        requires_provider_binding=False,
        requires_grant_binding=False,
        requires_target_digest_arg=False,
        implemented=False,
        agent_chat_authorize_allowed=False,
    ),
    ConsentOperationSpec(
        operation="privacy_policy_widen",
        risk_class="privacy_widen",
        summary="Widen standing privacy/egress policy.",
        danger_text=(
            "DANGER — privacy policy widen. Allows broader disclosure to agents or providers. "
            "Confirm the exact policy digest only if you intend this widening. Prefer "
            "`repository_privacy_grant` for exact recipe grants from chat."
        ),
        requires_provider_binding=False,
        requires_grant_binding=False,
        requires_target_digest_arg=True,
        implemented=False,
        agent_chat_authorize_allowed=False,
    ),
    ConsentOperationSpec(
        operation="backup_execute",
        risk_class="review_only",
        summary="Execute a backup plan (irreversible artifact write).",
        danger_text=(
            "DANGER — backup execute. Writes a durable backup for the exact plan digest. Approve "
            "only from the verified foreground console after reviewing the preview."
        ),
        requires_provider_binding=False,
        requires_grant_binding=False,
        requires_target_digest_arg=True,
        # Catalogued for ADR-016; prepare refuses until owning CLIs consume a durable grant.
        implemented=False,
        agent_chat_authorize_allowed=False,
    ),
    ConsentOperationSpec(
        operation="restore_execute",
        risk_class="review_only",
        summary="Execute a restore plan (may switch active route).",
        danger_text=(
            "DANGER — restore execute. May replace or switch local ledger state for the exact plan "
            "digest. Confirm only if you reviewed the preview. Recovery secrets never go in chat."
        ),
        requires_provider_binding=False,
        requires_grant_binding=False,
        requires_target_digest_arg=True,
        implemented=False,
        agent_chat_authorize_allowed=False,
    ),
    ConsentOperationSpec(
        operation="migrate_execute",
        risk_class="review_only",
        summary="Execute a storage migration plan.",
        danger_text=(
            "DANGER — migrate execute. Changes on-disk storage layout for the exact plan digest. "
            "Confirm only if you reviewed the preview and backup preflight."
        ),
        requires_provider_binding=False,
        requires_grant_binding=False,
        requires_target_digest_arg=True,
        implemented=False,
        agent_chat_authorize_allowed=False,
    ),
    ConsentOperationSpec(
        operation="skill_install",
        risk_class="review_only",
        summary="Install or replace a harness skill bundle.",
        danger_text=(
            "DANGER — skill install/replace. Changes agent guidance files for the exact preview "
            "digest. Confirm only if you reviewed the file list and digests."
        ),
        requires_provider_binding=False,
        requires_grant_binding=False,
        requires_target_digest_arg=True,
        implemented=False,
        agent_chat_authorize_allowed=False,
    ),
    ConsentOperationSpec(
        operation="harness_mcp_register",
        risk_class="review_only",
        summary="Register Yoetz as an MCP server in a harness config.",
        danger_text=(
            "DANGER — harness MCP registration. Mutates harness config for the exact preview "
            "digest. Confirm only if you reviewed the binary path and serve command."
        ),
        requires_provider_binding=False,
        requires_grant_binding=False,
        requires_target_digest_arg=True,
        implemented=False,
        agent_chat_authorize_allowed=False,
    ),
)

_OPS: Final[dict[str, ConsentOperationSpec]] = {spec.operation: spec for spec in CONSENT_OPERATIONS}


class ElevatedBootstrapError(Exception):
    """Bounded elevated-bootstrap failure with a stable reason token."""

    def __init__(self, reason: str) -> None:
        if type(reason) is not str or not reason:
            raise TypeError("elevated_reason_invalid")
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class PendingElevatedConsent:
    """One active elevated consent pending record."""

    pending_id: str
    operation: ElevatedOperation
    risk_class: RiskClass
    danger_text: str
    danger_digest: str
    created_at_unix: int
    expires_at_unix: int
    target_digest: str
    provider_binding: Mapping[str, str] | None
    grant_binding: Mapping[str, str] | None = None

    def as_json(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "schema": _SCHEMA,
            "pending_id": self.pending_id,
            "state": "pending",
            "operation": self.operation,
            "risk_class": self.risk_class,
            "danger_text": self.danger_text,
            "danger_digest": self.danger_digest,
            "created_at_unix": self.created_at_unix,
            "expires_at_unix": self.expires_at_unix,
            "target_digest": self.target_digest,
        }
        if self.provider_binding is not None:
            payload["provider_binding"] = dict(self.provider_binding)
        if self.grant_binding is not None:
            payload["grant_binding"] = dict(self.grant_binding)
        return payload


def operation_spec(operation: str) -> ConsentOperationSpec:
    try:
        return _OPS[operation]
    except KeyError as exc:
        raise ElevatedBootstrapError("operation_invalid") from exc


def elevated_dir(*, _state: Path | None = None) -> Path:
    root = state_dir() if _state is None else _state
    path = root / "elevated-bootstrap"
    ensure_owner_only_dir(path)
    return path


def pending_path(*, _state: Path | None = None) -> Path:
    return elevated_dir(_state=_state) / _PENDING_NAME


def review_path(*, _state: Path | None = None) -> Path:
    return elevated_dir(_state=_state) / _REVIEW_NAME


def audit_path(*, _state: Path | None = None) -> Path:
    return elevated_dir(_state=_state) / _AUDIT_NAME


def _danger_digest(
    *,
    operation: str,
    risk_class: str,
    danger_text: str,
    target_digest: str,
    pending_id: str,
    expires_at_unix: int,
    provider_binding: Mapping[str, str] | None,
    grant_binding: Mapping[str, str] | None = None,
) -> str:
    body: dict[str, JsonValue] = {
        "danger_text": danger_text,
        "expires_at_unix": expires_at_unix,
        "operation": operation,
        "pending_id": pending_id,
        "risk_class": risk_class,
        "schema": "yoetz.elevated-bootstrap.danger/2",
        "target_digest": target_digest,
    }
    if provider_binding is not None:
        body["provider_binding"] = dict(provider_binding)
    if grant_binding is not None:
        body["grant_binding"] = dict(grant_binding)
    return canonical_digest(body)


def prepare_pending(
    operation: ElevatedOperation,
    *,
    target_digest: str,
    provider_binding: Mapping[str, str] | None = None,
    grant_binding: Mapping[str, str] | None = None,
    _state: Path | None = None,
) -> PendingElevatedConsent:
    spec = operation_spec(operation)
    if not spec.implemented:
        raise ElevatedBootstrapError("operation_not_implemented")
    if spec.requires_provider_binding and provider_binding is None:
        raise ElevatedBootstrapError("provider_binding_required")
    if not spec.requires_provider_binding and provider_binding is not None:
        raise ElevatedBootstrapError("provider_binding_forbidden")
    if spec.requires_grant_binding and grant_binding is None:
        raise ElevatedBootstrapError("grant_binding_required")
    if not spec.requires_grant_binding and grant_binding is not None:
        raise ElevatedBootstrapError("grant_binding_forbidden")
    binding = (
        _validated_provider_binding(provider_binding) if provider_binding is not None else None
    )
    grant = _validated_grant_binding(grant_binding) if grant_binding is not None else None
    try:
        validated_digest = validate_sha256_digest(target_digest)
    except ProtocolValueError as exc:
        raise ElevatedBootstrapError("target_digest_invalid") from exc
    if review_path(_state=_state).is_file():
        raise ElevatedBootstrapError("review_in_progress")
    existing = load_pending(_state=_state)
    if existing is not None and existing.expires_at_unix > int(time.time()):
        raise ElevatedBootstrapError("pending_already_active")
    now = int(time.time())
    pending_id = secrets.token_hex(32)
    text = spec.danger_text
    expires = now + _TTL_SECONDS
    digest = _danger_digest(
        operation=operation,
        risk_class=spec.risk_class,
        danger_text=text,
        target_digest=validated_digest,
        pending_id=pending_id,
        expires_at_unix=expires,
        provider_binding=binding,
        grant_binding=grant,
    )
    pending = PendingElevatedConsent(
        pending_id=pending_id,
        operation=operation,
        risk_class=spec.risk_class,
        danger_text=text,
        danger_digest=digest,
        created_at_unix=now,
        expires_at_unix=expires,
        target_digest=validated_digest,
        provider_binding=binding,
        grant_binding=grant,
    )
    _write_pending(pending, _state=_state)
    _audit(
        {
            "event": "prepare",
            "operation": operation,
            "risk_class": spec.risk_class,
            "pending_id": pending_id,
            "danger_digest": digest,
            "expires_at_unix": expires,
        },
        _state=_state,
    )
    return pending


def _validated_provider_binding(binding: Mapping[str, str]) -> dict[str, str]:
    keys = set(binding)
    required = set(_PROVIDER_BINDING_KEYS)
    if keys != required and keys != required | _PROVIDER_BINDING_OPTIONAL_KEYS:
        raise ElevatedBootstrapError("provider_binding_invalid")
    normalized: dict[str, str] = {}
    for key in _PROVIDER_BINDING_KEYS:
        value = binding[key]
        if type(value) is not str or not value:
            raise ElevatedBootstrapError("provider_binding_invalid")
        if key in {"scope_digest", "purpose_digest"}:
            try:
                normalized[key] = validate_sha256_digest(value)
            except ProtocolValueError as exc:
                raise ElevatedBootstrapError("provider_binding_invalid") from exc
        else:
            normalized[key] = value
    if _PROVIDER_REPOSITORY_KEY in binding:
        try:
            normalized[_PROVIDER_REPOSITORY_KEY] = validate_commitment(
                binding[_PROVIDER_REPOSITORY_KEY]
            )
        except ProtocolValueError as exc:
            raise ElevatedBootstrapError("provider_binding_invalid") from exc
    try:
        ProviderCredentialTarget(
            action="set",
            provider_id=normalized["provider_id"],
            model_id=normalized["model_id"],
            endpoint_profile_id=normalized["endpoint_profile_id"],
            endpoint_profile_version=normalized["endpoint_profile_version"],
            purpose=normalized["purpose"],
            scope_digest=normalized["scope_digest"],
            purpose_digest=normalized["purpose_digest"],
            repository_privacy_commitment=normalized.get(_PROVIDER_REPOSITORY_KEY),
        )
    except (TypeError, ValueError) as exc:
        raise ElevatedBootstrapError("provider_binding_invalid") from exc
    return normalized


def _validated_grant_binding(binding: Mapping[str, str]) -> dict[str, str]:
    if set(binding) != set(_GRANT_BINDING_KEYS):
        raise ElevatedBootstrapError("grant_binding_invalid")
    recipe = binding["recipe"]
    commitment = binding["repository_privacy_commitment"]
    authority_digest = binding["authority_digest"]
    if type(recipe) is not str or recipe not in _GRANT_RECIPES:
        raise ElevatedBootstrapError("grant_binding_invalid")
    if type(authority_digest) is not str:
        raise ElevatedBootstrapError("grant_binding_invalid")
    try:
        validated_commitment = validate_commitment(commitment)
        validated_authority_digest = validate_sha256_digest(authority_digest)
    except ProtocolValueError as exc:
        raise ElevatedBootstrapError("grant_binding_invalid") from exc
    return {
        "recipe": recipe,
        "repository_privacy_commitment": validated_commitment,
        "authority_digest": validated_authority_digest,
    }


def grant_target_digest(grant_binding: Mapping[str, str]) -> str:
    """Canonical target digest for one exact repository privacy grant."""

    binding = _validated_grant_binding(grant_binding)
    return canonical_digest(
        {
            "kind": "repository_privacy_grant",
            "recipe": binding["recipe"],
            "repository_privacy_commitment": binding["repository_privacy_commitment"],
            "authority_digest": binding["authority_digest"],
        }
    )


def _require_int(value: object) -> int:
    if type(value) is not int or isinstance(value, bool):
        raise ElevatedBootstrapError("pending_corrupt")
    return value


def _require_str(value: object) -> str:
    if type(value) is not str or not value:
        raise ElevatedBootstrapError("pending_corrupt")
    return value


def _validated_pending_id(value: object) -> str:
    pending_id = _require_str(value)
    if len(pending_id) != 64 or any(
        character not in "0123456789abcdef" for character in pending_id
    ):
        raise ElevatedBootstrapError("pending_corrupt")
    return pending_id


def _invalidate_legacy(path: Path, *, _state: Path | None) -> NoReturn:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise ElevatedBootstrapError("pending_clear_failed") from exc
    _audit({"event": "legacy_pending_invalidated"}, _state=_state)
    raise ElevatedBootstrapError("legacy_pending_invalidated")


def _load_pending_path(
    path: Path,
    *,
    _state: Path | None,
    expire: bool,
) -> PendingElevatedConsent | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ElevatedBootstrapError("pending_corrupt") from exc
    if not isinstance(raw, dict):
        raise ElevatedBootstrapError("pending_corrupt")
    source = cast(dict[str, object], raw)
    if source.get("schema") == _LEGACY_SCHEMA:
        _invalidate_legacy(path, _state=_state)
    try:
        if source.get("schema") != _SCHEMA:
            raise ElevatedBootstrapError("pending_corrupt")
        expected_keys = {
            "created_at_unix",
            "danger_digest",
            "danger_text",
            "expires_at_unix",
            "operation",
            "pending_id",
            "risk_class",
            "schema",
            "state",
            "target_digest",
        }
        if "provider_binding" in source:
            expected_keys.add("provider_binding")
        if "grant_binding" in source:
            expected_keys.add("grant_binding")
        if set(source) != expected_keys or source.get("state") != "pending":
            raise ElevatedBootstrapError("pending_corrupt")
        operation = _require_str(source["operation"])
        if operation not in _OPS:
            raise ElevatedBootstrapError("pending_corrupt")
        spec = _OPS[operation]
        binding_raw = source.get("provider_binding")
        binding: dict[str, str] | None
        if binding_raw is None:
            binding = None
        elif isinstance(binding_raw, dict) and all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in cast(dict[object, object], binding_raw).items()
        ):
            try:
                binding = _validated_provider_binding(
                    {str(k): str(v) for k, v in cast(dict[object, object], binding_raw).items()}
                )
            except ElevatedBootstrapError as exc:
                raise ElevatedBootstrapError("pending_corrupt") from exc
        else:
            raise ElevatedBootstrapError("pending_corrupt")
        grant_raw = source.get("grant_binding")
        grant: dict[str, str] | None
        if grant_raw is None:
            grant = None
        elif isinstance(grant_raw, dict) and all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in cast(dict[object, object], grant_raw).items()
        ):
            try:
                grant = _validated_grant_binding(
                    {str(k): str(v) for k, v in cast(dict[object, object], grant_raw).items()}
                )
            except ElevatedBootstrapError as exc:
                raise ElevatedBootstrapError("pending_corrupt") from exc
        else:
            raise ElevatedBootstrapError("pending_corrupt")
        if spec.requires_provider_binding and binding is None:
            raise ElevatedBootstrapError("pending_corrupt")
        if not spec.requires_provider_binding and binding is not None:
            raise ElevatedBootstrapError("pending_corrupt")
        if spec.requires_grant_binding and grant is None:
            raise ElevatedBootstrapError("pending_corrupt")
        if not spec.requires_grant_binding and grant is not None:
            raise ElevatedBootstrapError("pending_corrupt")
        risk = _require_str(source["risk_class"])
        danger_text = _require_str(source["danger_text"])
        if risk != spec.risk_class or danger_text != spec.danger_text:
            raise ElevatedBootstrapError("pending_tampered")
        target_digest = validate_sha256_digest(_require_str(source["target_digest"]))
        danger_digest = validate_sha256_digest(_require_str(source["danger_digest"]))
        created_at = _require_int(source["created_at_unix"])
        expires_at = _require_int(source["expires_at_unix"])
        if created_at <= 0 or expires_at - created_at != _TTL_SECONDS:
            raise ElevatedBootstrapError("pending_tampered")
        pending = PendingElevatedConsent(
            pending_id=_validated_pending_id(source["pending_id"]),
            operation=operation,  # type: ignore[arg-type]
            risk_class=risk,  # type: ignore[arg-type]
            danger_text=danger_text,
            danger_digest=danger_digest,
            created_at_unix=created_at,
            expires_at_unix=expires_at,
            target_digest=target_digest,
            provider_binding=binding,
            grant_binding=grant,
        )
    except ElevatedBootstrapError:
        raise
    except (KeyError, TypeError, ValueError, ProtocolValueError) as exc:
        raise ElevatedBootstrapError("pending_corrupt") from exc
    expected = _danger_digest(
        operation=pending.operation,
        risk_class=pending.risk_class,
        danger_text=pending.danger_text,
        target_digest=pending.target_digest,
        pending_id=pending.pending_id,
        expires_at_unix=pending.expires_at_unix,
        provider_binding=pending.provider_binding,
        grant_binding=pending.grant_binding,
    )
    if not _exact_match(expected, pending.danger_digest):
        raise ElevatedBootstrapError("pending_tampered")
    if expire and pending.expires_at_unix <= int(time.time()):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ElevatedBootstrapError("pending_clear_failed") from exc
        _audit(
            {"event": "expire", "pending_id": pending.pending_id, "operation": pending.operation},
            _state=_state,
        )
        return None
    return pending


def load_pending(*, _state: Path | None = None) -> PendingElevatedConsent | None:
    claim = review_path(_state=_state)
    path = pending_path(_state=_state)
    if claim.is_file():
        # Complete an interrupted hard-link claim by ensuring the public pending name is gone.
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ElevatedBootstrapError("pending_clear_failed") from exc
        return None
    return _load_pending_path(path, _state=_state, expire=True)


def clear_pending(*, _state: Path | None = None) -> None:
    path = pending_path(_state=_state)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise ElevatedBootstrapError("pending_clear_failed") from exc


def _exact_match(left: str, right: str) -> bool:
    if type(left) is not str or type(right) is not str or len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def claim_pending_for_review(*, _state: Path | None = None) -> PendingElevatedConsent:
    """Atomically consume one pending request for a verified-console review."""

    pending = load_pending(_state=_state)
    if pending is None:
        raise ElevatedBootstrapError("pending_absent")
    if not operation_spec(pending.operation).implemented:
        raise ElevatedBootstrapError("operation_not_implemented")
    source = pending_path(_state=_state)
    claim = review_path(_state=_state)
    try:
        os.link(source, claim)
    except FileExistsError as exc:
        raise ElevatedBootstrapError("review_in_progress") from exc
    except FileNotFoundError as exc:
        raise ElevatedBootstrapError("pending_absent") from exc
    except OSError as exc:
        raise ElevatedBootstrapError("pending_claim_failed") from exc
    try:
        source.unlink()
    except OSError as exc:
        try:
            claim.unlink(missing_ok=True)
        except OSError:
            pass
        raise ElevatedBootstrapError("pending_claim_failed") from exc
    claimed = _load_pending_path(claim, _state=_state, expire=False)
    if claimed is None or claimed != pending:
        try:
            claim.unlink(missing_ok=True)
        except OSError:
            pass
        raise ElevatedBootstrapError("pending_tampered")
    if claimed.expires_at_unix <= int(time.time()):
        complete_review(claimed, outcome="expired", _state=_state)
        raise ElevatedBootstrapError("pending_expired")
    _audit(
        {
            "event": "review_claimed",
            "pending_id": pending.pending_id,
            "operation": pending.operation,
            "risk_class": pending.risk_class,
            "danger_digest": pending.danger_digest,
        },
        _state=_state,
    )
    return claimed


def complete_review(
    pending: PendingElevatedConsent,
    *,
    outcome: str,
    _state: Path | None = None,
) -> None:
    """Consume the claimed request exactly once for approval, denial, cancellation, or expiry."""

    if type(pending) is not PendingElevatedConsent:
        raise TypeError("pending_review_invalid")
    if outcome not in {"approved", "cancelled", "denied", "expired", "failed"}:
        raise ElevatedBootstrapError("review_outcome_invalid")
    claim = review_path(_state=_state)
    claimed = _load_pending_path(claim, _state=_state, expire=False)
    if claimed is None:
        raise ElevatedBootstrapError("pending_absent")
    if claimed != pending:
        raise ElevatedBootstrapError("pending_tampered")
    try:
        claim.unlink()
    except OSError as exc:
        raise ElevatedBootstrapError("pending_clear_failed") from exc
    _audit(
        {
            "event": "review_consumed",
            "pending_id": pending.pending_id,
            "operation": pending.operation,
            "outcome": outcome,
        },
        _state=_state,
    )


def projection_for_status(
    pending: PendingElevatedConsent | None,
) -> dict[str, JsonValue] | None:
    """Versioned agent-safe projection with exact bounded recovery/authorization guidance."""

    if pending is None:
        return None
    model = AgentSafePendingModel(
        schema="yoetz.consent.pending-agent/3",
        operation=pending.operation,
        risk_class=pending.risk_class,
        pending_id=pending.pending_id,
        danger_digest=pending.danger_digest,
        danger_text=pending.danger_text,
        expires_at_unix=pending.expires_at_unix,
        target_digest=pending.target_digest,
        repository_privacy_recipe=(
            None
            if pending.grant_binding is None
            else cast(
                RepositoryPrivacyRecipe,
                pending.grant_binding["recipe"],
            )
        ),
        review_command=("yoetz", "consent", "review"),
        authorize_command=(
            ("yoetz", "consent", "authorize")
            if operation_spec(pending.operation).agent_chat_authorize_allowed
            else None
        ),
    )
    return cast(dict[str, JsonValue], model.model_dump(mode="json", by_alias=True))


def catalog_payload() -> dict[str, JsonValue]:
    """Agent-facing catalog of default-safe vs consent-required operations."""

    operations: list[JsonValue] = []
    for spec in CONSENT_OPERATIONS:
        hint = f"yoetz consent prepare {spec.operation}"
        if spec.requires_target_digest_arg:
            hint += " --target-digest <sha256:...>"
        if spec.requires_grant_binding:
            hint += " --recipe <assisted_review|private|metadata_only>"
        # Only the profile identity is caller input; the purpose, its digests, and the repository
        # privacy commitment are derived by prepare. Naming them here sent agents hunting for
        # internals they cannot know.
        if spec.requires_provider_binding:
            hint += (
                " --provider-id <id> --model-id <id> --endpoint-profile-id <id>"
                " --endpoint-profile-version <version>"
            )
        operations.append(
            {
                "operation": spec.operation,
                "risk_class": spec.risk_class,
                "summary": spec.summary,
                "implemented": spec.implemented,
                "requires_provider_binding": spec.requires_provider_binding,
                "requires_grant_binding": spec.requires_grant_binding,
                "requires_target_digest_arg": spec.requires_target_digest_arg,
                "agent_chat_authorize_allowed": spec.agent_chat_authorize_allowed,
                "prepare_hint": hint,
            }
        )
    model = ConsentCatalogModel.model_validate(
        {
            "schema": "yoetz.consent.catalog/3",
            "default_safe": [
                "mcp.start",
                "mcp.publish_work",
                "mcp.check",
                "mcp.respond",
                "mcp.status",
                "mcp.receipt",
                "privacy.tighten",
            ],
            "rules": {
                "forbidden_secret_channels": [
                    channel for channel in _FORBIDDEN if channel != "stdin"
                ],
                "no_standing_yolo": True,
                "path_safety_not_waivable_by_consent": True,
                "independent_user_presence_required_for_agent_chat": False,
                "trusted_console_is_not_authority": True,
                "one_pending_at_a_time": True,
                "approval_arguments_forbidden": True,
                "agent_selected_initialization_secret_forbidden": True,
                "authorized_one_shot_stdin_permitted": True,
                "agent_attested_current_chat_instruction_permitted": True,
                "agent_attestation_is_independent_proof": False,
                "compromised_agent_can_forge_attestation": True,
            },
            "operations": operations,
        }
    )
    return cast(dict[str, JsonValue], model.model_dump(mode="json", by_alias=True))


def status_payload(*, _state: Path | None = None) -> dict[str, JsonValue]:
    model = ConsentStatusModel.model_validate(
        {
            "schema": "yoetz.elevated-bootstrap.status/3",
            "pending": projection_for_status(load_pending(_state=_state)),
            "consent_catalog": catalog_payload(),
        }
    )
    return cast(dict[str, JsonValue], model.model_dump(mode="json", by_alias=True))


def _write_pending(pending: PendingElevatedConsent, *, _state: Path | None) -> None:
    path = pending_path(_state=_state)
    ensure_owner_only_dir(path.parent)
    payload = canonical_encode(pending.as_json())
    tmp = path.with_suffix(".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _audit(event: Mapping[str, JsonValue], *, _state: Path | None) -> None:
    path = audit_path(_state=_state)
    ensure_owner_only_dir(path.parent)
    line = canonical_encode({**dict(event), "at_unix": int(time.time())}) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
