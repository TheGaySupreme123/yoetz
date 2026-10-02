//! `yoetz.domain.values` timestamps (`parse_rfc3339_millis`, `Timestamp` validation,
//! `timestamp_from_string`) without `datetime.strptime`.
//!
//! Each twin answers only for an exact `str` the reference accepts and returns `None` for
//! anything else, so the Python wrapper runs the reference and raises its exact refusal (with
//! its exception chain and a `yoetz` frame as the origin).

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyDateTime, PyString, PyTzInfo};
use yoetz_core::domain::values::{TimestampParts, parse_wire_timestamp};

use crate::registry::Slot;

static TIMESTAMP_CLASS: Slot = Slot::new();

fn wire_parts(value: &Bound<'_, PyAny>) -> Option<TimestampParts> {
    if unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    let text = unsafe { value.cast_unchecked::<PyString>() }.to_str().ok()?;
    parse_wire_timestamp(text)
}

/// Bind `Timestamp`.
#[pyfunction]
#[pyo3(name = "values_bind_timestamp")]
pub fn bind_timestamp(class: Bound<'_, PyAny>) {
    TIMESTAMP_CLASS.set(class.unbind());
}

/// The aware UTC `datetime` `parse_rfc3339_millis(value)` returns, or `None`.
#[pyfunction]
#[pyo3(name = "values_parse_rfc3339_millis")]
pub fn parse_rfc3339_millis<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyAny>>> {
    let Some((year, month, day, hour, minute, second, microsecond)) = wire_parts(value) else {
        return Ok(None);
    };
    let utc = PyTzInfo::utc(py)?;
    let parsed = PyDateTime::new(py, i32::from(year), month, day, hour, minute, second, microsecond, Some(&*utc))?;
    Ok(Some(parsed.into_any()))
}

/// Whether `value` is an exact `str` that `parse_rfc3339_millis` accepts.
#[pyfunction]
#[pyo3(name = "values_is_wire_timestamp")]
pub fn is_wire_timestamp(value: &Bound<'_, PyAny>) -> bool {
    wire_parts(value).is_some()
}

/// `timestamp_from_string(value)` for an accepted exact `str`, or `None`.
#[pyfunction]
#[pyo3(name = "values_timestamp_from_string")]
pub fn timestamp_from_string<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyAny>>> {
    if wire_parts(value).is_none() {
        return Ok(None);
    }
    let Some(class) = TIMESTAMP_CLASS.get(py) else {
        return Ok(None);
    };
    Ok(Some(class.call1((value,))?))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_timestamp, module)?)?;
    module.add_function(wrap_pyfunction!(parse_rfc3339_millis, module)?)?;
    module.add_function(wrap_pyfunction!(is_wire_timestamp, module)?)?;
    module.add_function(wrap_pyfunction!(timestamp_from_string, module)?)?;
    Ok(())
}
