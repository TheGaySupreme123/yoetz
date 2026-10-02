//! OS hardening primitives for `yoetz.adapters.keys.secret_memory`.
//!
//! The Python module keeps the anonymous `mmap` allocation, the handle lifecycle, and the
//! `memoryview` contract. These twins replace only the `ctypes` system calls it makes on a
//! mapping (`mlock`, `munlock`, `madvise(MADV_DONTDUMP)`), the zero overwrite, and the
//! `RLIMIT_CORE` suppression. Each one reports the same outcome its reference reports: failures
//! the reference swallows (`BufferError`, `ValueError`, `OSError`) are swallowed here, and any
//! other failure propagates.

use std::os::raw::c_void;
use std::sync::atomic::{Ordering, compiler_fence};

use pyo3::exceptions::{PyBufferError, PyOSError, PyValueError};
use pyo3::ffi;
use pyo3::intern;
use pyo3::prelude::*;

/// A writable buffer export held for the duration of one system call.
struct Exported {
    view: ffi::Py_buffer,
}

impl Exported {
    fn address(&self) -> *mut c_void {
        self.view.buf
    }

    fn len(&self) -> usize {
        usize::try_from(self.view.len).unwrap_or(0)
    }
}

impl Drop for Exported {
    fn drop(&mut self) {
        unsafe { ffi::PyBuffer_Release(&mut self.view) };
    }
}

enum Export {
    Held(Exported),
    /// The export failed with an error the reference swallows (`BufferError`, `ValueError`,
    /// for example a closed mapping).
    Refused,
    /// A read-only export: the reference raises its own `TypeError`, so it must decide.
    ReadOnly,
}

/// Acquire an export the way `ctypes.c_char.from_buffer` does (`PyBUF_SIMPLE`, then a
/// writability check).
fn export_writable(py: Python<'_>, mapping: &Bound<'_, PyAny>) -> PyResult<Export> {
    let mut view = std::mem::MaybeUninit::<ffi::Py_buffer>::uninit();
    let status =
        unsafe { ffi::PyObject_GetBuffer(mapping.as_ptr(), view.as_mut_ptr(), ffi::PyBUF_SIMPLE) };
    if status != 0 {
        let error = PyErr::fetch(py);
        if error.is_instance_of::<PyBufferError>(py) || error.is_instance_of::<PyValueError>(py) {
            return Ok(Export::Refused);
        }
        return Err(error);
    }
    let exported = Exported {
        view: unsafe { view.assume_init() },
    };
    if exported.view.readonly != 0 {
        return Ok(Export::ReadOnly);
    }
    Ok(Export::Held(exported))
}

/// `_lock_mapping` with a live libc: `mlock(address, size) == 0`; `None` when the reference
/// must decide (a read-only mapping).
#[pyfunction]
pub fn secret_memory_lock(
    py: Python<'_>,
    mapping: &Bound<'_, PyAny>,
    size: usize,
) -> PyResult<Option<bool>> {
    Ok(match export_writable(py, mapping)? {
        Export::Held(buffer) => Some(unsafe { libc::mlock(buffer.address(), size) } == 0),
        Export::Refused => Some(false),
        Export::ReadOnly => None,
    })
}

/// `_unlock_mapping` with a live libc: `munlock(address, size)`, result ignored. `False` when
/// the reference must decide (a read-only mapping).
#[pyfunction]
pub fn secret_memory_unlock(
    py: Python<'_>,
    mapping: &Bound<'_, PyAny>,
    size: usize,
) -> PyResult<bool> {
    Ok(match export_writable(py, mapping)? {
        Export::Held(buffer) => {
            unsafe { libc::munlock(buffer.address(), size) };
            true
        }
        Export::Refused => true,
        Export::ReadOnly => false,
    })
}

/// `_exclude_from_core_dump` on Linux: `madvise(address, size, MADV_DONTDUMP)`, result
/// ignored; the reference passes the Linux constant 16 literally. `False` when the reference
/// must decide (a read-only mapping).
#[pyfunction]
pub fn secret_memory_dontdump(
    py: Python<'_>,
    mapping: &Bound<'_, PyAny>,
    size: usize,
) -> PyResult<bool> {
    Ok(match export_writable(py, mapping)? {
        Export::Held(buffer) => {
            #[cfg(target_os = "linux")]
            unsafe {
                libc::madvise(buffer.address(), size, 16);
            }
            #[cfg(not(target_os = "linux"))]
            let _ = (buffer, size);
            true
        }
        Export::Refused => true,
        Export::ReadOnly => false,
    })
}

/// `_overwrite_mapping` for a mapping of at least `size` bytes: zero the first `size` bytes
/// and leave the position at 0. Returns `False` when the reference must decide (a read-only
/// mapping, or `size` beyond the mapping, where the reference's chunked writes differ).
#[pyfunction]
pub fn secret_memory_zeroize(
    py: Python<'_>,
    mapping: &Bound<'_, PyAny>,
    size: usize,
) -> PyResult<bool> {
    match export_writable(py, mapping)? {
        Export::Held(buffer) => {
            if size > buffer.len() {
                return Ok(false);
            }
            unsafe { std::ptr::write_bytes(buffer.address().cast::<u8>(), 0, size) };
            // The overwrite must not be elided even though nothing reads the bytes back here.
            compiler_fence(Ordering::SeqCst);
        }
        // The reference's first `seek(0)` raises `ValueError` on a closed mapping (swallowed);
        // any other refusal is the reference's to judge.
        Export::Refused => return Ok(mapping_closed(mapping)),
        // Not writable: the reference's `write` raises `TypeError`; let it.
        Export::ReadOnly => return Ok(false),
    }
    if let Err(error) = mapping.call_method1(intern!(py, "seek"), (0,)) {
        if !(error.is_instance_of::<PyBufferError>(py)
            || error.is_instance_of::<PyOSError>(py)
            || error.is_instance_of::<PyValueError>(py))
        {
            return Err(error);
        }
    }
    Ok(true)
}

fn mapping_closed(mapping: &Bound<'_, PyAny>) -> bool {
    mapping
        .getattr("closed")
        .and_then(|closed| closed.is_truthy())
        .unwrap_or(false)
}

/// `_suppress_core_dumps`: lower the soft `RLIMIT_CORE` to 0, keeping the hard limit.
#[pyfunction]
pub fn secret_memory_suppress_core_dumps() -> bool {
    let mut limit = libc::rlimit {
        rlim_cur: 0,
        rlim_max: 0,
    };
    if unsafe { libc::getrlimit(libc::RLIMIT_CORE, &mut limit) } != 0 {
        return false;
    }
    let lowered = libc::rlimit {
        rlim_cur: 0,
        rlim_max: limit.rlim_max,
    };
    if unsafe { libc::setrlimit(libc::RLIMIT_CORE, &lowered) } != 0 {
        return false;
    }
    let mut observed = libc::rlimit {
        rlim_cur: 1,
        rlim_max: 0,
    };
    if unsafe { libc::getrlimit(libc::RLIMIT_CORE, &mut observed) } != 0 {
        return false;
    }
    observed.rlim_cur == 0
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(secret_memory_lock, module)?)?;
    module.add_function(wrap_pyfunction!(secret_memory_unlock, module)?)?;
    module.add_function(wrap_pyfunction!(secret_memory_dontdump, module)?)?;
    module.add_function(wrap_pyfunction!(secret_memory_zeroize, module)?)?;
    module.add_function(wrap_pyfunction!(secret_memory_suppress_core_dumps, module)?)?;
    Ok(())
}
