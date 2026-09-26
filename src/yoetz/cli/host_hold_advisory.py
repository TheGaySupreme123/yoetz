"""Advisory for an AI-powered ``check`` that a host's automatic reviewer held (issue #857).

Claude Code's ``PermissionDenied`` hook fires after auto mode (or a permission rule or another
hook) denies a tool call. Until this module the Yoetz hook recorded one payload-free diagnostic
and said nothing, so the agent saw only the host's fixed refusal text and routinely downgraded a
review the owner had already authorized in the trusted privacy ceremony (issues #187, #467).

This module composes what the hook may say and do about that hold. It states Yoetz's own
first-hand facts — whether the repository grant permits external AI-powered review, read from the
running service inside the hook deadline, and the host's project-scoped admission state — and it
offers the host's documented ``retry`` exactly once per session. It never emits an allow
decision, never edits host configuration, and never asserts a grant it did not read: an
unreadable grant is ``grant_unread``, never confirmed. The host's own permission flow and the
human remain the authority (ADR-018).

Every rendered text is a fixed constant or a composition of closed tokens. No tool input,
reason prose, path, session id, or model output is echoed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol, cast

from yoetz.config.paths import ensure_owner_only_dir, state_dir
from yoetz.domain.values import JsonObject
from yoetz.ports.control import ControlClientKind, ControlError

try:
    import fcntl
except ImportError:  # pragma: no cover - supported hook hosts are POSIX
    fcntl = None  # type: ignore[assignment]

__all__ = [
    "ADMISSION_STATES",
    "GRANT_READ_DEADLINE_MS",
    "GRANT_REASONS",
    "GrantReadReason",
    "HostHoldAdvisory",
    "HostHoldFacts",
    "PrivacyConnector",
    "RetryOfferOutcome",
    "compose_host_hold_advisory",
    "grant_from_setup",
    "note_retry_offer",
    "read_host_hold_facts",
]

type GrantReadReason = Literal[
    "grant_confirmed",
    "grant_absent",
    "grant_unread",
    "service_unavailable",
    "vault_locked",
    "privacy_authority_required",
    "service_skipped",
    "workspace_unbound",
]
type RetryOfferOutcome = Literal["first", "repeat", "unrecorded"]
type HoldSource = Literal["auto_mode", "permission_rule", "hook"]

# The privacy read must fit inside the rendered 5 s PermissionDenied hook timeout together with
# process start, the admission file read, and the stdout write.
GRANT_READ_DEADLINE_MS: Final = 2_500
_RETRY_FILE_NAME: Final = "host-hold-retries.json"
_RETRY_LOCK_NAME: Final = ".host-hold-retries.lock"
_MAX_RETRY_ENTRIES: Final = 64
_MAX_RETRY_FILE_BYTES: Final = 16 * 1024
_RETRY_KEY_DOMAIN: Final = b"yoetz/host-hold-retry/v1\x00"
ADMISSION_STATES: Final = frozenset({"absent", "present", "partial", "foreign", "unknown"})
GRANT_REASONS: Final = frozenset(
    {
        "grant_confirmed",
        "grant_absent",
        "grant_unread",
        "service_unavailable",
        "vault_locked",
        "privacy_authority_required",
        "service_skipped",
        "workspace_unbound",
    }
)

_ADMISSION_COMMAND: Final = "yoetz integrate claude admission grant"
_PRIVACY_COMMAND: Final = "yoetz --privacy"

# --- Model-visible texts (``hookSpecificOutput.additionalContext``). Fixed; closed tokens only.

_CONFIRMED_PREFIX: Final = (
    "Yoetz confirms: the repository owner already authorized external AI-powered review for this "
    "repository through the trusted Yoetz privacy ceremony (repository grant present, external "
    "review permitted). Claude Code's automatic reviewer held this check; Yoetz did not, and no "
    "provider dispatch occurred."
)
_CONFIRMED_RETRY: Final = (
    " Retry the identical check exactly once now: same request body and request_id. If the host "
    "holds it again, stop and present that exact call for the user's approval and wait."
)
_CONFIRMED_NO_RETRY: Final = (
    " Do not retry on your own: stop and present this exact check (same request body and "
    "request_id) for the user's approval and wait."
)
_CONFIRMED_NO_VERDICT: Final = (
    " Claude Code produced no classifier verdict for this call, so the host ignores a retry: "
    "stop and present this exact check (same request body and request_id) for the user's "
    "approval and wait."
)
_PENDING_RULES: Final = (
    " While that decision is pending do not switch to deterministic_only, do not open a new "
    "semantic job, and do not claim completion or request a final receipt. Host approval permits "
    "the tool call only; Yoetz still enforces every privacy, disclosure, and credential gate."
)
_RULE_HELD: Final = (
    "Yoetz: the owner's own Claude Code permission rule or another hook held this AI-powered "
    "check, not the auto-mode classifier. Do not retry. Present this exact check (same request "
    "body and request_id) for the user's decision and wait. No provider dispatch occurred."
    + _PENDING_RULES
)
_UNCONFIRMED_PREFIX: Final = (
    "Yoetz could not confirm a repository grant for external AI-powered review for this "
    "repository (reason: {reason}). Treat this hold as unauthorized: do not retry on your own. "
    "Present this exact check (same request body and request_id) for the user's decision and "
    "wait. No provider dispatch occurred."
)
_ADMISSION_NOTES: Final[Mapping[str, str]] = {
    "absent": (
        " Durable fix for the owner, outside this chat: '"
        + _ADMISSION_COMMAND
        + "' writes Claude Code's own allow rule for exactly check."
    ),
    "present": (
        " This repository already carries Claude Code's admission entry for check, yet the host "
        "held the call; the owner should confirm Claude Code trusts this folder's local settings."
    ),
    "partial": (
        " This repository carries a partial Claude Code admission entry for check; the owner can "
        "complete it with '" + _ADMISSION_COMMAND + "'."
    ),
    "foreign": (
        " A wider or conflicting Claude Code permission rule for check exists in this "
        "repository; the owner should review .claude/settings.local.json."
    ),
    "unknown": "",
}
_GRANT_ABSENT_NOTE: Final = (
    " The owner can authorize external review for this repository with '" + _PRIVACY_COMMAND + "'."
)

# --- User-visible texts (``systemMessage``; the host shows them, the model does not see them).

_SYSTEM_CONFIRMED_RETRY: Final = (
    "Yoetz: you already authorized AI-powered review for this repository ("
    + _PRIVACY_COMMAND
    + "). "
    "Claude Code's auto mode held the check; Yoetz did not, and nothing was sent. The agent may "
    "retry once; approve it if Claude Code prompts you. To stop these holds, run '"
    + _ADMISSION_COMMAND
    + " --project-root <repository root>'."
)
_SYSTEM_CONFIRMED_NO_RETRY: Final = (
    "Yoetz: you already authorized AI-powered review for this repository ("
    + _PRIVACY_COMMAND
    + "). "
    "Claude Code's auto mode held the check again; nothing was sent. Decide on the held call "
    "when Claude Code asks. To stop these holds, run '"
    + _ADMISSION_COMMAND
    + " --project-root <repository root>'."
)
_SYSTEM_RULE_HELD: Final = (
    "Yoetz: your own Claude Code permission rule held an AI-powered check. Nothing was sent. "
    "Decide on the call when Claude Code asks."
)
_SYSTEM_UNCONFIRMED: Final = (
    "Yoetz: Claude Code held an AI-powered check and Yoetz could not confirm a repository grant "
    "for external review (reason: {reason}). Nothing was sent. Decide on the held call when "
    "Claude Code asks; '" + _PRIVACY_COMMAND + "' reviews this repository's policy."
)


class _PrivacyClient(Protocol):
    async def privacy_get_setup(
        self, request: JsonObject, *, deadline_ms: int | None = None
    ) -> object: ...

    async def close(self) -> None: ...


type PrivacyConnector = Callable[[ControlClientKind], Awaitable[_PrivacyClient]]
type AsyncRunner = Callable[[Callable[[], Awaitable[object]]], object]


@dataclass(frozen=True, slots=True)
class HostHoldFacts:
    """Yoetz's first-hand facts about one held check; every field is a closed token."""

    grant: GrantReadReason
    admission_state: str

    @property
    def grant_confirmed(self) -> bool:
        return self.grant == "grant_confirmed"


