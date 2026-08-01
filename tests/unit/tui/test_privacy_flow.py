"""``/privacy`` selects a policy; it never authorizes one.

The interface used to take its own approval for a widening and then hand over to a second,
differently worded approval in the trusted terminal. Only the second one gated anything, so the
first taught users that clicking "yes" in an untrusted surface is what changes privacy. These
tests pin the replacement: one recommendation, a plain selection, and a handoff — with the exact
``before → after`` diff and the actual authorization living in the trusted terminal ceremony.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from yoetz.tui.app import YoetzTui
from yoetz.tui.models import PrivacyPosture, PrivacyRecommendation
from yoetz.tui.symbols import Level
from yoetz.tui.widgets.views import BaseView, SelectionView


class _Runtime:
    """Only the two privacy questions ``command_privacy`` asks."""

    def __init__(self, posture: PrivacyPosture, recommendation: PrivacyRecommendation) -> None:
        self._posture = posture
        self._recommendation = recommendation

    async def privacy_posture(self) -> PrivacyPosture:
        return self._posture

    def privacy_recommendation(self) -> PrivacyRecommendation:
        return self._recommendation

    def project_root(self) -> str:
        return "/srv/yoetz"


class _Harness:
    """A ``YoetzTui`` with the mounted-widget surfaces replaced by recording stubs."""

    def __init__(self, posture: PrivacyPosture, recommendation: PrivacyRecommendation) -> None:
        runtime = _Runtime(posture, recommendation)
        self.app = YoetzTui(runtime)  # pyright: ignore[reportArgumentType]
        self.said: list[tuple[Level, str, tuple[str, ...]]] = []
        self.views: list[SelectionView] = []
        self.answers: list[str | None] = []
        self.handed: list[str] = []

        def say(
            level: Level,
            title: str,
            body: Sequence[str] = (),
            *,
            details: Sequence[str] = (),
        ) -> None:
            del details
            self.said.append((level, title, tuple(body)))

        async def ask(view: BaseView) -> str | None:
            assert type(view) is SelectionView
            self.views.append(view)
            return self.answers.pop(0)

        async def hand_over(recipe: str) -> None:
            self.handed.append(recipe)

        self.app.say = say  # pyright: ignore[reportAttributeAccessIssue]
        self.app.ask = ask  # pyright: ignore[reportAttributeAccessIssue]
        self.app._hand_privacy_to_trusted_terminal = hand_over  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]

    def choices(self, index: int) -> list[str]:
        return [option.key for option in self.views[index].options]

    @property
    def transcript(self) -> str:
        return "\n".join(f"{title}\n" + "\n".join(body) for _level, title, body in self.said)


def _posture(profile: str | None = "minimal_external") -> PrivacyPosture:
    return PrivacyPosture(profile=profile, llm_inference_enabled=True, readable=True)


def _recommendation(recipe: str = "metadata_only") -> PrivacyRecommendation:
    return PrivacyRecommendation(recipe, "Because it discloses the least.", "It costs detail.")


@pytest.mark.anyio
async def test_privacy_leads_with_the_current_posture_and_the_recommendation() -> None:
    harness = _Harness(_posture(), _recommendation())
    harness.answers = [None]

    await harness.app.command_privacy()

    assert "Currently: minimal external review" in harness.transcript
    assert "Recommended: Metadata only" in harness.transcript
    assert "Because it discloses the least." in harness.transcript
    # A recommendation without its cost is advice, not a choice.
    assert "It costs detail." in harness.transcript
    assert harness.choices(0) == ["keep", "recommended", "other"]


@pytest.mark.anyio
async def test_no_configured_provider_recommends_private() -> None:
    harness = _Harness(_posture("local_only"), _recommendation("private"))
    harness.answers = [None]

    await harness.app.command_privacy()

    assert "Recommended: Private" in harness.transcript


@pytest.mark.anyio
async def test_selecting_the_recommendation_hands_it_straight_to_the_trusted_terminal() -> None:
    harness = _Harness(_posture(), _recommendation())
    harness.answers = ["recommended"]

    await harness.app.command_privacy()

    # Exactly one selection view and no approval view: the interface never takes a consent.
    assert len(harness.views) == 1
    assert harness.handed == ["metadata_only"]


@pytest.mark.anyio
async def test_a_policy_already_matching_the_recommendation_is_not_offered_as_a_change() -> None:
    harness = _Harness(_posture("confirm_every_request"), _recommendation())
    harness.answers = [None]

    await harness.app.command_privacy()

    assert "already on the recommended privacy policy" in harness.transcript
    assert harness.choices(0) == ["keep", "other"]


@pytest.mark.anyio
@pytest.mark.parametrize("answer", [None, "keep"])
async def test_cancelling_or_keeping_changes_nothing(answer: str | None) -> None:
    harness = _Harness(_posture(), _recommendation())
    harness.answers = [answer]

    await harness.app.command_privacy()

    assert harness.handed == []
    assert "Privacy was left unchanged." in harness.transcript


@pytest.mark.anyio
async def test_other_options_offer_the_command_line_recipe_names() -> None:
    harness = _Harness(_posture(), _recommendation())
    harness.answers = ["other", "expanded_review"]

    await harness.app.command_privacy()

    assert harness.choices(1) == [
        "private",
        "metadata_only",
        "assisted_review",
        "expanded_review",
        "custom",
    ]
    assert harness.handed == ["expanded_review"]


@pytest.mark.anyio
async def test_backing_out_of_the_other_options_list_changes_nothing() -> None:
    harness = _Harness(_posture(), _recommendation())
    harness.answers = ["other", None]

    await harness.app.command_privacy()

    assert harness.handed == []
    assert "Privacy was left unchanged." in harness.transcript
