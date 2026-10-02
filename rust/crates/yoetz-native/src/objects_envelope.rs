//! `yoetz.adapters.objects.envelope` over live Python objects.
//!
//! The native twins only ever *accept*. Every input the acceptance core refuses, every failure
//! of a Python validator the twins call, and every replaced module attribute the reference
//! would consult goes to the Python reference, which raises the contract's exact exception and
//! exception chain. Object and task identifiers are validated by calling the module's own
//! `object_id`/`task_id` at call time, never by a native grammar.

use std::sync::{Arc, Mutex};

use pyo3::exceptions::PyValueError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDateTime, PyDict, PyString, PyTuple, PyTzInfo};
use pyo3::intern;
use yoetz_core::objects::envelope::{self as core, CreatedAt};

struct Bindings {
    globals: Py<PyDict>,
    header_class: Py<PyAny>,
    envelope_class: Py<PyAny>,
    kind_class: Py<PyAny>,
    kind_by_value: Py<PyDict>,
    object_new: Py<PyAny>,
    json_module: Py<PyAny>,
    stdlib_loads: Py<PyAny>,
    python_decode: Py<PyAny>,
    python_created_at: Py<PyAny>,
    /// Module attributes the reference consults, with the objects they held at bind time.
    watched: Vec<(Py<PyString>, Py<PyAny>)>,
}

static BINDINGS: Mutex<Option<Arc<Bindings>>> = Mutex::new(None);

fn bindings() -> PyResult<Arc<Bindings>> {
    BINDINGS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clone()
        .ok_or_else(|| PyValueError::new_err("object_envelope_not_bound"))
}

fn required<'py>(source: &Bound<'py, PyDict>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    source
        .get_item(name)?
        .ok_or_else(|| PyValueError::new_err("object_envelope_binding_missing"))
}

/// Bind the envelope module's globals, classes, and Python reference functions.
///
/// `watched` names the module attributes whose replacement must route a call to the Python
/// reference; their current values are captured now.
#[pyfunction]
pub fn bind_object_envelope(py: Python<'_>, source: &Bound<'_, PyDict>, watched: &Bound<'_, PyTuple>) -> PyResult<()> {
    let globals = required(source, "globals")?.cast_into::<PyDict>()?;
    let kind_class = required(source, "kind_class")?;
    let kind_by_value = kind_class.getattr("_value2member_map_")?.cast_into::<PyDict>()?;
    let json_module = py.import("json")?;
    let mut captured = Vec::with_capacity(watched.len());
    for name in watched.iter() {
        let name = name.cast_into::<PyString>()?;
        let value = globals
            .get_item(&name)?
            .ok_or_else(|| PyValueError::new_err("object_envelope_binding_missing"))?;
        captured.push((name.unbind(), value.unbind()));
    }
    let bound = Bindings {
        header_class: required(source, "header_class")?.unbind(),
        envelope_class: required(source, "envelope_class")?.unbind(),
        kind_class: kind_class.unbind(),
        kind_by_value: kind_by_value.unbind(),
        object_new: py.import("builtins")?.getattr("object")?.getattr("__new__")?.unbind(),
        stdlib_loads: required(source, "stdlib_loads")?.unbind(),
        json_module: json_module.into_any().unbind(),
        python_decode: required(source, "python_decode")?.unbind(),
        python_created_at: required(source, "python_created_at")?.unbind(),
        globals: globals.unbind(),
        watched: captured,
    };
    *BINDINGS.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(Arc::new(bound));
    Ok(())
}

impl Bindings {
    /// True when no consulted attribute was replaced since binding.
    fn unpatched(&self, py: Python<'_>) -> PyResult<bool> {
        let globals = self.globals.bind(py);
        for (name, original) in &self.watched {
            match globals.get_item(name.bind(py))? {
                Some(current) if current.is(original.bind(py)) => {}
                _ => return Ok(false),
            }
        }
        let loads = self.json_module.bind(py).getattr(intern!(py, "loads"))?;
        Ok(loads.is(self.stdlib_loads.bind(py)))
    }

    fn global<'py>(&self, py: Python<'py>, name: &Bound<'py, PyString>) -> PyResult<Option<Bound<'py, PyAny>>> {
        self.globals.bind(py).get_item(name)
    }

    /// A module integer limit read at call time, or `None` when it is not a plain `int`.
    fn limit(&self, py: Python<'_>, name: &Bound<'_, PyString>) -> PyResult<Option<i64>> {
        match self.global(py, name)? {
            Some(value) if unsafe { ffi::PyLong_CheckExact(value.as_ptr()) } != 0 => Ok(value.extract::<i64>().ok()),
            _ => Ok(None),
        }
    }