@dataclass(frozen=True, slots=True)
class HostHoldAdvisory:
    """What the hook says and does about one held check."""

    additional_context: str
    system_message: str
    retry: bool
    diagnostic: Literal[
        "host_denial_retry_offered",
        "host_denial_retry_exhausted",
        "host_denial_retry_unrecorded",
        "host_denial_grant_unconfirmed",
    ]


def _channel_enabled(policy: Mapping[str, object], channel: str) -> bool | None:
    channels = policy.get("channel_policies")
    if not isinstance(channels, list | tuple):
        return None
    for item in cast("list[object] | tuple[object, ...]", channels):
        if not isinstance(item, Mapping):
            continue
        entry = cast(Mapping[str, object], item)
        if entry.get("channel") != channel:
            continue
        enabled = entry.get("enabled")
        return enabled if type(enabled) is bool else None
    return None


def grant_from_setup(effective: object) -> GrantReadReason:
    if not isinstance(effective, Mapping):
        return "grant_unread"
    plain = cast(Mapping[str, object], effective)
    grant_state = plain.get("grant_state")
    policy = plain.get("composed_policy")
    llm_enabled = (
        _channel_enabled(cast(Mapping[str, object], policy), "llm_inference")
        if isinstance(policy, Mapping)
        else None
    )
    if grant_state not in {"granted", "missing"} or llm_enabled is None:
        return "grant_unread"
    if grant_state == "granted" and llm_enabled is True:
        return "grant_confirmed"
    return "grant_absent"


