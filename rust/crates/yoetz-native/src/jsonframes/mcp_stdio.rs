//! `yoetz.adapters.mcp_stdio._parse_frame` up to (not including) JSON-RPC model validation.

use pyo3::prelude::*;
use yoetz_core::protocol::json_compat::{self, CompatLimits, MAX_COMPAT_DEPTH};

use super::{big_int, exact_bytes, members_to_dict};

/// The parsed root object of an inbound frame, or `None` when the reference must decide.
///
/// Admits exactly what `_parse_frame` admits before `JSONRPCMessage.model_validate`: a
/// non-empty, BOM-free, NUL-free, UTF-8 frame that `json.loads` decodes (no `NaN`/`Infinity`
/// constant, no duplicate key, any integer `int()` builds, floats as `float()`, overflow to
/// infinity), whose root is an object, whose text is all encodable, and whose containers sit
/// at most `max_nesting` deep counting the root as 1 (`_validate_tree`).
#[pyfunction]
pub fn mcp_stdio_accept_frame<'py>(
    py: Python<'py>,
    frame: &Bound<'py, PyAny>,
    max_nesting: i64,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let Some(raw) = exact_bytes(frame) else {
        return Ok(None);
    };
    let Some(container_depth) = usize::try_from(max_nesting)
        .ok()
        .filter(|depth| (1..=MAX_COMPAT_DEPTH).contains(depth))
        .map(|depth| depth - 1)
    else {
        return Ok(None);
    };
    let limits = CompatLimits {
        max_value_depth: container_depth + 1,
        max_container_depth: container_depth,
        allow_overflow: true,
    };
    match json_compat::accept_object_line(raw, limits, big_int(py)) {
        Some(members) => Ok(Some(members_to_dict(py, members)?.into_any())),
        None => Ok(None),
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(mcp_stdio_accept_frame, module)?)?;
    Ok(())
}
