//! Managed-tree digests for the integrations: `portable_plugin._tree_digest` and the data
//! checks of `codex_skill._validated_text`.
//!
//! Each function answers only for the shape the integrations build (an exact `dict` of ASCII
//! `str` paths to exact `bytes`) and returns `None` otherwise, so the caller runs its Python
//! reference and raises exactly what it raises (for example `UnicodeEncodeError` from the
//! `path.encode("ascii")` sort key).

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyString};
use yoetz_core::fswalks::managed_tree::{self as core, Member};

/// Trees above this many bytes are hashed with the GIL released.
const DETACH_BYTES: usize = 64 * 1024;

/// Borrow ASCII `(path, data)` pairs in insertion order, or `None` for any other shape.
fn members<'a>(files: &'a Bound<'_, PyAny>) -> Option<Vec<(Bound<'a, PyString>, Bound<'a, PyBytes>)>> {
    let dict = files.cast_exact::<PyDict>().ok()?;
    let mut pairs = Vec::with_capacity(dict.len());
    for (key, value) in dict.iter() {
        let key = key.cast_into_exact::<PyString>().ok()?;
        let value = value.cast_into_exact::<PyBytes>().ok()?;
        if !key.to_str().ok()?.is_ascii() {
            return None;
        }
        pairs.push((key, value));
    }
    Some(pairs)
}

/// `_tree_digest(files)`: `None` when the reference must answer (any other shape, or a path the
/// canonical encoder refuses).
#[pyfunction]
pub fn managed_tree_digest(py: Python<'_>, files: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    let Some(pairs) = members(files) else {
        return Ok(None);
    };
    let mut borrowed: Vec<Member<'_>> = Vec::with_capacity(pairs.len());
    for (path, value) in &pairs {
        let Ok(path) = path.to_str() else {
            return Ok(None);
        };
        borrowed.push(Member { path, data: value.as_bytes() });
    }
    core::sort_ascii(&mut borrowed);
    let total: usize = borrowed.iter().map(|member| member.data.len()).sum();
    let digest = if total >= DETACH_BYTES {
        py.detach(|| core::tree_digest(&borrowed))
    } else {
        core::tree_digest(&borrowed)
    };
    Ok(digest.ok())
}

/// The data checks of `codex_skill._validated_text`; `None` for anything but exact `bytes`.
#[pyfunction]
pub fn managed_validated_text_ok(data: &Bound<'_, PyAny>, limit: usize) -> Option<bool> {
    let data = data.cast_exact::<PyBytes>().ok()?;
    Some(core::validated_text_ok(data.as_bytes(), limit))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(managed_tree_digest, module)?)?;
    module.add_function(wrap_pyfunction!(managed_validated_text_ok, module)?)?;
    Ok(())
}
