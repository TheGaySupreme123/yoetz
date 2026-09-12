"""The agent install guide names the platform boundary and the no-question-tool rule (issue #709).

Two first-time users hit the same two holes: an agent on Cursor, which has no structured question
tool, asked nothing and chose for them; and an agent on native Windows watched every command fail
as ``internal_error`` before working out that Yoetz is POSIX-only. The guide, the shipped install
page, and the CLI refusal must keep telling the same story.
"""

from __future__ import annotations

import re
from pathlib import Path

from yoetz.cli.entry import UNSUPPORTED_PLATFORM_MESSAGE

_ROOT = Path(__file__).resolve().parents[3]


def _text(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def _collapsed(path: str) -> str:
    return " ".join(_text(path).split())


def test_agent_start_checks_the_platform_before_installing() -> None:
    guide = _collapsed("docs/usage/agent-start.md")

    assert "## 0. Check the platform first" in guide
    assert "Yoetz runs on macOS and Linux" in guide
    assert "Native Windows is not supported" in guide
    assert "WSL 2" in guide
    assert "wsl --install" in guide
    assert 'wsl -e bash -lc "<command>"' in guide
    assert "PowerShell as administrator" in guide
    assert "curl -LsSf https://astral.sh/uv/install.sh | sh" in guide
    assert "uv tool update-shell" in guide
    # Section 0 precedes the install section, so the platform is known before anything installs.
    assert guide.index("## 0. Check the platform first") < guide.index("## 1. Install")
    # The WSL host cell is untested, and the guide says so rather than implying support.
    assert "untested and not claimed" in guide


def test_agent_start_tells_an_agent_without_a_question_tool_to_ask_in_chat() -> None:
    guide = _collapsed("docs/usage/agent-start.md")

    assert "### Ask in chat when you have no question tool" in guide
    assert "Cursor's agent among them" in guide
    assert "One decision per message" in guide
    assert "end your turn and wait" in guide
    assert "Never use `yoetz setup run --accept` or `--non-interactive`" in guide
    assert "### Hand over the terminal like it is their first" in guide
    assert "which application to open" in guide


def test_install_page_windows_section_matches_the_cli_refusal() -> None:
    page = _text("docs/usage/install-and-first-run.md")

    assert re.search(r"^## Windows$", page, flags=re.MULTILINE)
    collapsed = " ".join(page.split())
    assert "refuses with `unsupported_platform`" in collapsed
    assert "wsl --install" in collapsed
    assert "curl -LsSf https://astral.sh/uv/install.sh | sh" in collapsed
    # Shipped-product voice: no repository tooling or source paths on the usage page.
    assert "src/yoetz" not in page
    assert "uv run" not in page

    # The CLI line points at exactly that heading's GitHub anchor.
    assert UNSUPPORTED_PLATFORM_MESSAGE.startswith("unsupported_platform: ")
    assert "docs/usage/install-and-first-run.md#windows" in UNSUPPORTED_PLATFORM_MESSAGE
    assert "WSL 2" in UNSUPPORTED_PLATFORM_MESSAGE

    readme = " ".join(_text("README.md").split())
    assert "docs/usage/install-and-first-run.md#windows" in readme


def test_agent_start_lists_the_questions_in_order_with_recommendations() -> None:
    guide = _collapsed("docs/usage/agent-start.md")

    assert "#### The questions, ready to ask" in guide
    for heading in (
        "1. **Codex.**",
        "2. **Review mode.**",
        "3. **The exact change.**",
        "4. **Secret storage**",
        "5. **Provider and model**",
        "6. **Privacy policy**",
        "7. **Credential**: never a question",
    ):
        assert heading in guide, heading
    assert "No recommendation and no default" in guide
    assert "local only is a finished state, not a fallback" in guide
