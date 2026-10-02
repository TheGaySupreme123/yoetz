//! `yoetz.protocol.schemas` catalog loading: `_freeze_json`, `_uses_dynamic_reference`, and a
//! proof that lets `_validate_references` skip its Python loop.
//!
//! The two tree walks take their Python reference as `fallback` and hand it any node they do not
//! reproduce exactly (a non-`str` key, a key holding a lone surrogate, or nesting past
//! `NATIVE_RECURSION_LIMIT`), so the reference's own exception and recursion limit decide those.
//!
//! The reference proof never raises a refusal itself: it answers `True` only when every `$ref`
//! in the catalog is admissible and `referencing` 0.37 would resolve it (against the registry
//! `_build_registry` makes from the same documents), and `False` whenever it cannot tell, after
//! which the Python loop runs and raises exactly what it always raised.

use std::collections::HashMap;

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyList, PyString, PyTuple};
use yoetz_core::protocol::schema_refs::{
    RefDocument, RefFragment, Segment, is_subresource_position, list_index, split_reference, unescape_segment, utf16_cmp,
};

use crate::walk::{NATIVE_RECURSION_LIMIT, is_exact};

/// The text of a key already checked to convert (so never the empty fallback).
fn key_text<'a>(key: &'a Bound<'_, PyString>) -> &'a str {
    key.to_str().unwrap_or("")
}

fn freeze<'py>(py: Python<'py>, value: &Bound<'py, PyAny>, fallback: &Bound<'py, PyAny>, depth: usize) -> PyResult<Bound<'py, PyAny>> {
    if is_exact(value, ffi::PyDict_CheckExact) {
        if depth >= NATIVE_RECURSION_LIMIT {
            return fallback.call1((value,));
        }
        let source = unsafe { value.cast_unchecked::<PyDict>() };
        let mut entries: Vec<(Bound<'py, PyString>, Bound<'py, PyAny>)> = Vec::with_capacity(source.len());
        for (key, member) in source.iter() {
            if !is_exact(&key, ffi::PyUnicode_CheckExact) {
                // `str.encode` is the reference's to fail (or not) on this key.
                return fallback.call1((value,));
            }
            let key = unsafe { key.cast_into_unchecked::<PyString>() };
            if key.to_str().is_err() {
                // A lone surrogate does not encode to UTF-16: the reference raises.
                return fallback.call1((value,));
            }
            entries.push((key, member));
        }
        // Canonical catalog bytes already hold every object's keys in this order.
        let ordered = entries.windows(2).all(|pair| utf16_cmp(key_text(&pair[0].0), key_text(&pair[1].0)).is_lt());
        if !ordered {
            entries.sort_by(|left, right| utf16_cmp(key_text(&left.0), key_text(&right.0)));
        }
        let frozen = PyDict::new(py);
        for (key, member) in &entries {
            frozen.set_item(key, freeze(py, member, fallback, depth + 1)?)?;
        }
        return unsafe { Bound::from_owned_ptr_or_err(py, ffi::PyDictProxy_New(frozen.as_ptr())) };
    }
    if is_exact(value, ffi::PyList_CheckExact) {
        if depth >= NATIVE_RECURSION_LIMIT {
            return fallback.call1((value,));
        }
        let list = unsafe { value.cast_unchecked::<PyList>() };
        let mut members = Vec::with_capacity(list.len());
        let mut index = 0;
        // Re-read the length each step, like the reference's list iterator.
        while index < list.len() {
            members.push(freeze(py, &list.get_item(index)?, fallback, depth + 1)?);
            index += 1;
        }
        return Ok(PyTuple::new(py, members)?.into_any());
    }
    Ok(value.clone())
}

/// `_freeze_json(value)`; `fallback` is the Python reference.
#[pyfunction]
#[pyo3(name = "schemas_freeze_json")]
pub fn freeze_json<'py>(py: Python<'py>, value: &Bound<'py, PyAny>, fallback: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    freeze(py, value, fallback, 0)
}

fn uses_dynamic<'py>(value: &Bound<'py, PyAny>, fallback: &Bound<'py, PyAny>, depth: usize) -> PyResult<bool> {
    if is_exact(value, ffi::PyDict_CheckExact) {
        if depth >= NATIVE_RECURSION_LIMIT {
            return fallback.call1((value,))?.is_truthy();
        }
        let source = unsafe { value.cast_unchecked::<PyDict>() };
        for (key, item) in source.iter() {
            if !is_exact(&key, ffi::PyUnicode_CheckExact) {
                return fallback.call1((value,))?.is_truthy();
            }
            let key = unsafe { key.cast_unchecked::<PyString>() };
            // A key holding a lone surrogate equals neither marker.
            if let Ok(text) = key.to_str() {
                if text == "$dynamicRef" || text == "$recursiveRef" {
                    return Ok(true);
                }
            }
            if uses_dynamic(&item, fallback, depth + 1)? {
                return Ok(true);
            }
        }
        return Ok(false);
    }
    if is_exact(value, ffi::PyList_CheckExact) {
        if depth >= NATIVE_RECURSION_LIMIT {
            return fallback.call1((value,))?.is_truthy();
        }
        let list = unsafe { value.cast_unchecked::<PyList>() };
        let mut index = 0;
        while index < list.len() {
            if uses_dynamic(&list.get_item(index)?, fallback, depth + 1)? {
                return Ok(true);
            }
            index += 1;
        }
    }
    Ok(false)
}

