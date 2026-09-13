from __future__ import annotations

import ctypes
import signal
import time
from collections.abc import Callable
from types import TracebackType
from typing import Self

import pytest

from yoetz.adapters.integrations import linux_artifact_presence as module
from yoetz.adapters.integrations.linux_artifact_presence import (
    ConversationState,
    LinuxArtifactUserPresence,
    LinuxPamAuthenticator,
    PamMessage,
    PamResponse,
    ResponseAllocator,
    converse,
)
from yoetz.cli.trusted_console import TrustedConsoleError
from yoetz.ports.plugin_artifacts import ArtifactAuthority

_DIGEST = "sha256:" + "a" * 64
_REVIEW_ID = "b" * 64
_MODULE = "yoetz.adapters.integrations.linux_artifact_presence"


def _authority() -> ArtifactAuthority:
    return ArtifactAuthority("review_only", _DIGEST, _REVIEW_ID)


class _Console:
    """Scripted stand-in for the verified foreground console."""

    def __init__(
        self,
        *,
        secret: bytes | None = b"correct horse",
        open_error: str | None = None,
        read_error: str | None = None,
    ) -> None:
        self.secret = secret
        self.open_error = open_error
        self.read_error = read_error
        self.written: list[str] = []
        self.prompts: list[str] = []
        self.opened = 0
        self.closed = 0

    def __call__(self) -> Self:
        return self

    def __enter__(self) -> Self:
        if self.open_error is not None:
            raise TrustedConsoleError(self.open_error)
        self.opened += 1
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.closed += 1

    def write(self, value: str) -> None:
        self.written.append(value)

    def read_secret(self, prompt: str, maximum: int) -> bytearray:
        self.prompts.append(prompt)
        assert maximum > 0
        if self.read_error is not None:
            raise TrustedConsoleError(self.read_error)
        assert self.secret is not None
        return bytearray(self.secret)


class _Authenticator:
    """Scripted operating-system verifier that consumes the console password like PAM."""

    def __init__(self, expected: bytes, *, error: BaseException | None = None) -> None:
        self.expected = expected
        self.error = error
        self.accounts: list[str] = []
        self.results: list[bool] = []

    def authenticate(self, account: str, read_password: Callable[[], bytearray]) -> bool:
        self.accounts.append(account)
        if self.error is not None:
            raise self.error
        secret = read_password()
        try:
            result = bytes(secret) == self.expected
        finally:
            for index in range(len(secret)):
                secret[index] = 0
        self.results.append(result)
        return result


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MODULE}.sys.platform", "linux")
    monkeypatch.setattr(f"{_MODULE}.invoking_account", lambda: "operator")


def _previous_alarm() -> object:
    return signal.getsignal(signal.SIGALRM)


def test_linux_presence_reauthenticates_the_invoking_account_through_the_console(
    linux: None,
) -> None:
    console = _Console()
    authenticator = _Authenticator(b"correct horse")
    before = _previous_alarm()

    LinuxArtifactUserPresence(
        _authenticator=authenticator, _console=console
    ).verify_artifact_review(_authority())

    assert authenticator.accounts == ["operator"]
    assert authenticator.results == [True]
    assert console.opened == 1 and console.closed == 1
    banner = "".join(console.written)
    assert "plugin_artifact_apply" in banner
    assert _DIGEST in banner
    assert _REVIEW_ID in banner
    assert "operator" in banner
    assert console.prompts == ["Password for operator: "]
    assert signal.getsignal(signal.SIGALRM) is before
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_linux_presence_wrong_password_fails_closed(linux: None) -> None:
    console = _Console(secret=b"wrong")
    authenticator = _Authenticator(b"correct horse")

    with pytest.raises(RuntimeError, match="human_authority_unavailable"):
        LinuxArtifactUserPresence(
            _authenticator=authenticator, _console=console
        ).verify_artifact_review(_authority())
    assert authenticator.results == [False]


@pytest.mark.parametrize("reason", ["cancelled", "eof", "empty_input", "interrupted"])
def test_linux_presence_console_cancellation_fails_closed(linux: None, reason: str) -> None:
    console = _Console(read_error=reason)
    authenticator = _Authenticator(b"correct horse")

    with pytest.raises(RuntimeError, match="human_authority_unavailable"):
        LinuxArtifactUserPresence(
            _authenticator=authenticator, _console=console
        ).verify_artifact_review(_authority())
    assert authenticator.results == []
    assert console.closed == 1


def test_linux_presence_requires_the_trusted_foreground_console(linux: None) -> None:
    console = _Console(open_error="trusted_console_required")
    authenticator = _Authenticator(b"correct horse")

    with pytest.raises(RuntimeError, match="human_authority_unavailable"):
        LinuxArtifactUserPresence(
            _authenticator=authenticator, _console=console
        ).verify_artifact_review(_authority())
    assert authenticator.accounts == []


@pytest.mark.parametrize(
    "error", [OSError("libpam.so.0: cannot open shared object file"), RuntimeError("pam_start")]
)
def test_linux_presence_unavailable_pam_fails_closed(linux: None, error: BaseException) -> None:
    console = _Console()
    authenticator = _Authenticator(b"correct horse", error=error)

    with pytest.raises(RuntimeError, match="human_authority_unavailable") as caught:
        LinuxArtifactUserPresence(
            _authenticator=authenticator, _console=console
        ).verify_artifact_review(_authority())
    assert caught.value.__cause__ is error


