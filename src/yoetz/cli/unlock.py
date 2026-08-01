"""Foreground-only confidential local-human ceremony helper."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Final, Literal, Protocol, cast

from yoetz.cli.trusted_console import TrustedConsoleError, TrustedForegroundConsole
from yoetz.domain.privacy import PrivacyPolicyChange, PrivacyPolicyChangeValue
from yoetz.protocol.canonical import JsonValue, canonical_digest
from yoetz.service.confidential_client import (
    ConfidentialClientError,
    HumanControlClient,
    HumanControlSession,
)
from yoetz.service.confidential_protocol import (
    AuthorizationRequiredPhase,
    ConfidentialProtocolError,
    ConfidentialSecretPurpose,
    DecisionAction,
    DecisionRequiredPhase,
    EmptyVaultTarget,
    HumanCeremonyKind,
    HumanOpenTarget,
    HumanPhase,
    HumanPreview,
    HumanResult,
    IdleRelockPolicyChangePreview,
    IdleRelockPolicyResult,
    IdleRelockPolicyTarget,
    KeyringRetryPhase,
    KeyringRetryPreview,
    KeyringRetryResult,
    PortableRecoveryPreview,
    PortableRecoveryResult,
    PortableRecoveryTarget,
    PrivacyDecisionResult,
    PrivacyDisclosureDecisionPreview,
    PrivacyPendingTarget,
    PrivacyPolicyDecisionPreview,
    ProviderCredentialResult,
    ProviderCredentialRotatePreview,
    ProviderCredentialSetPreview,
    ProviderCredentialTarget,
    RetryAction,
    SecretRequiredPhase,
    SelectAuthorizationSourceAction,
    VaultInitializePreview,
    VaultStateResult,
    VaultUnlockPreview,
    validate_passphrase_buffer,
    validate_provider_credential_buffer,
)

__all__ = [
    "DisabledIdleRelockCliPolicyValue",
    "FiniteIdleRelockCliPolicyValue",
    "HumanCeremonyCliError",
    "IdleRelockCliPolicyValue",
    "IdleRelockCliResult",
    "change_idle_relock_policy",
    "initialize_passphrase_vault",
    "portable_recovery",
    "read_vault_passphrase_for_auto_unlock",
    "retry_keyring",
    "rotate_provider_credential",
    "run_human_ceremony",
    "run_human_ceremony_on_terminal",
    "set_provider_credential",
    "unlock_vault",
    "overwrite_secret_buffer",
]

_FIXED_ERROR_REASONS: Final = frozenset(
    {
        "cancelled",
        "confirmation_mismatch",
        "input_invalid",
        "interrupted",
        "preview_invalid",
        "result_invalid",
        "trusted_console_required",
    }
)


class HumanCeremonyCliError(Exception):
    """One bounded helper failure that never reflects preview or input bytes."""

    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        if type(reason) is not str or reason not in _FIXED_ERROR_REASONS:
            raise ValueError("human_ceremony_cli_reason_invalid")
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class FiniteIdleRelockCliPolicyValue:
    seconds: int
    mode: Literal["finite"] = "finite"

    def __post_init__(self) -> None:
        if (
            self.mode != "finite"
            or type(self.seconds) is not int
            or not 60 <= self.seconds <= 86_400
        ):
            raise ValueError("idle_relock_cli_policy_invalid")


@dataclass(frozen=True, slots=True)
class DisabledIdleRelockCliPolicyValue:
    mode: Literal["disabled"] = "disabled"

    def __post_init__(self) -> None:
        if self.mode != "disabled":
            raise ValueError("idle_relock_cli_policy_invalid")


type IdleRelockCliPolicyValue = FiniteIdleRelockCliPolicyValue | DisabledIdleRelockCliPolicyValue


@dataclass(frozen=True, slots=True)
class IdleRelockCliResult:
    outcome: Literal["applied", "denied"]
    previous: IdleRelockCliPolicyValue
    effective: IdleRelockCliPolicyValue
    scope: Literal["service_generation"]
    service_generation: int

    def __post_init__(self) -> None:
        if self.outcome not in {"applied", "denied"}:
            raise ValueError("idle_relock_cli_result_invalid")
        if type(self.previous) not in {
            FiniteIdleRelockCliPolicyValue,
            DisabledIdleRelockCliPolicyValue,
        } or type(self.effective) not in {
            FiniteIdleRelockCliPolicyValue,
            DisabledIdleRelockCliPolicyValue,
        }:
            raise ValueError("idle_relock_cli_result_invalid")
        if (
            self.scope != "service_generation"
            or type(self.service_generation) is not int
            or self.service_generation <= 0
        ):
            raise ValueError("idle_relock_cli_result_invalid")


def overwrite_secret_buffer(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0


class _ForegroundTerminal(TrustedForegroundConsole):
    """Compatibility facade that maps console failures into ceremony failures."""

    __slots__ = ()

    def __enter__(self) -> _ForegroundTerminal:
        try:
            super().__enter__()
        except TrustedConsoleError as exc:
            raise HumanCeremonyCliError(exc.reason) from exc
        return self

    def write(self, value: str) -> None:
        try:
            super().write(value)
        except TrustedConsoleError as exc:
            raise HumanCeremonyCliError(exc.reason) from exc

    def read_choice(self, prompt: str, allowed: tuple[bytes, ...]) -> bytes:
        try:
            return super().read_choice(prompt, allowed)
        except TrustedConsoleError as exc:
            raise HumanCeremonyCliError(exc.reason) from exc

    def read_secret(self, prompt: str, maximum: int) -> bytearray:
        try:
            return super().read_secret(prompt, maximum)
        except TrustedConsoleError as exc:
            raise HumanCeremonyCliError(exc.reason) from exc


class _CeremonyTerminal(Protocol):
    """Small terminal surface shared by prompted and fully supplied ceremonies."""

    def write(self, value: str) -> None: ...

    def read_choice(self, prompt: str, allowed: tuple[bytes, ...]) -> bytes: ...

    def read_secret(self, prompt: str, maximum: int) -> bytearray: ...


class _SuppliedSecretTerminal:
    """Non-prompting facade used only when every possible secret was supplied."""

    __slots__ = ()

    def write(self, value: str) -> None:
        if type(value) is not str:
            raise TypeError("terminal_text_invalid")

    def read_choice(self, prompt: str, allowed: tuple[bytes, ...]) -> bytes:
        del prompt, allowed
        raise HumanCeremonyCliError("input_invalid")

    def read_secret(self, prompt: str, maximum: int) -> bytearray:
        del prompt, maximum
        raise HumanCeremonyCliError("input_invalid")


def _target_json(target: HumanOpenTarget) -> dict[str, JsonValue]:
    if type(target) is EmptyVaultTarget:
        return {"expected_mode": target.expected_mode, "kind": target.kind}
    if type(target) is PortableRecoveryTarget:
        return {
            "confirmed_plan_digest": target.confirmed_plan_digest,
            "kind": target.kind,
            "operation": target.operation,
            "request_id": target.request_id,
        }
    if type(target) is ProviderCredentialTarget:
        return {
            "action": target.action,
            "endpoint_profile_id": target.endpoint_profile_id,
            "endpoint_profile_version": target.endpoint_profile_version,
            "kind": target.kind,
            "model_id": target.model_id,
            "provider_id": target.provider_id,
            "purpose": target.purpose,
            "purpose_digest": target.purpose_digest,
            "scope_digest": target.scope_digest,
        }
    if type(target) is PrivacyPendingTarget:
        return {
            "decision_kind": target.decision_kind,
            "kind": target.kind,
            "pending_id": target.pending_id,
        }
    if type(target) is IdleRelockPolicyTarget:
        result: dict[str, JsonValue] = {"kind": target.kind, "operation": target.operation}
        if target.seconds is not None:
            result["seconds"] = target.seconds
        return result
    raise TypeError("human_target_invalid")


def _verify_preview(
    kind: HumanCeremonyKind,
    target: HumanOpenTarget,
    session: HumanControlSession,
) -> HumanPreview:
    opened = session.opened
    if opened.binding.target_digest != canonical_digest(_target_json(target)):
        raise HumanCeremonyCliError("preview_invalid")
    preview = opened.preview
    valid = False
    if kind is HumanCeremonyKind.VAULT_INITIALIZE:
        valid = type(preview) is VaultInitializePreview
    elif kind is HumanCeremonyKind.VAULT_UNLOCK:
        valid = type(preview) is VaultUnlockPreview
    elif kind is HumanCeremonyKind.KEYRING_RETRY:
        valid = type(preview) is KeyringRetryPreview
    elif kind is HumanCeremonyKind.PORTABLE_RECOVERY:
        valid = (
            type(preview) is PortableRecoveryPreview
            and type(target) is PortableRecoveryTarget
            and preview.operation == target.operation
            and preview.request_id == target.request_id
            and preview.confirmed_plan_digest == target.confirmed_plan_digest
        )
    elif kind is HumanCeremonyKind.PROVIDER_CREDENTIAL_SET:
        valid = (
            type(preview) is ProviderCredentialSetPreview
            and type(target) is ProviderCredentialTarget
            and preview.target == target
        )
    elif kind is HumanCeremonyKind.PROVIDER_CREDENTIAL_ROTATE:
        valid = (
            type(preview) is ProviderCredentialRotatePreview
            and type(target) is ProviderCredentialTarget
            and preview.target == target
        )
    elif kind is HumanCeremonyKind.PRIVACY_POLICY_DECISION:
        valid = (
            type(preview) is PrivacyPolicyDecisionPreview
            and type(target) is PrivacyPendingTarget
            and preview.pending_id == target.pending_id
        )
    elif kind is HumanCeremonyKind.PRIVACY_DISCLOSURE_DECISION:
        valid = (
            type(preview) is PrivacyDisclosureDecisionPreview
            and type(target) is PrivacyPendingTarget
            and preview.pending_id == target.pending_id
            and preview.authorization_change == "none"
        )
    elif kind is HumanCeremonyKind.IDLE_RELOCK_POLICY_CHANGE:
        proposed: object = (
            "disabled"
            if type(target) is IdleRelockPolicyTarget and target.operation == "disable"
            else getattr(target, "seconds", None)
        )
        valid = (
            type(preview) is IdleRelockPolicyChangePreview
            and type(target) is IdleRelockPolicyTarget
            and preview.proposed == proposed
            and preview.service_generation == opened.binding.service_generation
            and preview.target_digest == opened.binding.target_digest
        )
    if not valid:
        raise HumanCeremonyCliError("preview_invalid")
    return preview


# Every label below is fixed here, in the trusted client. The service sends structured
# ``(area, field, before, after)`` records and never explanatory prose, so nothing a proposal
# author writes can appear on this screen — only values drawn from the closed policy vocabulary.
# ASCII only, including the arrow: this is written straight to /dev/tty with no terminal
# capability negotiation, and a change a human cannot read is a change they cannot judge.
_ARROW: Final = " -> "

_CHANGE_GROUPS: Final[tuple[tuple[str, tuple[tuple[str, str], ...]], ...]] = (
    (
        "Destination",
        (
            ("global", "network_egress"),
            ("channel", "enabled"),
            ("channel", "provider"),
            ("channel", "purposes"),
        ),
    ),
    (
        "Information disclosed",
        (
            ("channel", "categories"),
            ("channel", "data_classes"),
            ("review", "sections"),
            ("review", "excerpt_kinds"),
            ("review", "relevance"),
            ("review", "include_finding_prose"),
            ("review", "include_exact_command_text"),
        ),
    ),
    (
        "Authorization",
        (
            ("channel", "preview_required"),
            ("channel", "scope_ceiling"),
            ("channel", "authorization_ttl_seconds"),
            ("global", "effective_scope"),
            ("global", "provider_data_use_evidence"),
        ),
    ),
    (
        "Limits",
        (
            ("channel", "max_bytes"),
            ("channel", "max_tokens"),
            ("review", "max_excerpts"),
            ("review", "max_excerpt_bytes"),
            ("review", "max_total_excerpt_bytes"),
            ("review", "max_timeline_items"),
            ("review", "max_assessments"),
            ("review", "max_change_observations"),
            ("review", "max_omissions"),
        ),
    ),
    (
        "Local visibility",
        (
            ("local_model", "enabled"),
            ("local_model", "binding"),
            ("local_model", "categories"),
            ("local_model", "data_classes"),
            ("agent_context", "categories"),
            ("agent_context", "data_classes"),
            ("human_control", "categories"),
            ("human_control", "data_classes"),
        ),
    ),
)

_CHANGE_LABELS: Final[dict[tuple[str, str], str]] = {
    ("global", "network_egress"): "Data leaving this computer",
    ("global", "effective_scope"): "Policy applies to",
    ("global", "provider_data_use_evidence"): "Current provider data-use evidence",
    ("channel", "enabled"): "Channel",
    ("channel", "provider"): "Provider and model",
    ("channel", "purposes"): "Purposes",
    ("channel", "categories"): "Information allowed",
    ("channel", "data_classes"): "Sensitivity allowed",
    ("channel", "scope_ceiling"): "Authorization ceiling",
    ("channel", "preview_required"): "Confirmation",
    ("channel", "max_bytes"): "Maximum bytes per case",
    ("channel", "max_tokens"): "Maximum tokens per case",
    ("channel", "authorization_ttl_seconds"): "Authorization lifetime (seconds)",
    ("local_model", "enabled"): "Local model processing",
    ("local_model", "binding"): "Local model",
    ("local_model", "categories"): "Information the local model may receive",
    ("local_model", "data_classes"): "Sensitivity the local model may receive",
    ("agent_context", "categories"): "Information released to the agent host",
    ("agent_context", "data_classes"): "Sensitivity released to the agent host",
    ("human_control", "categories"): "Information shown on your own terminal",
    ("human_control", "data_classes"): "Sensitivity shown on your own terminal",
    ("review", "sections"): "Review sections built",
    ("review", "excerpt_kinds"): "Excerpt kinds built",
    ("review", "relevance"): "Selection reach",
    ("review", "include_finding_prose"): "Reviewer sees finding prose",
    ("review", "include_exact_command_text"): "Reviewer sees exact command text",
    ("review", "max_timeline_items"): "Maximum timeline items",
    ("review", "max_assessments"): "Maximum assessments",
    ("review", "max_change_observations"): "Maximum change observations",
    ("review", "max_excerpts"): "Maximum excerpts",
    ("review", "max_omissions"): "Maximum omissions",
    ("review", "max_excerpt_bytes"): "Maximum bytes per excerpt",
    ("review", "max_total_excerpt_bytes"): "Maximum excerpt bytes per case",
}

_CHANNEL_LABELS: Final[dict[str, str]] = {
    "llm_inference": "External model review",
    "product_telemetry": "Product telemetry",
    "crash_diagnostics": "Crash diagnostics",
    "update_checks": "Update checks",
    "capability_testing": "Capability testing",
}

_FLAG_WORDS: Final[dict[tuple[str, str], tuple[str, str]]] = {
    ("global", "network_egress"): ("Allowed", "Not allowed"),
    ("global", "provider_data_use_evidence"): ("Required", "Not required"),
    ("channel", "enabled"): ("On", "Off"),
    ("channel", "preview_required"): ("Ask before every request", "No confirmation"),
    ("local_model", "enabled"): ("On", "Off"),
}


def _change_line_label(change: PrivacyPolicyChange) -> str:
    base = _CHANGE_LABELS[(change.area, change.field)]
    if change.subject is None:
        return base
    channel = _CHANNEL_LABELS.get(change.subject, change.subject)
    return channel if change.field == "enabled" else f"{base} ({channel})"


def _binding_text(labels: tuple[str, ...]) -> str:
    parts = {name: value for name, _, value in (item.partition(":") for item in labels)}
    provider = parts.get("provider")
    model = parts.get("model")
    if provider is None or model is None:
        return ", ".join(labels)
    detail = ", ".join(
        value for value in (parts.get("endpoint"), parts.get("transport")) if value is not None
    )
    return f"{provider} / {model}" + (f" ({detail})" if detail else "")


def _scope_text(labels: tuple[str, ...]) -> str:
    parts = {name: value for name, _, value in (item.partition(":") for item in labels)}
    kind = parts.get("kind")
    if kind is None:
        return ", ".join(labels)
    narrower = ", ".join(
        f"{name} {parts[name]}" for name in ("workspace", "task", "request") if name in parts
    )
    return f"{kind}" + (f" ({narrower})" if narrower else "")


def _change_value_text(change: PrivacyPolicyChange, value: PrivacyPolicyChangeValue) -> str:
    if value.kind == "none":
        return "Not applicable"
    if value.kind == "flag":
        yes, no = _FLAG_WORDS.get((change.area, change.field), ("Yes", "No"))
        return yes if value.flag else no
    if value.kind == "count":
        return str(value.count)
    if not value.labels:
        return "None"
    if change.field in {"provider", "binding"}:
        return _binding_text(value.labels)
    if change.field == "effective_scope":
        return _scope_text(value.labels)
    return ", ".join(value.labels)


def _privacy_policy_change_text(preview: PrivacyPolicyDecisionPreview) -> str:
    widening = tuple(change for change in preview.changes if change.widens)
    lines = [
        "Action: decide privacy policy widening",
        f"Pending: {preview.pending_id}",
        "",
        "Privacy will become less restrictive.",
        f"{len(widening)} of {len(preview.changes)} changes below make it less restrictive; "
        "they are marked (!).",
    ]
    placed: set[tuple[str, str, str]] = set()
    for heading, members in _CHANGE_GROUPS:
        allowed = set(members)
        rows = [change for change in preview.changes if (change.area, change.field) in allowed]
        if not rows:
            continue
        lines.extend(("", heading))
        for change in rows:
            placed.add(change.identity)
            marker = "(!)" if change.widens else "   "
            before = _change_value_text(change, change.before)
            after = _change_value_text(change, change.after)
            lines.append(f"  {marker} {_change_line_label(change)}: {before}{_ARROW}{after}")
    if len(placed) != len(preview.changes):
        # A field the service is allowed to send but this screen has no group for would be
        # silently dropped, which is the exact defect this renderer exists to close.
        raise HumanCeremonyCliError("preview_invalid")
    lines.extend(
        (
            "",
            "The digest below identifies the exact proposal bytes. It is integrity evidence,",
            "not a description of the change; the lines above are the change.",
            f"Diff digest: {preview.diff_digest}",
            "",
        )
    )
    return "\n".join(lines)


def _render_preview(terminal: _CeremonyTerminal, preview: HumanPreview) -> None:
    terminal.write("Yoetz trusted foreground ceremony\n")
    if type(preview) is VaultInitializePreview:
        terminal.write("Action: initialize passphrase vault (irreversible mode selection)\n")
    elif type(preview) is VaultUnlockPreview:
        terminal.write("Action: unlock passphrase vault\n")
    elif type(preview) is KeyringRetryPreview:
        terminal.write(f"Action: retry OS keyring ({preview.operation})\n")
    elif type(preview) is PortableRecoveryPreview:
        terminal.write(
            f"Action: portable recovery {preview.operation}\n"
            f"Request: {preview.request_id}\n"
            f"Plan digest: {preview.confirmed_plan_digest}\n"
            f"Location commitment: {preview.location_commitment}\n"
            f"Content commitment: {preview.content_commitment}\n"
            f"Items: {preview.item_count}; bytes: {preview.total_bytes}\n"
        )
    elif type(preview) in {ProviderCredentialSetPreview, ProviderCredentialRotatePreview}:
        provider = cast(ProviderCredentialSetPreview | ProviderCredentialRotatePreview, preview)
        terminal.write(
            f"Action: provider credential {provider.target.action}\n"
            f"Provider: {provider.target.provider_id}\n"
            f"Model: {provider.target.model_id}\n"
            f"Endpoint profile: {provider.target.endpoint_profile_id}@"
            f"{provider.target.endpoint_profile_version}\n"
            f"Purpose: {provider.target.purpose}\n"
            f"Scope digest: {provider.target.scope_digest}\n"
        )
    elif type(preview) is PrivacyPolicyDecisionPreview:
        terminal.write(_privacy_policy_change_text(preview))
    elif type(preview) is PrivacyDisclosureDecisionPreview:
        terminal.write(
            f"Action: decide one disclosure (authorization change: none)\n"
            f"Pending: {preview.pending_id}\nCategory: {preview.category}\n"
            f"Destination commitment: {preview.destination_commitment}\n"
            f"Bytes: {preview.byte_count}; tokens: {preview.token_count}\n"
            f"Excerpt digest: {preview.excerpt_digest}\n"
            f"Preview:\n{preview.excerpt_preview}\n"
        )
    elif type(preview) is IdleRelockPolicyChangePreview:
        terminal.write(
            f"Action: change idle relock for service generation {preview.service_generation}\n"
            f"Current: {preview.current}; proposed: {preview.proposed}\n"
            "Restart restores the 900-second default. Explicit lock, session lock, suspend, "
            "and monitor loss still relock the service.\n"
        )
    else:
        raise HumanCeremonyCliError("preview_invalid")


def _validate_secret(source: bytearray, purpose: ConfidentialSecretPurpose) -> None:
    view = memoryview(source)
    try:
        if purpose is ConfidentialSecretPurpose.PROVIDER_CREDENTIAL:
            validate_provider_credential_buffer(view)
        else:
            validate_passphrase_buffer(view)
    except ConfidentialProtocolError as exc:
        raise HumanCeremonyCliError("input_invalid") from exc
    finally:
        view.release()


def _needs_confirmation(
    kind: HumanCeremonyKind,
    target: HumanOpenTarget,
    purpose: ConfidentialSecretPurpose,
) -> bool:
    return purpose is ConfidentialSecretPurpose.VAULT_INITIALIZE or (
        kind is HumanCeremonyKind.PORTABLE_RECOVERY
        and type(target) is PortableRecoveryTarget
        and target.operation == "create"
    )


def _read_secret(
    terminal: _CeremonyTerminal,
    kind: HumanCeremonyKind,
    target: HumanOpenTarget,
    purpose: ConfidentialSecretPurpose,
) -> bytearray:
    maximum = 8_192 if purpose is ConfidentialSecretPurpose.PROVIDER_CREDENTIAL else 1_024
    label = (
        "Provider credential: "
        if purpose is ConfidentialSecretPurpose.PROVIDER_CREDENTIAL
        else "Passphrase: "
    )
    first = terminal.read_secret(label, maximum)
    try:
        _validate_secret(first, purpose)
        if not _needs_confirmation(kind, target, purpose):
            return first
        confirmation = terminal.read_secret("Confirm passphrase: ", maximum)
        try:
            _validate_secret(confirmation, purpose)
            if not hmac.compare_digest(first, confirmation):
                raise HumanCeremonyCliError("confirmation_mismatch")
        finally:
            overwrite_secret_buffer(confirmation)
        return first
    except BaseException:
        overwrite_secret_buffer(first)
        raise


async def _send_secret(
    session: HumanControlSession,
    phase: SecretRequiredPhase,
    source: bytearray,
) -> None:
    try:
        secret_client = session._secret_client()  # pyright: ignore[reportPrivateUsage]
        token = session._session_token()  # pyright: ignore[reportPrivateUsage]
        await secret_client.send_once(phase.binding, source, token)
    finally:
        overwrite_secret_buffer(source)


def _expected_result_type(kind: HumanCeremonyKind) -> type[HumanResult]:
    if kind in {HumanCeremonyKind.VAULT_INITIALIZE, HumanCeremonyKind.VAULT_UNLOCK}:
        return VaultStateResult
    if kind is HumanCeremonyKind.KEYRING_RETRY:
        return KeyringRetryResult
    if kind is HumanCeremonyKind.PORTABLE_RECOVERY:
        return PortableRecoveryResult
    if kind in {
        HumanCeremonyKind.PROVIDER_CREDENTIAL_SET,
        HumanCeremonyKind.PROVIDER_CREDENTIAL_ROTATE,
    }:
        return ProviderCredentialResult
    if kind in {
        HumanCeremonyKind.PRIVACY_POLICY_DECISION,
        HumanCeremonyKind.PRIVACY_DISCLOSURE_DECISION,
    }:
        return PrivacyDecisionResult
    return IdleRelockPolicyResult


async def _cancel_quietly(session: HumanControlSession) -> None:
    try:
        await session.cancel()
    except ConfidentialClientError, OSError, RuntimeError:
        pass


async def _drive_session(
    session: HumanControlSession,
    terminal: _CeremonyTerminal,
    kind: HumanCeremonyKind,
    target: HumanOpenTarget,
    current: HumanPhase | HumanResult,
    provider_credential: bytearray | None = None,
    passphrase: bytearray | None = None,
    provider_reauthentication: bytearray | None = None,
) -> tuple[HumanResult, Literal["approve", "deny"] | None]:
    decision: Literal["approve", "deny"] | None = None
    for _ in range(8):
        if type(current) is _expected_result_type(kind):
            return cast(HumanResult, current), decision
        if type(current) is SecretRequiredPhase:
            purpose = current.binding.purpose
            supplied: bytearray | None = None
            if purpose is ConfidentialSecretPurpose.PROVIDER_CREDENTIAL:
                supplied = provider_credential
            elif purpose in {
                ConfidentialSecretPurpose.VAULT_INITIALIZE,
                ConfidentialSecretPurpose.VAULT_UNLOCK,
            }:
                supplied = passphrase
            elif purpose in {
                ConfidentialSecretPurpose.PROVIDER_REAUTHENTICATION,
                ConfidentialSecretPurpose.PRIVACY_REAUTHENTICATION,
                ConfidentialSecretPurpose.SECURITY_REAUTHENTICATION,
            }:
                # Privacy/security widen ceremonies re-auth with the vault passphrase.
                supplied = (
                    provider_reauthentication
                    if purpose is ConfidentialSecretPurpose.PROVIDER_REAUTHENTICATION
                    else passphrase
                )
            if supplied is not None:
                _validate_secret(supplied, purpose)
                secret_buffer = supplied
            else:
                secret_buffer = _read_secret(terminal, kind, target, purpose)
            await _send_secret(session, current, secret_buffer)
        elif type(current) is AuthorizationRequiredPhase:
            authorization_source: Literal["os_user_presence", "secret_reauthentication"]
            if (
                provider_reauthentication is not None or passphrase is not None
            ) and "secret_reauthentication" in current.available_sources:
                authorization_source = "secret_reauthentication"
            elif "os_user_presence" in current.available_sources:
                authorization_source = "os_user_presence"
            elif "secret_reauthentication" in current.available_sources:
                authorization_source = "secret_reauthentication"
            else:
                raise HumanCeremonyCliError("preview_invalid")
            terminal.write(f"Authorization: {authorization_source}\n")
            await session.send_action(SelectAuthorizationSourceAction(authorization_source))
        elif type(current) is KeyringRetryPhase:
            await session.send_action(RetryAction())
        elif type(current) is DecisionRequiredPhase:
            selected = terminal.read_choice(
                "Decision [approve/deny]: ",
                (b"approve", b"deny"),
            )
            decision = "approve" if selected == b"approve" else "deny"
            await session.send_action(DecisionAction(decision))
        else:
            raise HumanCeremonyCliError("result_invalid")
        current = await session.wait_phase_or_result()
    raise HumanCeremonyCliError("result_invalid")


async def _complete_human_ceremony(
    client: HumanControlClient,
    terminal: _CeremonyTerminal,
    kind: HumanCeremonyKind,
    target: HumanOpenTarget,
    provider_credential: bytearray | None,
    passphrase: bytearray | None,
    provider_reauthentication: bytearray | None,
) -> HumanResult:
    session = await client.open(kind, target)
    async with session:
        try:
            preview = _verify_preview(kind, target, session)
            _render_preview(terminal, preview)
            result, _decision = await _drive_session(
                session,
                terminal,
                kind,
                target,
                session.opened.phase,
                provider_credential,
                passphrase,
                provider_reauthentication,
            )
            return result
        except BaseException:
            await _cancel_quietly(session)
            raise


def _validate_supplied_secrets(
    kind: HumanCeremonyKind,
    provider_credential: bytearray | None,
    passphrase: bytearray | None,
    provider_reauthentication: bytearray | None,
) -> tuple[bool, bool]:
    if type(kind) is not HumanCeremonyKind:
        raise TypeError("human_ceremony_kind_invalid")
    provider_kind = kind in {
        HumanCeremonyKind.PROVIDER_CREDENTIAL_SET,
        HumanCeremonyKind.PROVIDER_CREDENTIAL_ROTATE,
    }
    passphrase_kind = kind in {
        HumanCeremonyKind.VAULT_INITIALIZE,
        HumanCeremonyKind.VAULT_UNLOCK,
    }
    if (
        (provider_credential is not None and not provider_kind)
        or (provider_reauthentication is not None and not provider_kind)
        or (passphrase is not None and not passphrase_kind)
    ):
        for supplied in (provider_credential, passphrase, provider_reauthentication):
            if supplied is not None:
                overwrite_secret_buffer(supplied)
        raise ValueError("provider_credential_target_invalid")
    return provider_kind, passphrase_kind


async def run_human_ceremony(
    kind: HumanCeremonyKind,
    target: HumanOpenTarget,
    provider_credential: bytearray | None = None,
    passphrase: bytearray | None = None,
    provider_reauthentication: bytearray | None = None,
) -> HumanResult:
    """Run one exact foreground YZH1/YZS1 ceremony and return structural state only."""

    provider_kind, passphrase_kind = _validate_supplied_secrets(
        kind,
        provider_credential,
        passphrase,
        provider_reauthentication,
    )
    fully_supplied = (
        passphrase is not None
        if passphrase_kind
        else provider_kind
        and provider_credential is not None
        and provider_reauthentication is not None
    )
    client = HumanControlClient()

    try:
        if fully_supplied:
            return await _complete_human_ceremony(
                client,
                _SuppliedSecretTerminal(),
                kind,
                target,
                provider_credential,
                passphrase,
                provider_reauthentication,
            )
        with _ForegroundTerminal() as terminal:
            return await _complete_human_ceremony(
                client,
                terminal,
                kind,
                target,
                provider_credential,
                passphrase,
                provider_reauthentication,
            )
    finally:
        await client.close()
        if provider_credential is not None:
            overwrite_secret_buffer(provider_credential)
        if passphrase is not None:
            overwrite_secret_buffer(passphrase)
        if provider_reauthentication is not None:
            overwrite_secret_buffer(provider_reauthentication)


async def run_human_ceremony_on_terminal(
    terminal: _CeremonyTerminal,
    kind: HumanCeremonyKind,
    target: HumanOpenTarget,
    *,
    provider_credential: bytearray | None = None,
    passphrase: bytearray | None = None,
    provider_reauthentication: bytearray | None = None,
) -> HumanResult:
    """Run a ceremony on an already verified console owned by the trusted helper."""

    _validate_supplied_secrets(
        kind,
        provider_credential,
        passphrase,
        provider_reauthentication,
    )
    client = HumanControlClient()
    try:
        return await _complete_human_ceremony(
            client,
            terminal,
            kind,
            target,
            provider_credential,
            passphrase,
            provider_reauthentication,
        )
    finally:
        await client.close()
        for supplied in (provider_credential, passphrase, provider_reauthentication):
            if supplied is not None:
                overwrite_secret_buffer(supplied)


async def initialize_passphrase_vault(passphrase: bytearray | None = None) -> VaultStateResult:
    result = await run_human_ceremony(
        HumanCeremonyKind.VAULT_INITIALIZE,
        EmptyVaultTarget(expected_mode="uninitialized"),
        passphrase=passphrase,
    )
    return cast(VaultStateResult, result)


async def unlock_vault(passphrase: bytearray | None = None) -> VaultStateResult:
    result = await run_human_ceremony(
        HumanCeremonyKind.VAULT_UNLOCK,
        EmptyVaultTarget(expected_mode="passphrase"),
        passphrase=passphrase,
    )
    return cast(VaultStateResult, result)


def read_vault_passphrase_for_auto_unlock() -> bytearray:
    """Read one existing vault passphrase from the foreground TTY for a repair ceremony."""

    with _ForegroundTerminal() as terminal:
        return _read_secret(
            terminal,
            HumanCeremonyKind.VAULT_UNLOCK,
            EmptyVaultTarget(expected_mode="passphrase"),
            ConfidentialSecretPurpose.VAULT_UNLOCK,
        )


async def retry_keyring(
    expected_mode: Literal["uninitialized", "os_keyring"] = "os_keyring",
) -> KeyringRetryResult:
    result = await run_human_ceremony(
        HumanCeremonyKind.KEYRING_RETRY,
        EmptyVaultTarget(expected_mode=expected_mode),
    )
    return cast(KeyringRetryResult, result)


async def portable_recovery(target: PortableRecoveryTarget) -> PortableRecoveryResult:
    result = await run_human_ceremony(HumanCeremonyKind.PORTABLE_RECOVERY, target)
    return cast(PortableRecoveryResult, result)


async def set_provider_credential(
    target: ProviderCredentialTarget,
    provider_credential: bytearray | None = None,
    provider_reauthentication: bytearray | None = None,
) -> ProviderCredentialResult:
    if target.action != "set":
        raise ValueError("provider_credential_target_invalid")
    result = await run_human_ceremony(
        HumanCeremonyKind.PROVIDER_CREDENTIAL_SET,
        target,
        provider_credential,
        provider_reauthentication=provider_reauthentication,
    )
    return cast(ProviderCredentialResult, result)


async def rotate_provider_credential(
    target: ProviderCredentialTarget,
) -> ProviderCredentialResult:
    if target.action != "rotate":
        raise ValueError("provider_credential_target_invalid")
    result = await run_human_ceremony(HumanCeremonyKind.PROVIDER_CREDENTIAL_ROTATE, target)
    return cast(ProviderCredentialResult, result)


def _idle_cli_value(value: Literal["disabled"] | int) -> IdleRelockCliPolicyValue:
    if value == "disabled":
        return DisabledIdleRelockCliPolicyValue()
    return FiniteIdleRelockCliPolicyValue(value)


async def change_idle_relock_policy(
    target: int | Literal["disabled"],
) -> IdleRelockCliResult:
    if target == "disabled":
        wire_target = IdleRelockPolicyTarget("disable")
    elif type(target) is int and 60 <= target <= 86_400:
        wire_target = IdleRelockPolicyTarget("set", target)
    else:
        raise ValueError("idle_relock_cli_target_invalid")

    client = HumanControlClient()
    with _ForegroundTerminal() as terminal:
        try:
            session = await client.open(HumanCeremonyKind.IDLE_RELOCK_POLICY_CHANGE, wire_target)
            async with session:
                try:
                    preview = _verify_preview(
                        HumanCeremonyKind.IDLE_RELOCK_POLICY_CHANGE,
                        wire_target,
                        session,
                    )
                    _render_preview(terminal, preview)
                    expected_generation = session.opened.binding.service_generation
                    result, decision = await _drive_session(
                        session,
                        terminal,
                        HumanCeremonyKind.IDLE_RELOCK_POLICY_CHANGE,
                        wire_target,
                        session.opened.phase,
                    )
                except BaseException:
                    await _cancel_quietly(session)
                    raise
        finally:
            await client.close()
    typed = cast(IdleRelockPolicyResult, result)
    if decision not in {"approve", "deny"}:
        raise HumanCeremonyCliError("result_invalid")
    proposed: Literal["disabled"] | int = target
    if (
        typed.service_generation != expected_generation
        or (decision == "approve" and typed.effective != proposed)
        or (decision == "deny" and typed.effective != typed.previous)
    ):
        raise HumanCeremonyCliError("result_invalid")
    return IdleRelockCliResult(
        outcome="applied" if decision == "approve" else "denied",
        previous=_idle_cli_value(typed.previous),
        effective=_idle_cli_value(typed.effective),
        scope=typed.scope,
        service_generation=typed.service_generation,
    )
