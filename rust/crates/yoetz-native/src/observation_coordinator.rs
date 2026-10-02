//! Captured-content kernels of `yoetz.application.observation_coordinator`:
//! `_captured_content_manifest` (base64, canonical JSON and the content digest in one pass) and
//! `_verified_captured_content` (strict parse, canonical round trip, validated base64 decode and
//! the content digest).
//!
//! Both answer only for inputs they reproduce byte-identically and return `None` otherwise, so
//! the Python wrapper runs the reference for every other input.

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyInt, PyTuple};
use yoetz_core::application::observation_coordinator::{self as core, ManifestFields};
use yoetz_core::protocol::canonical::Value;

use crate::observation_materialize::exact_str;

fn exact_int(value: &Bound<'_, PyAny>) -> Option<i64> {
    if unsafe { ffi::PyLong_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    value.extract::<i64>().ok()
}

/// `(canonical manifest bytes, "sha256:" content digest)` for one stored chunk, or `None`.
#[pyfunction]
pub fn coordinator_captured_content_manifest<'py>(py: Python<'py>, chunk: &Bound<'py, PyAny>) -> PyResult<Option<(Bound<'py, PyBytes>, String)>> {
    let kind = chunk.getattr(pyo3::intern!(py, "content_kind"))?.getattr(pyo3::intern!(py, "value"))?;
    let correlation = chunk.getattr(pyo3::intern!(py, "correlation_identity"))?;
    let source = chunk.getattr(pyo3::intern!(py, "source_commitment"))?;
    let media = chunk.getattr(pyo3::intern!(py, "media_type"))?;
    let part_index = chunk.getattr(pyo3::intern!(py, "part_index"))?;
    let part_count = chunk.getattr(pyo3::intern!(py, "part_count"))?;
    let redacted = chunk.getattr(pyo3::intern!(py, "redacted"))?;
    let content = chunk.getattr(pyo3::intern!(py, "content"))?;
    let (Some(content_kind), Some(correlation_identity), Some(source_commitment), Some(media_type), Some(part_index), Some(part_count)) = (
        exact_str(&kind),
        exact_str(&correlation),
        exact_str(&source),
        exact_str(&media),
        exact_int(&part_index),
        exact_int(&part_count),
    ) else {
        return Ok(None);
    };
    if unsafe { ffi::PyBool_Check(redacted.as_ptr()) } == 0 || unsafe { ffi::PyBytes_CheckExact(content.as_ptr()) } == 0 {
        return Ok(None);
    }
    let fields = ManifestFields {
        content_kind,
        correlation_identity,
        source_commitment,
        media_type,
        part_index,
        part_count,
        redacted: redacted.is_truthy()?,
    };
    let data = unsafe { content.cast_unchecked::<PyBytes>() }.as_bytes();
    Ok(core::encode_manifest(&fields, data).ok().map(|(encoded, digest)| (PyBytes::new(py, &encoded), digest)))
}

fn scalar<'py>(py: Python<'py>, value: &Value) -> Option<Bound<'py, PyAny>> {
    Some(match value {
        Value::Null => py.None().into_bound(py),
        Value::Bool(flag) => PyBool::new(py, *flag).to_owned().into_any(),
        Value::Int(number) => PyInt::new(py, *number).into_any(),
        Value::Str(text) => pyo3::types::PyString::new(py, text).into_any(),
        Value::Array(_) | Value::Object(_) => return None,
    })
}

/// `(content_kind, part_index, part_count, redacted, correlation_identity, source_commitment,
/// content_digest, content_bytes)` read from one verified manifest object, or `None`.
#[pyfunction]
pub fn coordinator_verified_captured_content<'py>(py: Python<'py>, material: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyTuple>>> {
    if unsafe { ffi::PyBytes_CheckExact(material.as_ptr()) } == 0 {
        return Ok(None);
    }
    let raw = unsafe { material.cast_unchecked::<PyBytes>() }.as_bytes();
    let Some(verified) = core::verify_manifest(raw) else {
        return Ok(None);
    };
    let (Some(kind), Some(index), Some(count), Some(correlation), Some(source)) = (
        scalar(py, &verified.content_kind),
        scalar(py, &verified.part_index),
        scalar(py, &verified.part_count),
        scalar(py, &verified.correlation_identity),
        scalar(py, &verified.source_commitment),
    ) else {
        return Ok(None);
    };
    let items: [Bound<'py, PyAny>; 8] = [
        kind,
        index,
        count,
        PyBool::new(py, verified.redacted).to_owned().into_any(),
        correlation,
        source,
        pyo3::types::PyString::new(py, &verified.content_digest).into_any(),
        PyInt::new(py, verified.content_bytes as i64).into_any(),
    ];
    Ok(Some(PyTuple::new(py, items)?))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(coordinator_captured_content_manifest, module)?)?;
    module.add_function(wrap_pyfunction!(coordinator_verified_captured_content, module)?)?;
    Ok(())
}
