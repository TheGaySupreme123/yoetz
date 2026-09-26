"""Advisory for a host reviewer holding an owner-authorized AI-powered ``check`` (issue #857).

Every supported host puts an automatic tool-call reviewer in front of MCP calls, and each one
refuses the policy-route ``check`` on the same criterion — data to a destination the user did
not name — because the owner's ``yoetz --privacy`` authorization is invisible to it. Host
admission (issue #467) is the durable fix, but a repository without it, or after admission
drift, sees every check held again, and the agent then has nothing first-hand that says the
owner already authorized this review. In practice it downgrades to deterministic-only or gives
up on review, which is the complaint.

This module puts Yoetz's own recorded fact in front of the model and the user, and lets the
identical call be retried once. Everything here is information, never authorization:

* the facts are read first-hand within the hook budget — the repository grant as the running
  service reports it, the route the bridge recorded it is serving, and the host's own admission
  file — and a fact that could not be read is reported as unconfirmed, never guessed;
* the texts are closed and payload-free: no tool input, path, reason prose, or id is echoed;
* ``retry`` is offered at most once per ``(session, tool call)``; the second hold of the same
  call gets the pause advisory, so #187's "one retry, then a human" stays intact;
* no branch emits a permission decision, writes an admission entry, or touches host settings.
  The retried call goes back through the host's own permission flow.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol, cast

from yoetz.config.paths import PathSafetyError, ensure_owner_only_dir, state_dir
from yoetz.domain.values import JsonObject
from yoetz.ports.control import ControlClientKind, ControlError, WorkspaceLocator
from yoetz.protocol.canonical import JsonValue

__all__ = [
    "ADMISSION_ADVISORY_HOSTS",
    "GRANT_UNCONFIRMED_REASONS",
    "HostDenialFacts",
    "PermissionDeniedAdvisory",
    "admission_absent_advisory",
    "compose_permission_denied_advisory",
    "read_host_denial_facts",
]

type AdvisoryHost = Literal["claude", "codex", "cursor"]
type GrantReason = Literal[
    "confirmed",
    "service_unavailable",
    "vault_locked",
    "grant_absent",
    "grant_not_permitting",
    "grant_unverifiable",
]
type DenialCase = Literal["grant_confirmed", "grant_unconfirmed", "owner_rule"]

ADMISSION_ADVISORY_HOSTS: Final[tuple[AdvisoryHost, ...]] = ("claude", "codex", "cursor")
# Closed reason tokens the unconfirmed advisory may append. Nothing outside this set reaches
# the model: a service failure, a host token, and a policy shape all collapse to one of these.
GRANT_UNCONFIRMED_REASONS: Final = frozenset(
    {
        "service_unavailable",
        "vault_locked",
        "grant_absent",
        "grant_not_permitting",
        "grant_unverifiable",
        "route_unobserved",
        "route_strict",
    }
)
_FACTS_DEADLINE_MS: Final = 2_000
_RETRY_MARKER_DIRNAME: Final = "observation"
_RETRY_MARKER_NAME: Final = "host-denial-retries.json"
_RETRY_MARKER_LOCK: Final = ".host-denial-retries.lock"
_RETRY_MARKER_DOMAIN: Final = b"yoetz/host-denial-retry/v1\x00"
_MAX_RETRY_MARKERS: Final = 256
_MAX_RETRY_MARKER_BYTES: Final = 64 * 1024

# Claude Code's closed origin tokens. The hooks reference (re-read 2026-09-26) documents
# ``source: "auto"`` for an auto-mode denial and reasons ``denied_by_classifier``,
# ``denied_by_rule``, and ``no_verdict``; earlier builds sent ``auto_mode`` /
# ``permission_rule`` / ``hook`` and ``classifier_denied``. Both spellings are admitted so a
# host build on either side of that rename lands in the right case; anything else is treated
# as the owner's own rule, which never earns a retry.
_CLASSIFIER_SOURCES: Final = frozenset({"auto", "auto_mode"})
_OWNER_RULE_SOURCES: Final = frozenset({"permission_rule", "hook", "rule"})
_NO_VERDICT_REASONS: Final = frozenset({"no_verdict"})
_OWNER_RULE_REASONS: Final = frozenset({"denied_by_rule"})

_ADMISSION_COMMANDS: Final[Mapping[AdvisoryHost, str]] = {
    "claude": "yoetz integrate claude admission grant",
    "codex": "yoetz integrate codex admission grant",
    "cursor": "yoetz integrate cursor admission grant",
}

_GRANT_CONFIRMED_CONTEXT: Final = (
    "Yoetz confirms: the repository owner already authorized external AI-powered review for "
    "this repository through the trusted Yoetz privacy ceremony (repository grant present, "
    "route: policy). This check was held by the host's automatic reviewer, not by Yoetz, and "
    "no provider dispatch occurred. Retry the identical check once — same request body and "
    "request id. If the host holds it again, stop and present this exact call for the user's "
    "manual approval; do not switch to deterministic-only, do not open a new semantic job, "
    "and do not claim completion or request a final receipt while that decision is pending. "
    "Host approval permits the tool call only; Yoetz still enforces every privacy, "
    "disclosure, and credential gate. Durable fix for the owner: yoetz integrate claude "
    "admission grant."
)
_GRANT_CONFIRMED_PAUSE_CONTEXT: Final = (
    "Yoetz confirms: the repository owner already authorized external AI-powered review for "
    "this repository (repository grant present, route: policy). The host's automatic reviewer "
    "held this same check again; Yoetz did not, and no provider dispatch occurred. Do not "
    "retry on your own: stop and present this exact check — same request body and request "
    "id — for the user's manual approval. Do not switch to deterministic-only, do not open a "
    "new semantic job, and do not claim completion or request a final receipt while that "
    "decision is pending. Host approval permits the tool call only; Yoetz still enforces "
    "every privacy, disclosure, and credential gate. Durable fix for the owner: yoetz "
    "integrate claude admission grant."
)
_GRANT_UNCONFIRMED_CONTEXT: Final = (
    "Yoetz could not confirm a repository grant for external AI-powered review (reason: "
    "{reason}). Treat this hold as unauthorized: stop and ask the user before any retry; do "
    "not downgrade silently. Yoetz did not run and no provider dispatch occurred."
)
_OWNER_RULE_CONTEXT: Final = (
    "The owner's own permission rule held this check. Ask the user; do not retry. Yoetz did "
    "not run and no provider dispatch occurred."
)
_SYSTEM_MESSAGE_CONFIRMED: Final = (
    "Yoetz: you already authorized AI-powered review for this repository (yoetz --privacy). "
    "Claude Code's auto mode held the check; Yoetz did not, and nothing was sent. Approve the "
    "retry when prompted, or make it durable: yoetz integrate claude admission grant "
    "--project-root <root>."
)
_SYSTEM_MESSAGE_CONFIRMED_PAUSE: Final = (
    "Yoetz: you already authorized AI-powered review for this repository (yoetz --privacy). "
    "Claude Code's auto mode held the same check again; Yoetz did not, and nothing was sent. "
    "The agent will ask for your manual approval of that exact check. To make it durable: "
    "yoetz integrate claude admission grant --project-root <root>."
)
_SYSTEM_MESSAGE_UNCONFIRMED: Final = (
    "Yoetz: Claude Code held an AI-powered review check; Yoetz did not run and nothing was "
    "sent. Yoetz could not confirm a repository grant for external review ({reason}), so the "
    "agent has been told to ask you before retrying."
)
_SYSTEM_MESSAGE_OWNER_RULE: Final = (
    "Yoetz: one of your own permission rules held an AI-powered review check; Yoetz did not "
    "run and nothing was sent. The agent has been told to ask you rather than retry."
)
_ADMISSION_ABSENT_ADVISORY: Final = (
    "AI-powered review is authorized in Yoetz for this repository, but this host has no "
    "admission entry, so its automatic reviewer may hold each check. The owner can run: "
    "{command}."
)


class _FactsClient(Protocol):
    async def service_status(self) -> object: ...

    async def privacy_get_setup(
        self, request: object, *, deadline_ms: int | None = None
    ) -> object: ...

    async def close(self) -> None: ...


# The same one-argument shape the hooks already bind their status reads to: the caller (or
# the default below) has already fenced the connection to the hook's workspace locator.
type FactsConnector = Callable[[ControlClientKind], Awaitable[object]]
type AsyncRunner = Callable[[Callable[[], Awaitable[object]]], object]


@dataclass(frozen=True, slots=True)
class HostDenialFacts:
    """Yoetz's first-hand facts for one workspace at the moment of a hold.

    ``grant`` is ``confirmed`` only when the running service reported the repository grant as
    ``granted`` with the ``llm_inference`` channel enabled; every other value names why it
    could not be confirmed. ``route_profile`` is the route the host's bridge last recorded it
    was serving (``None`` = unobserved). ``admission_state`` is the host's own admission file
    (``absent|present|partial|foreign|unknown``, ``None`` = unread).
    """

    grant: GrantReason
    route_profile: Literal["policy", "strict"] | None
    admission_state: str | None

    @property
    def review_authorized(self) -> bool:
        return self.grant == "confirmed" and self.route_profile == "policy"

    @property
    def unconfirmed_reason(self) -> str | None:
        """The closed reason token when review is not confirmed, else ``None``."""

        if self.grant != "confirmed":
            return self.grant
        if self.route_profile is None:
            return "route_unobserved"
        if self.route_profile == "strict":
            return "route_strict"
        return None


@dataclass(frozen=True, slots=True)
class PermissionDeniedAdvisory:
    """What the hook emits and records for one held call."""

    case: DenialCase
    retry: bool
    additional_context: str
    system_message: str
    diagnostic: Literal[
        "host_denial_retry_offered",
        "host_denial_retry_exhausted",
        "host_denial_grant_unconfirmed",
    ]


def _channel_enabled(policy: Mapping[str, object], channel: str) -> bool | None:
    """Read one channel's enabled flag; a non-boolean flag is unread, never disabled."""

    channels = policy.get("channel_policies")
    if not isinstance(channels, list | tuple):
        return None
    for item in cast("list[object] | tuple[object, ...]", channels):
        if not isinstance(item, Mapping):
            continue
        entry = cast(Mapping[str, object], item)
        if entry.get("channel") == channel:
            enabled = entry.get("enabled")
            return enabled if type(enabled) is bool else None
    return None


