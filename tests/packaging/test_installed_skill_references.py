"""Every reference the installed skill names must resolve without a repository checkout.

The 2026-07-27 Codex dogfood could not author a `start` request from the installed guidance:
`SKILL.md` linked four `references/*.md` files that were never packaged, and MCP resource
discovery failed, so the agent fell back to reading product source. Guidance that names a file the
installed agent cannot open is worse than no guidance -- it sends the reader somewhere empty.

These cases read only the *packaged* tree under ``src/yoetz/resources`` plus the resource registry,
never the repository-root working copy, so they fail exactly the way a real installation would.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from yoetz.mcp.resources import GUIDANCE_RESOURCES, read_resource

_PACKAGED_ROOT: Final = Path(__file__).resolve().parents[2] / "src" / "yoetz" / "resources"
_PACKAGED_SKILL: Final = _PACKAGED_ROOT / "skills" / "codex" / "yoetz" / "SKILL.md"

# `[text](target)` where target is not an absolute URL to somewhere off-product.
_MARKDOWN_LINK: Final = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_YOETZ_URI: Final = re.compile(r"`(yoetz://[^`]+)`")


def _skill_text() -> str:
    return _PACKAGED_SKILL.read_text(encoding="utf-8")


def _cadence_row(text: str, operation: str) -> str:
    prefix = f"| `{operation}` | "
    matches = [line for line in text.splitlines() if line.startswith(prefix)]
    assert len(matches) == 1, f"expected one cadence row for {operation}, found {len(matches)}"
    return matches[0].removeprefix(prefix).removesuffix(" |")


def test_the_skill_is_packaged_at_all() -> None:
    assert _PACKAGED_SKILL.is_file(), "the installed skill is missing from the packaged resources"
    assert _skill_text().strip(), "the installed skill is empty"


def test_step_zero_stops_on_an_empty_guidance_read() -> None:
    """An empty resources/read is a silent miss, not a successful Step 0 (issue #203)."""

    text = _skill_text()
    collapsed = " ".join(text.split())
    assert "resolve without any repository checkout" not in collapsed
    assert "If a `resources/read` result has no text, call `read_guidance`" in collapsed
    assert "with the same URI" in collapsed
    assert "`references/<name>.md`" in collapsed
    assert "Do not call `start` on an empty guidance body" in collapsed
    # #300 trimmed the inlined set to agent-instructions.md. The skill must not tell the agent it
    # already has workflow.md or coverage-and-receipts.md in context — a false pre-delivery claim
    # licenses skipping the fetch, which is strictly worse than the #203 empty read it replaced.
    assert "Initialize `instructions` already include `agent-instructions.md`;" in collapsed
    assert "`workflow.md`, and `coverage-and-receipts.md`" not in collapsed
    assert "Both are already in initialize `instructions`" not in collapsed
    assert "Neither is in initialize `instructions`; read both before the first `start`" in (
        collapsed
    )


def test_step_zero_does_not_use_resources_list_for_discovery() -> None:
    """A failed resources/list is not a missing server (issue #173)."""

    text = _skill_text()
    collapsed = " ".join(text.split())
    assert "Do not call `resources/list` or `list_mcp_resources`" in collapsed
    assert "The five URIs below are the complete catalog" in collapsed
    assert "A list failure is not a missing server" in collapsed
    assert "not a reason to read product source" in collapsed


def test_every_yoetz_uri_the_skill_names_is_a_registered_readable_resource() -> None:
    registered = {resource.uri for resource in GUIDANCE_RESOURCES}
    named = set(_YOETZ_URI.findall(_skill_text()))
    assert named, "the skill names no guidance resources"
    unregistered = sorted(named - registered)
    assert unregistered == [], f"skill names unregistered resource URIs: {unregistered}"
    for uri in sorted(named):
        # Reading proves the bytes are packaged and manifest-verified, not merely listed.
        assert read_resource(uri), f"registered but unreadable: {uri}"


def test_the_skill_has_no_relative_file_links_that_are_not_packaged() -> None:
    text = _skill_text()
    unresolved: list[str] = []
    for target in _MARKDOWN_LINK.findall(text):
        link = target.split("#", 1)[0].strip()
        if not link or link.startswith(("http://", "https://", "yoetz://", "mailto:")):
            continue
        if not (_PACKAGED_SKILL.parent / link).resolve().is_file():
            unresolved.append(link)
    assert unresolved == [], f"skill links files absent from the installed tree: {unresolved}"


@pytest.mark.parametrize("resource", GUIDANCE_RESOURCES, ids=lambda item: item.logical_name)
def test_every_registered_guidance_resource_is_readable_offline(resource: object) -> None:
    logical_name = getattr(resource, "logical_name")
    uri = getattr(resource, "uri")
    payload = read_resource(uri)
    assert payload, f"{logical_name} read back empty"
    # The packaged copy is the one an installation actually serves.
    packaged = _PACKAGED_ROOT / logical_name
    assert packaged.is_file(), f"{logical_name} is registered but not packaged"
    assert packaged.read_bytes() == payload


def test_the_skill_states_how_often_to_call_each_operation() -> None:
    """Knowing *when* to activate is not enough; the dogfood agent had to infer cadence.

    The skill is the only always-available surface that can answer "how often", so every operation
    has to be named there with a frequency, not merely described.
    """

    text = _skill_text()
    for operation in ("start", "publish_work", "status", "check", "respond", "receipt"):
        assert f"`{operation}`" in text, f"the skill never names {operation}"
    for cadence_marker in (
        "Once per task",
        "One batch per material transition",
        "Once at the end",
        "never one per file, tool call, or message",
    ):
        assert cadence_marker in text, f"the skill states no cadence for: {cadence_marker!r}"


def test_check_cadence_nudge_is_mirrored_from_workflow() -> None:
    skill = _skill_text()
    workflow = read_resource("yoetz://guidance/workflow.md").decode("utf-8")
    cadence = _cadence_row(workflow, "check")

    assert _cadence_row(skill, "check") == cadence
    for marker in (
        "Also consider a check when you move between subtasks or phases",
        "`deterministic_only` is local and fast",
        "reserve semantic review for the claim unless the transition itself warrants it",
        "A check with no new events since the last one adds nothing",
    ):
        assert marker in cadence
    assert (
        "- [ ] after a material subtask or phase transition, consider a deliberate-mode check"
        in skill
    )


def test_respond_cadence_is_mirrored_from_workflow() -> None:
    skill = _skill_text()
    workflow = read_resource("yoetz://guidance/workflow.md").decode("utf-8")

    assert _cadence_row(skill, "respond") == _cadence_row(workflow, "respond")


def test_the_skill_does_not_promise_that_responding_clears_a_finding() -> None:
    """Receipts keep every actionable finding unresolved, whatever disposition is recorded."""

    text = _skill_text()
    assert "unresolved_findings_remain" in text
    assert "it does not clear the finding" in text


def test_start_is_authorable_from_guidance_without_reading_product_source() -> None:
    """The workflow guidance must name what a first `start` call needs.

    Two `start` attempts failed in the dogfood: an empty object, then a guessed request-id shape
    and a guessed mode. Both are answerable from guidance alone.
    """

    workflow = read_resource("yoetz://guidance/workflow.md").decode("utf-8")
    combined = (
        workflow + read_resource("yoetz://guidance/agent-instructions.md").decode("utf-8")
    ).lower()
    for token in ("start", "request_id", "mode"):
        assert token in combined, f"guidance never mentions {token}"
