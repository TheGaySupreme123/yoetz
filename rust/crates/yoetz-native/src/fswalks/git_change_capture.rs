//! `yoetz.adapters.git_change_capture._new_file_diff`.

use pyo3::prelude::*;
use pyo3::types::PyBytes;
use yoetz_core::fswalks::git_change_capture as core;

/// `_new_file_diff(path, content, executable)` with the module's `_BINARY_PROBE_BYTES`.
#[pyfunction]
pub fn git_new_file_diff<'py>(
    py: Python<'py>,
    path: &[u8],
    content: &[u8],
    executable: bool,
    binary_probe: usize,
) -> (Bound<'py, PyBytes>, String) {
    let (text, added) = core::new_file_diff(path, content, executable, binary_probe);
    (PyBytes::new(py, &text), added)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(git_new_file_diff, module)?)?;
    Ok(())
}
