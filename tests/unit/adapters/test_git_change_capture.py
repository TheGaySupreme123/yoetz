"""Check-time change capture from a real local Git workspace (ADR-031, issue #883)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

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
