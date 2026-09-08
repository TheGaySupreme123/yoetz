from __future__ import annotations

import pytest

import yoetz.cli.entry as entry
import yoetz.cli.observe_hooks as observe_hooks


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
