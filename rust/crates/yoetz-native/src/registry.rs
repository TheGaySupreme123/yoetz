//! Python objects the Python package hands to the accelerator at import time.
//!
//! The accelerator never imports `yoetz` itself: the owning Python module binds the classes it
//! must raise or recognize (for example `ProtocolValueError` and `CanonicalFragment`). Bindings
//! are replaceable so a re-executed module rebinds its fresh class objects.

use std::sync::Mutex;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// One replaceable binding slot.
pub struct Slot(Mutex<Option<Py<PyAny>>>);

impl Slot {
    pub const fn new() -> Self {
        Slot(Mutex::new(None))
    }

    pub fn set(&self, value: Py<PyAny>) {
        *self.0.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(value);
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
pub fn protocol_error(py: Python<'_>, reason: &str) -> PyErr {
    match PROTOCOL_VALUE_ERROR.get(py) {
        Some(class) => match class.call1((reason,)) {
            Ok(instance) => PyErr::from_value(instance),
            Err(error) => error,
        },
        None => PyValueError::new_err(reason.to_owned()),
    }
}

/// Bind `yoetz.protocol.errors.ProtocolValueError`.
#[pyfunction]
pub fn bind_protocol_value_error(class: Bound<'_, PyAny>) {
    PROTOCOL_VALUE_ERROR.set(class.unbind());
}
