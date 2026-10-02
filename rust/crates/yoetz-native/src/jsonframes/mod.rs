//! `json.loads`-compatible fast paths for the MCP stdio transport and the Codex importers.
//!
//! Every function here is accept-only: it returns the exact object the Python reference would
//! return, or `None` when the reference must run (to accept, or to refuse with its own reason).
//! Decoding uses [`yoetz_core::protocol::json_compat`]; this module only builds Python objects.

use std::ffi::CString;

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString};
use yoetz_core::protocol::json_compat::{CompatLimits, CompatValue, MAX_COMPAT_DEPTH};

mod codex_jsonl;
mod codex_plan;
mod codex_rollout_jsonl;
mod mcp_stdio;

type Members<'a, 'py> = Vec<(std::borrow::Cow<'a, str>, CompatValue<'a, Bound<'py, PyAny>>)>;

/// Build a long integer literal exactly as the stdlib scanner does (`PyLong_FromString` applies
/// the interpreter's `int_max_str_digits` limit); any failure defers to the reference.
fn big_int<'py>(py: Python<'py>) -> impl FnMut(&str) -> Option<Bound<'py, PyAny>> {
    move |literal: &str| {
        let text = CString::new(literal).ok()?;
        let pointer = unsafe { ffi::PyLong_FromString(text.as_ptr(), std::ptr::null_mut(), 10) };
        if pointer.is_null() {
            let _ = PyErr::take(py);
            return None;
        }
        Some(unsafe { Bound::from_owned_ptr(py, pointer) })
    }
}

/// The depth limits for a reference that walks values from depth 0 and refuses any value
/// deeper than `max_depth` (`_validate_json_tree` in the Codex importers).
fn value_depth_limits(max_depth: i64) -> Option<CompatLimits> {
    let depth = usize::try_from(max_depth).ok().filter(|depth| *depth <= MAX_COMPAT_DEPTH)?;
    Some(CompatLimits { max_value_depth: depth, max_container_depth: depth, allow_overflow: false })
}

/// The raw bytes of an exact `bytes` object.
fn exact_bytes<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a [u8]> {
    if unsafe { ffi::PyBytes_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    Some(unsafe { value.cast_unchecked::<PyBytes>() }.as_bytes())
}

/// Convert an accepted value. Depth is bounded by the limits the value was accepted under.
fn to_python<'py>(py: Python<'py>, value: CompatValue<'_, Bound<'py, PyAny>>) -> PyResult<Bound<'py, PyAny>> {
    Ok(match value {
        CompatValue::Null => py.None().into_bound(py),
        CompatValue::Bool(truth) => PyBool::new(py, truth).to_owned().into_any(),
        CompatValue::Int(number) => PyInt::new(py, number).into_any(),
        CompatValue::Big(number) => number,
        CompatValue::Float(number) => PyFloat::new(py, number).into_any(),
        CompatValue::Str(text) => PyString::new(py, &text).into_any(),
        CompatValue::Array(items) => {
            let mut converted = Vec::with_capacity(items.len());
            for item in items {
                converted.push(to_python(py, item)?);
            }
            PyList::new(py, converted)?.into_any()
        }
        CompatValue::Object(members) => members_to_dict(py, members)?.into_any(),
    })
}

fn members_to_dict<'py>(py: Python<'py>, members: Members<'_, 'py>) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    for (key, item) in members {
        dict.set_item(PyString::new(py, &key), to_python(py, item)?)?;
    }
    Ok(dict)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    mcp_stdio::register(module)?;
    codex_jsonl::register(module)?;
    codex_rollout_jsonl::register(module)?;
    codex_plan::register(module)?;
    Ok(())
}
