//! `_fold_status_rows` of `yoetz.adapters.sqlite.observation`.
//!
//! The fold walks the ledger rows behind an observation status read. The twin handles rows of
//! the shapes the ledger stores (exact `str` session, source, and event kind; `bytes` or
//! non-`bytes` JSON blobs) and returns `None` for anything else, including any JSON blob the
//! strict parser refuses, so the Python reference raises (or folds) exactly as it would.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};

use pyo3::exceptions::PyValueError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PySet, PyString, PyTuple};
use yoetz_core::protocol::canonical::Value;
use yoetz_core::protocol::json;

struct Bindings {
    /// `ObservationSource` value -> member.
    sources: Py<PyDict>,
    source_lag: String,
    cursor_stale: String,
    content_capture_unavailable: String,
    unsupported_event: String,
}

static BINDINGS: Mutex<Option<Arc<Bindings>>> = Mutex::new(None);

/// Bind the observation enums the fold consults.
#[pyfunction]
pub fn bind_observation_status_fold(source: &Bound<'_, PyDict>) -> PyResult<()> {
    let required = |name: &str| -> PyResult<Bound<'_, PyAny>> {
        source
            .get_item(name)?
            .ok_or_else(|| PyValueError::new_err("observation_fold_binding_missing"))
    };
    let source_class = required("source_class")?;
    let sources = source_class
        .getattr("_value2member_map_")?
        .cast_into::<PyDict>()?;
    let gap = required("gap_class")?;
    let value = |name: &str| -> PyResult<String> { gap.getattr(name)?.getattr("value")?.extract() };
    let bound = Bindings {
        sources: sources.unbind(),
        source_lag: value("SOURCE_LAG")?,
        cursor_stale: value("CURSOR_STALE")?,
        content_capture_unavailable: value("CONTENT_CAPTURE_UNAVAILABLE")?,
        unsupported_event: value("UNSUPPORTED_EVENT")?,
    };
    *BINDINGS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(Arc::new(bound));
    Ok(())
}

fn exact_str<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a str> {
    if unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    unsafe { value.cast_unchecked::<PyString>() }.to_str().ok()
}

/// The strict-parsed blob when it is exact `bytes`: `Ok(None)` for a non-`bytes` blob (the
/// reference skips it), `Err(())` when the parser refuses it (the reference must raise).
fn parsed_blob(blob: &Bound<'_, PyAny>) -> Result<Option<Value>, ()> {
    if unsafe { ffi::PyBytes_CheckExact(blob.as_ptr()) } == 0 {
        return Ok(None);
    }
    let bytes = unsafe { blob.cast_unchecked::<PyBytes>() }.as_bytes();
    json::parse(bytes).map(Some).map_err(|_| ())
}

#[derive(Default)]
struct Session {
    current: HashSet<String>,
    unsupported: HashSet<String>,
}

/// `_fold_status_rows(rows, session_commitment)`, or `None` when the reference must decide.
#[pyfunction]
pub fn observation_status_fold<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
    session_commitment: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyTuple>>> {
    let Some(bound) = BINDINGS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clone()
    else {
        return Ok(None);
    };
    let wanted: Option<&str> = if session_commitment.is_none() {
        None
    } else {
        match exact_str(session_commitment) {
            Some(text) => Some(text),
            None => return Ok(None),
        }
    };
    if unsafe { ffi::PyList_CheckExact(rows.as_ptr()) } == 0 {
        return Ok(None);
    }
    let rows = unsafe { rows.cast_unchecked::<PyList>() };
    let sources = bound.sources.bind(py);
    let covered = PyList::empty(py);
    let mut seen_sources: Vec<Bound<'py, PyAny>> = Vec::with_capacity(4);
    let mut sessions: HashMap<String, Session> = HashMap::new();
    // Index access re-reads the length on every step, like the reference's iteration.
    let mut index = 0;
    while index < rows.len() {
        let row = rows.get_item(index)?;
        index += 1;
        if unsafe { ffi::PyTuple_CheckExact(row.as_ptr()) } == 0 {
            return Ok(None);
        }
        let row = unsafe { row.cast_unchecked::<PyTuple>() };
        if row.len() < 5 {
            return Ok(None);
        }
        let row_session = row.get_item(0)?;
        let source_value = row.get_item(1)?;
        if exact_str(&source_value).is_none() {
            return Ok(None);
        }
        let Some(source) = sources.get_item(&source_value)? else {
            return Ok(None);
        };
        let event_kind = row.get_item(2)?;
        let Some(event_kind) = exact_str(&event_kind) else {
            return Ok(None);
        };
        if unsafe { ffi::PyUnicode_CheckExact(row_session.as_ptr()) } == 0 {
            continue;
        }
        let Some(row_session) = exact_str(&row_session) else {
            return Ok(None);
        };
        if !seen_sources.iter().any(|seen| seen.is(&source)) {
            seen_sources.push(source);
        }
        let mut gap_codes: Vec<String> = Vec::new();
        match parsed_blob(&row.get_item(3)?) {
            Ok(Some(Value::Array(items))) => {
                for item in items {
                    if let Value::Str(text) = item {
                        gap_codes.push(text);
                    }
                }
            }
            Ok(_) => {}
            Err(()) => return Ok(None),
        }
        let session = sessions.entry(row_session.to_owned()).or_default();
        if event_kind != "observation_gap" {
            session.current.remove(&bound.source_lag);
            session.current.remove(&bound.cursor_stale);
            match parsed_blob(&row.get_item(4)?) {
                Ok(Some(Value::Array(items))) if !items.is_empty() => {
                    session.current.remove(&bound.content_capture_unavailable);
                }
                Ok(_) => {}
                Err(()) => return Ok(None),
            }
        }
        let unsupported = gap_codes
            .iter()
            .any(|code| *code == bound.unsupported_event);
        session.current.extend(gap_codes);
        if unsupported {
            session.unsupported.insert(event_kind.to_owned());
        }
    }
    for source in &seen_sources {
        covered.append(source)?;
    }
    let gaps = PySet::empty(py)?;
    let unsupported = PySet::empty(py)?;
    let collect = |session: &Session| -> PyResult<()> {
        for code in &session.current {
            gaps.add(code)?;
        }
        for kind in &session.unsupported {
            unsupported.add(kind)?;
        }
        Ok(())
    };
    match wanted {
        None => {
            for session in sessions.values() {
                collect(session)?;
            }
        }
        Some(name) => {
            if let Some(session) = sessions.get(name) {
                collect(session)?;
            }
        }
    }
    Ok(Some(PyTuple::new(
        py,
        [covered.into_any(), gaps.into_any(), unsupported.into_any()],
    )?))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_observation_status_fold, module)?)?;
    module.add_function(wrap_pyfunction!(observation_status_fold, module)?)?;
    Ok(())
}
