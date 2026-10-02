//! Python objects the Python package hands to the accelerator at import time.
//!
//! The accelerator never imports `yoetz` itself: the owning Python module binds the classes it
//! must raise or recognize (for example `ProtocolValueError` and `CanonicalFragment`). Bindings
//! are replaceable so a re-executed module rebinds its fresh class objects.

use std::sync::Mutex;

use pyo3::exceptions::PyValueError;
use pyo3::ffi;
use pyo3::prelude::*;

/// One replaceable binding slot.
pub struct Slot(Mutex<Option<Py<PyAny>>>);

impl Slot {
    pub const fn new() -> Self {
        Slot(Mutex::new(None))
    }

    pub fn set(&self, value: Py<PyAny>) {
        *self
            .0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(value);
    }

    pub fn get<'py>(&self, py: Python<'py>) -> Option<Bound<'py, PyAny>> {
        self.0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .as_ref()
            .map(|value| value.bind(py).clone())
    }
}

pub static PROTOCOL_VALUE_ERROR: Slot = Slot::new();

/// Build the `ProtocolValueError(reason)` the Python reference would raise.
///
/// A Python `raise` inside an `except` block chains the exception being handled as the new
/// exception's `__context__`; restoring a prebuilt exception from native code does not, so the
/// chain is attached here the way CPython attaches it.
pub fn protocol_error(py: Python<'_>, reason: &str) -> PyErr {
    match PROTOCOL_VALUE_ERROR.get(py) {
        Some(class) => match class.call1((reason,)) {
            Ok(instance) => {
                chain_handled_context(py, &instance);
                PyErr::from_value(instance)
            }
            Err(error) => error,
        },
        None => {
            let error = PyValueError::new_err(reason.to_owned());
            chain_handled_context(py, error.value(py).as_any());
            error
        }
    }
}

/// `raise ProtocolValueError(reason) from cause` inside the `except` that caught `cause`:
/// `cause` becomes both `__cause__` and `__context__`, as in the Python reference.
pub fn protocol_error_from(py: Python<'_>, reason: &str, cause: PyErr) -> PyErr {
    let error = protocol_error(py, reason);
    let cause = cause.into_value(py);
    let value = error.value(py);
    if !value.is(&cause) {
        // Both calls steal a reference.
        unsafe {
            ffi::PyException_SetContext(value.as_ptr(), cause.clone_ref(py).into_ptr());
            ffi::PyException_SetCause(value.as_ptr(), cause.into_ptr());
        }
    }
    error
}

/// Set `instance.__context__` to the exception currently being handled (`sys.exception()`),
/// unless it is `instance` itself, exactly as CPython's implicit chaining does on `raise`.
pub fn chain_handled_context(py: Python<'_>, instance: &Bound<'_, PyAny>) {
    let handled = unsafe { ffi::PyErr_GetHandledException() };
    if handled.is_null() {
        return;
    }
    // Owned: `PyErr_GetHandledException` returns a new reference.
    let handled = unsafe { Bound::from_owned_ptr(py, handled) };
    if handled.is_none() || handled.is(instance) {
        return;
    }
    if unsafe { ffi::PyExceptionInstance_Check(instance.as_ptr()) } == 0 {
        return;
    }
    // A fresh instance cannot already sit in the handled exception's context chain, so no
    // cycle can form. `PyException_SetContext` steals the reference.
    unsafe { ffi::PyException_SetContext(instance.as_ptr(), handled.into_ptr()) };
}

/// Bind `yoetz.protocol.errors.ProtocolValueError`.
#[pyfunction]
pub fn bind_protocol_value_error(class: Bound<'_, PyAny>) {
    PROTOCOL_VALUE_ERROR.set(class.unbind());
}