def test_linux_presence_deadline_fails_closed_and_restores_the_alarm(
    linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(f"{_MODULE}._TIMEOUT_SECONDS", 0.05)
    console = _Console()
    before = _previous_alarm()

    class _Stalled:
        def authenticate(self, account: str, read_password: Callable[[], bytearray]) -> bool:
            # A bounded stand-in for an operator who never answers: the alarm must interrupt it
            # long before the five-second ceiling.
            time.sleep(5)
            return True

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="human_authority_unavailable"):
        LinuxArtifactUserPresence(
            _authenticator=_Stalled(), _console=console
        ).verify_artifact_review(_authority())
    assert time.monotonic() - started < 4
    assert signal.getsignal(signal.SIGALRM) is before
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_linux_presence_refuses_off_linux_before_opening_the_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(f"{_MODULE}.sys.platform", "darwin")
    console = _Console()
    authenticator = _Authenticator(b"correct horse")

    with pytest.raises(RuntimeError, match="human_authority_unavailable"):
        LinuxArtifactUserPresence(
            _authenticator=authenticator, _console=console
        ).verify_artifact_review(_authority())
    assert console.opened == 0
    assert authenticator.accounts == []


def test_linux_presence_refuses_setup_composition_authority(linux: None) -> None:
    console = _Console()
    authenticator = _Authenticator(b"correct horse")

    with pytest.raises(RuntimeError, match="human_authority_unavailable"):
        LinuxArtifactUserPresence(
            _authenticator=authenticator, _console=console
        ).verify_artifact_review(ArtifactAuthority("setup_composition", _DIGEST))
    assert console.opened == 0


@pytest.mark.parametrize("name", ["", "with space", "non\x00printable", "ünïcode", "x" * 257])
def test_linux_presence_rejects_an_unusable_account_name(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    class _Entry:
        pw_name = name

    def getpwuid(_uid: int) -> _Entry:
        return _Entry()

    monkeypatch.setattr(f"{_MODULE}.sys.platform", "linux")
    monkeypatch.setattr("pwd.getpwuid", getpwuid)
    console = _Console()

    with pytest.raises(RuntimeError, match="human_authority_unavailable"):
        LinuxArtifactUserPresence(
            _authenticator=_Authenticator(b"x"), _console=console
        ).verify_artifact_review(_authority())
    assert console.opened == 0


def _messages(*styles: int):  # noqa: ANN202 - inferred ctypes array type
    keep = [PamMessage(style, b"Password: ") for style in styles]
    array = (ctypes.POINTER(PamMessage) * len(keep))(*(ctypes.pointer(item) for item in keep))
    return array, keep


def test_pam_conversation_answers_exactly_one_hidden_prompt_and_owns_the_memory() -> None:
    allocator = ResponseAllocator(ctypes.CDLL(None))
    state = ConversationState()
    secret = bytearray(b"correct horse")
    messages, _keep = _messages(4, 1, 3)
    out = ctypes.c_void_p()

    result = converse(
        allocator,
        state,
        lambda: secret,
        3,
        ctypes.addressof(messages),
        ctypes.addressof(out),
    )

    assert result == 0
    assert state.prompted == 1 and state.failed is False
    assert bytes(secret) == b"\x00" * len(b"correct horse")
    assert out.value
    answers = ctypes.cast(out.value, ctypes.POINTER(PamResponse))
    assert answers[0].resp is None and answers[2].resp is None
    assert answers[1].resp is not None
    assert ctypes.string_at(answers[1].resp) == b"correct horse"
    assert answers[1].resp_retcode == 0
    allocator.release(out.value, 3)


@pytest.mark.parametrize(
    ("styles", "count"),
    [((2,), 1), ((1, 1), 2), ((99,), 1), ((), 0)],
)
def test_pam_conversation_refuses_echoed_repeated_or_unknown_prompts(
    styles: tuple[int, ...], count: int
) -> None:
    allocator = ResponseAllocator(ctypes.CDLL(None))
    state = ConversationState()
    reads = 0

    def read_password() -> bytearray:
        nonlocal reads
        reads += 1
        return bytearray(b"secret")

    messages, _keep = _messages(*styles) if styles else _messages(1)
    out = ctypes.c_void_p()

    result = converse(
        allocator, state, read_password, count, ctypes.addressof(messages), ctypes.addressof(out)
    )

    assert result == 19
    assert state.failed is True
    assert out.value is None
    assert reads <= 1


def test_pam_conversation_records_console_failure_without_crossing_into_c() -> None:
    allocator = ResponseAllocator(ctypes.CDLL(None))
    state = ConversationState()

    def read_password() -> bytearray:
        raise TrustedConsoleError("cancelled")

    messages, _keep = _messages(1)
    out = ctypes.c_void_p()

    assert (
        converse(
            allocator, state, read_password, 1, ctypes.addressof(messages), ctypes.addressof(out)
        )
        == 19
    )
    assert state.failed is True
    assert out.value is None


def _libpam_available() -> bool:
    try:
        ctypes.CDLL("libpam.so.0")
    except OSError:
        return False
    return True


@pytest.mark.skipif(not _libpam_available(), reason="Linux-PAM is not installed on this host")
def test_real_pam_rejects_a_wrong_password_for_the_invoking_account() -> None:
    account = module.invoking_account()
    prompts = 0

    def read_password() -> bytearray:
        nonlocal prompts
        prompts += 1
        return bytearray(b"not-the-account-password-" + str(time.time_ns()).encode())

    assert LinuxPamAuthenticator().authenticate(account, read_password) is False
    assert prompts <= 1