async def _read_grant(connect: FactsConnector, *, deadline_ms: int) -> GrantReason:
    """Read the repository grant through a connection already bound to the hook's workspace."""

    client: _FactsClient | None = None
    try:
        connected = await connect(ControlClientKind.CLI)
        client = cast(_FactsClient, connected)
        status = await client.service_status()
        state = getattr(getattr(status, "state", None), "value", None)
        if state in {"locked", "unlocking"}:
            return "vault_locked"
        if state != "ready":
            return "service_unavailable"
        setup = await client.privacy_get_setup(
            JsonObject({"schema_version": "2.0.0"}), deadline_ms=deadline_ms
        )
        if not isinstance(setup, Mapping):
            return "grant_unverifiable"
        plain = cast(Mapping[str, object], dict(cast(Mapping[str, object], setup)))
        grant_state = plain.get("grant_state")
        if grant_state == "missing":
            return "grant_absent"
        if grant_state != "granted":
            return "grant_unverifiable"
        policy = plain.get("composed_policy")
        if not isinstance(policy, Mapping):
            return "grant_unverifiable"
        enabled = _channel_enabled(cast(Mapping[str, object], policy), "llm_inference")
        if enabled is None:
            return "grant_unverifiable"
        return "confirmed" if enabled else "grant_not_permitting"
    except ControlError as error:
        return "vault_locked" if error.reason == "vault_locked" else "service_unavailable"
    except Exception:
        return "service_unavailable"
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()