/// `_uses_dynamic_reference(value)`; `fallback` is the Python reference.
#[pyfunction]
#[pyo3(name = "schemas_uses_dynamic_reference")]
pub fn uses_dynamic_reference<'py>(value: &Bound<'py, PyAny>, fallback: &Bound<'py, PyAny>) -> PyResult<bool> {
    uses_dynamic(value, fallback, 0)
}

/// Whether `referencing` resolves pointer `fragment` within `root`, with every value it asks
/// for an `$id` answering `None`. `false` means undecided.
fn pointer_resolves<'py>(py: Python<'py>, root: &Bound<'py, PyAny>, fragment: &str) -> PyResult<bool> {
    let mut contents = root.clone();
    let mut segments: Vec<Segment> = Vec::new();
    for raw in fragment[1..].split('/') {
        if is_exact(&contents, ffi::PyList_CheckExact) {
            let Some(index) = list_index(raw) else {
                return Ok(false);
            };
            let list = unsafe { contents.cast_unchecked::<PyList>() };
            if index >= list.len() {
                return Ok(false);
            }
            let next = list.get_item(index)?;
            contents = next;
            segments.push(Segment::Index(index));
        } else if is_exact(&contents, ffi::PyDict_CheckExact) {
            let key = unescape_segment(raw);
            let dict = unsafe { contents.cast_unchecked::<PyDict>() };
            let Some(next) = dict.get_item(PyString::new(py, &key))? else {
                return Ok(false);
            };
            contents = next;
            segments.push(Segment::Key(key));
        } else {
            // A string is a `Sequence` there, anything else fails to index.
            return Ok(false);
        }
        if is_subresource_position(&segments) {
            if contents.is_instance_of::<PyBool>() {
                continue;
            }
            if !is_exact(&contents, ffi::PyDict_CheckExact) {
                // `contents.get("$id")` would raise.
                return Ok(false);
            }
            if unsafe { contents.cast_unchecked::<PyDict>() }.contains(pyo3::intern!(py, "$id"))? {
                // A nested base URI: leave it to `referencing`.
                return Ok(false);
            }
        }
    }
    Ok(true)
}

fn reference_resolves<'py>(
    py: Python<'py>,
    documents: &HashMap<String, Bound<'py, PyAny>>,
    current: &Bound<'py, PyAny>,
    reference: &str,
) -> PyResult<bool> {
    let Some((document, fragment)) = split_reference(reference) else {
        return Ok(false);
    };
    let root = match document {
        RefDocument::Local => current,
        RefDocument::External(base) => match documents.get(base) {
            Some(root) => root,
            None => return Ok(false),
        },
    };
    match fragment {
        RefFragment::Root => Ok(true),
        RefFragment::Pointer(pointer) => pointer_resolves(py, root, pointer),
    }
}

/// `True` when `_validate_references(plain_by_id, _build_registry(plain_by_id))` provably
/// returns without raising; `False` when only the Python loop can tell.
#[pyfunction]
#[pyo3(name = "schemas_references_resolvable")]
pub fn references_resolvable<'py>(py: Python<'py>, plain_by_id: &Bound<'py, PyAny>) -> PyResult<bool> {
    if !is_exact(plain_by_id, ffi::PyDict_CheckExact) {
        return Ok(false);
    }
    let source = unsafe { plain_by_id.cast_unchecked::<PyDict>() };
    let mut documents: HashMap<String, Bound<'py, PyAny>> = HashMap::with_capacity(source.len());
    for (schema_id, plain) in source.iter() {
        if !is_exact(&schema_id, ffi::PyUnicode_CheckExact) || !is_exact(&plain, ffi::PyDict_CheckExact) {
            return Ok(false);
        }
        let Ok(text) = unsafe { schema_id.cast_unchecked::<PyString>() }.to_str() else {
            return Ok(false);
        };
        documents.insert(text.to_owned(), plain);
    }
    for current in documents.values() {
        let mut stack: Vec<Bound<'py, PyAny>> = vec![current.clone()];
        while let Some(node) = stack.pop() {
            if is_exact(&node, ffi::PyDict_CheckExact) {
                for (key, item) in unsafe { node.cast_unchecked::<PyDict>() }.iter() {
                    if !is_exact(&key, ffi::PyUnicode_CheckExact) {
                        return Ok(false);
                    }
                    if unsafe { key.cast_unchecked::<PyString>() }.to_str().is_ok_and(|text| text == "$ref") {
                        if !is_exact(&item, ffi::PyUnicode_CheckExact) {
                            return Ok(false);
                        }
                        let Ok(reference) = unsafe { item.cast_unchecked::<PyString>() }.to_str() else {
                            return Ok(false);
                        };
                        if !reference_resolves(py, &documents, current, reference)? {
                            return Ok(false);
                        }
                        continue;
                    }
                    stack.push(item);
                }
            } else if is_exact(&node, ffi::PyList_CheckExact) {
                for item in unsafe { node.cast_unchecked::<PyList>() }.iter() {
                    stack.push(item);
                }
            }
        }
    }
    Ok(true)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(freeze_json, module)?)?;
    module.add_function(wrap_pyfunction!(uses_dynamic_reference, module)?)?;
    module.add_function(wrap_pyfunction!(references_resolvable, module)?)?;
    Ok(())
}