    /// Call the module's identifier validator; `false` (error discarded) when it refuses.
    fn identifier_ok(&self, py: Python<'_>, validator: &Bound<'_, PyString>, value: &Bound<'_, PyAny>) -> PyResult<bool> {
        let Some(function) = self.global(py, validator)? else {
            return Ok(false);
        };
        Ok(function.call1((value,)).is_ok())
    }
}

fn is_exact_str(value: &Bound<'_, PyAny>) -> bool {
    unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) != 0 }
}

fn exact_str<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a str> {
    if !is_exact_str(value) {
        return None;
    }
    unsafe { value.cast_unchecked::<PyString>() }.to_str().ok()
}

fn datetime_from<'py>(py: Python<'py>, fields: &CreatedAt) -> PyResult<Bound<'py, PyDateTime>> {
    let utc = PyTzInfo::utc(py)?;
    PyDateTime::new(
        py,
        fields.year,
        fields.month,
        fields.day,
        fields.hour,
        fields.minute,
        fields.second,
        fields.microsecond,
        Some(&*utc),
    )
}

/// `_created_at_from_wire(value) -> datetime`.
#[pyfunction]
pub fn object_envelope_created_at<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    if let Some(fields) = exact_str(value).and_then(core::created_at_from_wire) {
        return Ok(datetime_from(py, &fields)?.into_any());
    }
    bindings()?.python_created_at.bind(py).call1((value,))
}

/// The `ObjectEnvelopeHeader.__post_init__` checks; `False` means "ask the reference".
#[pyfunction]
pub fn object_envelope_header_valid(py: Python<'_>, header: &Bound<'_, PyAny>) -> PyResult<bool> {
    let bound = bindings()?;
    if !bound.unpatched(py)? {
        return Ok(false);
    }
    let created_at = header.getattr(intern!(py, "created_at"))?;
    if exact_str(&created_at).and_then(core::created_at_from_wire).is_none() {
        return Ok(false);
    }
    if !bound.identifier_ok(py, intern!(py, "object_id"), &header.getattr(intern!(py, "object_id"))?)? {
        return Ok(false);
    }
    let task = header.getattr(intern!(py, "task_id"))?;
    if !bound.identifier_ok(py, intern!(py, "task_id"), &task)? {
        return Ok(false);
    }
    if !header.getattr(intern!(py, "object_kind"))?.get_type().is(bound.kind_class.bind(py)) {
        return Ok(false);
    }
    let media_type = header.getattr(intern!(py, "media_type"))?;
    if !exact_str(&media_type).is_some_and(core::is_media_type) {
        return Ok(false);
    }
    let encryption = header.getattr(intern!(py, "encryption_format"))?;
    let key_slot = header.getattr(intern!(py, "key_slot"))?;
    let payload = header.getattr(intern!(py, "payload_algorithm"))?;
    let wrap = header.getattr(intern!(py, "wrap_algorithm"))?;
    if exact_str(&encryption) != Some(core::ENCRYPTION_FORMAT)
        || !exact_str(&key_slot).is_some_and(core::is_key_slot)
        || exact_str(&payload) != Some(core::PAYLOAD_ALGORITHM)
        || exact_str(&wrap) != Some(core::WRAP_ALGORITHM)
    {
        return Ok(false);
    }
    let size = header.getattr(intern!(py, "plaintext_size"))?;
    let Some(maximum) = bound.limit(py, intern!(py, "MAX_OBJECT_PLAINTEXT_BYTES"))? else {
        return Ok(false);
    };
    if unsafe { ffi::PyLong_CheckExact(size.as_ptr()) } == 0 {
        return Ok(false);
    }
    match size.extract::<i64>() {
        Ok(size) if (0..=maximum).contains(&size) => {}
        _ => return Ok(false),
    }
    let dek = header.getattr(intern!(py, "wrapped_dek"))?;
    if unsafe { ffi::PyBytes_CheckExact(dek.as_ptr()) } == 0
        || unsafe { dek.cast_unchecked::<PyBytes>() }.as_bytes().len() != core::WRAPPED_DEK_BYTES
    {
        return Ok(false);
    }
    Ok(true)
}

