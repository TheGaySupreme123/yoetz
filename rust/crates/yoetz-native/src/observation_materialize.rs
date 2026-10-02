//! Deterministic identities of `yoetz.application.observation_materialize`, plus the shared
//! UUIDv4-from-digest helper `lineage_coordinator._stable_id` and
//! `ObservationCoordinator._stable_operation_id` use.
//!
//! Every twin answers only for exact `str` (and exact `tuple`/`list`) inputs it can digest
//! byte-identically and returns `None` otherwise; the Python wrapper then runs the reference,
//! which raises its own refusal (a lone surrogate's `UnicodeEncodeError`, an unsupported
//! mapping version's `ValueError`, a profile refusal from `JsonObject`).

use std::sync::Mutex;

use pyo3::exceptions::PyValueError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyString, PyTuple};
use yoetz_core::application::observation_materialize as core;
use yoetz_core::protocol::canonical::{self as canonical, Value};

use crate::ids::kind_prefix;

struct Binding {
    id_domain: Vec<u8>,
    logical_domain: String,
    current_version: String,
    legacy_versions: Vec<String>,
    session_bound_versions: Vec<String>,
}

static BINDING: Mutex<Option<Binding>> = Mutex::new(None);

/// Exact `str` content, or `None` (another type, or a lone surrogate).
pub(crate) fn exact_str<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a str> {
    if unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    unsafe { value.cast_unchecked::<PyString>() }.to_str().ok()
}

/// The members of an exact `tuple` (or, with `lists`, an exact `list`) of exact `str`.
pub(crate) fn exact_strs(value: &Bound<'_, PyAny>, lists: bool) -> Option<Vec<String>> {
    let pointer = value.as_ptr();
    let items: Vec<Bound<'_, PyAny>> = if unsafe { ffi::PyTuple_CheckExact(pointer) } != 0 {
        unsafe { value.cast_unchecked::<PyTuple>() }
            .iter()
            .collect()
    } else if lists && unsafe { ffi::PyList_CheckExact(pointer) } != 0 {
        unsafe { value.cast_unchecked::<PyList>() }.iter().collect()
    } else {
        return None;
    };
    items
        .iter()
        .map(|item| exact_str(item).map(str::to_owned))
        .collect()
}

/// Bind the module's domains and mapping-version tables.
#[pyfunction]
pub fn materialize_bind(
    id_domain: &Bound<'_, PyBytes>,
    logical_domain: &str,
    current_version: &str,
    legacy_versions: Vec<String>,
    session_bound_versions: Vec<String>,
) {
    *BINDING
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(Binding {
        id_domain: id_domain.as_bytes().to_vec(),
        logical_domain: logical_domain.to_owned(),
        current_version: current_version.to_owned(),
        legacy_versions,
        session_bound_versions,
    });
}

fn with_binding<T>(body: impl FnOnce(&Binding) -> Option<T>) -> PyResult<Option<T>> {
    let guard = BINDING
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let Some(binding) = guard.as_ref() else {
        return Err(PyValueError::new_err("materialize_unbound"));
    };
    Ok(body(binding))
}

/// `stable_observation_id(kind=..., task_id=..., source_identity=..., mapping_version=...,
/// role=...)`.
#[pyfunction]
pub fn materialize_stable_observation_id(
    kind: &Bound<'_, PyAny>,
    task_id: &Bound<'_, PyAny>,
    source_identity: &Bound<'_, PyAny>,
    mapping_version: &Bound<'_, PyAny>,
    role: &Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    let Some(prefix) = kind_prefix(kind) else {
        return Ok(None);
    };
    // ``IdKind`` mixes in ``str``: a member's text is its ``value``.
    let Some(kind_value) = kind
        .cast::<PyString>()
        .ok()
        .and_then(|text| text.to_str().ok())
    else {
        return Ok(None);
    };
    let (Some(task), Some(source), Some(mapping), Some(role)) = (
        exact_str(task_id),
        exact_str(source_identity),
        exact_str(mapping_version),
        exact_str(role),
    ) else {
        return Ok(None);
    };
    with_binding(|binding| {
        let uuid = core::stable_observation_uuid(
            &binding.id_domain,
            [kind_value, task, source, mapping, role],
        );
        Some(prefix + &uuid)
    })
}

/// `_logical_identity_digest(components)`.
#[pyfunction]
pub fn materialize_logical_identity_digest(
    components: &Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    let Some(parts) = exact_strs(components, false) else {
        return Ok(None);
    };
    with_binding(|binding| {
        let parts: Vec<&str> = parts.iter().map(String::as_str).collect();
        Some(core::logical_identity_digest(
            &binding.logical_domain,
            &parts,
        ))
    })
}

/// `observation_operation_digest(...)`, keyword arguments resolved by the wrapper.
#[pyfunction]
pub fn materialize_operation_digest(
    task_id: &Bound<'_, PyAny>,
    logical_identity: &Bound<'_, PyAny>,
    draft_roles: &Bound<'_, PyAny>,
    mapping_version: &Bound<'_, PyAny>,
    session_id: &Bound<'_, PyAny>,
    writer_id: &Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    let (Some(task), Some(logical), Some(roles), Some(mapping)) = (
        exact_str(task_id),
        exact_str(logical_identity),
        exact_strs(draft_roles, true),
        exact_str(mapping_version),
    ) else {
        return Ok(None);
    };
    let session = (!session_id.is_none()).then(|| exact_str(session_id));
    let writer = (!writer_id.is_none()).then(|| exact_str(writer_id));
    with_binding(|binding| {
        let supported = mapping == binding.current_version
            || binding.legacy_versions.iter().any(|item| item == mapping);
        if !supported {
            return None;
        }
        let text = |value: &str| Value::Str(value.to_owned());
        let mut members = vec![
            ("protocol".to_owned(), text("yoetz")),
            ("kind".to_owned(), text("observation_materialize")),
            ("task_id".to_owned(), text(task)),
            ("logical_identity".to_owned(), text(logical)),
            (
                "roles".to_owned(),
                Value::Array(roles.into_iter().map(Value::Str).collect()),
            ),
            ("mapping_version".to_owned(), text(mapping)),
        ];
        if binding
            .session_bound_versions
            .iter()
            .any(|item| item == mapping)
        {
            let (Some(Some(session)), Some(Some(writer))) = (session, writer) else {
                return None;
            };
            members.push(("session_id".to_owned(), text(session)));
            members.push(("writer_id".to_owned(), text(writer)));
        } else if session.is_some() || writer.is_some() {
            return None;
        }
        canonical::request_digest(&Value::Object(members)).ok()
    })
}

/// `prefix + str(uuid.UUID(bytes=bytes.fromhex(digest.removeprefix("sha256:")[:32])))` with the
/// version and variant forced, for 32 plain hex digits only.
#[pyfunction]
pub fn stable_uuid4_from_hex_digest(digest: &Bound<'_, PyAny>, prefix: &str) -> Option<String> {
    let text = exact_str(digest)?;
    core::uuid4_from_hex_digest(text).map(|uuid| prefix.to_owned() + &uuid)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(materialize_bind, module)?)?;
    module.add_function(wrap_pyfunction!(materialize_stable_observation_id, module)?)?;
    module.add_function(wrap_pyfunction!(
        materialize_logical_identity_digest,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(materialize_operation_digest, module)?)?;
    module.add_function(wrap_pyfunction!(stable_uuid4_from_hex_digest, module)?)?;
    Ok(())
}
