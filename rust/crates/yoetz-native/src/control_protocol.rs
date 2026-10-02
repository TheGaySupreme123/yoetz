//! `yoetz.service.control_protocol._plain_wire_value` over live Python objects.
//!
//! Exact `dict`, exact `JsonObject`, exact `list`/`tuple`, and plain scalars are walked natively;
//! they can be none of the reference's earlier cases (`Enum`, `ControlError`, `BaseModel`,
//! dataclass). Every other node goes to the Python reference, which recurses through the module
//! global and so comes back here for its members.

use pyo3::exceptions::PyTypeError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};

use crate::registry::Slot;
use crate::walk::{
    JSON_OBJECT, NATIVE_RECURSION_LIMIT, is_exact, is_plain_scalar, is_type, json_object_items,
};

static REFERENCE: Slot = Slot::new();

/// Bind the Python reference `_plain_wire_value`.
#[pyfunction]
#[pyo3(name = "control_bind_plain_wire_value")]
pub fn bind_plain_wire_value(reference: Bound<'_, PyAny>) {
    REFERENCE.set(reference.unbind());
}

struct Walker<'py> {
    py: Python<'py>,
    reference: Bound<'py, PyAny>,
    json_object: Option<Bound<'py, PyAny>>,
}

impl<'py> Walker<'py> {
    fn member(
        &self,
        key: &Bound<'py, PyAny>,
        member: &Bound<'py, PyAny>,
        out: &Bound<'py, PyDict>,
        depth: usize,
    ) -> PyResult<()> {
        if !is_exact(key, ffi::PyUnicode_CheckExact) {
            return Err(PyTypeError::new_err("control_object_key_invalid"));
        }
        out.set_item(key, self.plain(member, depth + 1)?)
    }

    fn plain(&self, value: &Bound<'py, PyAny>, depth: usize) -> PyResult<Bound<'py, PyAny>> {
        if is_plain_scalar(value) {
            return Ok(value.clone());
        }
        if depth > NATIVE_RECURSION_LIMIT {
            return self.reference.call1((value,));
        }
        if is_exact(value, ffi::PyDict_CheckExact) {
            let out = PyDict::new(self.py);
            for (key, member) in unsafe { value.cast_unchecked::<PyDict>() }.iter() {
                self.member(&key, &member, &out, depth)?;
            }
            return Ok(out.into_any());
        }
        if let Some(class) = &self.json_object {
            if is_type(value, class) {
                let out = PyDict::new(self.py);
                for pair in json_object_items(value)?.iter() {
                    let pair = pair.cast_into::<PyTuple>()?;
                    self.member(&pair.get_item(0)?, &pair.get_item(1)?, &out, depth)?;
                }
                return Ok(out.into_any());
            }
        }
        if is_exact(value, ffi::PyList_CheckExact) {
            let list = unsafe { value.cast_unchecked::<PyList>() };
            let mut members = Vec::with_capacity(list.len());
            let mut index = 0;
            while index < list.len() {
                members.push(self.plain(&list.get_item(index)?, depth + 1)?);
                index += 1;
            }
            return Ok(PyList::new(self.py, members)?.into_any());
        }
        if is_exact(value, ffi::PyTuple_CheckExact) {
            let tuple = unsafe { value.cast_unchecked::<PyTuple>() };
            let mut members = Vec::with_capacity(tuple.len());
            for item in tuple.iter() {
                members.push(self.plain(&item, depth + 1)?);
            }
            return Ok(PyList::new(self.py, members)?.into_any());
        }
        self.reference.call1((value,))
    }
}

/// `_plain_wire_value(value)`.
#[pyfunction]
#[pyo3(name = "control_plain_wire_value")]
pub fn plain_wire_value<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    if is_plain_scalar(value) {
        return Ok(value.clone());
    }
    let Some(reference) = REFERENCE.get(py) else {
        return Err(pyo3::exceptions::PyRuntimeError::new_err(
            "plain_wire_value_unbound",
        ));
    };
    let walker = Walker {
        py,
        reference,
        json_object: JSON_OBJECT.get(py),
    };
    walker.plain(value, 0)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_plain_wire_value, module)?)?;
    module.add_function(wrap_pyfunction!(plain_wire_value, module)?)?;
    crate::control_pipeline::register(module)?;
    Ok(())
}
