//! `yoetz.service.bundle_upgrade` row digests: `_cell_value`, `_sorted_row_values`, and
//! `_stream_rows_digest`.
//!
//! A bundle verification streams every row of every table through `_cell_value` and
//! `canonical_encode` into one SHA-256. The twin iterates the cursor itself, encodes each row's
//! canonical bytes directly into a reused buffer, and feeds the digest, producing the reference's
//! exact digest and count.
//!
//! Refusal order follows the reference: every cell of a row is normalized by `_cell_value` (a
//! cell of an unsupported type raises `BundleUpgradeError(VERIFICATION_FAILED, {"check":
//! "value"})` at once) before `canonical_encode` sees the row (whose first offending cell raises
//! the canonical `ProtocolValueError`). The twins run only while the module's `canonical_encode`
//! and `_cell_value` globals are the originals bound at import; otherwise they call the Python
//! reference.

use pyo3::exceptions::PyRuntimeError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyString, PyTuple};
use sha2::{Digest, Sha256};
use yoetz_core::protocol::canonical::{self as core, MAX_SAFE_INTEGER, Reason};

use crate::registry::{Slot, protocol_error};
use crate::walk::is_exact;

static NAMESPACE: Slot = Slot::new();
static ORIGINAL_ENCODE: Slot = Slot::new();
static ORIGINAL_CELL_VALUE: Slot = Slot::new();
static REFERENCE_STREAM: Slot = Slot::new();
static REFERENCE_SORTED: Slot = Slot::new();

/// Bind the module namespace, the original `canonical_encode` and (native) `_cell_value`
/// globals, and the Python `_stream_rows_digest` and `_sorted_row_values` references.
#[pyfunction]
#[pyo3(name = "bundle_bind_digests")]
pub fn bind_digests(
    namespace: Bound<'_, PyDict>,
    original_encode: Bound<'_, PyAny>,
    original_cell_value: Bound<'_, PyAny>,
    reference_stream: Bound<'_, PyAny>,
    reference_sorted: Bound<'_, PyAny>,
) {
    NAMESPACE.set(namespace.into_any().unbind());
    ORIGINAL_ENCODE.set(original_encode.unbind());
    ORIGINAL_CELL_VALUE.set(original_cell_value.unbind());
    REFERENCE_STREAM.set(reference_stream.unbind());
    REFERENCE_SORTED.set(reference_sorted.unbind());
}

fn unbound() -> PyErr {
    PyRuntimeError::new_err("bundle_digests_unbound")
}

fn namespace(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    Ok(NAMESPACE
        .get(py)
        .ok_or_else(unbound)?
        .cast_into::<PyDict>()?)
}

fn global<'py>(namespace: &Bound<'py, PyDict>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    match namespace.get_item(PyString::intern(namespace.py(), name))? {
        Some(value) => Ok(value),
        None => Err(pyo3::exceptions::PyNameError::new_err(format!(
            "name '{name}' is not defined"
        ))),
    }
}

/// Whether the twins may run: both dependency globals are still the bound originals.
fn dependencies_original(py: Python<'_>, namespace: &Bound<'_, PyDict>) -> bool {
    let same = |name: &str, slot: &Slot| -> bool {
        match (namespace.get_item(PyString::intern(py, name)), slot.get(py)) {
            (Ok(Some(current)), Some(original)) => current.is(&original),
            _ => {
                let _ = PyErr::take(py);
                false
            }
        }
    };
    same("canonical_encode", &ORIGINAL_ENCODE) && same("_cell_value", &ORIGINAL_CELL_VALUE)
}

/// `BundleUpgradeError(BundleUpgradeReason.VERIFICATION_FAILED, False, {"check": check})`.
fn verification_failed(py: Python<'_>, namespace: &Bound<'_, PyDict>, check: &str) -> PyErr {
    let build = || -> PyResult<PyErr> {
        let reason = global(namespace, "BundleUpgradeReason")?.getattr("VERIFICATION_FAILED")?;
        let details = PyDict::new(py);
        details.set_item("check", check)?;
        let error = global(namespace, "BundleUpgradeError")?.call1((reason, false, details))?;
        Ok(PyErr::from_value(error))
    };
    match build() {
        Ok(error) | Err(error) => error,
    }
}