def _admission_repository_root(workspace_locator: str) -> Path:
    start = Path(workspace_locator).absolute()
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def _read_admission_state(host: AdvisoryHost, workspace_locator: str) -> str | None:
    try:
        from yoetz.adapters.integrations.host_admission import observe_host_admission

        observation = observe_host_admission(host, _admission_repository_root(workspace_locator))
    except Exception:
        return None
    state = observation.state.value
    return state if type(state) is str else None


def read_host_denial_facts(
    host: AdvisoryHost,
    workspace_locator: str | None,
    *,
    connect: FactsConnector | None = None,
    run_async: AsyncRunner | None = None,
    skip_service: bool = False,
    _state: Path | None = None,
    deadline_ms: int = _FACTS_DEADLINE_MS,
) -> HostDenialFacts:
    """Read the three facts, each fail-soft to its own unconfirmed/unread value.

    The route and admission reads are local files and run first; the grant read opens one
    bounded service connection only when the caller did not ask to stay local. Without a
    workspace locator no grant can be bound, so the grant reads as unverifiable.
    """

    from yoetz.application.serving_route import read_serving_route

    route_profile = read_serving_route(host, _state=_state)
    admission_state = (
        None if workspace_locator is None else _read_admission_state(host, workspace_locator)
    )
    grant: GrantReason
    if workspace_locator is None:
        grant = "grant_unverifiable"
    elif skip_service:
        grant = "service_unavailable"
    else:
        if connect is None:
            try:
                locator = WorkspaceLocator(workspace_locator)
            except ValueError:
                return HostDenialFacts("grant_unverifiable", route_profile, admission_state)
            from yoetz.service.client import connect_service

            async def _bound(kind: ControlClientKind) -> object:
                return await connect_service(kind, workspace_locator=locator)

            connector: FactsConnector = _bound
        else:
            connector = connect
        if run_async is None:
            import anyio

            runner: AsyncRunner = cast(AsyncRunner, anyio.run)
        else:
            runner = run_async

        async def _run() -> object:
            import anyio

            # One overall bound for connect, status, and the privacy read, so the three facts
            # always fit inside the host's five-second hook timeout. A read that does not finish
            # is unconfirmed, never guessed.
            with anyio.move_on_after(deadline_ms / 1000):
                return await _read_grant(connector, deadline_ms=deadline_ms)
            return "service_unavailable"

        try:
            grant = cast(GrantReason, runner(_run))
        except Exception:
            grant = "service_unavailable"
    return HostDenialFacts(grant, route_profile, admission_state)


