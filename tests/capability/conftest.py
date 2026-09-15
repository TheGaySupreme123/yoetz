"""Capability children need private roots outside shared temporary directories."""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    # Short socket paths, no symlink ancestors, no inherited test-instance ownership.
    with TemporaryDirectory(prefix=".yz-cap-", dir=Path.home().resolve()) as directory:
        yield Path(directory)
