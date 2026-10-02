//! Batch identifier validation of `yoetz.ports.importer` (`_ordered_ids`, `_sorted_ids`).
//!
//! The twins reuse the merged `yoetz.protocol.ids` grammar and accept only: an exact `tuple`
//! within its bound, of exact `str` members that all validate, with no duplicate (ordered) or
//! strictly increasing ASCII order (sorted). For any other input they return `None` and the
//! Python wrapper runs the reference, which raises the first offending item's exact refusal.

use std::collections::HashSet;

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyString, PyTuple};
use yoetz_core::protocol::ids::{Candidate, validate_actor_id_text, validate_id_text};

use crate::ids::{is_actor_kind, kind_prefix};

/// How one member is validated.
pub(crate) enum Grammar {
    Id(String),
    Actor,
}

impl Grammar {
    /// The grammar of an exact bound `IdKind` member, or `None`.
    pub(crate) fn of(kind: &Bound<'_, PyAny>) -> Option<Grammar> {
        if is_actor_kind(kind) {
            return Some(Grammar::Actor);
        }
        kind_prefix(kind).map(Grammar::Id)
    }

    /// Whether the exact `str` member validates (a lone surrogate never does).
    pub(crate) fn accepts(&self, text: &Bound<'_, PyString>) -> bool {
        let Ok(slice) = text.to_str() else {
            return false;
        };
        let count = unsafe { ffi::PyUnicode_GetLength(text.as_ptr()) }.max(0) as usize;
        match self {
            Grammar::Id(prefix) => validate_id_text(Candidate::Text(slice), count, prefix).is_ok(),
            Grammar::Actor => validate_actor_id_text(Candidate::Text(slice), count).is_ok(),
        }
    }
}

/// The exact-`str` members of an exact `tuple` within `maximum`, or `None`.
pub(crate) fn exact_str_members<'py>(value: &Bound<'py, PyAny>, maximum: usize) -> Option<Vec<Bound<'py, PyString>>> {
    if unsafe { ffi::PyTuple_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    let tuple = unsafe { value.cast_unchecked::<PyTuple>() };
    if tuple.len() > maximum {
        return None;
    }
    tuple
        .iter()
        .map(|item| {
            if unsafe { ffi::PyUnicode_CheckExact(item.as_ptr()) } == 0 {
                None
            } else {
                Some(unsafe { item.cast_into_unchecked::<PyString>() })
            }
        })
        .collect()
}

/// `_ordered_ids(value, kind=kind, maximum=maximum)`.
#[pyfunction]
pub fn importer_ordered_ids<'py>(py: Python<'py>, value: &Bound<'py, PyAny>, kind: &Bound<'py, PyAny>, maximum: usize) -> PyResult<Option<Bound<'py, PyTuple>>> {
    let (Some(grammar), Some(members)) = (Grammar::of(kind), exact_str_members(value, maximum)) else {
        return Ok(None);
    };
    let mut seen: HashSet<&str> = HashSet::with_capacity(members.len());
    for member in &members {
        if !grammar.accepts(member) {
            return Ok(None);
        }
        // ``accepts`` proved the text is valid UTF-8.
        let Ok(text) = member.to_str() else {
            return Ok(None);
        };
        if !seen.insert(text) {
            return Ok(None);
        }
    }
    Ok(Some(PyTuple::new(py, members)?))
}

/// `_sorted_ids(value, kind=kind, maximum=maximum)`; `kind` may be `None`.
#[pyfunction]
pub fn importer_sorted_ids<'py>(py: Python<'py>, value: &Bound<'py, PyAny>, kind: &Bound<'py, PyAny>, maximum: usize) -> PyResult<Option<Bound<'py, PyTuple>>> {
    let grammar = if kind.is_none() {
        None
    } else {
        match Grammar::of(kind) {
            Some(grammar) => Some(grammar),
            None => return Ok(None),
        }
    };
    let Some(members) = exact_str_members(value, maximum) else {
        return Ok(None);
    };
    let mut previous: Option<&str> = None;
    for member in &members {
        let Ok(text) = member.to_str() else {
            return Ok(None);
        };
        let valid = match &grammar {
            Some(grammar) => grammar.accepts(member),
            // ``isascii()`` and 1..=128 code points (ASCII, so bytes).
            None => text.is_ascii() && (1..=128).contains(&text.len()),
        };
        if !valid || previous.is_some_and(|before| text.as_bytes() <= before.as_bytes()) {
            return Ok(None);
        }
        previous = Some(text);
    }
    Ok(Some(PyTuple::new(py, members)?))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(importer_ordered_ids, module)?)?;
    module.add_function(wrap_pyfunction!(importer_sorted_ids, module)?)?;
    Ok(())
}