def _retry_marker_path(root: Path | None) -> Path:
    return (state_dir() if root is None else root) / _RETRY_MARKER_DIRNAME / _RETRY_MARKER_NAME


def _retry_marker_key(session_id: str, tool_use_id: str) -> str:
    digest = hashlib.sha256(
        _RETRY_MARKER_DOMAIN + session_id.encode("utf-8") + b"\x00" + tool_use_id.encode("utf-8")
    )
    return digest.hexdigest()


def _note_retry_offer(session_id: str, tool_use_id: str, *, _state: Path | None) -> bool:
    """Record that the one retry was offered for this call; return ``False`` if already offered.

    The marker holds only digests of the host identifiers, never the identifiers themselves,
    and is bounded to the most recent ``_MAX_RETRY_MARKERS`` offers. Any storage failure
    reads as "already offered": when the offer cannot be bounded, no retry is offered.
    """

    key = _retry_marker_key(session_id, tool_use_id)
    path = _retry_marker_path(_state)
    lock_path = path.with_name(_RETRY_MARKER_LOCK)
    descriptor: int | None = None
    try:
        ensure_owner_only_dir(path.parent)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        facts = os.fstat(descriptor)
        if (
            not stat.S_ISREG(facts.st_mode)
            or facts.st_uid != os.geteuid()
            or stat.S_IMODE(facts.st_mode) & 0o077
        ):
            return False
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        offered: list[str] = []
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raw = b""
        if raw:
            if len(raw) > _MAX_RETRY_MARKER_BYTES:
                return False
            loaded: object = json.loads(raw.decode("utf-8"))
            if type(loaded) is not list:
                return False
            offered = [item for item in cast(list[object], loaded) if type(item) is str]
        if key in offered:
            return False
        offered.append(key)
        offered = offered[-_MAX_RETRY_MARKERS:]
        encoded = json.dumps(offered, separators=(",", ":")).encode("utf-8") + b"\n"
        temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        handle = os.open(temporary, flags, 0o600)
        try:
            os.write(handle, encoded)
            os.fsync(handle)
        finally:
            os.close(handle)
        try:
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise
        return True
    except OSError, PathSafetyError, ValueError, UnicodeError:
        return False
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _closed_reason(token: str | None) -> str:
    return token if token in GRANT_UNCONFIRMED_REASONS else "grant_unverifiable"


