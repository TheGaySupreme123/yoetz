//! `yoetz.application.service._leaves` over live Python objects.
//!
//! The walk is side-effect free on exact `dict`, exact `JsonObject`, and exact `list`/`tuple`
//! nodes with exact `str` keys; on any other mapping (or a key the reference would escape through
//! a subclass's own `replace`) it hands the whole call to the Python reference.

use pyo3::exceptions::PyValueError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString, PyTuple};
use yoetz_core::protocol::canonical::MAX_JSON_DEPTH;
use yoetz_core::protocol::pointer::push_escaped;

use crate::registry::Slot;
use crate::walk::{
    JSON_OBJECT, NATIVE_RECURSION_LIMIT, is_exact, is_mapping_instance, is_plain_scalar, is_type,
    json_object_items,
};

static REFERENCE: Slot = Slot::new();

/// Bind the Python reference `_leaves`.
#[pyfunction]
#[pyo3(name = "service_bind_leaves")]
pub fn bind_leaves(reference: Bound<'_, PyAny>) {
    REFERENCE.set(reference.unbind());
}

struct Defer;

struct Walker<'py> {
    py: Python<'py>,
    json_object: Option<Bound<'py, PyAny>>,
    rows: Vec<Bound<'py, PyAny>>,
}

impl<'py> Walker<'py> {
    fn member(
        &mut self,
        key: &Bound<'py, PyAny>,
        item: &Bound<'py, PyAny>,
        pointer: &mut String,
        depth: usize,
    ) -> PyResult<Result<(), Defer>> {
        if !is_exact(key, ffi::PyUnicode_CheckExact) {
            return Ok(Err(Defer));
        }
        let Ok(text) = unsafe { key.cast_unchecked::<PyString>() }.to_str() else {
            return Ok(Err(Defer));
        };
        let mark = pointer.len();
        pointer.push('/');
        push_escaped(pointer, text);
        let outcome = self.walk(item, pointer, depth + 1);
        pointer.truncate(mark);
        outcome
    }

    fn walk(
        &mut self,
        value: &Bound<'py, PyAny>,
        pointer: &mut String,
        depth: usize,
    ) -> PyResult<Result<(), Defer>> {
        if depth > NATIVE_RECURSION_LIMIT {
            return Ok(Err(Defer));
        }
        if is_exact(value, ffi::PyDict_CheckExact) {
            for (key, item) in unsafe { value.cast_unchecked::<PyDict>() }.iter() {
                if self.member(&key, &item, pointer, depth)?.is_err() {
                    return Ok(Err(Defer));
                }
            }
            return Ok(Ok(()));
        }
        if let Some(class) = self.json_object.clone() {
            if is_type(value, &class) {
                for pair in json_object_items(value)?.iter() {
                    let pair = pair.cast_into::<PyTuple>()?;
                    if self
                        .member(&pair.get_item(0)?, &pair.get_item(1)?, pointer, depth)?
                        .is_err()
                    {
                        return Ok(Err(Defer));
                    }
                }
                return Ok(Ok(()));
            }
        }
        if is_exact(value, ffi::PyList_CheckExact) || is_exact(value, ffi::PyTuple_CheckExact) {
            let items: Vec<Bound<'py, PyAny>> = if is_exact(value, ffi::PyList_CheckExact) {
                unsafe { value.cast_unchecked::<PyList>() }.iter().collect()
            } else {
                unsafe { value.cast_unchecked::<PyTuple>() }
                    .iter()
                    .collect()
            };
            for (index, item) in items.iter().enumerate() {
                let mark = pointer.len();
                pointer.push('/');
                pointer.push_str(itoa::Buffer::new().format(index));
                let outcome = self.walk(item, pointer, depth + 1)?;
                pointer.truncate(mark);
                if outcome.is_err() {
                    return Ok(Err(Defer));
                }
            }
            return Ok(Ok(()));
        }
        if is_mapping_instance(self.py, value)? {
            return Ok(Err(Defer));
        }
        let row = PyTuple::new(
            self.py,
            [PyString::new(self.py, pointer).into_any(), value.clone()],
        )?;
        self.rows.push(row.into_any());
        Ok(Ok(()))
    }
}

