//! `yoetz_core::shlex` over Python strings: `shlex_split`, `shlex_quote`, and `shlex_join`.
//!
//! These mirror the standard library's `shlex.split`/`quote`/`join` byte for byte (the parity
//! harness fuzzes them against CPython). A string holding a lone surrogate, or any argument the
//! port does not model, goes to the standard library itself.

use pyo3::exceptions::PyValueError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};
use yoetz_core::shlex as core;

/// Exact `str` whose contents are valid UTF-8 (no lone surrogate).
pub fn exact_utf8<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a str> {
    if unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    unsafe { value.cast_unchecked::<PyString>() }.to_str().ok()
}

/// CPython's `ValueError` for a failed split.
pub fn split_error(error: core::ShlexError) -> PyErr {
    PyValueError::new_err(error.message())
}

fn stdlib<'py>(py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    py.import("shlex")?.getattr(name)
}

/// `shlex.split(s)` (posix, no comments).
#[pyfunction]
pub fn shlex_split<'py>(py: Python<'py>, s: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let Some(text) = exact_utf8(s) else {
        return stdlib(py, "split")?.call1((s,));
    };
    let words = core::split(text).map_err(split_error)?;
    Ok(PyList::new(py, words)?.into_any())
}

/// `shlex.quote(s)`.
#[pyfunction]
pub fn shlex_quote<'py>(py: Python<'py>, s: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    match exact_utf8(s) {
        // The reference returns its argument itself when nothing needs quoting.
        Some(text) if core::is_quote_safe(text) => Ok(s.clone()),
        Some(text) => Ok(PyString::new(py, &core::quote(text)).into_any()),
        None => stdlib(py, "quote")?.call1((s,)),
    }
}

/// `shlex.join(words)` for a list or tuple of `str`.
#[pyfunction]
pub fn shlex_join<'py>(py: Python<'py>, words: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let pointer = words.as_ptr();
    let exact_sequence = unsafe { ffi::PyList_CheckExact(pointer) != 0 || ffi::PyTuple_CheckExact(pointer) != 0 };
    if exact_sequence {
        let items: Vec<Bound<'py, PyAny>> = words.try_iter()?.collect::<PyResult<_>>()?;
        let mut texts: Vec<&str> = Vec::with_capacity(items.len());
        let mut all = true;
        for item in &items {
            match exact_utf8(item) {
                Some(text) => texts.push(text),
                None => {
                    all = false;
                    break;
                }
            }
        }
        if all {
            return Ok(PyString::new(py, &core::join(&texts)).into_any());
        }
    }
    stdlib(py, "join")?.call1((words,))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(shlex_split, module)?)?;
    module.add_function(wrap_pyfunction!(shlex_quote, module)?)?;
    module.add_function(wrap_pyfunction!(shlex_join, module)?)?;
    Ok(())
}