/// One `_cell_value` result, kept in a form both the Python value and its canonical bytes come
/// from.
enum Cell<'py> {
    Null,
    Bool(bool),
    Int(Bound<'py, PyAny>),
    Str(Bound<'py, PyString>),
    Blob { digest: String, size: usize },
}

/// `_cell_value(value)`; `None` marks the reference's `{"check": "value"}` refusal.
fn cell<'py>(value: &Bound<'py, PyAny>) -> Option<Cell<'py>> {
    if value.is_none() {
        return Some(Cell::Null);
    }
    if is_exact(value, ffi::PyUnicode_CheckExact) {
        return Some(Cell::Str(
            unsafe { value.cast_unchecked::<PyString>() }.clone(),
        ));
    }
    if is_exact(value, ffi::PyLong_CheckExact) {
        return Some(Cell::Int(value.clone()));
    }
    if is_exact(value, ffi::PyBool_Check) {
        return Some(Cell::Bool(value.as_ptr() == unsafe { ffi::Py_True() }));
    }
    if is_exact(value, ffi::PyBytes_CheckExact) {
        let raw = unsafe { value.cast_unchecked::<PyBytes>() }.as_bytes();
        let digest = hex::encode(Sha256::digest(raw));
        return Some(Cell::Blob {
            digest,
            size: raw.len(),
        });
    }
    None
}

/// The first NUL or lone surrogate in a string that failed UTF-8 conversion.
fn first_offender(text: &Bound<'_, PyString>) -> Reason {
    let pointer = text.as_ptr();
    let length = unsafe { ffi::PyUnicode_GetLength(pointer) };
    for index in 0..length.max(0) {
        let point = unsafe { ffi::PyUnicode_ReadChar(pointer, index) };
        if point == 0 {
            return core::NUL_BYTE_FORBIDDEN;
        }
        if (0xD800..=0xDFFF).contains(&point) {
            return core::LONE_SURROGATE;
        }
    }
    core::LONE_SURROGATE
}

fn encode_text(out: &mut Vec<u8>, text: &Bound<'_, PyString>) -> Result<(), Reason> {
    match text.to_str() {
        Ok(slice) => core::encode_str_into(out, slice),
        Err(_) => Err(first_offender(text)),
    }
}

/// Append `canonical_encode(cell)`; the first refusal is the canonical reason.
fn encode_cell(out: &mut Vec<u8>, cell: &Cell<'_>) -> Result<(), Reason> {
    match cell {
        Cell::Null => out.extend_from_slice(b"null"),
        Cell::Bool(true) => out.extend_from_slice(b"true"),
        Cell::Bool(false) => out.extend_from_slice(b"false"),
        Cell::Int(value) => {
            let mut overflow: std::os::raw::c_int = 0;
            let number =
                unsafe { ffi::PyLong_AsLongLongAndOverflow(value.as_ptr(), &mut overflow) };
            if overflow != 0 || !(-MAX_SAFE_INTEGER..=MAX_SAFE_INTEGER).contains(&number) {
                return Err(core::INTEGER_OUT_OF_SAFE_RANGE);
            }
            core::push_int(out, number);
        }
        Cell::Str(text) => encode_text(out, text)?,
        Cell::Blob { digest, size } => {
            // `{"blob_digest": ..., "blob_size": ...}`: already in canonical key order.
            out.extend_from_slice(b"{\"blob_digest\":\"sha256:");
            out.extend_from_slice(digest.as_bytes());
            out.extend_from_slice(b"\",\"blob_size\":");
            core::push_int(out, *size as i64);
            out.push(b'}');
        }
    }
    Ok(())
}

/// Normalize every member of one row; `None` is the first unsupported cell.
fn row_cells<'py>(
    members: impl Iterator<Item = PyResult<Bound<'py, PyAny>>>,
) -> PyResult<Option<Vec<Cell<'py>>>> {
    let mut cells = Vec::new();
    for member in members {
        match cell(&member?) {
            Some(value) => cells.push(value),
            None => return Ok(None),
        }
    }
    Ok(Some(cells))
}

/// Append `canonical_encode(tuple(cells))`.
fn encode_row(out: &mut Vec<u8>, cells: &[Cell<'_>]) -> Result<(), Reason> {
    out.push(b'[');
    for (index, value) in cells.iter().enumerate() {
        if index > 0 {
            out.push(b',');
        }
        encode_cell(out, value)?;
    }
    out.push(b']');
    Ok(())
}

/// The Python value `_cell_value` returns for `cell`.
fn cell_object<'py>(
    py: Python<'py>,
    namespace: &Bound<'py, PyDict>,
    value: Cell<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    Ok(match value {
        Cell::Null => py.None().into_bound(py),
        Cell::Bool(truth) => pyo3::types::PyBool::new(py, truth).to_owned().into_any(),
        Cell::Int(value) => value,
        Cell::Str(text) => text.into_any(),
        Cell::Blob { digest, size } => {
            let members = PyDict::new(py);
            members.set_item("blob_digest", format!("sha256:{digest}"))?;
            members.set_item("blob_size", size)?;
            global(namespace, "JsonObject")?.call1((members,))?
        }
    })
}

