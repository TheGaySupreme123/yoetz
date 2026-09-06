"""Revocation fences for current project advisory notes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import cast

import pytest

import yoetz.application.check as check

_PROJECT = "prj_00000000-0000-4000-8000-000000000001"
_REQUESTER = "tsk_00000000-0000-4000-8000-000000000001"
_COUNTERPART = "tsk_00000000-0000-4000-8000-000000000002"


@pytest.mark.anyio
@pytest.mark.parametrize("authority_change", ("revoke", "new_generation_same_members"))
async def test_advisory_notes_suppress_after_duplicate_reads_are_revoked(
    monkeypatch: pytest.MonkeyPatch,
    authority_change: str,
) -> None:
    class Catalog:
        generation = 1

        async def project_state(self, project: str) -> object:
            assert project == _PROJECT
            return SimpleNamespace(membership_generation=self.generation)

        async def list_task_project_ids(self, task_id: str) -> tuple[str, ...]:
            assert task_id == _REQUESTER
            return (_PROJECT,)

    class ProjectApplication:
        def __init__(self) -> None:
            self.catalog = Catalog()
            self.live_reads = 0
            self.revoked = False

        async def live_admitted_member_task_ids(
            self, task_id: str, *, project: str, expected_generation: int
        ) -> tuple[str, ...]:
            assert task_id == _REQUESTER
            assert project == _PROJECT
            self.live_reads += 1
            if expected_generation != self.catalog.generation:
                raise ValueError("coordination_generation_revoked")
            return () if self.revoked else (_COUNTERPART,)

    project_application = ProjectApplication()
    app = SimpleNamespace(project_application=project_application)

    async def duplicate_after_revoke(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        # Model the awaited context/ledger reads in the real duplicate helper: authority is
        # revoked after the pre-duplicate fence but before the note is projected.
        if authority_change == "revoke":
            project_application.revoked = True
        else:
            # A newly granted generation can contain identical members. Their equality must
            # not authorize duplicate finding facts read under the earlier generation.
            project_application.catalog.generation += 1
        return (_REQUESTER, _COUNTERPART)

    monkeypatch.setattr(check, "_duplicate_project_advisory_task_ids", duplicate_after_revoke)

    notes_reader = cast(
        Callable[[object, str, tuple[object, ...]], Awaitable[tuple[object, ...]]],
        getattr(check, "_current_project_advisory_notes"),
    )
    notes = await notes_reader(app, _REQUESTER, ())

    assert notes == ()
    assert project_application.live_reads == 3
