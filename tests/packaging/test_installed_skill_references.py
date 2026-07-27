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


def test_the_skill_is_packaged_at_all() -> None:
    assert _PACKAGED_SKILL.is_file(), "the installed skill is missing from the packaged resources"
    assert _skill_text().strip(), "the installed skill is empty"


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
