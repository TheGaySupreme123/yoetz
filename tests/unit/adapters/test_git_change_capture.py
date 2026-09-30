"""Check-time change capture from a real local Git workspace (ADR-031, issue #883)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import yoetz.adapters.git_change_capture as capture_module
from yoetz.adapters.git_change_capture import GitChangeCaptureAdapter
from yoetz.ports.change_capture import (
    ChangeCaptureUnavailable,
    CheckChangeCapture,
    TaskChangeBase,
    decode_check_change,
    encode_check_change,
)

_EMPTY_SHA1_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        env={
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": os.fspath(repository),
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": os.defpath,
        },
    )
    return completed.stdout.decode("utf-8")


def _commit(repository: Path, message: str) -> None:
    _git(
        repository,
        "-c",
        "user.name=Yoetz Test",
        "-c",
        "user.email=yoetz@example.invalid",
        "commit",
        "--quiet",
        "-am",
        message,
    )


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir(mode=0o700)
    _git(repository, "init", "--quiet")
    (repository / "selectors.ts").write_text(
        "export function key(value: number | string) {\n  return String(value);\n}\n",
        encoding="utf-8",
    )
    (repository / ".gitignore").write_text("local.env\nbuild/\n", encoding="utf-8")
    _git(repository, "add", "--", "selectors.ts", ".gitignore")
    _git(
        repository,
        "-c",
        "user.name=Yoetz Test",
        "-c",
        "user.email=yoetz@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "baseline",
    )
    return repository


@pytest.fixture(autouse=True)
def isolated_global_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The capture honours the owner's global ignore file; keep the test runner's own out of it.
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", os.fspath(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def _text(capture: CheckChangeCapture) -> str:
    return capture.text.decode("utf-8")


def test_script_rewrite_without_any_edit_tool_is_captured_against_the_task_base(
    tmp_path: Path,
) -> None:
    """kea shape: a shell Python rewrite and a commit, no ``apply_patch`` anywhere."""

    repository = _repository(tmp_path)
    adapter = GitChangeCaptureAdapter()
    base = adapter.read_task_base(os.fspath(repository))
    assert base.object_format == "sha1"

    target = repository / "selectors.ts"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "return String(value);", "return typeof value + ':' + String(value);"
        ),
        encoding="utf-8",
    )
    _commit(repository, "agent commit")
    (repository / "notes.md").write_text("follow-up\n", encoding="utf-8")

    capture = adapter.capture(os.fspath(repository), base)

    assert capture.base == "task_start"
    assert (capture.tracked_files, capture.untracked_files, capture.omitted_files) == (1, 1, 0)
    assert not capture.truncated
    text = _text(capture)
    assert text.startswith("Yoetz check-time change: captured by the Yoetz service")
    assert "Base: the commit HEAD named when this task started." in text
    assert "-  return String(value);" in text
    assert "+  return typeof value + ':' + String(value);" in text
    assert "diff --git a/notes.md b/notes.md" in text and "+follow-up" in text
    assert "  M selectors.ts (+1 -1)" in text
    assert "  A notes.md (+1 -0) untracked" in text


def test_without_a_recorded_base_the_change_is_against_head_and_says_so(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    adapter = GitChangeCaptureAdapter()
    target = repository / "selectors.ts"
    target.write_text("export const committed = 1;\n", encoding="utf-8")
    _commit(repository, "agent commit")
    target.write_text("export const committed = 2;\n", encoding="utf-8")

    capture = adapter.capture(os.fspath(repository), None)

    assert capture.base == "head"
    text = _text(capture)
    assert "commits made during the task may be missing" in text
    assert "-export const committed = 1;" in text
    assert "+export const committed = 2;" in text
    assert "return String(value)" not in text


def test_unresolvable_base_falls_back_to_head(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    missing = TaskChangeBase("sha1", "0" * 40)

    capture = GitChangeCaptureAdapter().capture(os.fspath(repository), missing)

    assert capture.base == "head"
    assert "Changed files: none. The working tree matches the base." in _text(capture)


def test_ignored_and_globally_ignored_files_are_never_read(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "local.env").write_text("API_TOKEN=repo-ignored-canary\n", encoding="utf-8")
    (repository / "build").mkdir()
    (repository / "build" / "out.js").write_text("build-canary\n", encoding="utf-8")
    global_ignore = Path(os.environ["HOME"]) / ".config" / "git" / "ignore"
    global_ignore.parent.mkdir(parents=True)
    global_ignore.write_text("*.secret\n", encoding="utf-8")
    (repository / "owner.secret").write_text("global-ignore-canary\n", encoding="utf-8")
    (repository / "kept.txt").write_text("kept\n", encoding="utf-8")

    capture = GitChangeCaptureAdapter().capture(os.fspath(repository), None)

    text = _text(capture)
    assert "+kept" in text
    for canary in ("repo-ignored-canary", "build-canary", "global-ignore-canary"):
        assert canary not in text
    assert "local.env" not in text and "owner.secret" not in text
    assert capture.untracked_files == 1


def test_links_are_named_but_never_followed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("outside-canary\n", encoding="utf-8")
    os.symlink(outside / "private.txt", repository / "link.txt")
    os.link(outside / "private.txt", repository / "hardlink.txt")

    capture = GitChangeCaptureAdapter().capture(os.fspath(repository), None)

    text = _text(capture)
    assert "outside-canary" not in text
    assert "  A link.txt untracked; not shown: links, special files" in text
    assert "  A hardlink.txt untracked; not shown: links, special files" in text
    assert capture.omitted_files == 2 and capture.truncated


def test_oversized_files_are_left_out_whole_and_the_rest_is_still_shown(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "a_generated.txt").write_text("x" * 200_000 + "\n", encoding="utf-8")
    (repository / "big.lock").write_text(
        "".join(f"line {index}\n" for index in range(40_000)), encoding="utf-8"
    )
    _git(repository, "add", "--", "big.lock")
    (repository / "z_small.py").write_text("print('kept')\n", encoding="utf-8")

    capture = GitChangeCaptureAdapter(_max_text_bytes=64 * 1024).capture(
        os.fspath(repository), None
    )

    text = _text(capture)
    assert capture.truncated and capture.omitted_files == 2
    assert len(capture.text) <= 64 * 1024
    assert "+print('kept')" in text
    assert "  A a_generated.txt untracked; not shown: too large for this capture" in text
    assert "  A big.lock (+40000 -0) not shown: the capture reached its size" in text
    assert "line 39999" not in text


def test_binary_untracked_file_is_described_not_embedded(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "image.bin").write_bytes(b"\x89PNG\0\0binary-canary")

    text = _text(GitChangeCaptureAdapter().capture(os.fspath(repository), None))

    assert "Binary files /dev/null and b/image.bin differ" in text
    assert "binary-canary" not in text


def test_unborn_repository_starts_from_the_empty_tree(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir(mode=0o700)
    _git(repository, "init", "--quiet")
    adapter = GitChangeCaptureAdapter()

    base = adapter.read_task_base(os.fspath(repository))
    assert base == TaskChangeBase("sha1", _EMPTY_SHA1_TREE)
    (repository / "first.py").write_text("print(1)\n", encoding="utf-8")
    _git(repository, "add", "--", "first.py")
    _commit(repository, "first commit")
    (repository / "second.py").write_text("print(2)\n", encoding="utf-8")

    capture = adapter.capture(os.fspath(repository), base)

    assert capture.base == "task_start"
    text = _text(capture)
    assert "+print(1)" in text and "+print(2)" in text


def test_subdirectory_locator_captures_the_whole_repository(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    nested = repository / "pkg"
    nested.mkdir()
    (repository / "selectors.ts").write_text("export const root = true;\n", encoding="utf-8")

    capture = GitChangeCaptureAdapter().capture(os.fspath(nested), None)

    assert "+export const root = true;" in _text(capture)


def test_partial_clone_is_refused_rather_than_fetched(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _git(repository, "config", "remote.origin.promisor", "true")

    with pytest.raises(ChangeCaptureUnavailable) as caught:
        GitChangeCaptureAdapter().capture(os.fspath(repository), None)
    assert caught.value.reason == "unsupported_repository"


def test_plain_directory_is_not_git(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir(mode=0o700)

    with pytest.raises(ChangeCaptureUnavailable) as caught:
        GitChangeCaptureAdapter().capture(os.fspath(plain), None)
    assert caught.value.reason == "not_git"


def test_capture_round_trips_through_its_object_encoding(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "selectors.ts").write_text("export const é = 'ü';\n", encoding="utf-8")
    capture = GitChangeCaptureAdapter().capture(os.fspath(repository), None)

    assert decode_check_change(encode_check_change(capture)) == capture


@pytest.mark.parametrize("placement", ["worktree_config", "two_sections_one_line", "include"])
def test_filter_driver_hidden_from_the_lexical_fence_is_refused_and_never_run(
    tmp_path: Path, placement: str
) -> None:
    repository = _repository(tmp_path)
    (repository / ".gitattributes").write_text("*.ts filter=probe\n", encoding="utf-8")
    marker = tmp_path / "filter-ran"
    driver = f'[filter "probe"]\n\tclean = "touch {marker}; cat"\n'
    config = repository / ".git" / "config"
    if placement == "worktree_config":
        _git(repository, "config", "extensions.worktreeConfig", "true")
        (repository / ".git" / "config.worktree").write_text(driver, encoding="utf-8")
    elif placement == "two_sections_one_line":
        with config.open("a", encoding="utf-8") as handle:
            handle.write("[core]" + driver)
    else:
        included = tmp_path / "included.cfg"
        included.write_text(driver, encoding="utf-8")
        _git(repository, "config", "extensions.worktreeConfig", "true")
        (repository / ".git" / "config.worktree").write_text(
            f"[include]\n\tpath = {included}\n", encoding="utf-8"
        )
    (repository / "selectors.ts").write_text("export const changed = 1;\n", encoding="utf-8")

    with pytest.raises(ChangeCaptureUnavailable) as raised:
        GitChangeCaptureAdapter().capture(os.fspath(repository), None)

    assert raised.value.reason in {"unsupported_repository", "unsafe_root"}
    assert not marker.exists()


def test_repository_excludes_file_and_included_global_ignore_are_honoured(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    local_ignore = repository / ".git" / "local-ignore"
    local_ignore.write_text("owner-notes.txt\n", encoding="utf-8")
    _git(repository, "config", "core.excludesFile", os.fspath(local_ignore))
    (repository / "owner-notes.txt").write_text("repository-ignored words\n", encoding="utf-8")

    home = Path(os.environ["HOME"])
    (home / "global-ignore").write_text("personal.txt\n", encoding="utf-8")
    (home / "extra.gitconfig").write_text(
        f"[core]\n\texcludesFile = {home / 'global-ignore'}\n", encoding="utf-8"
    )
    (home / ".gitconfig").write_text(
        f"[include]\n\tpath = {home / 'extra.gitconfig'}\n", encoding="utf-8"
    )
    (tmp_path / "other").mkdir()
    other = _repository(tmp_path / "other")
    (other / "personal.txt").write_text("globally ignored words\n", encoding="utf-8")

    first = _text(GitChangeCaptureAdapter().capture(os.fspath(repository), None))
    second = _text(GitChangeCaptureAdapter().capture(os.fspath(other), None))

    assert "owner-notes.txt" not in first and "repository-ignored words" not in first
    assert "personal.txt" not in second and "globally ignored words" not in second


_NAME_ONLY = "not shown: listed by name only, as for every file named like this"


def test_untracked_credential_files_are_named_but_never_read(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / ".env").write_text("DB_PASS=hunter2hunter2\n", encoding="utf-8")
    (repository / "deploy.pem").write_text("not a marker but still a key\n", encoding="utf-8")

    text = _text(GitChangeCaptureAdapter().capture(os.fspath(repository), None))

    assert "hunter2hunter2" not in text and "still a key" not in text
    assert f".env untracked; {_NAME_ONLY}" in text
    assert f"deploy.pem untracked; {_NAME_ONLY}" in text


def test_tracked_credential_files_are_named_but_their_change_is_never_shown(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / ".env.production").write_text("DB_PASS=before-before\n", encoding="utf-8")
    _git(repository, "add", "--", ".env.production")
    _commit(repository, "track an env file")
    adapter = GitChangeCaptureAdapter()
    base = adapter.read_task_base(os.fspath(repository))

    (repository / ".env.production").write_text("DB_PASS=after-after-after\n", encoding="utf-8")
    (repository / "service.key").write_text("committed key material\n", encoding="utf-8")
    _git(repository, "add", "--", "service.key")
    _commit(repository, "agent commits a key")
    (repository / "selectors.ts").write_text("export const shown = 1;\n", encoding="utf-8")

    capture = adapter.capture(os.fspath(repository), base)
    text = _text(capture)

    for secret in ("before-before", "after-after-after", "committed key material"):
        assert secret not in text
    assert f"M .env.production (+1 -1) {_NAME_ONLY}" in text
    assert f"A service.key (+1 -0) {_NAME_ONLY}" in text
    assert "export const shown = 1;" in text
    assert capture.omitted_files == 2 and capture.truncated


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now


def _slow_git(
    monkeypatch: pytest.MonkeyPatch,
    *,
    seconds_per_call: float,
    slow_from_call: int = 0,
    seconds_before: float = 0.0,
) -> tuple[_FakeClock, list[float]]:
    """Git calls from ``slow_from_call`` on take ``seconds_per_call`` of a fake clock.

    Earlier calls take ``seconds_before``.
    A call whose granted timeout is shorter than that ends the way the hardened runner ends a
    timed-out Git: the clock advances by the timeout and the call fails. Nothing sleeps.
    """

    clock = _FakeClock()
    timeouts: list[float] = []
    real = capture_module.run_read_only_git

    def run(*args: Any, **kwargs: Any) -> tuple[int, bytes]:
        timeout = float(kwargs["timeout_seconds"])
        timeouts.append(timeout)
        seconds = seconds_per_call if len(timeouts) > slow_from_call else seconds_before
        if timeout < seconds:
            clock.now += timeout
            raise ValueError("git_failed")
        clock.now += seconds
        return real(*args, **kwargs)

    monkeypatch.setattr(capture_module, "time", clock)
    monkeypatch.setattr(capture_module, "run_read_only_git", run)
    return clock, timeouts


def test_slow_git_makes_the_capture_unavailable_within_its_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "selectors.ts").write_text("export const changed = 1;\n", encoding="utf-8")
    clock, timeouts = _slow_git(monkeypatch, seconds_per_call=6.0)
    started = clock.now

    with pytest.raises(ChangeCaptureUnavailable) as raised:
        GitChangeCaptureAdapter(_deadline_seconds=20.0).capture(os.fspath(repository), None)

    assert raised.value.reason == "git_failed"
    # Each call got at most 10 s and never more than what was left of the 20 s deadline.
    assert timeouts[0] == 10.0 and all(timeout <= 10.0 for timeout in timeouts)
    assert timeouts[-1] < 6.0
    assert clock.now - started == pytest.approx(20.0)


def test_deadline_inside_the_diff_lists_every_file_and_shows_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "selectors.ts").write_text("export const changed = 1;\n", encoding="utf-8")
    (repository / "notes.txt").write_text("untracked words\n", encoding="utf-8")
    # Config, object format, HEAD, name-status and numstat take 3 s each; the patch never ends.
    clock, timeouts = _slow_git(
        monkeypatch, seconds_per_call=60.0, slow_from_call=5, seconds_before=3.0
    )
    started = clock.now

    capture = GitChangeCaptureAdapter(_deadline_seconds=20.0).capture(os.fspath(repository), None)
    text = _text(capture)

    assert timeouts[5] == pytest.approx(5.0)  # the patch got only what was left
    assert clock.now - started == pytest.approx(20.0)
    assert capture.truncated
    assert (
        "selectors.ts (+1 -3) not shown: the capture reached its size, time or file limit" in text
    )
    assert "export const changed" not in text
    assert "untracked words" not in text and "the untracked count above is a lower bound" in text


def test_git_without_show_scope_fails_closed_before_any_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git older than 2.26 rejects ``--show-scope``; the filter check is never skipped."""

    repository = _repository(tmp_path)
    (repository / "selectors.ts").write_text("export const changed = 1;\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    real = capture_module.run_read_only_git

    def run(handle: Any, arguments: tuple[str, ...], **kwargs: Any) -> tuple[int, bytes]:
        calls.append(arguments)
        if "--show-scope" in arguments:
            raise ValueError("git_failed")  # exit 129: unknown option
        return real(handle, arguments, **kwargs)

    monkeypatch.setattr(capture_module, "run_read_only_git", run)

    with pytest.raises(ChangeCaptureUnavailable) as raised:
        GitChangeCaptureAdapter().capture(os.fspath(repository), None)

    assert raised.value.reason == "git_failed"
    assert not any("diff" in arguments for arguments in calls)


def test_untracked_listing_over_its_limit_lists_what_fits_and_discloses_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "selectors.ts").write_text("export const changed = 1;\n", encoding="utf-8")
    for index in range(40):
        (repository / f"generated-{index:02d}.txt").write_text(f"row {index}\n", encoding="utf-8")
    monkeypatch.setattr(capture_module, "_LIST_LIMIT", 256)

    capture = GitChangeCaptureAdapter().capture(os.fspath(repository), None)
    text = _text(capture)

    assert capture.truncated
    assert 0 < capture.untracked_files < 40
    assert "export const changed = 1;" in text  # the tracked change is not lost
    assert "+row 0" in text and "generated-39.txt" not in text
    assert "the untracked count above is a lower bound" in text


def test_global_ignore_read_past_the_deadline_makes_the_capture_unavailable() -> None:
    global_excludes_file = getattr(capture_module, "_global_excludes_file")

    with pytest.raises(ChangeCaptureUnavailable) as raised:
        global_excludes_file(0.0)

    assert raised.value.reason == "git_failed"


def test_long_file_listing_never_overruns_the_object_bound(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    deep = repository / ("a" * 60) / ("b" * 60) / ("c" * 60)
    deep.mkdir(parents=True)
    for index in range(100):
        (deep / f"link{index:03d}{'q' * 20}").symlink_to("/dev/null")
    filler = repository / "zz"
    filler.mkdir()
    for index in range(399):
        (filler / f"s{index:03d}").write_text("y" * 700 + "\n", encoding="utf-8")

    capture = GitChangeCaptureAdapter().capture(os.fspath(repository), None)
    text = _text(capture)

    assert capture.truncated
    assert "more changed files are not listed" in text
