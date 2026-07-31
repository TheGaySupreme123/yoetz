"""Portable verified foreground-console boundary for confidential human input."""

from __future__ import annotations

import ctypes
import hmac
import os
import sys
from types import TracebackType
from typing import Final, Protocol, Self, cast

__all__ = ["TrustedConsoleError", "TrustedForegroundConsole"]

_TTY_PATH: Final = "/dev/tty"
_CHOICE_MAX_BYTES: Final = 32
_TRUST_FAILURE: Final = "trusted_console_required"
_ERROR_REASONS: Final = frozenset(
    {
        _TRUST_FAILURE,
        "cancelled",
        "input_invalid",
        "interrupted",
    }
)


class TrustedConsoleError(Exception):
    """Bounded console failure that never reflects input or operating-system text."""

    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        if type(reason) is not str or reason not in _ERROR_REASONS:
            raise ValueError("trusted_console_reason_invalid")
        self.reason = reason
        super().__init__(reason)


def _overwrite(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0


class _ConsoleAdapter(Protocol):
    def open(self) -> None: ...

    def close(self) -> None: ...

    def write(self, value: str) -> None: ...

    def read_line(self, prompt: str, maximum: int, *, hidden: bool) -> bytearray: ...


class _PosixConsoleAdapter:
    """`/dev/tty` adapter with matching-terminal and foreground-pgrp checks."""

    __slots__ = ("_fd",)

    def __init__(self) -> None:
        self._fd = -1

    @property
    def fd(self) -> int:
        if self._fd < 0:
            raise TrustedConsoleError(_TRUST_FAILURE)
        return self._fd

    def open(self) -> None:
        if not os.isatty(0) or not os.isatty(2):
            raise TrustedConsoleError(_TRUST_FAILURE)
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(_TTY_PATH, flags)
        except OSError as exc:
            raise TrustedConsoleError(_TRUST_FAILURE) from exc
        try:
            if not os.isatty(fd):
                raise TrustedConsoleError(_TRUST_FAILURE)
            if os.fstat(0).st_rdev != os.fstat(2).st_rdev:
                raise TrustedConsoleError(_TRUST_FAILURE)
            if os.tcgetpgrp(fd) != os.getpgrp():
                raise TrustedConsoleError(_TRUST_FAILURE)
        except TrustedConsoleError:
            os.close(fd)
            raise
        except Exception as exc:
            os.close(fd)
            raise TrustedConsoleError(_TRUST_FAILURE) from exc
        self._fd = fd

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def write(self, value: str) -> None:
        if type(value) is not str:
            raise TypeError("console_text_invalid")
        encoded = value.encode("utf-8", errors="strict")
        position = 0
        while position < len(encoded):
            try:
                written = os.write(self.fd, encoded[position:])
            except OSError as exc:
                raise TrustedConsoleError("interrupted") from exc
            if written <= 0:
                raise TrustedConsoleError("interrupted")
            position += written

    def read_line(self, prompt: str, maximum: int, *, hidden: bool) -> bytearray:
        if type(maximum) is not int or maximum <= 0:
            raise TypeError("console_bound_invalid")
        try:
            import termios
        except ImportError as exc:  # pragma: no cover - POSIX runtimes provide termios
            raise TrustedConsoleError(_TRUST_FAILURE) from exc
        original = None
        storage = bytearray(maximum + 1)
        used = 0
        try:
            if hidden:
                original = termios.tcgetattr(self.fd)
                changed = original.copy()
                changed[3] = cast(int, changed[3]) & ~termios.ECHO
                termios.tcsetattr(self.fd, termios.TCSADRAIN, changed)
            self.write(prompt)
            while used < len(storage):
                view = memoryview(storage)[used:]
                try:
                    count = os.readv(self.fd, [view])
                except KeyboardInterrupt as exc:
                    raise TrustedConsoleError("cancelled") from exc
                except InterruptedError as exc:
                    raise TrustedConsoleError("interrupted") from exc
                finally:
                    view.release()
                if count <= 0:
                    raise TrustedConsoleError("input_invalid")
                used += count
                if storage[used - 1] == 10:
                    break
            if used == 0 or storage[used - 1] != 10:
                raise TrustedConsoleError("input_invalid")
            if used == 1:
                raise TrustedConsoleError("input_invalid")
            storage[used - 1] = 0
            for index in range(used, len(storage)):
                storage[index] = 0
            del storage[used - 1 :]
            return storage
        except BaseException:
            _overwrite(storage)
            raise
        finally:
            if original is not None:
                try:
                    termios.tcsetattr(self.fd, termios.TCSADRAIN, original)
                finally:
                    self.write("\n")


class _WindowsConsoleApi(Protocol):
    def open_input(self) -> int: ...

    def open_output(self) -> int: ...

    def is_console(self, handle: int) -> bool: ...

    def standard_handles_are_console(self) -> bool: ...

    def attached_process_ids(self) -> tuple[int, ...]: ...

    def write(self, handle: int, value: str) -> None: ...

    def read_line(self, handle: int, maximum: int, *, hidden: bool) -> bytearray: ...

    def close(self, handle: int) -> None: ...


class _CtypesWindowsConsoleApi:
    """Narrow Win32 adapter; never opens or reads redirected standard handles."""

    __slots__ = ("_kernel32",)

    _GENERIC_READ: Final = 0x80000000
    _GENERIC_WRITE: Final = 0x40000000
    _FILE_SHARE_READ: Final = 0x00000001
    _FILE_SHARE_WRITE: Final = 0x00000002
    _OPEN_EXISTING: Final = 3
    _FILE_TYPE_CHAR: Final = 0x0002
    _ENABLE_ECHO_INPUT: Final = 0x0004
    _ENABLE_LINE_INPUT: Final = 0x0002
    _CP_UTF8: Final = 65001
    _WC_ERR_INVALID_CHARS: Final = 0x00000080
    _STD_INPUT_HANDLE: Final = -10
    _STD_ERROR_HANDLE: Final = -12
    _INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value

    def __init__(self) -> None:
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            raise TrustedConsoleError(_TRUST_FAILURE)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.CreateFileW.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        )
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.GetFileType.argtypes = (ctypes.c_void_p,)
        kernel32.GetFileType.restype = ctypes.c_uint32
        kernel32.GetConsoleMode.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
        kernel32.GetConsoleMode.restype = ctypes.c_int
        kernel32.SetConsoleMode.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.SetConsoleMode.restype = ctypes.c_int
        kernel32.GetConsoleProcessList.argtypes = (
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_uint32,
        )
        kernel32.GetConsoleProcessList.restype = ctypes.c_uint32
        kernel32.GetStdHandle.argtypes = (ctypes.c_int32,)
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        kernel32.WriteConsoleW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        )
        kernel32.WriteConsoleW.restype = ctypes.c_int
        kernel32.ReadConsoleW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        )
        kernel32.ReadConsoleW.restype = ctypes.c_int
        kernel32.WideCharToMultiByte.argtypes = (
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        kernel32.WideCharToMultiByte.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int
        self._kernel32 = kernel32

    def _open(self, name: str, access: int) -> int:
        handle = self._kernel32.CreateFileW(
            name,
            access,
            self._FILE_SHARE_READ | self._FILE_SHARE_WRITE,
            None,
            self._OPEN_EXISTING,
            0,
            None,
        )
        if handle in {0, self._INVALID_HANDLE_VALUE}:
            raise TrustedConsoleError(_TRUST_FAILURE)
        return int(handle)

    def open_input(self) -> int:
        return self._open("CONIN$", self._GENERIC_READ | self._GENERIC_WRITE)

    def open_output(self) -> int:
        return self._open("CONOUT$", self._GENERIC_READ | self._GENERIC_WRITE)

    def is_console(self, handle: int) -> bool:
        if handle in {0, self._INVALID_HANDLE_VALUE}:
            return False
        mode = ctypes.c_uint32()
        return bool(
            self._kernel32.GetFileType(handle) == self._FILE_TYPE_CHAR
            and self._kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        )

    def standard_handles_are_console(self) -> bool:
        for identifier in (self._STD_INPUT_HANDLE, self._STD_ERROR_HANDLE):
            raw = self._kernel32.GetStdHandle(identifier)
            if raw is None or not self.is_console(int(raw)):
                return False
        return True

    def attached_process_ids(self) -> tuple[int, ...]:
        size = 64
        while size <= 4096:
            values = (ctypes.c_uint32 * size)()
            count = int(self._kernel32.GetConsoleProcessList(values, size))
            if count == 0:
                raise TrustedConsoleError(_TRUST_FAILURE)
            if count <= size:
                return tuple(int(values[index]) for index in range(count))
            size = count
        raise TrustedConsoleError(_TRUST_FAILURE)

    def write(self, handle: int, value: str) -> None:
        written = ctypes.c_uint32()
        if not self._kernel32.WriteConsoleW(
            handle,
            value,
            len(value),
            ctypes.byref(written),
            None,
        ) or int(written.value) != len(value):
            raise TrustedConsoleError("interrupted")

    def read_line(self, handle: int, maximum: int, *, hidden: bool) -> bytearray:
        original = ctypes.c_uint32()
        if not self._kernel32.GetConsoleMode(handle, ctypes.byref(original)):
            raise TrustedConsoleError(_TRUST_FAILURE)
        changed = int(original.value) | self._ENABLE_LINE_INPUT
        if hidden:
            changed &= ~self._ENABLE_ECHO_INPUT
        if not self._kernel32.SetConsoleMode(handle, changed):
            raise TrustedConsoleError(_TRUST_FAILURE)
        buffer = ctypes.create_unicode_buffer(maximum + 2)
        read = ctypes.c_uint32()
        try:
            if not self._kernel32.ReadConsoleW(
                handle,
                buffer,
                maximum + 1,
                ctypes.byref(read),
                None,
            ):
                raise TrustedConsoleError("input_invalid")
            used = int(read.value)
            while used > 0 and buffer[used - 1] in {"\r", "\n"}:
                used -= 1
            if used <= 0:
                raise TrustedConsoleError("input_invalid")
            encoded_size = int(
                self._kernel32.WideCharToMultiByte(
                    self._CP_UTF8,
                    self._WC_ERR_INVALID_CHARS,
                    buffer,
                    used,
                    None,
                    0,
                    None,
                    None,
                )
            )
            if encoded_size <= 0 or encoded_size > maximum:
                raise TrustedConsoleError("input_invalid")
            encoded = bytearray(encoded_size)
            destination = (ctypes.c_char * encoded_size).from_buffer(encoded)
            converted = int(
                self._kernel32.WideCharToMultiByte(
                    self._CP_UTF8,
                    self._WC_ERR_INVALID_CHARS,
                    buffer,
                    used,
                    destination,
                    encoded_size,
                    None,
                    None,
                )
            )
            if converted != encoded_size:
                _overwrite(encoded)
                raise TrustedConsoleError("input_invalid")
            return encoded
        finally:
            ctypes.memset(ctypes.addressof(buffer), 0, ctypes.sizeof(buffer))
            if (
                not self._kernel32.SetConsoleMode(handle, int(original.value))
                and sys.exc_info()[0] is None
            ):
                raise TrustedConsoleError("interrupted")

    def close(self, handle: int) -> None:
        self._kernel32.CloseHandle(handle)


class _WindowsConsoleAdapter:
    """`CONIN$`/`CONOUT$` adapter with real-console and attachment checks."""

    __slots__ = ("_api", "_input", "_output")

    def __init__(self, api: _WindowsConsoleApi | None = None) -> None:
        self._api = _CtypesWindowsConsoleApi() if api is None else api
        self._input = 0
        self._output = 0

    def open(self) -> None:
        input_handle = 0
        output_handle = 0
        try:
            if not self._api.standard_handles_are_console():
                raise TrustedConsoleError(_TRUST_FAILURE)
            input_handle = self._api.open_input()
            output_handle = self._api.open_output()
            if not self._api.is_console(input_handle) or not self._api.is_console(output_handle):
                raise TrustedConsoleError(_TRUST_FAILURE)
            if os.getpid() not in self._api.attached_process_ids():
                raise TrustedConsoleError(_TRUST_FAILURE)
        except TrustedConsoleError:
            if output_handle:
                self._api.close(output_handle)
            if input_handle:
                self._api.close(input_handle)
            raise
        except Exception as exc:
            if output_handle:
                self._api.close(output_handle)
            if input_handle:
                self._api.close(input_handle)
            raise TrustedConsoleError(_TRUST_FAILURE) from exc
        self._input = input_handle
        self._output = output_handle

    def close(self) -> None:
        if self._output:
            self._api.close(self._output)
            self._output = 0
        if self._input:
            self._api.close(self._input)
            self._input = 0

    def write(self, value: str) -> None:
        if type(value) is not str:
            raise TypeError("console_text_invalid")
        if not self._output:
            raise TrustedConsoleError(_TRUST_FAILURE)
        self._api.write(self._output, value)

    def read_line(self, prompt: str, maximum: int, *, hidden: bool) -> bytearray:
        if type(maximum) is not int or maximum <= 0:
            raise TypeError("console_bound_invalid")
        if not self._input or not self._output:
            raise TrustedConsoleError(_TRUST_FAILURE)
        self.write(prompt)
        encoded = self._api.read_line(self._input, maximum, hidden=hidden)
        if hidden:
            self.write("\n")
        if not encoded or len(encoded) > maximum:
            _overwrite(encoded)
            raise TrustedConsoleError("input_invalid")
        return encoded


def _new_adapter() -> _ConsoleAdapter:
    if os.name == "nt" or sys.platform == "win32":
        return _WindowsConsoleAdapter()
    return _PosixConsoleAdapter()


class TrustedForegroundConsole:
    """Verified foreground console that never falls back to redirected stdio."""

    __slots__ = ("_adapter",)

    def __init__(self) -> None:
        self._adapter = _new_adapter()

    def __enter__(self) -> Self:
        self._adapter.open()
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self._adapter.close()

    def write(self, value: str) -> None:
        self._adapter.write(value)

    def read_choice(self, prompt: str, allowed: tuple[bytes, ...]) -> bytes:
        if (
            type(allowed) is not tuple
            or not allowed
            or not all(type(item) is bytes for item in allowed)
        ):
            raise TypeError("console_choices_invalid")
        value = self._adapter.read_line(prompt, _CHOICE_MAX_BYTES, hidden=False)
        try:
            for candidate in allowed:
                if hmac.compare_digest(value, candidate):
                    return candidate
            raise TrustedConsoleError("input_invalid")
        finally:
            _overwrite(value)

    def read_secret(self, prompt: str, maximum: int) -> bytearray:
        return self._adapter.read_line(prompt, maximum, hidden=True)
