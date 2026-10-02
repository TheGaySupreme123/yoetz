//! `yoetz.protocol.canonical` over live Python objects.
//!
//! The walker visits Python values directly (no intermediate tree) and applies the reference's
//! checks in the reference's order: fragment, `None`, `bool`, exact `int`, any `float`, exact
//! `str`, exact `list`/`tuple`, then an exact `dict` or exact `JsonObject`. Mapping keys are
//! validated in insertion order before any member value, and member values are visited in
//! sorted-key order, so the first refusal is always the one the reference reports.
//!
//! Every other value (a `str`/`int`/`list`/`dict` subclass, any other `Mapping`, an enum, an
//! arbitrary object) defers the whole call: the walker raises before running any Python code
//! of that value, and the module's Python wrapper re-runs the reference, which then reaches
//! that value with its own lookups, iteration order, and exception chain. The wrappers replay
//! every refusal through the reference the same way, so a native refusal only has to be a
//! refusal; its class and reason are the reference's.

use std::os::raw::c_void;

use pyo3::exceptions::{PyNotImplementedError, PyTypeError};
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBytes, PyDict, PyInt, PyList, PyString, PyTuple};
use yoetz_core::protocol::canonical::{self as core, MAX_JSON_DEPTH, MAX_SAFE_INTEGER, Reason};
use yoetz_core::protocol::json::{self, JsonSink, JsonText};

use crate::registry::{Slot, protocol_error};

static CANONICAL_FRAGMENT: Slot = Slot::new();
static BUILTIN_INT: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// Hand the whole call back to the Python reference. Only the module's Python wrappers call
/// these twins, and they re-run the reference on any exception; Rust callers treat any error
/// as "defer" too. Raised before any Python code of the offending value runs.
fn defer() -> PyErr {
    PyNotImplementedError::new_err("native_defer")
}

/// The bound `JsonObject` class, when `yoetz.domain.values` has bound it.
fn json_object_class(py: Python<'_>) -> Option<Bound<'_, PyAny>> {
    crate::walk::JSON_OBJECT.get(py)
}

#[inline]
fn is_type(value: &Bound<'_, PyAny>, class: Option<&Bound<'_, PyAny>>) -> bool {
    class.is_some_and(|class| value.get_type().as_ptr() == class.as_ptr())
}

/// An exact `str`, `int`, `bool`, `float`, or `None`: values the reference never treats as a
/// container (no ABC can claim them).
#[inline]
fn is_plain_scalar(value: &Bound<'_, PyAny>) -> bool {
    value.is_none()
        || is_exact(value, ffi::PyUnicode_CheckExact)
        || is_exact(value, ffi::PyLong_CheckExact)
        || is_exact(value, ffi::PyBool_Check)
        || is_exact(value, ffi::PyFloat_CheckExact)
}

#[inline]
fn is_exact(value: &Bound<'_, PyAny>, check: unsafe fn(*mut ffi::PyObject) -> i32) -> bool {
    unsafe { check(value.as_ptr()) != 0 }
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

/// `_validate_string` for an exact `str`.
fn validate_pystr<'a>(py: Python<'_>, text: &'a Bound<'_, PyString>) -> PyResult<&'a str> {
    match text.to_str() {
        Ok(slice) => {
            core::validate_str(slice).map_err(|reason| protocol_error(py, reason))?;
            Ok(slice)
        }
        Err(_) => Err(protocol_error(py, first_offender(text))),
    }
}

struct Encoder<'py> {
    py: Python<'py>,
    out: Vec<u8>,
    fragment: Option<Bound<'py, PyAny>>,
    json_object: Option<Bound<'py, PyAny>>,
    base_depth: usize,
    levels: i64,
}

impl<'py> Encoder<'py> {
    fn new(py: Python<'py>, base_depth: usize) -> Self {
        Encoder {
            py,
            out: Vec::with_capacity(1024),
            fragment: CANONICAL_FRAGMENT.get(py),
            json_object: json_object_class(py),
            base_depth,
            levels: -1,
        }
    }

    #[inline]
    fn fail(&self, reason: Reason) -> PyErr {
        protocol_error(self.py, reason)
    }

    #[inline]
    fn enter_container(&mut self, depth: usize) -> PyResult<()> {
        if depth >= MAX_JSON_DEPTH {
            return Err(self.fail(core::NESTING_TOO_DEEP));
        }
        let relative = (depth - self.base_depth) as i64;
        if relative > self.levels {
            self.levels = relative;
        }
        Ok(())
    }

