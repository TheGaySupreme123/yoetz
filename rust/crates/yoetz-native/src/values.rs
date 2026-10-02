//! `yoetz.domain.values` freezing (`freeze_json`, `_freeze_json`) over live Python objects.
//!
//! The twin builds the same `JsonObject` instances the reference builds (`object.__new__` plus
//! the two slot assignments `object.__setattr__` performs) and raises the same
//! `ProtocolValueError` for the first offending input in the reference's order: a mapping's keys
//! are all validated in iteration order, then its values are all read, then each value is frozen.
//! Container nesting is bounded by `MAX_JSON_DEPTH` before any member is visited, so the native
//! recursion is bounded too.

use pyo3::exceptions::PyException;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString, PyTuple};
use yoetz_core::protocol::canonical::{self as core, MAX_JSON_DEPTH, MAX_SAFE_INTEGER, Reason};

use crate::registry::{PROTOCOL_VALUE_ERROR, protocol_error};
use crate::walk::{
    JSON_OBJECT, is_actual_mapping, is_exact, is_type, json_object_items, protocol_error_from,
};

/// Bind `JsonObject`.
#[pyfunction]
#[pyo3(name = "values_bind_json_object")]
pub fn bind_json_object(class: Bound<'_, PyAny>) {
    JSON_OBJECT.set(class.unbind());
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

/// `ensure_canonical_value(text)` for an exact `str`.
fn ensure_text(py: Python<'_>, text: &Bound<'_, PyString>) -> PyResult<()> {
    match text.to_str() {
        Ok(slice) => core::validate_str(slice).map_err(|reason| protocol_error(py, reason)),
        Err(_) => Err(protocol_error(py, first_offender(text))),
    }
}

/// `_validate_object_key(value)`.
fn validate_object_key<'py>(
    py: Python<'py>,
    key: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyString>> {
    if !is_exact(key, ffi::PyUnicode_CheckExact) {
        return Err(protocol_error(py, core::OBJECT_KEY_NOT_STRING));
    }
    let text = unsafe { key.cast_unchecked::<PyString>() };
    ensure_text(py, text)?;
    Ok(text.clone())
}

/// Map an exception raised while reading a non-dict mapping the way the reference's
/// `except ProtocolValueError: raise / except Exception: raise ProtocolValueError(...) from exc`
/// does; a `BaseException` that is not an `Exception` propagates untouched.
fn mapping_read_error(py: Python<'_>, error: PyErr) -> PyErr {
    if let Some(class) = PROTOCOL_VALUE_ERROR.get(py) {
        if error.matches(py, &class).unwrap_or(false) {
            return error;
        }
    }
    if error.is_instance_of::<PyException>(py) {
        return protocol_error_from(py, core::UNSUPPORTED_JSON_TYPE, error);
    }
    error
}

/// The members of a mapping: every key validated (in iteration order), then every value read.
fn mapping_members<'py>(
    py: Python<'py>,
    mapping: &Bound<'py, PyAny>,
) -> PyResult<Vec<(Bound<'py, PyString>, Bound<'py, PyAny>)>> {
    if is_exact(mapping, ffi::PyDict_CheckExact) {
        let dict = unsafe { mapping.cast_unchecked::<PyDict>() };
        let mut keys = Vec::with_capacity(dict.len());
        for key in dict.keys() {
            keys.push(validate_object_key(py, &key)?);
        }
        let mut members = Vec::with_capacity(keys.len());
        for key in keys {
            // A dict cannot hold a duplicate key; a key validated above is still present
            // unless a key's own hooks mutated the dict, which an exact str cannot.
            let value = dict
                .get_item(&key)?
                .ok_or_else(|| protocol_error(py, core::UNSUPPORTED_JSON_TYPE))?;
            members.push((key, value));
        }
        return Ok(members);
    }
    let mut keys: Vec<Bound<'py, PyString>> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let iterator = mapping
        .try_iter()
        .map_err(|error| mapping_read_error(py, error))?;
    for raw_key in iterator {
        let raw_key = raw_key.map_err(|error| mapping_read_error(py, error))?;
        let key = validate_object_key(py, &raw_key)?;
        // Validated keys are exact str with valid UTF-8.
        let text = key.to_str()?.to_owned();
        if !seen.insert(text) {
            return Err(protocol_error(py, core::DUPLICATE_OBJECT_KEY));
        }
        keys.push(key);
    }
    let mut members = Vec::with_capacity(keys.len());
    for key in keys {
        let value = mapping
            .get_item(&key)
            .map_err(|error| mapping_read_error(py, error))?;
        members.push((key, value));
    }
    Ok(members)
}

/// `object.__new__(JsonObject)` with `_items` and `_index` set as `object.__setattr__` sets them.
pub fn new_json_object<'py>(
    py: Python<'py>,
    class: &Bound<'py, PyAny>,
    pairs: Vec<(Bound<'py, PyString>, Bound<'py, PyAny>)>,
) -> PyResult<Bound<'py, PyAny>> {
    let index = PyDict::new(py);
    let mut items = Vec::with_capacity(pairs.len());
    for (key, value) in pairs {
        index.set_item(&key, &value)?;
        items.push(PyTuple::new(py, [key.into_any(), value])?);
    }
    let items = PyTuple::new(py, items)?;
    unsafe {
        let empty = PyTuple::empty(py);
        let new = (*std::ptr::addr_of!(ffi::PyBaseObject_Type))
            .tp_new
            .expect("object.__new__");
        let instance = new(
            class.as_ptr().cast::<ffi::PyTypeObject>(),
            empty.as_ptr(),
            std::ptr::null_mut(),
        );
        let instance = Bound::from_owned_ptr_or_err(py, instance)?;
        let proxy = Bound::from_owned_ptr_or_err(py, ffi::PyDictProxy_New(index.as_ptr()))?;
        if ffi::PyObject_GenericSetAttr(
            instance.as_ptr(),
            pyo3::intern!(py, "_items").as_ptr(),
            items.as_ptr(),
        ) < 0
        {
            return Err(PyErr::fetch(py));
        }
        if ffi::PyObject_GenericSetAttr(
            instance.as_ptr(),
            pyo3::intern!(py, "_index").as_ptr(),
            proxy.as_ptr(),
        ) < 0
        {
            return Err(PyErr::fetch(py));
        }
        Ok(instance)
    }
}

