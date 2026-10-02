//! `yoetz.cli.observe_hooks` edit-capture helpers over Python strings.
//!
//! An argument the core does not model (a non-`str`, a `str` subclass, a lone surrogate, or a
//! non-ASCII locator that would be casefolded) answers `NotImplemented` so the wrapper runs the
//! Python reference.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyString, PyTuple};
use yoetz_core::cli::observe_hooks as core;

use super::defer;
use crate::shlex::exact_utf8;

/// `None` or an exact, encodable `str`; `Err(())` for anything else.
fn locator<'a>(value: &'a Bound<'_, PyAny>) -> Result<Option<&'a str>, ()> {
    if value.is_none() {
        return Ok(None);
    }
    exact_utf8(value).map(Some).ok_or(())
}

/// An exact `int` at least `minimum`, saturated to `usize`.
fn exact_count(value: &Bound<'_, PyAny>, minimum: i64) -> Option<usize> {
    if unsafe { pyo3::ffi::PyLong_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    let number = value
        .extract::<i64>()
        .ok()
        .filter(|number| *number >= minimum)?;
    Some(usize::try_from(number).unwrap_or(usize::MAX))
}

#[pyfunction]
pub fn observe_workspace_relative_edit_path<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    workspace_locator: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let Some(text) = exact_utf8(value) else {
        return Ok(defer(py));
    };
    let Ok(workspace) = locator(workspace_locator) else {
        return Ok(defer(py));
    };
    Ok(match core::workspace_relative_edit_path(text, workspace) {
        Ok(Some(relative)) => PyString::new(py, &relative).into_any(),
        Ok(None) => py.None().into_bound(py),
        Err(core::Defer) => defer(py),
    })
}

fn rewrite<'py>(
    py: Python<'py>,
    text: &Bound<'py, PyAny>,
    workspace_locator: &Bound<'py, PyAny>,
    run: fn(&str, Option<&str>) -> Result<String, core::Defer>,
) -> PyResult<Bound<'py, PyAny>> {
    let Some(source) = exact_utf8(text) else {
        return Ok(defer(py));
    };
    let Ok(workspace) = locator(workspace_locator) else {
        return Ok(defer(py));
    };
    Ok(match run(source, workspace) {
        Ok(rewritten) => PyString::new(py, &rewritten).into_any(),
        Err(core::Defer) => defer(py),
    })
}

#[pyfunction]
pub fn observe_sanitize_patch_paths<'py>(
    py: Python<'py>,
    text: &Bound<'py, PyAny>,
    workspace_locator: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    rewrite(py, text, workspace_locator, core::sanitize_patch_paths)
}

#[pyfunction]
pub fn observe_sanitize_patch_result<'py>(
    py: Python<'py>,
    text: &Bound<'py, PyAny>,
    workspace_locator: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    rewrite(py, text, workspace_locator, core::sanitize_patch_result)
}

#[pyfunction]
pub fn observe_shell_heredocs<'py>(
    py: Python<'py>,
    command: &Bound<'py, PyAny>,
    max_heredocs: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let (Some(text), Some(max_heredocs)) = (exact_utf8(command), exact_count(max_heredocs, 0))
    else {
        return Ok(defer(py));
    };
    let mut triples = Vec::new();
    for (prefix, suffix, body) in core::shell_heredocs(text, max_heredocs) {
        let triple = [
            PyString::new(py, prefix).into_any(),
            PyString::new(py, suffix).into_any(),
            PyString::new(py, &body).into_any(),
        ];
        triples.push(PyTuple::new(py, triple)?);
    }
    Ok(PyList::new(py, triples)?.into_any())
}

#[pyfunction]
pub fn observe_codex_header_exit_codes<'py>(
    py: Python<'py>,
    text: &Bound<'py, PyAny>,
    max_lines: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    // ``split(sep, n)[:n]`` with ``n < 1`` slices differently; the reference decides it.
    let (Some(source), Some(max_lines)) = (exact_utf8(text), exact_count(max_lines, 1)) else {
        return Ok(defer(py));
    };
    Ok(PyTuple::new(py, core::codex_header_exit_codes(source, max_lines))?.into_any())
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(
        observe_workspace_relative_edit_path,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(observe_sanitize_patch_paths, module)?)?;
    module.add_function(wrap_pyfunction!(observe_sanitize_patch_result, module)?)?;
    module.add_function(wrap_pyfunction!(observe_shell_heredocs, module)?)?;
    module.add_function(wrap_pyfunction!(observe_codex_header_exit_codes, module)?)?;
    Ok(())
}
