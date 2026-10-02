//! `yoetz.adapters.importers.codex_jsonl._parse_json_line` (with `_validate_json_tree`).

use pyo3::prelude::*;
use yoetz_core::protocol::json_compat;

use super::{big_int, exact_bytes, members_to_dict, value_depth_limits};

/// The decoded line object, or `None` when the reference must decide.
///
/// Admits exactly what `_parse_json_line` returns: a non-empty, NUL-free, BOM-free UTF-8 line
/// that `json.loads` decodes with no duplicate key and no `NaN`/`Infinity` constant, whose
/// floats are finite, whose text is encodable, whose values sit at most `max_depth` deep, and
/// whose root is an object.
#[pyfunction]
pub fn codex_jsonl_accept_line<'py>(
    py: Python<'py>,
    content: &Bound<'py, PyAny>,
    max_depth: i64,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let (Some(raw), Some(limits)) = (exact_bytes(content), value_depth_limits(max_depth)) else {
        return Ok(None);
    };
    match json_compat::accept_object_line(raw, limits, big_int(py)) {
        Some(members) => Ok(Some(members_to_dict(py, members)?.into_any())),
        None => Ok(None),
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(codex_jsonl_accept_line, module)?)?;
    Ok(())
}
