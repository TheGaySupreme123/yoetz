//! `yoetz.kernel.projections` hot loops over live Python objects.
//!
//! `ProjectionState.__post_init__` rebuilds every collection on each fold step. Entries the
//! already-validated prior projection holds by identity are carried without re-validation
//! (issue #886); everything else goes back to the Python `admit` callback, which runs the
//! reference's per-entry validation and returns the key and value to store. The twins below only
//! replace the loop: no record is constructed, read or validated here.

use std::os::raw::{c_int, c_void};
use std::ptr;

use pyo3::exceptions::PyTypeError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

/// The exact `dict` *value* is, or the exact `dict` a `mappingproxy` wraps; `None` otherwise.
///
/// A `mappingproxy` exposes its wrapped mapping only through its GC traversal (as
/// `gc.get_referents` does); the pointer is borrowed from the proxy, which keeps it alive.
pub(crate) fn trusted_dict(value: &Bound<'_, PyAny>) -> Option<*mut ffi::PyObject> {
    let pointer = value.as_ptr();
    unsafe {
        if ffi::PyDict_CheckExact(pointer) != 0 {
            return Some(pointer);
        }
        let kind = ffi::Py_TYPE(pointer);
        if kind != ptr::addr_of_mut!(ffi::PyDictProxy_Type) {
            return None;
        }
        let traverse = (*kind).tp_traverse?;
        unsafe extern "C" fn first_referent(object: *mut ffi::PyObject, arg: *mut c_void) -> c_int {
            let slot = arg as *mut *mut ffi::PyObject;
            unsafe {
                if (*slot).is_null() {
                    *slot = object;
                }
            }
            0
        }
        let mut found: *mut ffi::PyObject = ptr::null_mut();
        traverse(
            pointer,
            first_referent,
            (&mut found as *mut *mut ffi::PyObject).cast(),
        );
        if found.is_null() || ffi::PyDict_CheckExact(found) == 0 {
            return None;
        }
        Some(found)
    }
}

/// Every `(key, value)` of an exact dict, in insertion order, as owned references.
pub(crate) fn dict_items<'py>(
    dict: &Bound<'py, PyDict>,
) -> Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)> {
    let py = dict.py();
    let mut items = Vec::with_capacity(dict.len());
    let mut position: ffi::Py_ssize_t = 0;
    let mut key: *mut ffi::PyObject = ptr::null_mut();
    let mut value: *mut ffi::PyObject = ptr::null_mut();
    unsafe {
        while ffi::PyDict_Next(dict.as_ptr(), &mut position, &mut key, &mut value) != 0 {
            items.push((
                Bound::from_borrowed_ptr(py, key),
                Bound::from_borrowed_ptr(py, value),
            ));
        }
    }
    items
}

#[inline]
fn is_simple_key(key: &Bound<'_, PyAny>) -> bool {
    unsafe {
        ffi::PyUnicode_CheckExact(key.as_ptr()) != 0 || ffi::PyLong_CheckExact(key.as_ptr()) != 0
    }
}

/// `admit(key, value)` must return exactly a `(key, value)` pair.
fn admitted<'py>(
    admit: &Bound<'py, PyAny>,
    key: &Bound<'py, PyAny>,
    value: &Bound<'py, PyAny>,
) -> PyResult<(Bound<'py, PyAny>, Bound<'py, PyAny>)> {
    let pair = admit.call1((key, value))?;
    let tuple = pair
        .cast::<PyTuple>()
        .map_err(|_| PyTypeError::new_err("admit_result_not_pair"))?;
    if tuple.len() != 2 {
        return Err(PyTypeError::new_err("admit_result_not_pair"));
    }
    Ok((tuple.get_item(0)?, tuple.get_item(1)?))
}

