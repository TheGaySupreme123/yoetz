"""Import-diet regression guards for the Codex hook entry path (#242).

A hook is always a fresh process, so its import graph is paid per tool call.
Before this bound, ``yoetz hooks observe`` loaded typer, pydantic, the protocol
schema catalog, and the whole ``yoetz.application`` package — ~325 ms of the
measured 1.67-2.50 s hook, none of which a hook consumes.

These probes spawn a clean interpreter and only import modules; nothing here
touches the user's state directory or the repository working tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, cast

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_PROBE_TIMEOUT: Final = 60
# Everything a hook must never pull in: the CLI framework, the wire model and
# schema catalog, the service control client, and the service-internal
# application package.
_FORBIDDEN: Final = (
    "pydantic",
    "jsonschema",
    "typer",
    "anyio",
    "yoetz.protocol.models",
    "yoetz.protocol.schemas",
    "yoetz.service.client",
    "yoetz.cli.app",
    "yoetz.cli.render",
    "yoetz.application.publish_work",
    "yoetz.application.unit_of_work",
    "yoetz.ports.ledger",
    "yoetz.domain.events",
    "yoetz.application.recommendations",
    "yoetz.application.package_update",
)
# Drift ceiling: the measured set after the diet is well under this, so a
# future eager import shows up here even if it is not on the named list.
_MAX_MODULES: Final = 400


def _probe(source: str) -> dict[str, object]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONSTARTUP", None)
    seeded_source = f"import sys; sys.path.insert(0, {os.fspath(_REPO_ROOT / 'src')!r})\n" + source
    completed = subprocess.run(  # noqa: S603 - fixed in-repository interpreter and source
        [sys.executable, "-I", "-c", seeded_source],
        capture_output=True,
        env=environment,
        check=False,
        timeout=_PROBE_TIMEOUT,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    parsed: dict[str, object] = json.loads(completed.stdout.decode("utf-8"))
    return parsed


_REPORT: Final = (
    "import json, sys\n"
    "{statement}\n"
    "print(json.dumps({{'modules': sorted(sys.modules), 'count': len(sys.modules)}}))\n"
)


def _modules(statement: str) -> list[str]:
    report = _probe(_REPORT.format(statement=statement))
    return [str(name) for name in cast(Sequence[object], report["modules"])]


def test_hook_entry_does_not_import_named_heavy_modules() -> None:
    loaded = set(_modules("from yoetz.cli import entry"))
    offenders = sorted(loaded & set(_FORBIDDEN))
    assert not offenders, (
        "the console entry shim must reach the observe hook without the full CLI "
        f"import graph; it loaded {offenders}"
    )


def test_observe_hooks_module_import_set_is_bounded() -> None:
    loaded = _modules("import yoetz.cli.observe_hooks")
    offenders = sorted(set(loaded) & set(_FORBIDDEN))
    assert not offenders, f"yoetz.cli.observe_hooks eagerly imported {offenders}"
    assert len(loaded) < _MAX_MODULES, (
        f"the hook import graph grew to {len(loaded)} modules; every one of them "
        "is paid on every tool call"
    )


def test_yoetz_application_package_import_is_lazy() -> None:
    eager = _modules("import yoetz.application")
    assert "yoetz.application.publish_work" not in eager, (
        "importing the application package must not drag publish_work -> "
        "unit_of_work -> ports.ledger -> domain.events -> protocol.models"
    )
    resolved = _modules("from yoetz.application import Application")
    assert "yoetz.application.service" in resolved, (
        "the lazy re-export must still resolve the documented public names"
    )
