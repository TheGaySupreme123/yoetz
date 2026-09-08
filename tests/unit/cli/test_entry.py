"""Tests for the lightweight native hook console dispatch."""

from __future__ import annotations

import sys

import pytest

from yoetz.cli import entry, observe_hooks


def test_observe_fast_path_propagates_handler_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exit_seven(**_kwargs: object) -> int:
        return 7

    monkeypatch.setattr(observe_hooks, "handle_observe", exit_seven)

    assert (
        entry._observe_fast_path(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            ["--event", "PostToolUse"]
        )
        == 7
    )


def test_observe_fast_path_degrades_handler_failure_to_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**_kwargs: object) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(observe_hooks, "handle_observe", fail)

    assert (
        entry._observe_fast_path(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            ["--event", "PostToolUse"]
        )
        == 0
    )


def test_claude_observe_fast_path_forwards_profile_and_entry_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_handler(**kwargs: object) -> int:
        captured.update(kwargs)
        return 7

    monkeypatch.setattr(entry, "_ENTRY_MONOTONIC", 12.5)
    monkeypatch.setattr(observe_hooks, "handle_claude_observe", fake_handler)

    assert (
        entry._claude_observe_fast_path(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            [
                "--workspace",
                "/project",
                "--event",
                "PostToolUse",
                "--observation-profile",
                "claude-code-ordinary-observation-v1",
            ]
        )
        == 7
    )
    assert captured == {
        "event_name": "PostToolUse",
        "workspace": "/project",
        "observation_profile": "claude-code-ordinary-observation-v1",
        "_entry_monotonic": 12.5,
    }


def test_cursor_observe_fast_path_forwards_profile_and_entry_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_handler(**kwargs: object) -> int:
        captured.update(kwargs)
        return 3

    monkeypatch.setattr(entry, "_ENTRY_MONOTONIC", 8.25)
    monkeypatch.setattr(observe_hooks, "handle_cursor_observe", fake_handler)

    assert (
        entry._cursor_observe_fast_path(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            [
                "--event",
                "postToolUse",
                "--workspace",
                ".",
                "--observation-profile",
                "cursor-ordinary-observation-v1",
            ]
        )
        == 3
    )
    assert captured == {
        "event_name": "postToolUse",
        "workspace": ".",
        "observation_profile": "cursor-ordinary-observation-v1",
        "_entry_monotonic": 8.25,
    }


@pytest.mark.parametrize(
    "arguments",
    [
        ["--event", "PostToolUse", "--event", "Stop"],
        ["--event", "PostToolUse", "--unknown", "value"],
        ["--event"],
        ["--help"],
    ],
)
def test_claude_fast_path_falls_through_for_non_exact_arguments(arguments: list[str]) -> None:
    assert (
        entry._claude_observe_fast_path(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            arguments
        )
        is None
    )


def test_main_dispatches_claude_before_loading_full_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["yoetz", "hooks", "claude-observe", "--event", "Stop"])

    def fast_path(_arguments: list[str]) -> int:
        return 9

    monkeypatch.setattr(entry, "_claude_observe_fast_path", fast_path)

    with pytest.raises(SystemExit) as caught:
        entry.main()

    assert caught.value.code == 9
