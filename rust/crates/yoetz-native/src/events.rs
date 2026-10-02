//! `yoetz.domain.events` twins: the accepted-record entry digest, `_id_tuple`,
//! `_evidence_result_tuple`, and `_validate_ascii_sorted_unique`.
//!
//! `events_entry_digest(record)` reads the record's primitive fields and digests the
//! accepted-record preimage without building the two frozen JSON objects the reference builds.
//! It returns `None` whenever the record holds anything the reference would not encode on its
//! common path (a non-exact `str` or `int`, a lone surrogate, an out-of-profile integer, an
//! attribute that raises); the Python wrapper then runs the reference, which raises its exact
//! error or returns its exact digest.
//!
//! The tuple validators raise the reference's shape refusal and call the item constructor (or
//! the `evidence_id`/`result_id` module globals, read at call time) for each item in order, so
//! constructor errors surface exactly as in the reference. A member set that is not plainly
//! ASCII, sorted, and unique is handed to the Python `_validate_ascii_sorted_unique`, which raises
//! the exact refusal with its `field` and exception chain.

use pyo3::exceptions::PyNameError;
use pyo3::ffi;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyString, PyTuple};
use yoetz_core::domain::events::{self as core, CoveragePreimage, EntryPreimage};

use crate::registry::{Slot, protocol_error};

static GLOBALS: Slot = Slot::new();
static COVERAGE: Slot = Slot::new();
static FALLBACK_VALIDATE: Slot = Slot::new();
static MAX_REF_LIST: Slot = Slot::new();

const INVALID_EVENT_VALUE_TYPE: &str = "invalid_event_value_type";

/// Bind the module namespace, the exact `Coverage` class, the Python
/// `_validate_ascii_sorted_unique`, and the `MAX_REF_LIST` default bound.
#[pyfunction]
pub fn bind_events(
    globals: Bound<'_, PyDict>,
    coverage: Bound<'_, PyAny>,
    validate: Bound<'_, PyAny>,
    max_ref_list: Bound<'_, PyAny>,
) {
    GLOBALS.set(globals.into_any().unbind());
    COVERAGE.set(coverage.unbind());
    FALLBACK_VALIDATE.set(validate.unbind());
    MAX_REF_LIST.set(max_ref_list.unbind());
}

#[inline]
fn is_exact_str(value: &Bound<'_, PyAny>) -> bool {
    unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) != 0 }
}

#[inline]
fn is_exact_tuple(value: &Bound<'_, PyAny>) -> bool {
    unsafe { ffi::PyTuple_CheckExact(value.as_ptr()) != 0 }
}

/// An exact `str` attribute, or `None` (any raised error is discarded).
fn str_attr<'py>(
    owner: &Bound<'py, PyAny>,
    name: &Bound<'py, PyString>,
) -> Option<Bound<'py, PyString>> {
    let value = owner.getattr(name).ok()?;
    if !is_exact_str(&value) {
        return None;
    }
    Some(unsafe { value.cast_into_unchecked::<PyString>() })
}

/// `owner.<name>.value` as an exact `str` (enum members).
fn enum_value<'py>(
    owner: &Bound<'py, PyAny>,
    name: &Bound<'py, PyString>,
) -> Option<Bound<'py, PyString>> {
    let member = owner.getattr(name).ok()?;
    str_attr(&member, intern!(owner.py(), "value"))
}

/// An exact `int` attribute that fits `i64`.
fn int_attr(owner: &Bound<'_, PyAny>, name: &Bound<'_, PyString>) -> Option<i64> {
    exact_i64(&owner.getattr(name).ok()?)
}

/// An exact tuple of exact `str` members (the frozen JSON array the reference would encode).
fn str_tuple<'py>(
    owner: &Bound<'py, PyAny>,
    name: &Bound<'py, PyString>,
) -> Option<Vec<Bound<'py, PyString>>> {
    let value = owner.getattr(name).ok()?;
    if !is_exact_tuple(&value) {
        return None;
    }
    let tuple = unsafe { value.cast_unchecked::<PyTuple>() };
    let mut items = Vec::with_capacity(tuple.len());
    for item in tuple.iter() {
        if !is_exact_str(&item) {
            return None;
        }
        items.push(unsafe { item.cast_into_unchecked::<PyString>() });
    }
    Some(items)
}

