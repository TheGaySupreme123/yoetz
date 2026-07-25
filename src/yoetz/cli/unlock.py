"""Foreground-only confidential local-human ceremony helper."""

from __future__ import annotations

import hmac
import os
import termios
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Final, Literal, Protocol, Self, cast

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
    "set_provider_credential",
    "unlock_vault",
]

_TTY_PATH: Final = "/dev/tty"
_CHOICE_MAX_BYTES: Final = 32
_FIXED_ERROR_REASONS: Final = frozenset(
    {
        "background_process",
        "cancelled",
        "confirmation_mismatch",
        "input_invalid",
        "interrupted",
        "preview_invalid",
        "result_invalid",
        "tty_mismatch",
        "tty_required",
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


def _overwrite(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0


class _ForegroundTerminal:
    """Verified foreground controlling terminal; never falls back to stdio input."""

    __slots__ = ("_fd",)

    def __init__(self) -> None:
        self._fd = -1

    @property
    def fd(self) -> int:
        if self._fd < 0:
            raise HumanCeremonyCliError("tty_required")
        return self._fd

    def __enter__(self) -> Self:
        if not os.isatty(0) or not os.isatty(2):
            raise HumanCeremonyCliError("tty_required")
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(_TTY_PATH, flags)
        except OSError as exc:
            raise HumanCeremonyCliError("tty_required") from exc
        try:
            if not os.isatty(fd):
                raise HumanCeremonyCliError("tty_required")
            # macOS can expose /dev/tty as a root-owned controlling-terminal alias
            # with a different device number than the user's terminal endpoints.
            # The alias is still the correct no-echo input surface. Require stdin
            # and stderr to be terminals for the same user-visible endpoint, then
            # separately verify that this process is foreground on /dev/tty.
            # Terminal device ownership is not a reliable user-identity signal.
            if os.fstat(0).st_rdev != os.fstat(2).st_rdev:
                raise HumanCeremonyCliError("tty_mismatch")
            if os.tcgetpgrp(fd) != os.getpgrp():
                raise HumanCeremonyCliError("background_process")
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def write(self, value: str) -> None:
        if type(value) is not str:
            raise TypeError("terminal_text_invalid")
        encoded = value.encode("utf-8", errors="strict")
        position = 0
        while position < len(encoded):
            try:
                written = os.write(self.fd, encoded[position:])
            except OSError as exc:
                raise HumanCeremonyCliError("interrupted") from exc
            if written <= 0:
                raise HumanCeremonyCliError("interrupted")
            position += written

    def _read_line(self, prompt: str, maximum: int, *, hidden: bool) -> bytearray:
        if type(maximum) is not int or maximum <= 0:
            raise TypeError("terminal_bound_invalid")
        original: Any = None
        storage = bytearray(maximum + 1)
        used = 0
        try:
            if hidden:
                original = termios.tcgetattr(self.fd)
                changed = list(original)
                changed[3] = cast(int, changed[3]) & ~termios.ECHO
                termios.tcsetattr(self.fd, termios.TCSADRAIN, changed)
            self.write(prompt)
            while used < len(storage):
                view = memoryview(storage)[used:]
                try:
                    count = os.readv(self.fd, [view])
                except InterruptedError as exc:
                    raise HumanCeremonyCliError("interrupted") from exc
                finally:
                    view.release()
                if count <= 0:
                    raise HumanCeremonyCliError("input_invalid")
                used += count
                if storage[used - 1] == 10:
                    break
            if used == 0 or storage[used - 1] != 10:
                raise HumanCeremonyCliError("input_invalid")
            storage[used - 1] = 0
            for index in range(used, len(storage)):
                storage[index] = 0
            del storage[used - 1 :]
            return storage
        except BaseException:
            _overwrite(storage)
            raise
        finally:
            if original is not None:
                try:
                    termios.tcsetattr(self.fd, termios.TCSADRAIN, original)
                finally:
                    self.write("\n")

    def read_choice(
        self,
        prompt: str,
        allowed: tuple[bytes, ...],
    ) -> bytes:
        if type(allowed) is not tuple or not allowed:
            raise TypeError("terminal_choices_invalid")
        value = self._read_line(prompt, _CHOICE_MAX_BYTES, hidden=False)
        try:
            for candidate in allowed:
                if hmac.compare_digest(value, candidate):
                    return candidate
            raise HumanCeremonyCliError("input_invalid")
        finally:
            _overwrite(value)

    def read_secret(self, prompt: str, maximum: int) -> bytearray:
        return self._read_line(prompt, maximum, hidden=True)


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
        terminal.write(
            f"Action: decide privacy policy widening\nPending: {preview.pending_id}\n"
            f"Diff digest: {preview.diff_digest}\n"
            f"Categories: {', '.join(preview.categories)}\n"
            f"Scopes: {', '.join(preview.scopes)}\n"
        )
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
            _overwrite(confirmation)
        return first
    except BaseException:
        _overwrite(first)
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
        _overwrite(source)


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


async def run_human_ceremony(
    kind: HumanCeremonyKind,
    target: HumanOpenTarget,
    provider_credential: bytearray | None = None,
    passphrase: bytearray | None = None,
    provider_reauthentication: bytearray | None = None,
) -> HumanResult:
    """Run one exact foreground YZH1/YZS1 ceremony and return structural state only."""

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
                _overwrite(supplied)
        raise ValueError("provider_credential_target_invalid")
    fully_supplied = (
        passphrase is not None
        if passphrase_kind
        else provider_kind
        and provider_credential is not None
        and provider_reauthentication is not None
    )
    client = HumanControlClient()

    async def complete(terminal: _CeremonyTerminal) -> HumanResult:
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

    try:
        if fully_supplied:
            try:
                return await complete(_SuppliedSecretTerminal())
            finally:
                await client.close()
        with _ForegroundTerminal() as terminal:
            try:
                return await complete(terminal)
            finally:
                await client.close()
    finally:
        if provider_credential is not None:
            _overwrite(provider_credential)
        if passphrase is not None:
            _overwrite(passphrase)
        if provider_reauthentication is not None:
            _overwrite(provider_reauthentication)


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