/// `_leaves(value, pointer="")`.
#[pyfunction]
#[pyo3(name = "service_leaves", signature = (value, pointer = None))]
pub fn leaves<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    pointer: Option<&Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyAny>> {
    let Some(reference) = REFERENCE.get(py) else {
        return Err(pyo3::exceptions::PyRuntimeError::new_err("leaves_unbound"));
    };
    let defer = || match pointer {
        Some(pointer) => reference.call1((value, pointer)),
        None => reference.call1((value,)),
    };
    let mut start = String::new();
    if let Some(pointer) = pointer {
        if !is_exact(pointer, ffi::PyUnicode_CheckExact) {
            return defer();
        }
        let Ok(text) = unsafe { pointer.cast_unchecked::<PyString>() }.to_str() else {
            return defer();
        };
        start.push_str(text);
    }
    let mut walker = Walker {
        py,
        json_object: JSON_OBJECT.get(py),
        rows: Vec::new(),
    };
    match walker.walk(value, &mut start, 0)? {
        Ok(()) => Ok(PyTuple::new(py, walker.rows)?.into_any()),
        Err(Defer) => defer(),
    }
}

static PLAIN_REFERENCE: Slot = Slot::new();

/// Bind the Python reference `_plain_nested_mappings`.
#[pyfunction]
#[pyo3(name = "service_bind_plain_nested_mappings")]
pub fn bind_plain_nested_mappings(reference: Bound<'_, PyAny>) {
    PLAIN_REFERENCE.set(reference.unbind());
}

struct Plain<'py> {
    py: Python<'py>,
    reference: Bound<'py, PyAny>,
    json_object: Option<Bound<'py, PyAny>>,
}

impl<'py> Plain<'py> {
    fn too_deep(depth: i64) -> Option<PyErr> {
        (depth >= MAX_JSON_DEPTH as i64).then(|| PyValueError::new_err("projection_value_too_deep"))
    }

    fn plain(&self, value: &Bound<'py, PyAny>, depth: i64) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        if is_plain_scalar(value) {
            return Ok(value.clone());
        }
        let is_json_object = self
            .json_object
            .as_ref()
            .is_some_and(|class| is_type(value, class));
        if is_exact(value, ffi::PyDict_CheckExact) || is_json_object {
            if let Some(error) = Self::too_deep(depth) {
                return Err(error);
            }
            let out = PyDict::new(py);
            if is_json_object {
                for pair in json_object_items(value)?.iter() {
                    let pair = pair.cast_into::<PyTuple>()?;
                    out.set_item(
                        pair.get_item(0)?,
                        self.plain(&pair.get_item(1)?, depth + 1)?,
                    )?;
                }
            } else {
                for (key, item) in unsafe { value.cast_unchecked::<PyDict>() }.iter() {
                    out.set_item(key, self.plain(&item, depth + 1)?)?;
                }
            }
            return Ok(out.into_any());
        }
        if is_exact(value, ffi::PyTuple_CheckExact) {
            if let Some(error) = Self::too_deep(depth) {
                return Err(error);
            }
            let mut members = Vec::new();
            for item in unsafe { value.cast_unchecked::<PyTuple>() }.iter() {
                members.push(self.plain(&item, depth + 1)?);
            }
            return Ok(PyTuple::new(py, members)?.into_any());
        }
        if is_exact(value, ffi::PyList_CheckExact) {
            if let Some(error) = Self::too_deep(depth) {
                return Err(error);
            }
            let list = unsafe { value.cast_unchecked::<PyList>() };
            let mut members = Vec::with_capacity(list.len());
            let mut index = 0;
            while index < list.len() {
                members.push(self.plain(&list.get_item(index)?, depth + 1)?);
                index += 1;
            }
            return Ok(PyList::new(py, members)?.into_any());
        }
        if is_mapping_instance(py, value)? {
            return self.reference.call1((value, depth));
        }
        Ok(value.clone())
    }
}

/// `_plain_nested_mappings(value, depth=0)`.
#[pyfunction]
#[pyo3(name = "service_plain_nested_mappings", signature = (value, depth = None))]
pub fn plain_nested_mappings<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    depth: Option<&Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyAny>> {
    let Some(reference) = PLAIN_REFERENCE.get(py) else {
        return Err(pyo3::exceptions::PyRuntimeError::new_err(
            "plain_nested_mappings_unbound",
        ));
    };
    let start = match depth {
        None => 0,
        Some(depth) if is_exact(depth, ffi::PyLong_CheckExact) => match depth.extract::<i64>() {
            Ok(depth) if (0..=MAX_JSON_DEPTH as i64).contains(&depth) => depth,
            _ => return reference.call1((value, depth)),
        },
        Some(depth) => return reference.call1((value, depth)),
    };
    Plain {
        py,
        reference,
        json_object: JSON_OBJECT.get(py),
    }
    .plain(value, start)
}

static REPLACE_REFERENCE: Slot = Slot::new();

/// Bind the Python reference `_replace_pointer`.
#[pyfunction]
#[pyo3(name = "service_bind_replace_pointer")]
pub fn bind_replace_pointer(reference: Bound<'_, PyAny>) {
    REPLACE_REFERENCE.set(reference.unbind());
}