def compose_permission_denied_advisory(
    payload: Mapping[str, JsonValue],
    facts: HostDenialFacts,
    *,
    _state: Path | None = None,
) -> PermissionDeniedAdvisory:
    """Choose the closed advisory for one Claude Code ``PermissionDenied`` on a scoped check.

    ``retry`` is ``True`` only when every condition holds: the grant is confirmed and the
    serving route is policy; the reason is not ``no_verdict`` (the host ignores ``retry``
    there, and a retry offer would be noise); the source is the auto-mode classifier — never
    the owner's own permission rule or another hook; the call carries a session and tool-use
    identity the offer can be bounded to; and no retry was already offered for that identity.
    """

    source = payload.get("source")
    reason = payload.get("reason")
    owner_rule = source in _OWNER_RULE_SOURCES or reason in _OWNER_RULE_REASONS
    if owner_rule:
        return PermissionDeniedAdvisory(
            "owner_rule",
            False,
            _OWNER_RULE_CONTEXT,
            _SYSTEM_MESSAGE_OWNER_RULE,
            "host_denial_grant_unconfirmed",
        )
    if not facts.review_authorized:
        token = _closed_reason(facts.unconfirmed_reason)
        return PermissionDeniedAdvisory(
            "grant_unconfirmed",
            False,
            _GRANT_UNCONFIRMED_CONTEXT.format(reason=token),
            _SYSTEM_MESSAGE_UNCONFIRMED.format(reason=token),
            "host_denial_grant_unconfirmed",
        )
    classifier = source is None or source in _CLASSIFIER_SOURCES
    session_id = payload.get("session_id")
    tool_use_id = payload.get("tool_use_id")
    identity_bounded = (
        type(session_id) is str
        and 0 < len(session_id) <= 256
        and type(tool_use_id) is str
        and 0 < len(tool_use_id) <= 256
    )
    offer = (
        classifier
        and reason not in _NO_VERDICT_REASONS
        and identity_bounded
        and _note_retry_offer(cast(str, session_id), cast(str, tool_use_id), _state=_state)
    )
    if offer:
        return PermissionDeniedAdvisory(
            "grant_confirmed",
            True,
            _GRANT_CONFIRMED_CONTEXT,
            _SYSTEM_MESSAGE_CONFIRMED,
            "host_denial_retry_offered",
        )
    return PermissionDeniedAdvisory(
        "grant_confirmed",
        False,
        _GRANT_CONFIRMED_PAUSE_CONTEXT,
        _SYSTEM_MESSAGE_CONFIRMED_PAUSE,
        "host_denial_retry_exhausted",
    )


def admission_absent_advisory(
    host: AdvisoryHost,
    workspace_locator: str | None,
    *,
    connect: FactsConnector | None = None,
    run_async: AsyncRunner | None = None,
    skip_service: bool = False,
    _state: Path | None = None,
) -> str | None:
    """One bounded ``SessionStart`` line when a policy-route host has no admission entry.

    Emitted only when all three hold: the host's bridge recorded a policy serving route, the
    host's own admission file reads ``absent`` (never ``unknown``, ``partial``, ``foreign``,
    or ``present``), and the running service confirms the repository grant permits external
    review. The two local reads run first so a strict route or an admitted repository costs no
    service round-trip; any unread fact keeps the line silent.
    """

    if host not in ADMISSION_ADVISORY_HOSTS or workspace_locator is None:
        return None
    try:
        from yoetz.application.serving_route import read_serving_route

        if read_serving_route(host, _state=_state) != "policy":
            return None
        if _read_admission_state(host, workspace_locator) != "absent":
            return None
        facts = read_host_denial_facts(
            host,
            workspace_locator,
            connect=connect,
            run_async=run_async,
            skip_service=skip_service,
            _state=_state,
        )
    except Exception:
        return None
    if not facts.review_authorized or facts.admission_state != "absent":
        return None
    return _ADMISSION_ABSENT_ADVISORY.format(command=_ADMISSION_COMMANDS[host])