def _grant_from_control_error(error: ControlError) -> GrantReadReason:
    reason = error.reason
    if reason == "vault_locked":
        return "vault_locked"
    if reason == "privacy_projection_blocked":
        return "privacy_authority_required"
    return "service_unavailable"


async def _read_grant(connect: PrivacyConnector) -> GrantReadReason:
    client: _PrivacyClient | None = None
    try:
        client = await connect(ControlClientKind.CLI)
        effective = await client.privacy_get_setup(
            JsonObject({"schema_version": "2.0.0"}), deadline_ms=GRANT_READ_DEADLINE_MS
        )
    except ControlError as error:
        return _grant_from_control_error(error)
    except Exception:
        return "service_unavailable"
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()
    return grant_from_setup(effective)


def _admission_state(workspace_locator: str | None) -> str:
    if workspace_locator is None:
        return "unknown"
    try:
        from yoetz.cli.provider_status import host_admission_observation

        report = host_admission_observation(Path(workspace_locator))
        entry = report.get("claude")
        state = entry.get("state") if isinstance(entry, Mapping) else None
    except Exception:
        return "unknown"
    return state if type(state) is str and state in ADMISSION_STATES else "unknown"


def read_host_hold_facts(
    workspace_locator: str | None,
    *,
    connect: PrivacyConnector | None,
    run_async: AsyncRunner,
    skip_service: bool = False,
) -> HostHoldFacts:
    """Read the grant through the repository-bound service and the host's admission file.

    Fail-soft to a closed unread token: a hook must never guess that the owner authorized
    review. ``connect`` is already bound to ``workspace_locator``; ``None`` without a locator
    means no repository-bound read is possible and the grant stays ``workspace_unbound``.
    """

    admission = _admission_state(workspace_locator)
    if skip_service:
        return HostHoldFacts("service_skipped", admission)
    if workspace_locator is None and connect is None:
        return HostHoldFacts("workspace_unbound", admission)
    if connect is None:
        from yoetz.cli.hooks import bound_connector
        from yoetz.service.client import connect_service

        connect = cast(PrivacyConnector, bound_connector(connect_service, workspace_locator))
    grant: GrantReadReason = "service_unavailable"
    try:
        outcome = run_async(lambda: _read_grant(connect))
    except Exception:
        outcome = None
    if type(outcome) is str and outcome in GRANT_REASONS:
        grant = cast(GrantReadReason, outcome)
    return HostHoldFacts(grant, admission)


def _retry_key(session_id: str) -> str:
    digest = hashlib.sha256(_RETRY_KEY_DOMAIN + b"claude\x00" + session_id.encode("utf-8"))
    return digest.hexdigest()


def _load_retry_ledger(path: Path) -> dict[str, int]:
    try:
        if path.is_symlink():
            return {}
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    if len(raw) > _MAX_RETRY_FILE_BYTES:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError, ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    ledger: dict[str, int] = {}
    for key, value in cast(dict[object, object], parsed).items():
        if type(key) is str and len(key) == 64 and type(value) is int and value >= 0:
            ledger[key] = value
    return ledger