    fn encode(&mut self, value: &Bound<'py, PyAny>, depth: usize) -> PyResult<()> {
        let py = self.py;
        if let Some(fragment) = &self.fragment {
            if value.get_type().as_ptr() == fragment.as_ptr() {
                let levels: i64 = value.getattr("levels")?.extract()?;
                if levels >= 0 && depth as i64 + levels >= MAX_JSON_DEPTH as i64 {
                    return Err(self.fail(core::NESTING_TOO_DEEP));
                }
                if levels >= 0 {
                    let reach = (depth - self.base_depth) as i64 + levels;
                    if reach > self.levels {
                        self.levels = reach;
                    }
                }
                let text = value.getattr("text")?;
                let text = text.cast::<PyString>()?;
                self.out.extend_from_slice(text.to_str()?.as_bytes());
                return Ok(());
            }
        }
        let pointer = value.as_ptr();
        if value.is_none() {
            self.out.extend_from_slice(b"null");
            return Ok(());
        }
        if unsafe { ffi::PyBool_Check(pointer) } != 0 {
            let truth = pointer == unsafe { ffi::Py_True() };
            self.out
                .extend_from_slice(if truth { b"true" } else { b"false" });
            return Ok(());
        }
        if is_exact(value, ffi::PyLong_CheckExact) {
            let mut overflow: std::os::raw::c_int = 0;
            let number = unsafe { ffi::PyLong_AsLongLongAndOverflow(pointer, &mut overflow) };
            if overflow != 0 || !(-MAX_SAFE_INTEGER..=MAX_SAFE_INTEGER).contains(&number) {
                return Err(self.fail(core::INTEGER_OUT_OF_SAFE_RANGE));
            }
            core::push_int(&mut self.out, number);
            return Ok(());
        }
        if unsafe { ffi::PyFloat_Check(pointer) } != 0 {
            return Err(self.fail(core::FLOAT_FORBIDDEN));
        }
        if is_exact(value, ffi::PyUnicode_CheckExact) {
            let text = unsafe { value.cast_unchecked::<PyString>() };
            return match text.to_str() {
                Ok(slice) => {
                    core::encode_str_into(&mut self.out, slice).map_err(|reason| self.fail(reason))
                }
                Err(_) => Err(self.fail(first_offender(text))),
            };
        }
        if is_exact(value, ffi::PyList_CheckExact) {
            self.enter_container(depth)?;
            let list = unsafe { value.cast_unchecked::<PyList>() };
            self.out.push(b'[');
            // Index access re-reads the length, so a list mutated by a nested __getattr__
            // cannot be read out of bounds.
            let mut index = 0;
            while index < list.len() {
                if index > 0 {
                    self.out.push(b',');
                }
                let item = list.get_item(index)?;
                self.encode(&item, depth + 1)?;
                index += 1;
            }
            self.out.push(b']');
            return Ok(());
        }
        if is_exact(value, ffi::PyTuple_CheckExact) {
            self.enter_container(depth)?;
            let tuple = unsafe { value.cast_unchecked::<PyTuple>() };
            self.out.push(b'[');
            for (index, item) in tuple.iter().enumerate() {
                if index > 0 {
                    self.out.push(b',');
                }
                self.encode(&item, depth + 1)?;
            }
            self.out.push(b']');
            return Ok(());
        }
        if is_exact(value, ffi::PyDict_CheckExact) || is_type(value, self.json_object.as_ref()) {
            self.enter_container(depth)?;
            let (keys, items) = mapping_members(py, value)?;
            let mut order: Vec<(&str, usize)> = Vec::with_capacity(keys.len());
            for (index, key) in keys.iter().enumerate() {
                // Already validated: conversion cannot fail here.
                order.push((key.to_str()?, index));
            }
            order.sort_by(|left, right| core::utf16_cmp(left.0, right.0));
            self.out.push(b'{');
            for (position, (key, index)) in order.into_iter().enumerate() {
                if position > 0 {
                    self.out.push(b',');
                }
                core::encode_str_into(&mut self.out, key).map_err(|reason| self.fail(reason))?;
                self.out.push(b':');
                self.encode(&items[index], depth + 1)?;
            }
            self.out.push(b'}');
            return Ok(());
        }
        // Any other value: a non-exact mapping, a subclass, or an unsupported type. The
        // reference decides (it may call the value's own Python code), so defer.
        Err(defer())
    }
}

