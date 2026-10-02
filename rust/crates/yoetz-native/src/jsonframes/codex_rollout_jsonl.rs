//! `yoetz.adapters.importers.codex_rollout_jsonl`: `_redact_json_tree(_parse_json_line(...))`.

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyString, PyTuple};
use yoetz_core::protocol::json_compat::{self, CompatValue};

use super::{Members, big_int, exact_bytes, to_python, value_depth_limits};

/// Rebuilds an accepted tree the way `_redact_json_tree.redact` does: every string (keys
/// included, in document order) goes through the module's redaction function, floats become
/// `None`, and a redacted key that collides with an earlier one refuses the line.
struct Redactor<'py> {
    py: Python<'py>,
    redact: Bound<'py, PyAny>,
    /// Set when the redaction function returned something other than `(bytes, ...)` whose
    /// bytes decode as UTF-8, so its Python `.decode` produced the text; only then can the
    /// reference's final tree check refuse (an exact `str` holding a lone surrogate included).
    unusual: bool,
}

impl<'py> Redactor<'py> {
    /// `redacted, _detected = redact(text.encode("utf-8")); redacted.decode("utf-8")`.
    fn text(&mut self, text: &str) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let result = self.redact.call1((PyBytes::new(py, text.as_bytes()),))?;
        let redacted = first_of_pair(&result)?;
        if let Some(raw) = exact_bytes(&redacted) {
            if let Ok(decoded) = std::str::from_utf8(raw) {
                return Ok(PyString::new(py, decoded).into_any());
            }
        }
        // The reference's own call raises the exact error (or returns an unusual object).
        let options = PyDict::new(py);
        options.set_item("errors", "strict")?;
        let decoded = redacted.call_method("decode", ("utf-8",), Some(&options))?;
        // Whatever `.decode` returned (a `str` subclass, a non-`str`, or an exact `str` with a
        // lone surrogate) is vouched for only by the reference's `_validate_json_tree`.
        self.unusual = true;
        Ok(decoded)
    }

    fn value(&mut self, value: CompatValue<'_, Bound<'py, PyAny>>) -> PyResult<Bound<'py, PyAny>> {
        match value {
            CompatValue::Str(text) => self.text(&text),
            CompatValue::Float(_) => Ok(self.py.None().into_bound(self.py)),
            CompatValue::Array(items) => {
                let mut converted = Vec::with_capacity(items.len());
                for item in items {
                    converted.push(self.value(item)?);
                }
                Ok(PyList::new(self.py, converted)?.into_any())
            }
            CompatValue::Object(members) => Ok(self.object(members)?.into_any()),
            other => to_python(self.py, other),
        }
    }

    fn object(&mut self, members: Members<'_, 'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(self.py);
        for (key, item) in members {
            let key = self.text(&key)?;
            if dict.contains(&key)? {
                return Err(PyValueError::new_err("duplicate_object_key"));
            }
            let item = self.value(item)?;
            dict.set_item(key, item)?;
        }
        Ok(dict)
    }
}

/// `first, _ = value` with the interpreter's unpacking rules.
fn first_of_pair<'py>(value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    if unsafe { ffi::PyTuple_CheckExact(value.as_ptr()) } != 0 {
        let pair = unsafe { value.cast_unchecked::<PyTuple>() };
        if pair.len() == 2 {
            return pair.get_item(0);
        }
    }
    let pointer = value.as_ptr();
    let mut items = match value.try_iter() {
        Ok(items) => items,
        Err(error) => {
            // CPython's unpack_iterable rewrites the error for a plainly non-iterable value.
            let kind = unsafe { ffi::Py_TYPE(pointer) };
            if error.is_instance_of::<PyTypeError>(value.py())
                && unsafe { (*kind).tp_iter.is_none() }
                && unsafe { ffi::PySequence_Check(pointer) } == 0
            {
                let name = unsafe { std::ffi::CStr::from_ptr((*kind).tp_name) }.to_string_lossy();
                return Err(PyTypeError::new_err(format!(
                    "cannot unpack non-iterable {name} object"
                )));
            }
            return Err(error);
        }
    };
    let first = items.next().transpose()?;
    let second = items.next().transpose()?;
    match (first, second) {
        (Some(first), Some(_)) => {
            if items.next().transpose()?.is_some() {
                let sized = unsafe {
                    ffi::PyList_CheckExact(pointer) != 0
                        || ffi::PyTuple_CheckExact(pointer) != 0
                        || ffi::PyDict_CheckExact(pointer) != 0
                };
                return Err(PyValueError::new_err(if sized {
                    format!(
                        "too many values to unpack (expected 2, got {})",
                        value.len()?
                    )
                } else {
                    "too many values to unpack (expected 2)".to_owned()
                }));
            }
            Ok(first)
        }
        (Some(_), None) => Err(PyValueError::new_err(
            "not enough values to unpack (expected 2, got 1)",
        )),
        _ => Err(PyValueError::new_err(
            "not enough values to unpack (expected 2, got 0)",
        )),
    }
}

/// The redacted line object, `None` when the line does not decode (the reference must then run
/// both steps), or the redaction step's own error.
///
/// Decoding admits exactly what `_parse_json_line` returns (see `codex_jsonl_accept_line`);
/// redaction then calls `redact` (the module's `redact_sensitive_content`) in the reference's
/// order. `validate` (the module's `_validate_json_tree`) runs on the result only when the
/// redaction function returned an object the native walk cannot vouch for.
#[pyfunction]
pub fn codex_rollout_accept_redacted_line<'py>(
    py: Python<'py>,
    content: &Bound<'py, PyAny>,
    max_depth: i64,
    redact: Bound<'py, PyAny>,
    validate: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let (Some(raw), Some(limits)) = (exact_bytes(content), value_depth_limits(max_depth)) else {
        return Ok(None);
    };
    let Some(members) = json_compat::accept_object_line(raw, limits, big_int(py)) else {
        return Ok(None);
    };
    let mut redactor = Redactor {
        py,
        redact,
        unusual: false,
    };
    let result = redactor.object(members)?;
    if redactor.unusual {
        validate.call1((&result,))?;
    }
    Ok(Some(result.into_any()))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(
        codex_rollout_accept_redacted_line,
        module
    )?)?;
    Ok(())
}