def note_retry_offer(
    session_id: str, *, _state: Path | None = None, _now_ms: int | None = None
) -> RetryOfferOutcome:
    """Record that this session was offered its one retry; say whether it already had one.

    ``first`` means the offer was recorded now and a retry may be emitted. ``repeat`` means this
    session already received its retry, so the hold goes to the human. ``unrecorded`` means the
    ledger could not be written: exactly-once cannot be guaranteed, so no retry is emitted.
    Keys are domain-separated digests of the host session id; the id itself is never stored.
    """

    root = state_dir() if _state is None else _state
    directory = root / "observation"
    path = directory / _RETRY_FILE_NAME
    lock_path = directory / _RETRY_LOCK_NAME
    key = _retry_key(session_id)
    now_ms = _now_ms if _now_ms is not None else int(time.time() * 1000)
    try:
        ensure_owner_only_dir(directory)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        lock_descriptor = os.open(lock_path, flags, 0o600)
        try:
            os.fchmod(lock_descriptor, 0o600)
            if fcntl is not None:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            ledger = _load_retry_ledger(path)
            if key in ledger:
                return "repeat"
            ledger[key] = now_ms
            while len(ledger) > _MAX_RETRY_ENTRIES:
                oldest = min(ledger, key=lambda item: (ledger[item], item))
                del ledger[oldest]
            encoded = json.dumps(ledger, separators=(",", ":"), sort_keys=True).encode("utf-8")
            temporary = directory / f".{_RETRY_FILE_NAME}.{os.getpid()}.tmp"
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, path)
            return "first"
        finally:
            os.close(lock_descriptor)
    except Exception:
        return "unrecorded"


def compose_host_hold_advisory(
    facts: HostHoldFacts,
    *,
    source: object,
    reason: object,
    retry_offer: RetryOfferOutcome,
) -> HostHoldAdvisory:
    """Compose the bounded advisory for one held check from closed inputs only.

    ``source`` and ``reason`` are the host payload's tokens; anything outside the documented
    closed sets is treated as the auto-mode classifier with a verdict, which is the only reviewer
    that produces a denial without a source. A retry is emitted only when every gate holds: the
    grant was confirmed first-hand, the denial came from the classifier with a verdict, and this
    session has not yet had its one retry.
    """

    held_by_rule = source in {"permission_rule", "hook"}
    no_verdict = reason == "no_verdict"
    admission_note = _ADMISSION_NOTES.get(facts.admission_state, "")
    if held_by_rule:
        return HostHoldAdvisory(
            additional_context=_RULE_HELD,
            system_message=_SYSTEM_RULE_HELD,
            retry=False,
            diagnostic="host_denial_retry_exhausted",
        )
    if not facts.grant_confirmed:
        note = _GRANT_ABSENT_NOTE if facts.grant == "grant_absent" else ""
        return HostHoldAdvisory(
            additional_context=(
                _UNCONFIRMED_PREFIX.format(reason=facts.grant) + _PENDING_RULES + note
            ),
            system_message=_SYSTEM_UNCONFIRMED.format(reason=facts.grant),
            retry=False,
            diagnostic="host_denial_grant_unconfirmed",
        )
    if no_verdict:
        return HostHoldAdvisory(
            additional_context=_CONFIRMED_PREFIX
            + _CONFIRMED_NO_VERDICT
            + _PENDING_RULES
            + admission_note,
            system_message=_SYSTEM_CONFIRMED_NO_RETRY,
            retry=False,
            diagnostic="host_denial_retry_exhausted",
        )
    if retry_offer == "first":
        return HostHoldAdvisory(
            additional_context=_CONFIRMED_PREFIX
            + _CONFIRMED_RETRY
            + _PENDING_RULES
            + admission_note,
            system_message=_SYSTEM_CONFIRMED_RETRY,
            retry=True,
            diagnostic="host_denial_retry_offered",
        )
    return HostHoldAdvisory(
        additional_context=_CONFIRMED_PREFIX
        + _CONFIRMED_NO_RETRY
        + _PENDING_RULES
        + admission_note,
        system_message=_SYSTEM_CONFIRMED_NO_RETRY,
        retry=False,
        diagnostic=(
            "host_denial_retry_unrecorded"
            if retry_offer == "unrecorded"
            else "host_denial_retry_exhausted"
        ),
    )