/// A mapping's validated keys and their member values, in insertion order.
type MappingMembers<'py> = (Vec<Bound<'py, PyString>>, Vec<Bound<'py, PyAny>>);

/// Collect an exact `dict`'s or exact `JsonObject`'s members, validating every key in
/// insertion order first. No Python code runs while the dict is iterated.
fn mapping_members<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<MappingMembers<'py>> {
    let capacity = if is_exact(value, ffi::PyDict_CheckExact) {
        unsafe { value.cast_unchecked::<PyDict>() }.len()
    } else {
        0
    };
    let mut keys = Vec::with_capacity(capacity);
    let mut items = Vec::with_capacity(capacity);
    let mut admit = |key: Bound<'py, PyAny>, item: Bound<'py, PyAny>| -> PyResult<()> {
        if !is_exact(&key, ffi::PyUnicode_CheckExact) {
            return Err(protocol_error(py, core::OBJECT_KEY_NOT_STRING));
        }
        let key = key.cast_into::<PyString>().map_err(PyErr::from)?;
        validate_pystr(py, &key)?;
        keys.push(key);
        items.push(item);
        Ok(())
    };
    if is_exact(value, ffi::PyDict_CheckExact) {
        let dict = unsafe { value.cast_unchecked::<PyDict>() };
        for (key, item) in dict.iter() {
            admit(key, item)?;
        }
    } else {
        // An exact ``JsonObject``'s ``items()`` yields its ``_items`` pairs in order.
        for pair in crate::walk::json_object_items(value)?.iter() {
            let pair = pair.cast_into::<PyTuple>()?;
            admit(pair.get_item(0)?, pair.get_item(1)?)?;
        }
    }
    Ok((keys, items))
}

fn encode_value<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    depth: usize,
) -> PyResult<Encoder<'py>> {
    let mut encoder = Encoder::new(py, depth);
    encoder.encode(value, depth)?;
    Ok(encoder)
}

#[pyfunction]
pub fn bind_canonical_fragment(class: Bound<'_, PyAny>) {
    CANONICAL_FRAGMENT.set(class.unbind());
}

/// `canonical_encode(value) -> bytes`.
#[pyfunction]
pub fn canonical_encode<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyBytes>> {
    let encoder = encode_value(py, value, 0)?;
    Ok(PyBytes::new(py, &encoder.out))
}

/// `_canonical_text(value, *, depth=0) -> str`.
#[pyfunction]
#[pyo3(signature = (value, *, depth = 0))]
pub fn canonical_text<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    depth: usize,
) -> PyResult<Bound<'py, PyString>> {
    let encoder = encode_value(py, value, depth)?;
    // The encoder only emits UTF-8 copied from valid str slices and ASCII syntax.
    Ok(PyString::new(py, unsafe {
        std::str::from_utf8_unchecked(&encoder.out)
    }))
}

/// `(canonical text, container levels)` in one pass, for `canonical_fragment`.
#[pyfunction]
pub fn canonical_fragment_parts<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<(Bound<'py, PyString>, i64)> {
    let encoder = encode_value(py, value, 0)?;
    let text = PyString::new(py, unsafe { std::str::from_utf8_unchecked(&encoder.out) });
    Ok((text, encoder.levels))
}