/// `_cell_value(value)`.
#[pyfunction]
#[pyo3(name = "bundle_cell_value")]
pub fn cell_value<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let namespace = namespace(py)?;
    match cell(value) {
        Some(Cell::Null | Cell::Bool(_) | Cell::Int(_) | Cell::Str(_)) => Ok(value.clone()),
        Some(blob) => cell_object(py, &namespace, blob),
        None => Err(verification_failed(py, &namespace, "value")),
    }
}

/// `_sorted_row_values(rows)`.
#[pyfunction]
#[pyo3(name = "bundle_sorted_row_values")]
pub fn sorted_row_values<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let namespace = namespace(py)?;
    if !dependencies_original(py, &namespace) {
        return REFERENCE_SORTED.get(py).ok_or_else(unbound)?.call1((rows,));
    }
    let mut normalized: Vec<Vec<Cell<'py>>> = Vec::new();
    for row in rows.try_iter()? {
        let row = row?;
        match row_cells(row.try_iter()?)? {
            Some(cells) => normalized.push(cells),
            None => return Err(verification_failed(py, &namespace, "value")),
        }
    }
    let mut keyed = Vec::with_capacity(normalized.len());
    for cells in normalized {
        let mut key = Vec::new();
        encode_row(&mut key, &cells).map_err(|reason| protocol_error(py, reason))?;
        keyed.push((key, cells));
    }
    // `sorted` is stable and compares the key bytes lexicographically, exactly like `Vec<u8>`.
    keyed.sort_by(|left, right| left.0.cmp(&right.0));
    let mut out = Vec::with_capacity(keyed.len());
    for (_, cells) in keyed {
        let mut members = Vec::with_capacity(cells.len());
        for value in cells {
            members.push(cell_object(py, &namespace, value)?);
        }
        out.push(PyTuple::new(py, members)?);
    }
    Ok(PyTuple::new(py, out)?.into_any())
}

/// `_stream_rows_digest(cursor, *, table) -> (digest, count)`.
#[pyfunction]
#[pyo3(name = "bundle_stream_rows_digest", signature = (cursor, *, table))]
pub fn stream_rows_digest<'py>(
    py: Python<'py>,
    cursor: &Bound<'py, PyAny>,
    table: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let namespace = namespace(py)?;
    if !dependencies_original(py, &namespace) || !is_exact(table, ffi::PyUnicode_CheckExact) {
        let kwargs = PyDict::new(py);
        kwargs.set_item("table", table)?;
        return REFERENCE_STREAM
            .get(py)
            .ok_or_else(unbound)?
            .call((cursor,), Some(&kwargs));
    }
    let table = unsafe { table.cast_unchecked::<PyString>() };
    let mut digest = Sha256::new();
    digest.update(b"{\"rows\":[");
    let mut count: u64 = 0;
    let mut buffer = Vec::with_capacity(1024);
    for raw in cursor.try_iter()? {
        let raw = raw?;
        if !is_exact(&raw, ffi::PyTuple_CheckExact) {
            return Err(verification_failed(py, &namespace, "row"));
        }
        let row = unsafe { raw.cast_unchecked::<PyTuple>() };
        let Some(cells) = row_cells(row.iter().map(Ok))? else {
            return Err(verification_failed(py, &namespace, "value"));
        };
        buffer.clear();
        if count > 0 {
            buffer.push(b',');
        }
        encode_row(&mut buffer, &cells).map_err(|reason| protocol_error(py, reason))?;
        digest.update(&buffer);
        count += 1;
    }
    buffer.clear();
    buffer.extend_from_slice(b"],\"table\":");
    encode_text(&mut buffer, table).map_err(|reason| protocol_error(py, reason))?;
    buffer.push(b'}');
    digest.update(&buffer);
    let text = format!("sha256:{}", hex::encode(digest.finalize()));
    Ok((text, count).into_pyobject(py)?.into_any())
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_digests, module)?)?;
    module.add_function(wrap_pyfunction!(cell_value, module)?)?;
    module.add_function(wrap_pyfunction!(sorted_row_values, module)?)?;
    module.add_function(wrap_pyfunction!(stream_rows_digest, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rows_without_python_cells_encode_canonically() {
        let cells = [
            Cell::Null,
            Cell::Bool(true),
            Cell::Bool(false),
            Cell::Blob {
                digest: hex::encode(Sha256::digest(b"")),
                size: 0,
            },
        ];
        let mut out = Vec::new();
        encode_row(&mut out, &cells).unwrap();
        assert_eq!(
            out,
            b"[null,true,false,{\"blob_digest\":\"sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\",\"blob_size\":0}]"
        );
        let mut empty = Vec::new();
        encode_row(&mut empty, &[]).unwrap();
        assert_eq!(empty, b"[]");
    }
}
