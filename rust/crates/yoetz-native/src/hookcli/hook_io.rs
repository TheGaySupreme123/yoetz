//! `yoetz.cli.hook_io._parse_cursor_hook_document` and the oversized-body identity skim.

use std::collections::HashMap;

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyInt, PyList, PyString};
use yoetz_core::cli::hook_io::{self as core, CursorKey, CursorValue};
use yoetz_core::protocol::canonical::LONE_SURROGATE;

use crate::registry::protocol_error;

/// Builds Python values, sharing one `str` per distinct key like the stdlib scanner's memo.
struct Builder<'py, 'a> {
    py: Python<'py>,
    keys: HashMap<&'a str, Bound<'py, PyString>>,
}

impl<'py, 'a> Builder<'py, 'a> {
    fn key(&mut self, key: &'a CursorKey<'a>) -> PyResult<Bound<'py, PyString>> {
        let CursorKey::Text(text) = key else {
            // An accepted document has no unencodable key.
            return Err(protocol_error(self.py, LONE_SURROGATE));
        };
        let text: &'a str = text.as_ref();
        if let Some(existing) = self.keys.get(text) {
            return Ok(existing.clone());
        }
        let built = PyString::new(self.py, text);
        self.keys.insert(text, built.clone());
        Ok(built)
    }

    /// Depth is bounded: an accepted tree nests at most `MAX_JSON_DEPTH` containers.
    fn value(&mut self, value: &'a CursorValue<'a>) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        Ok(match value {
            CursorValue::Null => py.None().into_bound(py),
            CursorValue::Bool(truth) => PyBool::new(py, *truth).to_owned().into_any(),
            CursorValue::Int(number) => PyInt::new(py, *number).into_any(),
            CursorValue::Str(text) => PyString::new(py, text).into_any(),
            CursorValue::BadStr(reason) => return Err(protocol_error(py, reason)),
            CursorValue::Array(items) => {
                let mut converted = Vec::with_capacity(items.len());
                for item in items {
                    converted.push(self.value(item)?);
                }
                PyList::new(py, converted)?.into_any()
            }
            CursorValue::Object(members) => self.object(members)?.into_any(),
        })
    }

    fn object(
        &mut self,
        members: &'a [(CursorKey<'a>, CursorValue<'a>)],
    ) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(self.py);
        for (key, item) in members {
            dict.set_item(self.key(key)?, self.value(item)?)?;
        }
        Ok(dict)
    }
}

fn exact_bytes<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a [u8]> {
    if unsafe { ffi::PyBytes_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    Some(unsafe { value.cast_unchecked::<PyBytes>() }.as_bytes())
}

/// `_parse_cursor_hook_document(data)` for exact `bytes`; anything else is `NotImplemented`.
#[pyfunction]
pub fn cursor_hook_document<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let Some(raw) = exact_bytes(data) else {
        return Ok(super::defer(py));
    };
    let members =
        core::parse_cursor_hook_document(raw).map_err(|reason| protocol_error(py, reason))?;
    let mut builder = Builder {
        py,
        keys: HashMap::new(),
    };
    Ok(builder.object(&members)?.into_any())
}

/// `_cursor_identity_payload(_parse_cursor_hook_document(data))` without materializing the
/// discarded content.
#[pyfunction]
pub fn cursor_hook_identity<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let Some(raw) = exact_bytes(data) else {
        return Ok(super::defer(py));
    };
    let members =
        core::parse_cursor_hook_document(raw).map_err(|reason| protocol_error(py, reason))?;
    let view = core::cursor_identity_payload(&members);
    let mut builder = Builder {
        py,
        keys: HashMap::new(),
    };
    Ok(builder.object(&view)?.into_any())
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(cursor_hook_document, module)?)?;
    module.add_function(wrap_pyfunction!(cursor_hook_identity, module)?)?;
    Ok(())
}