/// `canonical_digest(value) -> "sha256:<hex>"`.
#[pyfunction]
pub fn canonical_digest(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<String> {
    let encoder = encode_value(py, value, 0)?;
    Ok(core::sha256_prefixed(&encoder.out))
}

/// `ensure_canonical_value(value, *, depth=0) -> None`.
#[pyfunction]
#[pyo3(signature = (value, *, depth = 0))]
pub fn ensure_canonical_value(
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
    depth: usize,
) -> PyResult<()> {
    encode_value(py, value, depth)?;
    Ok(())
}

/// `container_levels(value) -> int` (no validation, like the reference).
///
/// Walks exact `list`/`tuple`/`dict`/`JsonObject` containers and plain scalars; anything else,
/// or a container nested past `DEFER_NESTING` (the reference has no bound of its own and stops
/// only at Python's recursion limit), defers to the reference.
#[pyfunction]
pub fn container_levels(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<i64> {
    let fragment = CANONICAL_FRAGMENT.get(py);
    let json_object = json_object_class(py);
    levels_of(value, fragment.as_ref(), json_object.as_ref(), 0)
}

fn levels_of(
    value: &Bound<'_, PyAny>,
    fragment: Option<&Bound<'_, PyAny>>,
    json_object: Option<&Bound<'_, PyAny>>,
    recursion: usize,
) -> PyResult<i64> {
    if is_type(value, fragment) {
        return value.getattr("levels")?.extract();
    }
    if is_plain_scalar(value) {
        return Ok(-1);
    }
    if recursion >= json::DEFER_NESTING {
        return Err(defer());
    }
    let mut deepest = -1;
    if is_exact(value, ffi::PyList_CheckExact) {
        let list = unsafe { value.cast_unchecked::<PyList>() };
        // Index access re-reads the length; no Python code runs inside this walk.
        let mut index = 0;
        while index < list.len() {
            let item = list.get_item(index)?;
            deepest = deepest.max(levels_of(&item, fragment, json_object, recursion + 1)?);
            index += 1;
        }
    } else if is_exact(value, ffi::PyTuple_CheckExact) {
        for item in unsafe { value.cast_unchecked::<PyTuple>() }.iter() {
            deepest = deepest.max(levels_of(&item, fragment, json_object, recursion + 1)?);
        }
    } else if is_exact(value, ffi::PyDict_CheckExact) {
        for item in unsafe { value.cast_unchecked::<PyDict>() }.values().iter() {
            deepest = deepest.max(levels_of(&item, fragment, json_object, recursion + 1)?);
        }
    } else if is_type(value, json_object) {
        for pair in crate::walk::json_object_items(value)?.iter() {
            let item = pair.cast_into::<PyTuple>()?.get_item(1)?;
            deepest = deepest.max(levels_of(&item, fragment, json_object, recursion + 1)?);
        }
    } else {
        return Err(defer());
    }
    Ok(1 + deepest)
}

/// `_validate_string(value) -> None`.
#[pyfunction]
pub fn validate_string(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<()> {
    let text = value
        .cast::<PyString>()
        .map_err(|_| PyTypeError::new_err("expected string or bytes-like object"))?;
    validate_pystr(py, text)?;
    Ok(())
}

/// `_encode_string(value) -> str`.
#[pyfunction]
pub fn encode_string<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyString>> {
    let text = value
        .cast::<PyString>()
        .map_err(|_| PyTypeError::new_err("expected string or bytes-like object"))?;
    let slice = validate_pystr(py, text)?;
    let mut out = Vec::with_capacity(slice.len() + 2);
    core::encode_str_into(&mut out, slice).map_err(|reason| protocol_error(py, reason))?;
    Ok(PyString::new(py, unsafe {
        std::str::from_utf8_unchecked(&out)
    }))
}

/// `ensure_canonical_set(values) -> None`.
#[pyfunction]
pub fn ensure_canonical_set(py: Python<'_>, values: &Bound<'_, PyAny>) -> PyResult<()> {
    // The reference's `isinstance(values, list | tuple)` and `for member in values` can run a
    // subclass's (or a spoofed `__class__`'s) Python code: only exact sequences stay native.
    if !(is_exact(values, ffi::PyList_CheckExact) || is_exact(values, ffi::PyTuple_CheckExact)) {
        return Err(defer());
    }
    let mut previous: Option<Vec<u8>> = None;
    for member in values.try_iter()? {
        let member = member?;
        if !is_exact(&member, ffi::PyUnicode_CheckExact) {
            return Err(protocol_error(py, core::SET_MEMBER_NOT_ASCII));
        }
        let text = unsafe { member.cast_unchecked::<PyString>() };
        let encoded = match text.to_str() {
            Ok(slice) if slice.is_ascii() => slice.as_bytes(),
            _ => return Err(protocol_error(py, core::SET_MEMBER_NOT_ASCII)),
        };
        if let Some(prior) = &previous {
            if encoded == prior.as_slice() {
                return Err(protocol_error(py, core::DUPLICATE_SET_MEMBER));
            }
            if encoded < prior.as_slice() {
                return Err(protocol_error(py, core::UNSORTED_SET_FIELD));
            }
        }
        previous = Some(encoded.to_vec());
    }
    Ok(())
}

/// `canonical_integer_string(value) -> str`.
#[pyfunction]
pub fn canonical_integer_string(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<String> {
    if !is_exact(value, ffi::PyLong_CheckExact) {
        return Err(protocol_error(py, core::INTEGER_OUT_OF_SQLITE_RANGE));
    }
    let mut overflow: std::os::raw::c_int = 0;
    let number = unsafe { ffi::PyLong_AsLongLongAndOverflow(value.as_ptr(), &mut overflow) };
    if overflow != 0 {
        return Err(protocol_error(py, core::INTEGER_OUT_OF_SQLITE_RANGE));
    }
    core::canonical_integer_string(number).map_err(|reason| protocol_error(py, reason))
}

/// `parse_canonical_integer_string(value, *, signed=False) -> int`.
#[pyfunction]
#[pyo3(signature = (value, *, signed = false))]
pub fn parse_canonical_integer_string(
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
    signed: bool,
) -> PyResult<i64> {
    if !is_exact(value, ffi::PyUnicode_CheckExact) {
        return Err(protocol_error(py, core::NONCANONICAL_INTEGER_STRING));
    }
    let text = unsafe { value.cast_unchecked::<PyString>() };
    let Ok(slice) = text.to_str() else {
        return Err(protocol_error(py, core::NONCANONICAL_INTEGER_STRING));
    };
    core::parse_canonical_integer_string(slice, signed).map_err(|reason| protocol_error(py, reason))
}

/// `request_digest(identity) -> str`.
#[pyfunction]
pub fn request_digest(py: Python<'_>, identity: &Bound<'_, PyAny>) -> PyResult<String> {
    reject_ledger_assigned_fields(py, identity, 0)?;
    canonical_digest(py, identity)
}

/// The reference's `_reject_ledger_assigned_fields` walk over exact containers; any other
/// value it would inspect (it calls `issubclass` on each node and iterates mappings live)
/// defers. No Python code runs in this walk, so the dict snapshot is the live iteration.
fn reject_ledger_assigned_fields(
    py: Python<'_>,
    node: &Bound<'_, PyAny>,
    depth: usize,
) -> PyResult<()> {
    let fragment = CANONICAL_FRAGMENT.get(py);
    let json_object = json_object_class(py);
    reject_ledger_walk(py, node, depth, fragment.as_ref(), json_object.as_ref())
}

fn reject_ledger_walk(
    py: Python<'_>,
    node: &Bound<'_, PyAny>,
    depth: usize,
    fragment: Option<&Bound<'_, PyAny>>,
    json_object: Option<&Bound<'_, PyAny>>,
) -> PyResult<()> {
    if is_plain_scalar(node) || is_type(node, fragment) {
        return Ok(());
    }
    let is_dict = is_exact(node, ffi::PyDict_CheckExact);
    if is_dict || is_type(node, json_object) {
        if depth >= MAX_JSON_DEPTH {
            return Err(protocol_error(py, core::NESTING_TOO_DEEP));
        }
        let pairs: Vec<(Bound<'_, PyAny>, Bound<'_, PyAny>)> = if is_dict {
            unsafe { node.cast_unchecked::<PyDict>() }.iter().collect()
        } else {
            let mut collected = Vec::new();
            for pair in crate::walk::json_object_items(node)?.iter() {
                let pair = pair.cast_into::<PyTuple>()?;
                collected.push((pair.get_item(0)?, pair.get_item(1)?));
            }
            collected
        };
        for (key, item) in pairs {
            if is_exact(&key, ffi::PyUnicode_CheckExact) {
                if let Ok(name) = unsafe { key.cast_unchecked::<PyString>() }.to_str() {
                    if core::REQUEST_DIGEST_FENCE_KEYS.contains(&name) {
                        return Err(protocol_error(py, core::LEDGER_ASSIGNED_FIELD));
                    }
                }
            }
            reject_ledger_walk(py, &item, depth + 1, fragment, json_object)?;
        }
        return Ok(());
    }
    if is_exact(node, ffi::PyList_CheckExact) || is_exact(node, ffi::PyTuple_CheckExact) {
        if depth >= MAX_JSON_DEPTH {
            return Err(protocol_error(py, core::NESTING_TOO_DEEP));
        }
        for item in node.try_iter()? {
            reject_ledger_walk(py, &item?, depth + 1, fragment, json_object)?;
        }
        return Ok(());
    }
    Err(defer())
}

/// `sha256:<hex>` of raw bytes.
#[pyfunction]
pub fn sha256_prefixed(data: &[u8]) -> String {
    core::sha256_prefixed(data)
}

// ---------------------------------------------------------------------------------------------
// strict_json_parse
// ---------------------------------------------------------------------------------------------

struct PySink<'py> {
    py: Python<'py>,
    depth: usize,
    /// Set when the parsed value may fail the profile walk (deep nesting, a decoded NUL, or a
    /// lone surrogate); only then is the exact-order validation walk needed.
    suspect: bool,
}

impl<'py> PySink<'py> {
    fn text(&mut self, text: JsonText<'_>) -> PyResult<Bound<'py, PyString>> {
        match text {
            JsonText::Borrowed(slice) => Ok(PyString::new(self.py, slice)),
            JsonText::Owned(owned) => {
                if owned.as_bytes().contains(&0) {
                    self.suspect = true;
                }
                Ok(PyString::new(self.py, &owned))
            }
            JsonText::Wide(points) => {
                self.suspect = true;
                unsafe {
                    let pointer = ffi::PyUnicode_FromKindAndData(
                        ffi::PyUnicode_4BYTE_KIND as _,
                        points.as_ptr() as *const c_void,
                        points.len() as ffi::Py_ssize_t,
                    );
                    Ok(Bound::from_owned_ptr_or_err(self.py, pointer)?
                        .cast_into_unchecked::<PyString>())
                }
            }
        }
    }
}

impl<'py> JsonSink for PySink<'py> {
    type Value = Bound<'py, PyAny>;
    type Key = Bound<'py, PyString>;
    type Array = Vec<Bound<'py, PyAny>>;
    type Object = Vec<(Bound<'py, PyString>, Bound<'py, PyAny>)>;
    type Error = PyErr;

    fn fail(&mut self, reason: Reason) -> PyErr {
        protocol_error(self.py, reason)
    }
    fn null(&mut self) -> PyResult<Self::Value> {
        Ok(self.py.None().into_bound(self.py))
    }
    fn boolean(&mut self, value: bool) -> PyResult<Self::Value> {
        Ok(pyo3::types::PyBool::new(self.py, value)
            .to_owned()
            .into_any())
    }
    fn integer(&mut self, literal: &str) -> PyResult<Self::Value> {
        if literal == "-0" {
            return Err(self.fail(core::FLOAT_FORBIDDEN));
        }
        if literal.len() <= 18 {
            let number: i64 = literal
                .parse()
                .map_err(|_| self.fail(core::INTEGER_OUT_OF_SAFE_RANGE))?;
            if !(-MAX_SAFE_INTEGER..=MAX_SAFE_INTEGER).contains(&number) {
                return Err(self.fail(core::INTEGER_OUT_OF_SAFE_RANGE));
            }
            return Ok(PyInt::new(self.py, number).into_any());
        }
        // Defer to int() itself so its digit-limit refusal is the reference's exact error.
        let builtin = BUILTIN_INT.get_or_try_init(self.py, || -> PyResult<Py<PyAny>> {
            Ok(self.py.import("builtins")?.getattr("int")?.unbind())
        })?;
        builtin.bind(self.py).call1((literal,))?;
        Err(self.fail(core::INTEGER_OUT_OF_SAFE_RANGE))
    }
    fn float(&mut self, _literal: &str) -> PyResult<Self::Value> {
        Err(self.fail(core::FLOAT_FORBIDDEN))
    }
    fn string(&mut self, text: JsonText<'_>) -> PyResult<Self::Value> {
        Ok(self.text(text)?.into_any())
    }
    fn key(&mut self, text: JsonText<'_>) -> PyResult<Self::Key> {
        self.text(text)
    }
    fn begin_array(&mut self) -> PyResult<Self::Array> {
        if self.depth >= MAX_JSON_DEPTH {
            self.suspect = true;
        }
        self.depth += 1;
        Ok(Vec::new())
    }
    fn push(&mut self, array: &mut Self::Array, value: Self::Value) -> PyResult<()> {
        array.push(value);
        Ok(())
    }
    fn end_array(&mut self, array: Self::Array) -> PyResult<Self::Value> {
        self.depth -= 1;
        Ok(PyList::new(self.py, array)?.into_any())
    }
    fn begin_object(&mut self) -> PyResult<Self::Object> {
        if self.depth >= MAX_JSON_DEPTH {
            self.suspect = true;
        }
        self.depth += 1;
        Ok(Vec::new())
    }
    fn insert(
        &mut self,
        object: &mut Self::Object,
        key: Self::Key,
        value: Self::Value,
    ) -> PyResult<()> {
        object.push((key, value));
        Ok(())
    }
    fn end_object(&mut self, object: Self::Object) -> PyResult<Self::Value> {
        self.depth -= 1;
        let dict = PyDict::new(self.py);
        let pairs = object.len();
        for (key, value) in object {
            dict.set_item(key, value)?;
        }
        if dict.len() != pairs {
            return Err(self.fail(core::DUPLICATE_OBJECT_KEY));
        }
        Ok(dict.into_any())
    }
}

/// `strict_json_parse(data, *, validate=True)` for Rust callers (nesting bounded at
/// `MAX_PARSE_NESTING`, an approximation of the reference's stack-dependent guard).
pub fn strict_json_parse<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
    validate: bool,
) -> PyResult<Bound<'py, PyAny>> {
    parse_strict(py, data, validate, json::MAX_PARSE_NESTING)
}

/// `strict_json_parse(data, *, validate=True)` for the Python wrapper, which replays every
/// refusal through the reference: a document nested past `DEFER_NESTING` stops here so the
/// reference decides with its real recursion guard.
#[pyfunction]
#[pyo3(name = "strict_json_parse", signature = (data, *, validate = true))]
pub fn strict_json_parse_py<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
    validate: bool,
) -> PyResult<Bound<'py, PyAny>> {
    parse_strict(py, data, validate, json::DEFER_NESTING)
}

