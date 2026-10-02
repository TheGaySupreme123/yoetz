//! The SQL authorizer callbacks of `yoetz.adapters.sqlite.connection`.
//!
//! SQLite invokes the authorizer for every table, column, and function a statement touches,
//! so these run on every prepare. The twins decide every non-`PRAGMA` action natively. `PRAGMA`
//! decisions consult the module's pragma policy sets, so they (and any argument that is not an
//! exact `int` action with `None`/exact-`str` names) go to the Python reference.

use std::sync::{Arc, Mutex};

use pyo3::exceptions::PyValueError;
use pyo3::ffi;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyString};

struct Bindings {
    globals: Py<PyDict>,
    ok: Py<PyAny>,
    deny: Py<PyAny>,
    select: i64,
    read: i64,
    function: i64,
    recursive: i64,
    pragma: i64,
    attach: i64,
    detach: i64,
    create_vtable: i64,
    drop_vtable: i64,
    python_read_only: Py<PyAny>,
    python_writer: Py<PyAny>,
    python_migration: Py<PyAny>,
    native_writer: Py<PyAny>,
}

static BINDINGS: Mutex<Option<Arc<Bindings>>> = Mutex::new(None);

fn bindings() -> PyResult<Arc<Bindings>> {
    BINDINGS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clone()
        .ok_or_else(|| PyValueError::new_err("sqlite_authorizer_not_bound"))
}

fn required<'py>(source: &Bound<'py, PyDict>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    source
        .get_item(name)?
        .ok_or_else(|| PyValueError::new_err("sqlite_authorizer_binding_missing"))
}

/// Bind `apsw`'s action codes and results, the module globals, and the Python references.
#[pyfunction]
pub fn bind_sqlite_authorizers(source: &Bound<'_, PyDict>) -> PyResult<()> {
    let apsw = required(source, "apsw")?;
    let code = |name: &str| -> PyResult<i64> { apsw.getattr(name)?.extract() };
    let bound = Bindings {
        globals: required(source, "globals")?.cast_into::<PyDict>()?.unbind(),
        ok: apsw.getattr("SQLITE_OK")?.unbind(),
        deny: apsw.getattr("SQLITE_DENY")?.unbind(),
        select: code("SQLITE_SELECT")?,
        read: code("SQLITE_READ")?,
        function: code("SQLITE_FUNCTION")?,
        recursive: code("SQLITE_RECURSIVE")?,
        pragma: code("SQLITE_PRAGMA")?,
        attach: code("SQLITE_ATTACH")?,
        detach: code("SQLITE_DETACH")?,
        create_vtable: code("SQLITE_CREATE_VTABLE")?,
        drop_vtable: code("SQLITE_DROP_VTABLE")?,
        python_read_only: required(source, "python_read_only")?.unbind(),
        python_writer: required(source, "python_writer")?.unbind(),
        python_migration: required(source, "python_migration")?.unbind(),
        native_writer: required(source, "native_writer")?.unbind(),
    };
    *BINDINGS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(Arc::new(bound));
    Ok(())
}

/// An exact-`int` action code that fits `i64`.
fn action_code(action: &Bound<'_, PyAny>) -> Option<i64> {
    if unsafe { ffi::PyLong_CheckExact(action.as_ptr()) } == 0 {
        return None;
    }
    action.extract::<i64>().ok()
}

/// `None` or an exact `str`, as `Some(None)` / `Some(Some(text))`; anything else is `None`.
fn name_of<'a>(value: &'a Bound<'_, PyAny>) -> Option<Option<&'a str>> {
    if value.is_none() {
        return Some(None);
    }
    if unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    // A string with lone surrogates equals none of the ASCII names compared below.
    Some(Some(
        unsafe { value.cast_unchecked::<PyString>() }
            .to_str()
            .unwrap_or("\u{FFFD}"),
    ))
}

enum Decision {
    Ok,
    Deny,
    Reference,
}

impl Bindings {
    fn writer_decision(&self, action: i64, second: Option<&str>) -> Decision {
        if action == self.attach
            || action == self.create_vtable
            || action == self.detach
            || action == self.drop_vtable
        {
            return Decision::Deny;
        }
        if action == self.function && second == Some("load_extension") {
            return Decision::Deny;
        }
        if action == self.pragma {
            return Decision::Reference;
        }
        Decision::Ok
    }

