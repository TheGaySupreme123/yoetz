"""An open session's bridge keeps one release's code after an in-place upgrade (#820).

``yoetz upgrade --accept`` replaces the package while sessions keep running. A module the bridge
first imports after that would be the next release's code, so every module the bridge imports
inside a function must already be loaded by the time it serves.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yoetz.mcp.server as bridge
import yoetz.service.client as client

_LAZY_IMPORT = re.compile(
    r"^[ \t]+(?:from ((?:yoetz|packaging)[\w.]*) import|import ((?:yoetz|packaging)[\w.]*))",
    re.MULTILINE,
)

_PROBE = r"""
import json, sys
import yoetz.mcp.server as bridge
bridge._load_bridge_code()
print(json.dumps(sorted(sys.modules)))
"""


def _lazy_imports(*modules: object) -> set[str]:
    names: set[str] = set()
    for module in modules:
        source = Path(getattr(module, "__file__")).read_text(encoding="utf-8")
        names.update(first or second for first, second in _LAZY_IMPORT.findall(source))
    return names


def test_every_lazy_bridge_import_is_loaded_before_serving() -> None:
    # A fresh interpreter: this test process has already imported most of the package.
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and in-repo probe
        (sys.executable, "-c", _PROBE),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4_096:]
    loaded = set(json.loads(completed.stdout))
    missing = sorted(_lazy_imports(bridge, client) - loaded)
    assert missing == [], f"add these to _BRIDGE_LAZY_MODULES: {missing}"