enum Replaced<'py> {
    Done(Bound<'py, PyAny>),
    Failed(&'static str),
    Defer,
}

struct Replacer<'py> {
    py: Python<'py>,
    parts: Vec<String>,
    replacement: Bound<'py, PyAny>,
    json_object: Option<Bound<'py, PyAny>>,
}

impl<'py> Replacer<'py> {
    fn replace_at(&self, value: &Bound<'py, PyAny>, depth: usize) -> PyResult<Replaced<'py>> {
        let py = self.py;
        if depth == self.parts.len() {
            return Ok(Replaced::Done(self.replacement.clone()));
        }
        let part = self.parts[depth].as_str();
        let is_json_object = self
            .json_object
            .as_ref()
            .is_some_and(|class| is_type(value, class));
        if is_exact(value, ffi::PyDict_CheckExact) || is_json_object {
            let source = if is_json_object {
                let copy = PyDict::new(py);
                for pair in json_object_items(value)?.iter() {
                    let pair = pair.cast_into::<PyTuple>()?;
                    copy.set_item(pair.get_item(0)?, pair.get_item(1)?)?;
                }
                copy
            } else {
                unsafe { value.cast_unchecked::<PyDict>() }.copy()?
            };
            let key = PyString::new(py, part);
            let Some(child) = source.get_item(&key)? else {
                return Ok(Replaced::Failed("projection_pointer_unresolved"));
            };
            return Ok(match self.replace_at(&child, depth + 1)? {
                Replaced::Done(replaced) => {
                    source.set_item(key, replaced)?;
                    Replaced::Done(source.into_any())
                }
                other => other,
            });
        }
        if is_exact(value, ffi::PyList_CheckExact) || is_exact(value, ffi::PyTuple_CheckExact) {
            let mut members: Vec<Bound<'py, PyAny>> = if is_exact(value, ffi::PyList_CheckExact) {
                unsafe { value.cast_unchecked::<PyList>() }.iter().collect()
            } else {
                unsafe { value.cast_unchecked::<PyTuple>() }
                    .iter()
                    .collect()
            };
            let Some(index) = yoetz_core::protocol::pointer::array_index(part) else {
                return Ok(Replaced::Failed("projection_pointer_unresolved"));
            };
            if index >= members.len() {
                return Ok(Replaced::Failed("projection_pointer_unresolved"));
            }
            return Ok(match self.replace_at(&members[index], depth + 1)? {
                Replaced::Done(replaced) => {
                    members[index] = replaced;
                    Replaced::Done(PyTuple::new(py, members)?.into_any())
                }
                other => other,
            });
        }
        if is_mapping_instance(py, value)? {
            return Ok(Replaced::Defer);
        }
        Ok(Replaced::Failed("projection_pointer_invalid"))
    }
}

/// `_replace_pointer(root, pointer, replacement)`.
#[pyfunction]
#[pyo3(name = "service_replace_pointer")]
pub fn replace_pointer<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    pointer: &Bound<'py, PyAny>,
    replacement: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let Some(reference) = REPLACE_REFERENCE.get(py) else {
        return Err(pyo3::exceptions::PyRuntimeError::new_err(
            "replace_pointer_unbound",
        ));
    };
    let defer = || reference.call1((root, pointer, replacement));
    if !is_exact(pointer, ffi::PyUnicode_CheckExact) {
        return defer();
    }
    let Ok(text) = unsafe { pointer.cast_unchecked::<PyString>() }.to_str() else {
        return defer();
    };
    // ``_segments``: the pointer must start with "/", then each segment is unescaped.
    let Some(rest) = text.strip_prefix('/') else {
        return Err(PyValueError::new_err("projection_pointer_invalid"));
    };
    let parts: Vec<String> = rest
        .split('/')
        .map(|segment| segment.replace("~1", "/").replace("~0", "~"))
        .collect();
    if parts.len() > NATIVE_RECURSION_LIMIT {
        return defer();
    }
    let replacer = Replacer {
        py,
        parts,
        replacement: replacement.clone(),
        json_object: JSON_OBJECT.get(py),
    };
    match replacer.replace_at(root, 0)? {
        Replaced::Done(value) => Ok(value),
        Replaced::Failed(reason) => Err(PyValueError::new_err(reason)),
        Replaced::Defer => defer(),
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_replace_pointer, module)?)?;
    module.add_function(wrap_pyfunction!(replace_pointer, module)?)?;
    module.add_function(wrap_pyfunction!(bind_leaves, module)?)?;
    module.add_function(wrap_pyfunction!(leaves, module)?)?;
    module.add_function(wrap_pyfunction!(bind_plain_nested_mappings, module)?)?;
    module.add_function(wrap_pyfunction!(plain_nested_mappings, module)?)?;
    Ok(())
}
