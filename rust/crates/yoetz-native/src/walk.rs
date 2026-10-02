//! Shared helpers for the native twins that walk live Python trees (`values`, `ids`, `models`,
//! `control_protocol`, and the service leaf walk).
//!
//! Each twin handles the exact built-in shapes natively and hands anything else back to its
//! Python reference, so a subclass, a custom `Mapping`, or a hostile object always takes the
//! reference's path.

use std::cell::Cell;

use pyo3::exceptions::PyRuntimeError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyDict, PyString, PyTuple};

use crate::registry::{Slot, protocol_error};

/// `yoetz.domain.values.JsonObject`, bound by `yoetz.domain.values`.
pub static JSON_OBJECT: Slot = Slot::new();

static MAPPING_ABC: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static SEQUENCE_ABC: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// Native recursion depth after which a walk hands the subtree to its Python reference, whose
/// own recursion limit then decides (no hostile nesting can exhaust the native stack).
///
/// The depth is cumulative per thread: a walker that runs Python code (the reference for one
/// node, a callback) publishes its depth through [`call_python`], and a native walker entered from
/// that Python code starts at [`entry_depth`]. Once a walk passes the limit, the reference it
/// hands the subtree to runs in *reference-only* mode ([`reference_only`]): every native walker
/// entered meanwhile defers to its own Python reference at once, so the reference recursion is
/// made of Python frames only and CPython's recursion guard decides, exactly as it does without
/// the accelerator.
pub const NATIVE_RECURSION_LIMIT: usize = 192;

thread_local! {
    /// The native walk depth suspended below the Python code now running on this thread.
    static SUSPENDED_DEPTH: Cell<usize> = const { Cell::new(0) };
}

/// Restores the suspended depth when the Python call it guards returns or unwinds.
struct Suspended(usize);

impl Drop for Suspended {
    fn drop(&mut self) {
        SUSPENDED_DEPTH.with(|cell| cell.set(self.0));
    }
}

/// The depth a native walker entered on this thread starts from: the depth of the native walks
/// suspended below it in Python code (`0` when there is none).
#[inline]
pub fn entry_depth() -> usize {
    SUSPENDED_DEPTH.with(Cell::get)
}

/// Whether a native walker entered now must defer to its Python reference at once: it runs
/// inside a reference that a walk handed a subtree past `NATIVE_RECURSION_LIMIT`.
#[inline]
pub fn reference_only() -> bool {
    entry_depth() >= NATIVE_RECURSION_LIMIT
}

/// Run Python code (the reference for one node, a callback) from a native walk `depth` levels
/// deep: a native walker entered from inside it starts at `depth`, and at or past
/// `NATIVE_RECURSION_LIMIT` it is reference-only.
#[inline]
pub fn call_python<T>(depth: usize, call: impl FnOnce() -> T) -> T {
    let outer = SUSPENDED_DEPTH.with(|cell| cell.replace(depth.max(cell.get())));
    let _restore = Suspended(outer);
    call()
}

/// Run the Python reference for a subtree a walk does not descend into because it passed
/// `NATIVE_RECURSION_LIMIT`, in reference-only mode.
#[inline]
pub fn defer_deep<T>(call: impl FnOnce() -> T) -> T {
    call_python(NATIVE_RECURSION_LIMIT, call)
}

/// Live iteration over an exact `dict` while Python code may run between steps.
///
/// PyO3's dict iterator panics when the dict changes size during iteration, and its
/// `PanicException` is a `BaseException`. This cursor visits the entries in the order CPython's
/// `dict.items()` iterator does (`PyDict_Next` over the entry table) and raises what that iterator
/// raises on the first step after a mutation: `RuntimeError('dictionary changed size during
/// iteration')`, or `RuntimeError('dictionary keys changed during iteration')` when the size is
/// unchanged but more entries turn up than the dict held at the start.
pub struct DictItems<'py> {
    dict: Bound<'py, PyDict>,
    position: ffi::Py_ssize_t,
    used: ffi::Py_ssize_t,
    remaining: ffi::Py_ssize_t,
}

impl<'py> DictItems<'py> {
    pub fn new(dict: &Bound<'py, PyDict>) -> Self {
        let used = unsafe { ffi::PyDict_Size(dict.as_ptr()) };
        DictItems {
            dict: dict.clone(),
            position: 0,
            used,
            remaining: used,
        }
    }

    /// The cursor over `value`, which the caller has checked is an exact `dict`.
    pub fn of(value: &Bound<'py, PyAny>) -> Self {
        debug_assert!(is_exact(value, ffi::PyDict_Check));
        Self::new(unsafe { value.cast_unchecked::<PyDict>() })
    }

    /// The next `(key, value)` pair, `None` once exhausted, or CPython's `RuntimeError` after a
    /// mutation.
    pub fn next_item(&mut self) -> PyResult<Option<(Bound<'py, PyAny>, Bound<'py, PyAny>)>> {
        let dict = self.dict.as_ptr();
        if unsafe { ffi::PyDict_Size(dict) } != self.used {
            // Sticky, like CPython's iterator.
            self.used = -1;
            return Err(PyRuntimeError::new_err(
                "dictionary changed size during iteration",
            ));
        }
        let mut key: *mut ffi::PyObject = std::ptr::null_mut();
        let mut value: *mut ffi::PyObject = std::ptr::null_mut();
        if unsafe { ffi::PyDict_Next(dict, &mut self.position, &mut key, &mut value) } == 0 {
            return Ok(None);
        }
        if self.remaining == 0 {
            self.used = -1;
            return Err(PyRuntimeError::new_err(
                "dictionary keys changed during iteration",
            ));
        }
        self.remaining -= 1;
        let py = self.dict.py();
        // SAFETY: `PyDict_Next` returned borrowed references to live entries; both become owned
        // references before any Python code can run.
        unsafe {
            Ok(Some((
                Bound::from_borrowed_ptr(py, key),
                Bound::from_borrowed_ptr(py, value),
            )))
        }
    }
}