fn check_frozen_depth(
    py: Python<'_>,
    class: &Bound<'_, PyAny>,
    value: &Bound<'_, PyAny>,
    depth: usize,
) -> PyResult<()> {
    if depth >= MAX_JSON_DEPTH {
        return Err(protocol_error(py, core::NESTING_TOO_DEEP));
    }
    for pair in json_object_items(value)?.iter() {
        let item = pair.cast::<PyTuple>()?.get_item(1)?;
        check_member_depth(py, class, &item, depth + 1)?;
    }
    Ok(())
}

fn check_member_depth(
    py: Python<'_>,
    class: &Bound<'_, PyAny>,
    item: &Bound<'_, PyAny>,
    depth: usize,
) -> PyResult<()> {
    if is_type(item, class) {
        check_frozen_depth(py, class, item, depth)
    } else if is_exact(item, ffi::PyTuple_CheckExact) {
        if depth >= MAX_JSON_DEPTH {
            return Err(protocol_error(py, core::NESTING_TOO_DEEP));
        }
        for member in unsafe { item.cast_unchecked::<PyTuple>() }.iter() {
            check_member_depth(py, class, &member, depth + 1)?;
        }
        Ok(())
    } else {
        Ok(())
    }
}

fn freeze<'py>(
    py: Python<'py>,
    class: &Bound<'py, PyAny>,
    value: &Bound<'py, PyAny>,
    depth: usize,
) -> PyResult<Bound<'py, PyAny>> {
    let pointer = value.as_ptr();
    if value.is_none() || unsafe { ffi::PyBool_Check(pointer) } != 0 {
        return Ok(value.clone());
    }
    if is_exact(value, ffi::PyLong_CheckExact) {
        let mut overflow: std::os::raw::c_int = 0;
        let number = unsafe { ffi::PyLong_AsLongLongAndOverflow(pointer, &mut overflow) };
        if overflow != 0 || !(-MAX_SAFE_INTEGER..=MAX_SAFE_INTEGER).contains(&number) {
            return Err(protocol_error(py, core::INTEGER_OUT_OF_SAFE_RANGE));
        }
        return Ok(value.clone());
    }
    if unsafe { ffi::PyFloat_Check(pointer) } != 0 {
        return Err(protocol_error(py, core::FLOAT_FORBIDDEN));
    }
    if is_exact(value, ffi::PyUnicode_CheckExact) {
        ensure_text(py, unsafe { value.cast_unchecked::<PyString>() })?;
        return Ok(value.clone());
    }
    if is_type(value, class) {
        check_frozen_depth(py, class, value, depth)?;
        return Ok(value.clone());
    }
    if is_exact(value, ffi::PyList_CheckExact) || is_exact(value, ffi::PyTuple_CheckExact) {
        if depth >= MAX_JSON_DEPTH {
            return Err(protocol_error(py, core::NESTING_TOO_DEEP));
        }
        let mut frozen = Vec::new();
        if is_exact(value, ffi::PyList_CheckExact) {
            let list = unsafe { value.cast_unchecked::<PyList>() };
            // Re-read the length each step, like the reference's list iterator.
            let mut index = 0;
            while index < list.len() {
                frozen.push(freeze(py, class, &list.get_item(index)?, depth + 1)?);
                index += 1;
            }
        } else {
            for item in unsafe { value.cast_unchecked::<PyTuple>() }.iter() {
                frozen.push(freeze(py, class, &item, depth + 1)?);
            }
        }
        return Ok(PyTuple::new(py, frozen)?.into_any());
    }
    if is_actual_mapping(py, value) {
        if depth >= MAX_JSON_DEPTH {
            return Err(protocol_error(py, core::NESTING_TOO_DEEP));
        }
        let members = mapping_members(py, value)?;
        let mut pairs = Vec::with_capacity(members.len());
        for (key, item) in members {
            let frozen = freeze(py, class, &item, depth + 1)?;
            pairs.push((key, frozen));
        }
        return new_json_object(py, class, pairs);
    }
    Err(protocol_error(py, core::UNSUPPORTED_JSON_TYPE))
}

fn json_object_class(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    JSON_OBJECT
        .get(py)
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("json_object_unbound"))
}

/// `_freeze_json(value, *, depth)`.
#[pyfunction]
#[pyo3(name = "values_freeze_json_at", signature = (value, *, depth))]
pub fn freeze_json_at<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    depth: usize,
) -> PyResult<Bound<'py, PyAny>> {
    let class = json_object_class(py)?;
    freeze(py, &class, value, depth)
}

/// `freeze_json(value)`.
#[pyfunction]
#[pyo3(name = "values_freeze_json")]
pub fn freeze_json<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let class = json_object_class(py)?;
    freeze(py, &class, value, 0)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_json_object, module)?)?;
    module.add_function(wrap_pyfunction!(freeze_json_at, module)?)?;
    module.add_function(wrap_pyfunction!(freeze_json, module)?)?;
    Ok(())
}
