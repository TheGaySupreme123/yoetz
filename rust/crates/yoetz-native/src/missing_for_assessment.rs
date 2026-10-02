//! `yoetz.application.missing_for_assessment._diff_scope` over Python values.
//!
//! The command must be an exact `str` and the roots an exact `frozenset` of exact `str`, all
//! valid UTF-8; anything else (a lone surrogate included) is answered by the Python reference.
//! Roots are visited in the frozenset's own iteration order, as the reference's `_inside` does,
//! and the result is built as the reference builds it: a `set` in token order, then `frozenset`.

use pyo3::exceptions::PyNameError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyFrozenSet, PySet, PyString};
use yoetz_core::application::missing_for_assessment as core;

use crate::registry::Slot;
use crate::shlex::exact_utf8;

static FALLBACK_DIFF_SCOPE: Slot = Slot::new();

/// Bind the Python `_diff_scope` the twin defers to.
#[pyfunction]
pub fn bind_missing_for_assessment(diff_scope: Bound<'_, PyAny>) {
    FALLBACK_DIFF_SCOPE.set(diff_scope.unbind());
}

fn fallback<'py>(
    py: Python<'py>,
    command: &Bound<'py, PyAny>,
    roots: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    FALLBACK_DIFF_SCOPE
        .get(py)
        .ok_or_else(|| PyNameError::new_err("yoetz_native_missing_for_assessment_unbound"))?
        .call1((command, roots))
}

/// The tables and patterns the twin hard-codes, for the import-time drift check.
#[pyfunction]
pub fn missing_for_assessment_tables(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let tables = PyDict::new(py);
    tables.set_item("_WHOLE_TREE", core::WHOLE_TREE)?;
    tables.set_item("_SUMMARY_DIFF_OPTIONS", core::SUMMARY_DIFF_OPTIONS.to_vec())?;
    tables.set_item("_REVISION", core::REVISION_PATTERN)?;
    Ok(tables)
}

/// `_diff_scope(command, roots)`.
#[pyfunction]
pub fn diff_scope<'py>(
    py: Python<'py>,
    command: &Bound<'py, PyAny>,
    roots: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    if command.is_none() {
        return Ok(py.None().into_bound(py));
    }
    let Some(text) = exact_utf8(command) else {
        return fallback(py, command, roots);
    };
    if unsafe { ffi::PyFrozenSet_CheckExact(roots.as_ptr()) } == 0 {
        return fallback(py, command, roots);
    }
    let members: Vec<Bound<'py, PyAny>> = unsafe { roots.cast_unchecked::<PyFrozenSet>() }
        .iter()
        .collect();
    let mut root_texts: Vec<&str> = Vec::with_capacity(members.len());
    for member in &members {
        match exact_utf8(member) {
            Some(root) => root_texts.push(root),
            None => return fallback(py, command, roots),
        }
    }
    let Some(specs) = core::diff_scope(text, &root_texts) else {
        return Ok(py.None().into_bound(py));
    };
    let set = PySet::empty(py)?;
    if specs.is_empty() {
        set.add(PyString::new(py, core::WHOLE_TREE))?;
    }
    for spec in specs {
        set.add(PyString::new(py, &spec))?;
    }
    // `frozenset(specs)` copies the set exactly as the reference does.
    unsafe { Bound::from_owned_ptr_or_err(py, ffi::PyFrozenSet_New(set.as_ptr())) }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_missing_for_assessment, module)?)?;
    module.add_function(wrap_pyfunction!(missing_for_assessment_tables, module)?)?;
    module.add_function(wrap_pyfunction!(diff_scope, module)?)?;
    Ok(())
}