#[inline]
pub fn is_exact(value: &Bound<'_, PyAny>, check: unsafe fn(*mut ffi::PyObject) -> i32) -> bool {
    unsafe { check(value.as_ptr()) != 0 }
}

#[inline]
pub fn is_type(value: &Bound<'_, PyAny>, class: &Bound<'_, PyAny>) -> bool {
    value.get_type().as_ptr() == class.as_ptr()
}

/// An exact `str`, `int`, `bool`, `float`, or `None`: no ABC can claim these.
#[inline]
pub fn is_plain_scalar(value: &Bound<'_, PyAny>) -> bool {
    value.is_none()
        || is_exact(value, ffi::PyUnicode_CheckExact)
        || is_exact(value, ffi::PyLong_CheckExact)
        || is_exact(value, ffi::PyBool_Check)
        || is_exact(value, ffi::PyFloat_CheckExact)
}

fn abc<'py>(
    py: Python<'py>,
    cell: &'static PyOnceLock<Py<PyAny>>,
    name: &str,
) -> PyResult<&'py Bound<'py, PyAny>> {
    cell.get_or_try_init(py, || -> PyResult<Py<PyAny>> {
        Ok(py.import("collections.abc")?.getattr(name)?.unbind())
    })
    .map(|value| value.bind(py))
}

pub fn mapping_abc(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    abc(py, &MAPPING_ABC, "Mapping")
}

pub fn sequence_abc(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    abc(py, &SEQUENCE_ABC, "Sequence")
}

/// `isinstance(value, collections.abc.Mapping)`.
pub fn is_mapping_instance(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<bool> {
    if is_exact(value, ffi::PyDict_CheckExact) {
        return Ok(true);
    }
    if is_plain_scalar(value)
        || is_exact(value, ffi::PyList_CheckExact)
        || is_exact(value, ffi::PyTuple_CheckExact)
    {
        return Ok(false);
    }
    value.is_instance(mapping_abc(py)?)
}

/// `isinstance(value, collections.abc.Sequence)`.
pub fn is_sequence_instance(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<bool> {
    if is_exact(value, ffi::PyList_CheckExact)
        || is_exact(value, ffi::PyTuple_CheckExact)
        || is_exact(value, ffi::PyUnicode_CheckExact)
    {
        return Ok(true);
    }
    if value.is_none()
        || is_exact(value, ffi::PyLong_CheckExact)
        || is_exact(value, ffi::PyBool_Check)
        || is_exact(value, ffi::PyDict_CheckExact)
    {
        return Ok(false);
    }
    value.is_instance(sequence_abc(py)?)
}

/// `issubclass(type(value), Mapping)`, treating any failure as `False` like the reference.
pub fn is_actual_mapping(py: Python<'_>, value: &Bound<'_, PyAny>) -> bool {
    if is_exact(value, ffi::PyDict_Check) {
        return true;
    }
    if is_plain_scalar(value)
        || is_exact(value, ffi::PyList_CheckExact)
        || is_exact(value, ffi::PyTuple_CheckExact)
    {
        return false;
    }
    let Ok(mapping) = mapping_abc(py) else {
        return false;
    };
    let result = unsafe { ffi::PyObject_IsSubclass(value.get_type().as_ptr(), mapping.as_ptr()) };
    if result < 0 {
        let _ = PyErr::take(py);
        return false;
    }
    result == 1
}

/// `ProtocolValueError(reason)` raised `from cause`.
pub fn protocol_error_from(py: Python<'_>, reason: &str, cause: PyErr) -> PyErr {
    let error = protocol_error(py, reason);
    error.set_cause(py, Some(cause));
    error
}

/// The `_items` tuple of an exact `JsonObject` (its `(key, value)` pairs in order).
pub fn json_object_items<'py>(value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyTuple>> {
    let items = value.getattr(pyo3::intern!(value.py(), "_items"))?;
    Ok(items.cast_into::<PyTuple>()?)
}

/// The `_index` mapping proxy of an exact `JsonObject`.
pub fn json_object_index<'py>(value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    value.getattr(pyo3::intern!(value.py(), "_index"))
}

/// `str` content of an exact `str`, or `None` when it holds a lone surrogate.
pub fn exact_text<'a>(value: &'a Bound<'_, PyString>) -> Option<&'a str> {
    value.to_str().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suspended_depth_is_cumulative_and_restored() {
        assert_eq!(entry_depth(), 0);
        assert!(!reference_only());
        call_python(40, || {
            assert_eq!(entry_depth(), 40);
            assert!(!reference_only());
            // A shallower walk entered meanwhile never lowers the published depth.
            call_python(10, || assert_eq!(entry_depth(), 40));
            call_python(90, || {
                assert_eq!(entry_depth(), 90);
                defer_deep(|| {
                    assert_eq!(entry_depth(), NATIVE_RECURSION_LIMIT);
                    assert!(reference_only());
                });
                assert_eq!(entry_depth(), 90);
            });
            assert_eq!(entry_depth(), 40);
        });
        assert_eq!(entry_depth(), 0);
    }

    #[test]
    fn suspended_depth_is_restored_on_unwind() {
        let outcome = std::panic::catch_unwind(|| defer_deep(|| panic!("unwind")));
        assert!(outcome.is_err());
        assert_eq!(entry_depth(), 0);
        assert!(!reference_only());
    }
}