    fn read_only_decision(&self, action: i64) -> Decision {
        if action == self.select
            || action == self.read
            || action == self.function
            || action == self.recursive
        {
            return Decision::Ok;
        }
        if action == self.pragma {
            return Decision::Reference;
        }
        Decision::Deny
    }

    fn result<'py>(
        &self,
        py: Python<'py>,
        decision: Decision,
        reference: &Py<PyAny>,
        args: Args<'_, 'py>,
    ) -> PyResult<Bound<'py, PyAny>> {
        match decision {
            Decision::Ok => Ok(self.ok.bind(py).clone()),
            Decision::Deny => Ok(self.deny.bind(py).clone()),
            Decision::Reference => reference.bind(py).call1(args),
        }
    }
}

type Args<'a, 'py> = (
    &'a Bound<'py, PyAny>,
    &'a Bound<'py, PyAny>,
    &'a Bound<'py, PyAny>,
    &'a Bound<'py, PyAny>,
    &'a Bound<'py, PyAny>,
);

/// Parse the fast-path arguments: exact-int action and `None`/exact-`str` names.
fn fast_args<'a>(
    action: &Bound<'_, PyAny>,
    first: &'a Bound<'_, PyAny>,
    second: &'a Bound<'_, PyAny>,
) -> Option<(i64, Option<&'a str>, Option<&'a str>)> {
    Some((action_code(action)?, name_of(first)?, name_of(second)?))
}

/// `_read_only_authorizer(action, first, second, database, trigger) -> int`.
#[pyfunction]
pub fn sqlite_read_only_authorizer<'py>(
    py: Python<'py>,
    action: &Bound<'py, PyAny>,
    first: &Bound<'py, PyAny>,
    second: &Bound<'py, PyAny>,
    database: &Bound<'py, PyAny>,
    trigger: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let bound = bindings()?;
    let args = (action, first, second, database, trigger);
    let decision = match fast_args(action, first, second) {
        Some((code, _, _)) => bound.read_only_decision(code),
        None => Decision::Reference,
    };
    bound.result(py, decision, &bound.python_read_only, args)
}

/// `_writer_authorizer(action, first, second, database, trigger) -> int`.
#[pyfunction]
pub fn sqlite_writer_authorizer<'py>(
    py: Python<'py>,
    action: &Bound<'py, PyAny>,
    first: &Bound<'py, PyAny>,
    second: &Bound<'py, PyAny>,
    database: &Bound<'py, PyAny>,
    trigger: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let bound = bindings()?;
    let args = (action, first, second, database, trigger);
    let decision = match fast_args(action, first, second) {
        Some((code, _, second)) => bound.writer_decision(code, second),
        None => Decision::Reference,
    };
    bound.result(py, decision, &bound.python_writer, args)
}

/// `_migration_authorizer(action, first, second, database, trigger) -> int`: migration-scoped
/// `PRAGMA`s (reference), otherwise the module's current `_writer_authorizer`.
#[pyfunction]
pub fn sqlite_migration_authorizer<'py>(
    py: Python<'py>,
    action: &Bound<'py, PyAny>,
    first: &Bound<'py, PyAny>,
    second: &Bound<'py, PyAny>,
    database: &Bound<'py, PyAny>,
    trigger: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let bound = bindings()?;
    let args = (action, first, second, database, trigger);
    let Some((code, _, second_name)) = fast_args(action, first, second) else {
        return bound.python_migration.bind(py).call1(args);
    };
    if code == bound.pragma {
        return bound.python_migration.bind(py).call1(args);
    }
    let writer = bound
        .globals
        .bind(py)
        .get_item(intern!(py, "_writer_authorizer"))?
        .ok_or_else(|| PyValueError::new_err("sqlite_authorizer_binding_missing"))?;
    if !writer.is(bound.native_writer.bind(py)) {
        return writer.call1(args);
    }
    bound.result(
        py,
        bound.writer_decision(code, second_name),
        &bound.python_writer,
        args,
    )
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_sqlite_authorizers, module)?)?;
    module.add_function(wrap_pyfunction!(sqlite_read_only_authorizer, module)?)?;
    module.add_function(wrap_pyfunction!(sqlite_writer_authorizer, module)?)?;
    module.add_function(wrap_pyfunction!(sqlite_migration_authorizer, module)?)?;
    Ok(())
}
