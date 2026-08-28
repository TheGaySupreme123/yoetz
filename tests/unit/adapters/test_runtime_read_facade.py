"""Read-route runtime facades must cover what read-routed code calls.

``status(view=operation)`` failed for every caller, every time: it calls ``lookup_task_operation``, but
a read route hands the application ``_ReadLedger``, a ``__slots__`` facade that exposed only a
subset of the port and not that one, so the call raised ``AttributeError`` before reaching data — and
the recovery view was unavailable exactly when a caller needed it.

Every status test passed throughout, because they drive the raw ledger directly and never cross
the routing facade. Two surfaces that have to agree, and nothing compared them. This does.
"""

from __future__ import annotations

import ast
import pathlib

from yoetz.adapters import runtime as runtime_module
from yoetz.application import status as status_module

# ``execute_status`` is the read-routed entry point: it routes with ``RouteAccess.PAYLOAD_READ``
# and therefore receives the facades below rather than the real ports. Write and import-review
# routes get the real objects, so their mutator calls are legitimately absent from the facades.
assert status_module.__file__ is not None
_READ_ROUTED_MODULE = pathlib.Path(status_module.__file__)

_FACADES = {
    "ledger": runtime_module._ReadLedger,  # pyright: ignore[reportPrivateUsage]
    "objects": runtime_module._PayloadObjects,  # pyright: ignore[reportPrivateUsage]
    "importer": runtime_module._StatusImporter,  # pyright: ignore[reportPrivateUsage]
}


def _calls_on_runtime_attributes(source: pathlib.Path) -> dict[str, set[str]]:
    """Collect direct ``runtime.<port>.<method>(...)`` calls, not local port aliases."""

    found: dict[str, set[str]] = {name: set() for name in _FACADES}
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        owner = func.value
        if (
            isinstance(owner, ast.Attribute)
            and owner.attr in found
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "runtime"
        ):
            found[owner.attr].add(func.attr)
    return found


def test_read_facades_expose_every_port_method_the_read_view_calls() -> None:
    """Anything ``execute_status`` calls on a routed port must exist on that port's facade."""

    called = _calls_on_runtime_attributes(_READ_ROUTED_MODULE)
    assert called["ledger"], "no runtime.ledger calls found; the scan is broken, not the facade"

    missing: dict[str, list[str]] = {}
    for attribute, facade in _FACADES.items():
        exposed = {name for name in dir(facade) if not name.startswith("_")}
        absent = sorted(called[attribute] - exposed)
        if absent:
            missing[attribute] = absent
    assert not missing, (
        f"read-routed status calls methods the facade does not expose: {missing}. "
        "A read route receives the facade, so this raises AttributeError in production while "
        "tests that use the raw port keep passing. The scan detects direct runtime.<port> calls "
        "only; extend it before relying on aliases such as `ledger = runtime.ledger`."
    )


def test_read_ledger_can_look_up_an_operation() -> None:
    """Pin the specific method whose absence broke recovery, so it cannot silently vanish."""

    facade = runtime_module._ReadLedger  # pyright: ignore[reportPrivateUsage]
    assert callable(getattr(facade, "lookup_operation", None))
    assert callable(getattr(facade, "lookup_task_operation", None))
    assert callable(getattr(facade, "load_disclosure_wait", None))