/// `[member.value for member in owner.<name>]` over an exact tuple of enum members.
fn enum_tuple<'py>(
    owner: &Bound<'py, PyAny>,
    name: &Bound<'py, PyString>,
) -> Option<Vec<Bound<'py, PyString>>> {
    let value = owner.getattr(name).ok()?;
    if !is_exact_tuple(&value) {
        return None;
    }
    let tuple = unsafe { value.cast_unchecked::<PyTuple>() };
    let py = owner.py();
    let mut items = Vec::with_capacity(tuple.len());
    for member in tuple.iter() {
        items.push(str_attr(&member, intern!(py, "value"))?);
    }
    Some(items)
}

fn texts<'a>(items: &'a [Bound<'_, PyString>]) -> Option<Vec<&'a str>> {
    items.iter().map(|item| item.to_str().ok()).collect()
}

/// The digest, or `None` when the reference must answer.
fn entry_digest_of(py: Python<'_>, record: &Bound<'_, PyAny>) -> Option<String> {
    let schema = record.getattr(intern!(py, "schema")).ok()?;
    let author = record.getattr(intern!(py, "author")).ok()?;
    let writer = record.getattr(intern!(py, "writer")).ok()?;
    let ledger = record.getattr(intern!(py, "ledger")).ok()?;
    let coverage = record.getattr(intern!(py, "coverage")).ok()?;
    let payload_ref = record.getattr(intern!(py, "payload_ref")).ok()?;
    // `coverage_to_json` refuses anything but the exact class.
    if !coverage.get_type().is(COVERAGE.get(py)?) {
        return None;
    }
    let accepted_at = ledger.getattr(intern!(py, "accepted_at")).ok()?;
    let occurred_at = record.getattr(intern!(py, "occurred_at")).ok()?;

    let protocol = str_attr(record, intern!(py, "protocol"))?;
    let protocol_version = str_attr(record, intern!(py, "protocol_version"))?;
    let event_id = str_attr(record, intern!(py, "event_id"))?;
    let task_id = str_attr(record, intern!(py, "task_id"))?;
    let session_id = str_attr(record, intern!(py, "session_id"))?;
    let schema_name = str_attr(&schema, intern!(py, "name"))?;
    let schema_version = str_attr(&schema, intern!(py, "version"))?;
    let actor_id = str_attr(&author, intern!(py, "actor_id"))?;
    let actor_type = enum_value(&author, intern!(py, "actor_type"))?;
    let assurance = enum_value(&author, intern!(py, "assurance"))?;
    let writer_id = str_attr(&writer, intern!(py, "writer_id"))?;
    let writer_sequence = int_attr(&writer, intern!(py, "sequence"))?;
    let writer_previous = str_attr(&writer, intern!(py, "previous_entry_digest"))?;
    let ingestion_sequence = int_attr(&ledger, intern!(py, "ingestion_sequence"))?;
    let ledger_previous = str_attr(&ledger, intern!(py, "previous_entry_digest"))?;
    let accepted_at = str_attr(&accepted_at, intern!(py, "wire"))?;
    let operation_id = str_attr(record, intern!(py, "operation_id"))?;
    let occurred_at = str_attr(&occurred_at, intern!(py, "wire"))?;
    let causal_parents = str_tuple(record, intern!(py, "causal_parents"))?;
    let publication_channel = enum_value(record, intern!(py, "publication_channel"))?;
    let channels = enum_tuple(&coverage, intern!(py, "publication_channels"))?;
    let authorship = enum_value(&coverage, intern!(py, "authorship_assurance"))?;
    let observation = enum_value(&coverage, intern!(py, "artifact_observation"))?;
    let immutability = enum_value(&coverage, intern!(py, "evidence_immutability"))?;
    let freshness = enum_value(&coverage, intern!(py, "ledger_freshness"))?;
    let check_types = enum_tuple(&coverage, intern!(py, "check_types"))?;
    let known_gaps = str_tuple(&coverage, intern!(py, "known_gaps"))?;
    let object_id = str_attr(&payload_ref, intern!(py, "object_id"))?;
    let media_type = str_attr(&payload_ref, intern!(py, "media_type"))?;
    let plaintext_size = int_attr(&payload_ref, intern!(py, "plaintext_size"))?;
    let commitment = str_attr(&payload_ref, intern!(py, "commitment"))?;
    let encryption_format = str_attr(&payload_ref, intern!(py, "encryption_format"))?;
    let redaction = enum_value(record, intern!(py, "redaction"))?;
    let artifact_refs = str_tuple(record, intern!(py, "artifact_refs"))?;
    let evidence_refs = str_tuple(record, intern!(py, "evidence_refs"))?;

    let causal_parents = texts(&causal_parents)?;
    let channels = texts(&channels)?;
    let check_types = texts(&check_types)?;
    let known_gaps = texts(&known_gaps)?;
    let artifact_refs = texts(&artifact_refs)?;
    let evidence_refs = texts(&evidence_refs)?;
    let preimage = EntryPreimage {
        protocol: protocol.to_str().ok()?,
        protocol_version: protocol_version.to_str().ok()?,
        event_id: event_id.to_str().ok()?,
        task_id: task_id.to_str().ok()?,
        session_id: session_id.to_str().ok()?,
        schema_name: schema_name.to_str().ok()?,
        schema_version: schema_version.to_str().ok()?,
        actor_id: actor_id.to_str().ok()?,
        actor_type: actor_type.to_str().ok()?,
        assurance: assurance.to_str().ok()?,
        writer_id: writer_id.to_str().ok()?,
        writer_sequence,
        writer_previous_entry_digest: writer_previous.to_str().ok()?,
        ingestion_sequence,
        ledger_previous_entry_digest: ledger_previous.to_str().ok()?,
        accepted_at: accepted_at.to_str().ok()?,
        operation_id: operation_id.to_str().ok()?,
        occurred_at: occurred_at.to_str().ok()?,
        causal_parents: &causal_parents,
        publication_channel: publication_channel.to_str().ok()?,
        coverage: CoveragePreimage {
            publication_channels: &channels,
            authorship_assurance: authorship.to_str().ok()?,
            artifact_observation: observation.to_str().ok()?,
            evidence_immutability: immutability.to_str().ok()?,
            ledger_freshness: freshness.to_str().ok()?,
            check_types: &check_types,
            known_gaps: &known_gaps,
        },
        payload_object_id: object_id.to_str().ok()?,
        payload_media_type: media_type.to_str().ok()?,
        payload_plaintext_size: plaintext_size,
        payload_commitment: commitment.to_str().ok()?,
        payload_encryption_format: encryption_format.to_str().ok()?,
        redaction: redaction.to_str().ok()?,
        artifact_refs: &artifact_refs,
        evidence_refs: &evidence_refs,
    };
    core::entry_digest(&preimage).ok()
}

/// `compute_entry_digest(accepted_record_digest_preimage(record))`, or `None` when the record
/// needs the Python reference.
#[pyfunction]
pub fn events_entry_digest(py: Python<'_>, record: &Bound<'_, PyAny>) -> Option<String> {
    entry_digest_of(py, record)
}

/// Whether `members` (an exact tuple) are exact, plainly ASCII, strictly increasing strings.
fn plainly_sorted(members: &Bound<'_, PyTuple>) -> bool {
    let mut items = Vec::with_capacity(members.len());
    for member in members.iter() {
        if !is_exact_str(&member) {
            return false;
        }
        items.push(unsafe { member.cast_into_unchecked::<PyString>() });
    }
    match texts(&items) {
        Some(members) => core::ascii_sorted_unique(members),
        None => false,
    }
}

fn validate_members(
    py: Python<'_>,
    members: &Bound<'_, PyAny>,
    field: &Bound<'_, PyAny>,
) -> PyResult<()> {
    if is_exact_tuple(members) && plainly_sorted(unsafe { members.cast_unchecked::<PyTuple>() }) {
        return Ok(());
    }
    let Some(validate) = FALLBACK_VALIDATE.get(py) else {
        return Err(PyNameError::new_err("_validate_ascii_sorted_unique"));
    };
    let kwargs = PyDict::new(py);
    kwargs.set_item(intern!(py, "field"), field)?;
    validate.call((members,), Some(&kwargs))?;
    Ok(())
}

/// `_validate_ascii_sorted_unique(values, *, field=None) -> None`.
#[pyfunction]
#[pyo3(signature = (values, *, field = None))]
pub fn validate_ascii_sorted_unique(
    py: Python<'_>,
    values: &Bound<'_, PyAny>,
    field: Option<Bound<'_, PyAny>>,
) -> PyResult<()> {
    let field = field.unwrap_or_else(|| py.None().into_bound(py));
    validate_members(py, values, &field)
}

/// `_tuple(value, minimum, maximum)`: the exact-tuple and length gate.
fn shaped<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    minimum: &Bound<'py, PyAny>,
    maximum: Option<Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyTuple>> {
    if !is_exact_tuple(value) {
        return Err(protocol_error(py, INVALID_EVENT_VALUE_TYPE));
    }
    let tuple = unsafe { value.cast_unchecked::<PyTuple>() }.clone();
    let maximum = match maximum {
        Some(maximum) => maximum,
        None => MAX_REF_LIST
            .get(py)
            .ok_or_else(|| PyNameError::new_err("MAX_REF_LIST"))?,
    };
    let within = match (exact_i64(minimum), exact_i64(&maximum)) {
        (Some(low), Some(high)) => {
            let length = tuple.len() as i64;
            low <= length && length <= high
        }
        // `not minimum <= len(items) <= maximum`, with Python's own comparison semantics.
        _ => {
            let length = tuple.len().into_pyobject(py)?;
            minimum.le(&length)? && length.le(&maximum)?
        }
    };
    if !within {
        return Err(protocol_error(py, INVALID_EVENT_VALUE_TYPE));
    }
    Ok(tuple)
}

fn exact_i64(value: &Bound<'_, PyAny>) -> Option<i64> {
    if unsafe { ffi::PyLong_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    value.extract::<i64>().ok()
}

fn zero(py: Python<'_>) -> Bound<'_, PyAny> {
    0_i64
        .into_pyobject(py)
        .map(Bound::into_any)
        .unwrap_or_else(|never| match never {})
}

fn none_if_missing<'py>(py: Python<'py>, field: Option<Bound<'py, PyAny>>) -> Bound<'py, PyAny> {
    field.unwrap_or_else(|| py.None().into_bound(py))
}

/// `_id_tuple(value, constructor, *, minimum=0, maximum=MAX_REF_LIST, field=None)`.
#[pyfunction]
#[pyo3(signature = (value, constructor, *, minimum = None, maximum = None, field = None))]
pub fn id_tuple<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    constructor: &Bound<'py, PyAny>,
    minimum: Option<Bound<'py, PyAny>>,
    maximum: Option<Bound<'py, PyAny>>,
    field: Option<Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyTuple>> {
    let minimum = minimum.unwrap_or_else(|| zero(py));
    let raw = shaped(py, value, &minimum, maximum)?;
    let mut validated = Vec::with_capacity(raw.len());
    for item in raw.iter() {
        validated.push(constructor.call1((item,))?);
    }
    let result = PyTuple::new(py, validated)?;
    validate_members(py, result.as_any(), &none_if_missing(py, field))?;
    Ok(result)
}

/// The first four code points of `text` equal `prefix` (an ASCII literal), like `str.startswith`.
fn starts_with(text: &Bound<'_, PyString>, prefix: &str) -> bool {
    let pointer = text.as_ptr();
    let length = unsafe { ffi::PyUnicode_GetLength(pointer) };
    if length < prefix.len() as isize {
        return false;
    }
    prefix.bytes().enumerate().all(|(index, byte)| {
        let point = unsafe { ffi::PyUnicode_ReadChar(pointer, index as isize) };
        point == u32::from(byte)
    })
}

fn module_global<'py>(py: Python<'py>, name: &Bound<'py, PyString>) -> PyResult<Bound<'py, PyAny>> {
    let globals = GLOBALS
        .get(py)
        .ok_or_else(|| PyNameError::new_err("events"))?;
    let globals = globals.cast::<PyDict>()?;
    globals
        .get_item(name)?
        .ok_or_else(|| PyNameError::new_err(name.to_string()))
}

/// `_evidence_result_tuple(value, *, minimum=0, maximum=MAX_REF_LIST, field=None)`.
#[pyfunction]
#[pyo3(signature = (value, *, minimum = None, maximum = None, field = None))]
pub fn evidence_result_tuple<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    minimum: Option<Bound<'py, PyAny>>,
    maximum: Option<Bound<'py, PyAny>>,
    field: Option<Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyTuple>> {
    let minimum = minimum.unwrap_or_else(|| zero(py));
    let raw = shaped(py, value, &minimum, maximum)?;
    let mut validated = Vec::with_capacity(raw.len());
    for item in raw.iter() {
        if !is_exact_str(&item) {
            return Err(protocol_error(py, INVALID_EVENT_VALUE_TYPE));
        }
        let text = unsafe { item.cast_unchecked::<PyString>() };
        let constructor = if starts_with(text, "evd_") {
            module_global(py, intern!(py, "evidence_id"))?
        } else if starts_with(text, "res_") {
            module_global(py, intern!(py, "result_id"))?
        } else {
            return Err(protocol_error(py, INVALID_EVENT_VALUE_TYPE));
        };
        validated.push(constructor.call1((item,))?);
    }
    let result = PyTuple::new(py, validated)?;
    validate_members(py, result.as_any(), &none_if_missing(py, field))?;
    Ok(result)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_events, module)?)?;
    module.add_function(wrap_pyfunction!(events_entry_digest, module)?)?;
    module.add_function(wrap_pyfunction!(validate_ascii_sorted_unique, module)?)?;
    module.add_function(wrap_pyfunction!(id_tuple, module)?)?;
    module.add_function(wrap_pyfunction!(evidence_result_tuple, module)?)?;
    Ok(())
}
