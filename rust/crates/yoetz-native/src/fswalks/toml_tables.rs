//! `yoetz.adapters.integrations.toml_tables` over exact `bytes` and UTF-8 encodable `str`.
//!
//! The Python wrappers only call these for exact `bytes` input and a non-empty table; anything
//! else stays on the reference. A table with a lone surrogate raises the same strict-codec
//! `UnicodeEncodeError` the reference's `table.encode("utf-8")` raises.

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyString};
use yoetz_core::fswalks::toml_tables as core;

/// `exact_table_span(raw, table)`.
#[pyfunction]
pub fn toml_exact_table_span(raw: &[u8], table: &Bound<'_, PyString>) -> PyResult<Option<(usize, usize)>> {
    Ok(core::exact_table_span(raw, table.to_str()?.as_bytes()))
}

/// `strip_exact_table(raw, table)`; `raw` itself when nothing is removed.
#[pyfunction]
pub fn toml_strip_exact_table<'py>(
    py: Python<'py>,
    raw: &Bound<'py, PyBytes>,
    table: &Bound<'py, PyString>,
) -> PyResult<Bound<'py, PyAny>> {
    match core::strip_exact_table(raw.as_bytes(), table.to_str()?.as_bytes()) {
        Some(stripped) => Ok(PyBytes::new(py, &stripped).into_any()),
        None => Ok(raw.clone().into_any()),
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(toml_exact_table_span, module)?)?;
    module.add_function(wrap_pyfunction!(toml_strip_exact_table, module)?)?;
    Ok(())
}
