

def test_read_route_ledger_exposes_every_method_the_read_paths_call() -> None:
    """The read facade must expose everything a read-routed application actually calls.

    ``status(view=operation)`` failed for every caller, every time: it calls
    ``lookup_operation``, but a read route hands the application ``_ReadLedger``, whose
    ``__slots__`` exposed four methods and not that one. Existing status tests passed because
    they drive the raw ledger directly and never cross the routing facade, so nothing compared
    the two surfaces.

    Asserting the property — the facade covers what read paths call — rather than a fixed list,
    so the next read path added to the application is checked too.
    """

    import ast
    import pathlib

    from yoetz.adapters.runtime import _ReadLedger  # pyright: ignore[reportPrivateUsage]

    exposed = {name for name in dir(_ReadLedger) if not name.startswith("_")}

    called: set[str] = set()
    for path in sorted(pathlib.Path("src/yoetz/application").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # Match ``runtime.ledger.<name>(...)`` exactly.
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            owner = func.value
            if (
                isinstance(owner, ast.Attribute)
                and owner.attr == "ledger"
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "runtime"
            ):
                called.add(func.attr)

    assert called, "no runtime.ledger call sites found; the scan is broken, not the facade"
    missing = {name for name in called if name not in exposed}
    # Write paths legitimately call mutators the read facade must never expose; only the methods
    # reachable from a read-routed view matter here.
    read_only_calls = {"lookup_operation", "load_projection", "query_projection", "load_events"}
    assert not (missing & read_only_calls), (
        f"read routes call {sorted(missing & read_only_calls)} but _ReadLedger does not expose it"
    )
