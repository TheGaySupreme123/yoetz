//! Shared helpers for the native twins that walk live Python trees (`values`, `ids`, `models`,
//! `control_protocol`, and the service leaf walk).
//!
//! Each twin handles the exact built-in shapes natively and hands anything else back to its
//! Python reference, so a subclass, a custom `Mapping`, or a hostile object always takes the
//! reference's path.

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyString, PyTuple};

use crate::registry::{Slot, protocol_error};

/// `yoetz.domain.values.JsonObject`, bound by `yoetz.domain.values`.
pub static JSON_OBJECT: Slot = Slot::new();

static MAPPING_ABC: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static SEQUENCE_ABC: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// Native recursion depth after which a walk hands the subtree to its Python reference, whose
/// own recursion limit then decides (no hostile nesting can exhaust the native stack).
pub const NATIVE_RECURSION_LIMIT: usize = 192;

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

fn abc<'py>(py: Python<'py>, cell: &'static PyOnceLock<Py<PyAny>>, name: &str) -> PyResult<&'py Bound<'py, PyAny>> {
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
    if is_plain_scalar(value) || is_exact(value, ffi::PyList_CheckExact) || is_exact(value, ffi::PyTuple_CheckExact) {
        return Ok(false);
    }
    value.is_instance(mapping_abc(py)?)
}

/// `isinstance(value, collections.abc.Sequence)`.
pub fn is_sequence_instance(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<bool> {
    if is_exact(value, ffi::PyList_CheckExact) || is_exact(value, ffi::PyTuple_CheckExact) || is_exact(value, ffi::PyUnicode_CheckExact) {
        return Ok(true);
    }
    if value.is_none() || is_exact(value, ffi::PyLong_CheckExact) || is_exact(value, ffi::PyBool_Check) || is_exact(value, ffi::PyDict_CheckExact) {
        return Ok(false);
    }
    value.is_instance(sequence_abc(py)?)
}

/// `issubclass(type(value), Mapping)`, treating any failure as `False` like the reference.
pub fn is_actual_mapping(py: Python<'_>, value: &Bound<'_, PyAny>) -> bool {
    if is_exact(value, ffi::PyDict_Check) {
        return true;
    }
    if is_plain_scalar(value) || is_exact(value, ffi::PyList_CheckExact) || is_exact(value, ffi::PyTuple_CheckExact) {
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
