//! `yoetz.cli.hook_timing`: the stored document's decode + validation, and the fold that
//! turns the stored bytes and one sample into the next document's bytes.

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyInt, PyList, PyString};
use yoetz_core::cli::hook_timing::{self as core, Read, Sample};
use yoetz_core::protocol::json_compat::CompatValue;

use super::defer;
use crate::shlex::exact_utf8;

/// Largest `now_ms` the fold models; Python integers are unbounded.
const MAX_NOW_MS: i64 = 1 << 60;
const MAX_SAMPLE_MS: i64 = 3_600_000;

fn exact_bytes<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a [u8]> {
    if unsafe { ffi::PyBytes_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    Some(unsafe { value.cast_unchecked::<PyBytes>() }.as_bytes())
}

fn exact_int(value: &Bound<'_, PyAny>) -> Option<i64> {
    if unsafe { ffi::PyLong_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    value.extract::<i64>().ok()
}

/// `_updated_document(document, ...)` where `document` is the stored bytes (`None` restarts).
///
/// Returns `(encoded or None, restarted)` like the reference, or `NotImplemented` when the
/// stored bytes or an argument need the reference.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn hook_timing_fold<'py>(
    py: Python<'py>,
    raw: &Bound<'py, PyAny>,
    host: &Bound<'py, PyAny>,
    event: &Bound<'py, PyAny>,
    path: &Bound<'py, PyAny>,
    outcome: &Bound<'py, PyAny>,
    sample: &Bound<'py, PyAny>,
    now_ms: &Bound<'py, PyAny>,
    max_entries: &Bound<'py, PyAny>,
    max_file_bytes: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let (Some(host), Some(event), Some(path), Some(outcome)) =
        (exact_utf8(host), exact_utf8(event), exact_utf8(path), exact_utf8(outcome))
    else {
        return Ok(defer(py));
    };
    let (Some(ms), Some(now_ms), Some(max_entries), Some(max_file_bytes)) =
        (exact_int(sample), exact_int(now_ms), exact_int(max_entries), exact_int(max_file_bytes))
    else {
        return Ok(defer(py));
    };
    if !(0..=MAX_SAMPLE_MS).contains(&ms) || !(-MAX_NOW_MS..=MAX_NOW_MS).contains(&now_ms) || max_entries <= 0 {
        return Ok(defer(py));
    }
    let max_entries = usize::try_from(max_entries).unwrap_or(usize::MAX);
    let document = if raw.is_none() {
        None
    } else {
        let Some(bytes) = exact_bytes(raw) else {
            return Ok(defer(py));
        };
        match core::read_document(bytes, max_entries) {
            Read::Valid(document) => Some(document),
            Read::Invalid => None,
            Read::Defer => return Ok(defer(py)),
        }
    };
    let sample = Sample { host, event, path, outcome, ms, now_ms };
    let (folded, restarted) = core::fold(document, &sample, max_entries);
    let encoded = core::encode(&folded);
    let restarted = PyBool::new(py, restarted).to_owned().into_any();
    let encoded = if i64::try_from(encoded.len()).is_ok_and(|length| length > max_file_bytes) {
        py.None().into_bound(py)
    } else {
        PyBytes::new(py, encoded.as_bytes()).into_any()
    };
    Ok(pyo3::types::PyTuple::new(py, [encoded, restarted])?.into_any())
}

fn to_python<'py>(py: Python<'py>, value: &CompatValue<'_, ()>) -> PyResult<Bound<'py, PyAny>> {
    // Only a validated document (depth <= 4, integers and text only) reaches here.
    Ok(match value {
        CompatValue::Null => py.None().into_bound(py),
        CompatValue::Bool(truth) => PyBool::new(py, *truth).to_owned().into_any(),
        CompatValue::Int(number) => PyInt::new(py, *number).into_any(),
        CompatValue::Str(text) => PyString::new(py, text).into_any(),
        CompatValue::Array(items) => {
            let mut converted = Vec::with_capacity(items.len());
            for item in items {
                converted.push(to_python(py, item)?);
            }
            PyList::new(py, converted)?.into_any()
        }
        CompatValue::Object(members) => {
            let dict = PyDict::new(py);
            for (key, item) in members {
                dict.set_item(PyString::new(py, key), to_python(py, item)?)?;
            }
            dict.into_any()
        }
        CompatValue::Big(()) | CompatValue::Float(_) => {
            return Err(pyo3::exceptions::PyValueError::new_err("unvalidated_number"));
        }
    })
}

/// `_valid_document(json.loads(raw))`: the document object, `None`, or `NotImplemented`.
#[pyfunction]
pub fn hook_timing_document<'py>(
    py: Python<'py>,
    raw: &Bound<'py, PyAny>,
    max_entries: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let (Some(bytes), Some(max_entries)) = (exact_bytes(raw), exact_int(max_entries)) else {
        return Ok(defer(py));
    };
    let Ok(max_entries) = usize::try_from(max_entries) else {
        return Ok(defer(py));
    };
    let Some(value) = core::decode(bytes) else {
        return Ok(defer(py));
    };
    if core::validate(&value, max_entries).is_none() {
        return Ok(py.None().into_bound(py));
    }
    // Build the decoded value itself so member order matches ``json.loads``.
    to_python(py, &value)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(hook_timing_fold, module)?)?;
    module.add_function(wrap_pyfunction!(hook_timing_document, module)?)?;
    Ok(())
}