fn parse_strict<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
    validate: bool,
    nesting: usize,
) -> PyResult<Bound<'py, PyAny>> {
    let owned;
    let raw: &[u8] = if is_exact(data, ffi::PyByteArray_CheckExact) {
        owned = unsafe { data.cast_unchecked::<pyo3::types::PyByteArray>() }.to_vec();
        &owned
    } else if is_exact(data, ffi::PyBytes_CheckExact) {
        unsafe { data.cast_unchecked::<PyBytes>() }.as_bytes()
    } else {
        return Err(protocol_error(py, core::INPUT_NOT_BYTES));
    };
    let text = json::precheck(raw).map_err(|reason| protocol_error(py, reason))?;
    let mut sink = PySink {
        py,
        depth: 0,
        suspect: false,
    };
    let value = json::scan_limited(text, &mut sink, nesting)?;
    if validate && sink.suspect {
        encode_value(py, &value, 0)?;
    }
    Ok(value)
}

/// `is_canonical_json_bytes(data) -> bool`: whether `canonical_encode(strict_json_parse(data))`
/// would succeed and equal `data`, checked in one pass without building a value. Anything but
/// an exact `bytes` or `bytearray` answers `False` (the reference raises `input_not_bytes`).
#[pyfunction]
pub fn is_canonical_json_bytes(data: &Bound<'_, PyAny>) -> bool {
    if is_exact(data, ffi::PyBytes_CheckExact) {
        let raw = unsafe { data.cast_unchecked::<PyBytes>() }.as_bytes();
        return yoetz_core::protocol::canonical_check::is_canonical_json_bytes(raw);
    }
    if is_exact(data, ffi::PyByteArray_CheckExact) {
        let owned = unsafe { data.cast_unchecked::<pyo3::types::PyByteArray>() }.to_vec();
        return yoetz_core::protocol::canonical_check::is_canonical_json_bytes(&owned);
    }
    false
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_canonical_fragment, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_encode, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_text, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_fragment_parts, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_digest, module)?)?;
    module.add_function(wrap_pyfunction!(ensure_canonical_value, module)?)?;
    module.add_function(wrap_pyfunction!(container_levels, module)?)?;
    module.add_function(wrap_pyfunction!(validate_string, module)?)?;
    module.add_function(wrap_pyfunction!(encode_string, module)?)?;
    module.add_function(wrap_pyfunction!(ensure_canonical_set, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_integer_string, module)?)?;
    module.add_function(wrap_pyfunction!(parse_canonical_integer_string, module)?)?;
    module.add_function(wrap_pyfunction!(request_digest, module)?)?;
    module.add_function(wrap_pyfunction!(sha256_prefixed, module)?)?;
    module.add_function(wrap_pyfunction!(strict_json_parse_py, module)?)?;
    module.add_function(wrap_pyfunction!(is_canonical_json_bytes, module)?)?;
    Ok(())
}