/// Build an instance of a frozen slotted dataclass without running `__init__`.
fn assemble<'py>(
    py: Python<'py>,
    bound: &Bindings,
    class: &Bound<'py, PyAny>,
    fields: &[(&Bound<'py, PyString>, Bound<'py, PyAny>)],
) -> PyResult<Bound<'py, PyAny>> {
    let instance = bound.object_new.bind(py).call1((class,))?;
    for (name, value) in fields {
        let status = unsafe { ffi::PyObject_GenericSetAttr(instance.as_ptr(), name.as_ptr(), value.as_ptr()) };
        if status != 0 {
            return Err(PyErr::fetch(py));
        }
    }
    Ok(instance)
}

/// `decode_object_envelope(data) -> ObjectEnvelope`.
#[pyfunction]
pub fn object_envelope_decode<'py>(py: Python<'py>, data: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let bound = bindings()?;
    if let Some(envelope) = decode_accepted(py, &bound, data)? {
        return Ok(envelope);
    }
    bound.python_decode.bind(py).call1((data,))
}

fn decode_accepted<'py>(py: Python<'py>, bound: &Bindings, data: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyAny>>> {
    if unsafe { ffi::PyBytes_CheckExact(data.as_ptr()) } == 0 || !bound.unpatched(py)? {
        return Ok(None);
    }
    let (Some(max_header), Some(max_plaintext)) = (
        bound.limit(py, intern!(py, "MAX_OBJECT_HEADER_BYTES"))?,
        bound.limit(py, intern!(py, "MAX_OBJECT_PLAINTEXT_BYTES"))?,
    ) else {
        return Ok(None);
    };
    let (Ok(max_header), Ok(max_plaintext)) = (usize::try_from(max_header), u64::try_from(max_plaintext)) else {
        return Ok(None);
    };
    let bytes = unsafe { data.cast_unchecked::<PyBytes>() }.as_bytes();
    let Some(frame) = core::decode_frame(bytes, max_header, max_plaintext) else {
        return Ok(None);
    };
    let header = &frame.header;
    let Some(kind) = bound.kind_by_value.bind(py).get_item(&header.object_kind)? else {
        return Ok(None);
    };
    let object_id = PyString::new(py, &header.object_id).into_any();
    let task_id = PyString::new(py, &header.task_id).into_any();
    if !bound.identifier_ok(py, intern!(py, "object_id"), &object_id)?
        || !bound.identifier_ok(py, intern!(py, "task_id"), &task_id)?
    {
        return Ok(None);
    }
    let header_object = assemble(
        py,
        bound,
        bound.header_class.bind(py),
        &[
            (intern!(py, "created_at"), PyString::new(py, &header.created_at).into_any()),
            (intern!(py, "encryption_format"), PyString::new(py, core::ENCRYPTION_FORMAT).into_any()),
            (intern!(py, "key_slot"), PyString::new(py, &header.key_slot).into_any()),
            (intern!(py, "media_type"), PyString::new(py, &header.media_type).into_any()),
            (intern!(py, "object_id"), object_id),
            (intern!(py, "object_kind"), kind),
            (intern!(py, "payload_algorithm"), PyString::new(py, core::PAYLOAD_ALGORITHM).into_any()),
            (intern!(py, "plaintext_size"), header.plaintext_size.into_pyobject(py)?.into_any()),
            (intern!(py, "task_id"), task_id),
            (intern!(py, "wrap_algorithm"), PyString::new(py, core::WRAP_ALGORITHM).into_any()),
            (intern!(py, "wrapped_dek"), PyBytes::new(py, &header.wrapped_dek).into_any()),
        ],
    )?;
    let envelope = assemble(
        py,
        bound,
        bound.envelope_class.bind(py),
        &[
            (intern!(py, "header"), header_object),
            (intern!(py, "header_bytes"), PyBytes::new(py, &bytes[9..frame.header_end]).into_any()),
            (intern!(py, "payload_nonce"), PyBytes::new(py, &bytes[frame.header_end..frame.nonce_end]).into_any()),
            (intern!(py, "ciphertext"), PyBytes::new(py, &bytes[frame.nonce_end..frame.ciphertext_end]).into_any()),
            (intern!(py, "tag"), PyBytes::new(py, &bytes[frame.ciphertext_end..]).into_any()),
        ],
    )?;
    Ok(Some(envelope))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_object_envelope, module)?)?;
    module.add_function(wrap_pyfunction!(object_envelope_created_at, module)?)?;
    module.add_function(wrap_pyfunction!(object_envelope_header_valid, module)?)?;
    module.add_function(wrap_pyfunction!(object_envelope_decode, module)?)?;
    Ok(())
}
