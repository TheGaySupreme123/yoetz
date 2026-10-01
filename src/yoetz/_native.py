"""Optional Rust accelerator loader.

``yoetz_native`` is a separately installed extension module built from the repository's
``rust/`` workspace. It is never a dependency: the wheel stays pure Python and every module keeps
its Python implementation. When the accelerator is importable and its interface revision matches
this package, pure modules bind their hot functions to its byte- and error-identical twins.

``YZ_NATIVE=0`` keeps the pure-Python implementations even when the accelerator is installed;
``YZ_NATIVE=require`` turns a missing or mismatched accelerator into an import error, so a parity
run cannot silently fall back. The variable deliberately sits outside the ``YOETZ_`` namespace,
which strict configuration loading reserves for configuration leaves.
"""

from __future__ import annotations

import os
from types import ModuleType
from typing import Any, Final

__all__ = ["INTERFACE_VERSION", "NATIVE_ENV", "native", "native_functions"]

INTERFACE_VERSION: Final = 1
NATIVE_ENV: Final = "YZ_NATIVE"


def _load() -> ModuleType | None:
    mode = os.environ.get(NATIVE_ENV, "")
    if mode == "0":
        return None
    try:
        from importlib import import_module

        module = import_module("yoetz_native")
    except ImportError:
        if mode == "require":
            raise
        return None
    if getattr(module, "INTERFACE_VERSION", None) != INTERFACE_VERSION:
        if mode == "require":
            raise ImportError("yoetz_native_interface_mismatch")
        return None
    from yoetz.protocol.errors import ProtocolValueError

    module.bind_protocol_value_error(ProtocolValueError)
    return module


native: Final[ModuleType | None] = _load()


def native_functions(*names: str) -> tuple[Any, ...] | None:
    """Return the named accelerator functions, or ``None`` unless every one is present.

    A module binds all of its twins or none of them, so an accelerator built from an older
    revision can only ever leave the pure-Python implementations in place.
    """

    if native is None:
        return None
    resolved = tuple(getattr(native, name, None) for name in names)
    if any(function is None for function in resolved):
        if os.environ.get(NATIVE_ENV, "") == "require":
            raise ImportError("yoetz_native_function_missing")
        return None
    return resolved
