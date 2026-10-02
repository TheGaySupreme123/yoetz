//! `yoetz.protocol.ids` validation over live Python objects.
//!
//! `new_id` stays in Python: it reads randomness through `os.urandom`, which the tests replace.

use std::collections::HashMap;
use std::sync::Mutex;

use pyo3::exceptions::PyTypeError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::exceptions::PyKeyError;
use pyo3::types::PyString;
use yoetz_core::protocol::canonical::Reason;
use yoetz_core::protocol::ids::{self as core, Candidate};

use crate::registry::protocol_error;
use crate::walk::exact_text;

/// The bound `IdKind` class, its `ACTOR` member, and each member's prefix keyed by identity.
struct Kinds {
    class: Py<PyAny>,
    actor: Py<PyAny>,
    prefixes: HashMap<usize, String>,
    /// Keeps every member alive so the identity keys stay valid.
    _members: Vec<Py<PyAny>>,
}

static KINDS: Mutex<Option<Kinds>> = Mutex::new(None);

/// Bind `IdKind`, `IdKind.ACTOR`, and `PREFIX_BY_KIND`.
#[pyfunction]
#[pyo3(name = "ids_bind_kinds")]
pub fn bind_id_kinds(class: Bound<'_, PyAny>, actor: Bound<'_, PyAny>, prefix_by_kind: Bound<'_, PyAny>) -> PyResult<()> {
    let mut prefixes = HashMap::new();
    let mut members = Vec::new();
    let items = prefix_by_kind.call_method0("items")?;
    for pair in items.try_iter()? {
        let (member, prefix): (Bound<'_, PyAny>, String) = pair?.extract()?;
        if prefix.len() != 4 || !prefix.is_ascii() {
            return Err(PyTypeError::new_err("id_prefix_invalid"));
        }
        prefixes.insert(member.as_ptr() as usize, prefix);
        members.push(member.unbind());
    }
    let kinds = Kinds { class: class.unbind(), actor: actor.unbind(), prefixes, _members: members };
    *KINDS.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(kinds);
    Ok(())
}

enum Verdict {
    Valid,
    Refused(Reason),
}

fn candidate<'a>(text: &'a Bound<'_, PyString>) -> (Candidate<'a>, usize) {
    let count = unsafe { ffi::PyUnicode_GetLength(text.as_ptr()) }.max(0) as usize;
    match exact_text(text) {
        Some(slice) => (Candidate::Text(slice), count),
        None => (Candidate::NotUtf8, count),
    }
}

fn actor_verdict(value: &Bound<'_, PyAny>) -> Verdict {
    // ``issubclass(type(value), str)``: the real type, so a spoofed ``__class__`` is refused.
    let Ok(text) = value.cast::<PyString>() else {
        return Verdict::Refused(core::ID_WRONG_TYPE);
    };
    let (text, count) = candidate(text);
    match core::validate_actor_id_text(text, count) {
        Ok(()) => Verdict::Valid,
        Err(reason) => Verdict::Refused(reason),
    }
}

fn id_verdict(kind: &Bound<'_, PyAny>, value: &Bound<'_, PyAny>) -> PyResult<Verdict> {
    let guard = KINDS.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let Some(kinds) = guard.as_ref() else {
        return Err(PyTypeError::new_err("id_kind_wrong_type"));
    };
    if kind.get_type().as_ptr() != kinds.class.as_ptr() {
        return Err(PyTypeError::new_err("id_kind_wrong_type"));
    }
    if kind.as_ptr() == kinds.actor.as_ptr() {
        drop(guard);
        return Ok(actor_verdict(value));
    }
    let Some(prefix) = kinds.prefixes.get(&(kind.as_ptr() as usize)) else {
        // Unreachable while PREFIX_BY_KIND names every member; the reference's lookup would
        // raise KeyError.
        return Err(PyKeyError::new_err(kind.clone().unbind()));
    };
    let Ok(text) = value.cast::<PyString>() else {
        return Ok(Verdict::Refused(core::ID_WRONG_TYPE));
    };
    let (text, count) = candidate(text);
    Ok(match core::validate_id_text(text, count, prefix) {
        Ok(()) => Verdict::Valid,
        Err(reason) => Verdict::Refused(reason),
    })
}

/// The prefix of an exact bound `IdKind` member other than `ACTOR`, or `None` (also before
/// `ids_bind_kinds` ran). Callers that need the reference's refusal defer to it on `None`.
pub(crate) fn kind_prefix(kind: &Bound<'_, PyAny>) -> Option<String> {
    let guard = KINDS.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let kinds = guard.as_ref()?;
    if kind.get_type().as_ptr() != kinds.class.as_ptr() || kind.as_ptr() == kinds.actor.as_ptr() {
        return None;
    }
    kinds.prefixes.get(&(kind.as_ptr() as usize)).cloned()
}

/// Whether `kind` is the bound `IdKind.ACTOR` member.
pub(crate) fn is_actor_kind(kind: &Bound<'_, PyAny>) -> bool {
    let guard = KINDS.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    guard.as_ref().is_some_and(|kinds| kind.as_ptr() == kinds.actor.as_ptr())
}

/// `validate_id(kind, value) -> value` (the same object).
#[pyfunction]
#[pyo3(name = "ids_validate_id")]
pub fn validate_id<'py>(py: Python<'py>, kind: &Bound<'py, PyAny>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    match id_verdict(kind, value)? {
        Verdict::Valid => Ok(value.clone()),
        Verdict::Refused(reason) => Err(protocol_error(py, reason)),
    }
}

/// `is_valid_id(kind, value) -> bool`.
#[pyfunction]
#[pyo3(name = "ids_is_valid_id")]
pub fn is_valid_id(kind: &Bound<'_, PyAny>, value: &Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(matches!(id_verdict(kind, value)?, Verdict::Valid))
}

/// `validate_actor_id(value) -> value` (the same object).
#[pyfunction]
#[pyo3(name = "ids_validate_actor_id")]
pub fn validate_actor_id<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    match actor_verdict(value) {
        Verdict::Valid => Ok(value.clone()),
        Verdict::Refused(reason) => Err(protocol_error(py, reason)),
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_id_kinds, module)?)?;
    module.add_function(wrap_pyfunction!(validate_id, module)?)?;
    module.add_function(wrap_pyfunction!(is_valid_id, module)?)?;
    module.add_function(wrap_pyfunction!(validate_actor_id, module)?)?;
    Ok(())
}
