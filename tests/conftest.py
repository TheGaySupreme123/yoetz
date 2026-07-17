"""Shared deterministic pytest support for all Yoetz test suites."""

from __future__ import annotations

from types import ModuleType

import pytest

from builders import clock as clock_builder_module
from builders import events as event_builder_module
from builders import ids as id_builder_module
from builders import operations as operation_builder_module
from fixture_loader import FixtureLoader, build_fixture_loader


@pytest.fixture(scope="session")
def fixture_loader() -> FixtureLoader:
    """Expose the lazy read-only loader without touching Wave A resources at collection."""

    return build_fixture_loader()


@pytest.fixture(scope="session")
def id_builders() -> ModuleType:
    return id_builder_module


@pytest.fixture(scope="session")
def clock_builders() -> ModuleType:
    return clock_builder_module


@pytest.fixture(scope="session")
def event_builders() -> ModuleType:
    return event_builder_module


@pytest.fixture(scope="session")
def operation_builders() -> ModuleType:
    return operation_builder_module
