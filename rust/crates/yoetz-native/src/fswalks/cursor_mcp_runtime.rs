//! `yoetz.adapters.integrations.cursor_mcp_runtime._linux_snapshots` and the argv classifiers it
//! uses (`classify_serve_argv`, `_cursor_helper_comm`).
//!
//! The Python module keeps its public classifiers; the scan twin returns plain rows the module
//! turns into `CursorMcpProcessSnapshot` values. `False` asks the caller to use the reference
//! (a launcher tuple holding a string that is not valid UTF-8).

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyList, PyString, PyTuple};
use yoetz_core::fswalks::cursor_mcp_runtime::{self as core, Launcher};

fn launcher_strings(expected: Option<&Bound<'_, PyTuple>>) -> Option<Option<Vec<String>>> {
    let Some(expected) = expected else {
        return Some(None);
    };
    let mut parts = Vec::with_capacity(expected.len());
    for part in expected.iter() {
        let text = part.cast::<PyString>().ok()?;
        parts.push(text.to_str().ok()?.to_owned());
    }
    Some(Some(parts))
}

/// `_linux_snapshots(expected_launcher)` rows: `(cursor_helper, route | None, launcher | None)`.
#[pyfunction]
pub fn cursor_linux_snapshots<'py>(
    py: Python<'py>,
    proc_root: &str,
    expected_launcher: Option<&Bound<'py, PyTuple>>,
    max_processes: usize,
) -> PyResult<Bound<'py, PyAny>> {
    let Some(expected) = launcher_strings(expected_launcher) else {
        return Ok(PyBool::new(py, false).to_owned().into_any());
    };
    let root = std::path::PathBuf::from(proc_root);
    let scanned = py.detach(|| core::linux_snapshots(&root, expected.as_deref(), max_processes));
    let Some(rows) = scanned else {
        return Ok(py.None().into_bound(py));
    };
    let list = PyList::empty(py);
    for row in rows {
        let route = match row.kind {
            core::Kind::Foreign => None,
            kind => Some(kind.as_str()),
        };
        list.append((row.cursor_helper, route, row.launcher.map(Launcher::as_str)))?;
    }
    Ok(list.into_any())
}

/// `classify_serve_argv(tokens, expected_launcher)` over `str` tokens (parity probes only; the
/// module keeps the Python classifier as its public name).
#[pyfunction]
pub fn cursor_classify_serve_argv(
    tokens: Vec<String>,
    expected_launcher: Option<Vec<String>>,
) -> (Option<&'static str>, Option<&'static str>) {
    let (kind, launcher) = core::classify_serve_argv(&tokens, expected_launcher.as_deref());
    (kind.map(core::Kind::as_str), launcher.map(Launcher::as_str))
}

/// `_cursor_helper_comm(value)` for a `str` (parity probes only).
#[pyfunction]
pub fn cursor_helper_comm(value: &str) -> bool {
    core::cursor_helper_comm(value)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(cursor_linux_snapshots, module)?)?;
    module.add_function(wrap_pyfunction!(cursor_classify_serve_argv, module)?)?;
    module.add_function(wrap_pyfunction!(cursor_helper_comm, module)?)?;
    Ok(())
}