/// The shared walk. `lookup` selects the reference's carry rule:
///
/// * `true`: carry when `trusted.get(key) is value` (a positional walk over the trusted dict
///   answers that without hashing while both dicts hold the identical key at the same position);
/// * `false`: carry only an identical key and value at the same position as the trusted prior.
///
/// Uncarried entries go through `admit` in source order, so the first refusal is the
/// reference's. The result keeps source insertion order and the source's key objects.
fn carry<'py>(
    source: &Bound<'py, PyAny>,
    trusted: &Bound<'py, PyAny>,
    admit: &Bound<'py, PyAny>,
    lookup: bool,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let py = source.py();
    let Ok(source) = source.cast_exact::<PyDict>() else {
        return Ok(None);
    };
    let Some(trusted) = trusted_dict(trusted) else {
        return Ok(None);
    };
    // Keep the trusted dict alive independently of the caller's proxy for the whole walk.
    let trusted = unsafe { Bound::from_borrowed_ptr(py, trusted) };
    let items = dict_items(source);
    let mut misses: Vec<(usize, Bound<'py, PyAny>, Bound<'py, PyAny>)> = Vec::new();
    let mut key_changed = false;
    let mut parallel = true;
    let mut trusted_position: ffi::Py_ssize_t = 0;
    for (index, (key, value)) in items.iter().enumerate() {
        let mut carried: Option<bool> = None;
        if parallel {
            let mut trusted_key: *mut ffi::PyObject = ptr::null_mut();
            let mut trusted_value: *mut ffi::PyObject = ptr::null_mut();
            let more = unsafe {
                ffi::PyDict_Next(
                    trusted.as_ptr(),
                    &mut trusted_position,
                    &mut trusted_key,
                    &mut trusted_value,
                )
            };
            if more != 0 && trusted_key == key.as_ptr() && (!lookup || is_simple_key(key)) {
                carried = Some(trusted_value == value.as_ptr());
            } else {
                parallel = false;
            }
        }
        let carried = match carried {
            Some(found) => found,
            None if lookup => {
                let found = unsafe { ffi::PyDict_GetItemWithError(trusted.as_ptr(), key.as_ptr()) };
                if found.is_null() {
                    if let Some(error) = PyErr::take(py) {
                        return Err(error);
                    }
                    false
                } else {
                    found == value.as_ptr()
                }
            }
            None => false,
        };
        if !carried {
            let (new_key, new_value) = admitted(admit, key, value)?;
            if new_key.as_ptr() != key.as_ptr() {
                key_changed = true;
            }
            misses.push((index, new_key, new_value));
        }
    }
    if !key_changed {
        let copied =
            unsafe { Bound::from_owned_ptr_or_err(py, ffi::PyDict_Copy(source.as_ptr()))? };
        let copied = unsafe { copied.cast_into_unchecked::<PyDict>() };
        for (index, new_key, new_value) in &misses {
            if new_value.as_ptr() != items[*index].1.as_ptr() {
                copied.set_item(new_key, new_value)?;
            }
        }
        return Ok(Some(copied));
    }
    let result = PyDict::new(py);
    let mut pending = misses.iter().peekable();
    for (index, (key, value)) in items.iter().enumerate() {
        match pending.peek() {
            Some((miss, new_key, new_value)) if *miss == index => {
                result.set_item(new_key, new_value)?;
                pending.next();
            }
            _ => result.set_item(key, value)?,
        }
    }
    Ok(Some(result))
}

/// Twin of `projections._carry_trusted` for an exact `dict` source and a `dict`/`mappingproxy`
/// trusted prior; `None` when the inputs are outside that shape (the caller runs the reference).
#[pyfunction]
fn projection_carry_trusted<'py>(
    source: &Bound<'py, PyAny>,
    trusted: &Bound<'py, PyAny>,
    admit: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    carry(source, trusted, admit, true)
}

/// Accelerated `projections._carry_positional`: carries only entries the validated prior holds
/// as the identical key and value at the same position; admits every other entry.
#[pyfunction]
fn projection_carry_positional<'py>(
    source: &Bound<'py, PyAny>,
    trusted: &Bound<'py, PyAny>,
    admit: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    carry(source, trusted, admit, false)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(projection_carry_trusted, module)?)?;
    module.add_function(wrap_pyfunction!(projection_carry_positional, module)?)?;
    Ok(())
}
