"""Python references for accelerator twins whose refusals are replayed.

A twin bound directly over a Python function raises from native code: its exception has no
frame in the owning module (diagnostics record the innermost ``yoetz`` frame as the origin) and
none of the reference's ``__cause__``/``__context__`` chain. A module that binds such a twin
therefore binds a thin wrapper instead: the accepted path stays native, and on any exception the
wrapper returns whatever the Python reference returns, or lets it raise its own refusal. The
reference is called after the ``except`` block, so the native exception never becomes its
implicit ``__context__``.

The reference must not re-enter the twins while it runs: each nested call would retry natively,
add a frame per level (moving where Python's recursion limit fires), and repeat work. It
therefore runs as a copy of the Python function whose module globals resolve every name still
bound to its wrapper to that name's reference copy. Every other global, including one a test
replaced, resolves live from the module, exactly as the reference would see it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import FunctionType
from typing import Any

__all__ = ["ReplayReferences", "adopt_identity", "reference_functions"]


_ABSENT: Any = object()


class _ReplayGlobals(dict[str, Any]):
    """Module globals for reference copies: wrapped names resolve to their references."""

    __slots__ = ("_bound", "_builtins", "_module", "_references", "_sizes")

    _bound: dict[str, object]
    _builtins: dict[str, Any]
    _module: dict[str, Any]
    _references: dict[str, Callable[..., Any]]
    _sizes: tuple[int, int]

    def __missing__(self, name: str) -> Any:
        # A name the last snapshot lacks (added since, or a concurrent refresh in progress)
        # resolves live.
        value = self._module.get(name, _ABSENT)
        if value is _ABSENT:
            # Answered here rather than by raising: a raise per builtin lookup is slow.
            value = self._builtins.get(name, _ABSENT)
            if value is _ABSENT:
                raise KeyError(name)
            return value
        reference = self._references.get(name)
        if reference is not None and value is self._bound[name]:
            return reference
        return value

    def refresh(self) -> None:
        """Snapshot the module's current globals, wrapped names mapped to their references.

        Builtins are copied in under the module's names (a module global shadows a builtin,
        as in a lookup), so no name the reference uses falls through to ``__missing__``.
        """

        module = self._module
        builtins = self._builtins
        sizes = (len(module), len(builtins))
        if sizes != self._sizes:
            # A name was added or removed since the last snapshot: rebuild it, so a removed
            # global is gone again. (Concurrent copies resolve live through ``__missing__``.)
            name = self["__name__"]
            self.clear()
            self["__name__"] = name
            self._sizes = sizes
        self.update(builtins)
        self.update(module)
        bound = self._bound
        for name, reference in self._references.items():
            if self.get(name, _ABSENT) is bound[name]:
                self[name] = reference


class ReplayReferences:
    """The replay copies of one module's references, by name.

    Looking a copy up snapshots the module's globals first, so a copy sees every global as the
    reference would at that moment (a replaced one included) without a per-lookup cost.
    """

    __slots__ = ("_copies", "_namespace")

    def __init__(self, namespace: _ReplayGlobals, copies: dict[str, Callable[..., Any]]) -> None:
        self._namespace = namespace
        self._copies = copies

    def __getitem__(self, name: str) -> Callable[..., Any]:
        self._namespace.refresh()
        return self._copies[name]


def reference_functions(
    module_globals: dict[str, Any],
    references: Mapping[str, Callable[..., Any]],
) -> ReplayReferences:
    """Return replay copies of *references* (name -> Python function) for one module.

    Call this after the module bound its wrappers: a name resolves to its reference copy only
    while the module still binds the wrapper it binds now.
    """

    namespace = _ReplayGlobals()
    # Read with ``dict.get`` (no ``__missing__``) by diagnostics and function creation.
    namespace["__name__"] = module_globals["__name__"]
    builtins = module_globals["__builtins__"]
    namespace["__builtins__"] = builtins
    namespace._builtins = builtins if type(builtins) is dict else vars(builtins)  # pyright: ignore[reportPrivateUsage]
    namespace._module = module_globals  # pyright: ignore[reportPrivateUsage]
    namespace._sizes = (-1, -1)  # pyright: ignore[reportPrivateUsage]
    namespace._bound = {name: module_globals[name] for name in references}  # pyright: ignore[reportPrivateUsage]
    copies: dict[str, Callable[..., Any]] = {}
    for name, function in references.items():
        if type(function) is not FunctionType:
            raise TypeError("replay_reference_not_function")
        copy = FunctionType(
            function.__code__,
            namespace,
            function.__name__,
            function.__defaults__,
            function.__closure__,
        )
        copy.__kwdefaults__ = function.__kwdefaults__
        copy.__qualname__ = function.__qualname__
        copy.__doc__ = function.__doc__
        copy.__type_params__ = function.__type_params__
        copies[name] = copy
    namespace._references = copies  # pyright: ignore[reportPrivateUsage]
    return ReplayReferences(namespace, copies)


def adopt_identity[F: Callable[..., Any]](wrapper: F, reference: Callable[..., Any]) -> F:
    """Give *wrapper* the reference's name, qualified name, and docstring."""

    wrapper.__name__ = reference.__name__
    wrapper.__qualname__ = reference.__qualname__
    wrapper.__doc__ = reference.__doc__
    return wrapper
