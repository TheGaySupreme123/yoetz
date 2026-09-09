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

from yoetz.adapters.integrations.codex_skill import load_packaged_skill_members
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
    assert "Coverage and setup details are not prerequisites" in collapsed
    assert "Before the first `check`" in collapsed


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


def test_installed_guidance_links_and_explicit_anchors_resolve() -> None:
    members = load_packaged_skill_members()
    # Portable hosts install this skill with the same canonical references.
    documents = {
        name: data.decode("utf-8") for name, data in members.items() if name.endswith(".md")
    }
    documents["portable-SKILL.md"] = (_PACKAGED_ROOT / "skills/portable/yoetz/SKILL.md").read_text(
        encoding="utf-8"
    )
    for name, text in documents.items():
        for target in _MARKDOWN_LINK.findall(text):
            if target.startswith(("http://", "https://", "yoetz://", "mailto:")):
                continue
            relative, _, anchor = target.partition("#")
            resolved = str(Path(name).parent / relative) if relative else name
            assert resolved in documents, f"{name}: missing installed reference {target}"
            if anchor:
                destination = documents[resolved]
                headings = {
                    re.sub(r"[^a-z0-9 -]", "", line.lstrip("# ").lower()).replace(" ", "-")
                    for line in destination.splitlines()
                    if line.startswith("#")
                }
                assert f'id="{anchor}"' in destination or anchor in headings, (name, target)


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


def test_skill_routes_to_the_readable_cadence_owner() -> None:
    """The entrypoint routes to one maintained cadence, rather than copying its whole table."""

    skill = _skill_text()
    assert "yoetz://guidance/workflow.md" in skill
    workflow = read_resource("yoetz://guidance/workflow.md").decode("utf-8")
    for operation in ("start", "publish_work", "status", "check", "respond", "receipt"):
        assert f"`{operation}`" in skill
        assert _cadence_row(workflow, operation)
    cadence = _cadence_row(workflow, "check")
    assert "A check with no new events since the last one adds nothing" in cadence
    assert "not the finding's `subject_frontier`" in _cadence_row(workflow, "respond")


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
