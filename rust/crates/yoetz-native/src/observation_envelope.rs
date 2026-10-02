//! `yoetz.domain.observation.observation_envelope_from_json` and the batch decoder
//! `SqliteObservationStore._envelopes_from_rows` built on it.
//!
//! The twin performs the reference's own steps (exact `JsonObject`, the nine-key set, the
//! `ObservationSource` lookup, `observation_cursor_from_json`) natively and calls every
//! collaborator the reference calls (`ObservationCursor`, `timestamp_from_string`,
//! `_structural_payload`, `_content_object_refs`, `_sorted_unique_gap_codes`,
//! `ObservationEnvelope`) through the module namespace in the reference's order, so the
//! envelope is still constructed and validated by Python and every refusal those raise is the
//! reference's. When a collaborator global was replaced, or an input needs one of the
//! reference's own refusals, the twin returns `None` and the wrapper runs the reference.

use pyo3::exceptions::{PyException, PyNameError};
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyString, PyTuple};

use crate::registry::Slot;
use crate::walk::{JSON_OBJECT, is_type, json_object_items};

static GLOBALS: Slot = Slot::new();
/// The collaborators as bound at import time, by name.
static ORIGINALS: Slot = Slot::new();
/// The Python `observation_envelope_from_json`.
static REFERENCE: Slot = Slot::new();

const ENVELOPE_KEYS: [&str; 9] = [
    "session_commitment",
    "event_kind",
    "source_identity",
    "source",
    "cursor",
    "receipt_time",
    "structural_payload",
    "content_object_refs",
    "gap_codes",
];
const CURSOR_KEYS: [&str; 5] = [
    "source_generation",
    "byte_position",
    "event_position",
    "last_source_commitment",
    "mapping_version",
];

/// Bind the module namespace, the collaborators the twin may stand in for or call (name ->
/// original), and the Python reference.
#[pyfunction]
pub fn envelope_bind(globals: Bound<'_, PyDict>, originals: Bound<'_, PyDict>, reference: Bound<'_, PyAny>) {
    GLOBALS.set(globals.into_any().unbind());
    ORIGINALS.set(originals.into_any().unbind());
    REFERENCE.set(reference.unbind());
}

fn slot<'py>(py: Python<'py>, slot: &Slot) -> PyResult<Bound<'py, PyAny>> {
    slot.get(py).ok_or_else(|| PyNameError::new_err("yoetz_native_envelope_unbound"))
}

/// The collaborators, or `None` when any global differs from the one bound at import.
fn collaborators<'py>(py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
    let globals = slot(py, &GLOBALS)?;
    let globals = globals.cast::<PyDict>()?;
    let originals = slot(py, &ORIGINALS)?;
    let originals = originals.cast_into::<PyDict>()?;
    for (name, original) in originals.iter() {
        match globals.get_item(&name)? {
            Some(current) if current.is(&original) => {}
            _ => return Ok(None),
        }
    }
    Ok(Some(originals))
}

fn get<'py>(table: &Bound<'py, PyDict>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    table.get_item(name)?.ok_or_else(|| PyNameError::new_err(name.to_owned()))
}

/// The members of an exact `JsonObject` whose key set is exactly `keys`, in `keys` order.
fn exact_members<'py>(value: &Bound<'py, PyAny>, keys: &[&str]) -> PyResult<Option<Vec<Bound<'py, PyAny>>>> {
    let py = value.py();
    let Some(class) = JSON_OBJECT.get(py) else {
        return Ok(None);
    };
    if !is_type(value, &class) {
        return Ok(None);
    }
    let Ok(items) = json_object_items(value) else {
        return Ok(None);
    };
    if items.len() != keys.len() {
        return Ok(None);
    }
    let mut members: Vec<Option<Bound<'py, PyAny>>> = vec![None; keys.len()];
    for pair in items.iter() {
        let Ok(pair) = pair.cast_into::<PyTuple>() else {
            return Ok(None);
        };
        if pair.len() != 2 {
            return Ok(None);
        }
        let key = pair.get_item(0)?;
        if unsafe { ffi::PyUnicode_CheckExact(key.as_ptr()) } == 0 {
            return Ok(None);
        }
        let Ok(text) = unsafe { key.cast_unchecked::<PyString>() }.to_str() else {
            return Ok(None);
        };
        let Some(index) = keys.iter().position(|name| *name == text) else {
            return Ok(None);
        };
        if members[index].is_some() {
            return Ok(None);
        }
        members[index] = Some(pair.get_item(1)?);
    }
    Ok(members.into_iter().collect())
}

fn kwargs<'py>(py: Python<'py>, keys: &[&str], values: &[Bound<'py, PyAny>]) -> PyResult<Bound<'py, PyDict>> {
    let arguments = PyDict::new(py);
    for (key, value) in keys.iter().zip(values) {
        arguments.set_item(*key, value)?;
    }
    Ok(arguments)
}

/// The envelope, or `None` to run the reference.
fn envelope_from_json<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyAny>>> {
    let Some(table) = collaborators(py)? else {
        return Ok(None);
    };
    let Some(fields) = exact_members(value, &ENVELOPE_KEYS)? else {
        return Ok(None);
    };
    // ``ObservationSource(source)``: an exact ``str`` naming a member resolves through the
    // enum's value map; anything else needs the reference's refusal.
    let raw_source = &fields[3];
    if unsafe { ffi::PyUnicode_CheckExact(raw_source.as_ptr()) } == 0 {
        return Ok(None);
    }
    let source_class = get(&table, "ObservationSource")?;
    let value_map = source_class.getattr(pyo3::intern!(py, "_value2member_map_"))?;
    let Ok(value_map) = value_map.cast_into::<PyDict>() else {
        return Ok(None);
    };
    let Some(source) = value_map.get_item(raw_source)? else {
        return Ok(None);
    };
    // ``observation_cursor_from_json(cursor)``.
    let Some(cursor_fields) = exact_members(&fields[4], &CURSOR_KEYS)? else {
        return Ok(None);
    };
    let cursor = get(&table, "ObservationCursor")?.call((), Some(&kwargs(py, &CURSOR_KEYS, &cursor_fields)?))?;
    let receipt_time = get(&table, "timestamp_from_string")?.call1((&fields[5],))?;
    let structural = get(&table, "_structural_payload")?.call1((&fields[6],))?;
    let content_refs = get(&table, "_content_object_refs")?.call1((&fields[7],))?;
    let gap_codes = get(&table, "_sorted_unique_gap_codes")?.call1((&fields[8],))?;
    let arguments = [
        fields[0].clone(),
        fields[1].clone(),
        fields[2].clone(),
        source,
        cursor,
        receipt_time,
        structural,
        content_refs,
        gap_codes,
    ];
    let envelope = get(&table, "ObservationEnvelope")?.call((), Some(&kwargs(py, &ENVELOPE_KEYS, &arguments)?))?;
    Ok(Some(envelope))
}

/// `observation_envelope_from_json(value)`, or `None` to run the reference.
#[pyfunction]
#[pyo3(name = "envelope_from_json")]
pub fn envelope_from_json_py<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyAny>>> {
    envelope_from_json(py, value)
}

/// `SqliteObservationStore._envelopes_from_rows(rows)`: each row's first column parsed as
/// strict JSON (a parse refusal propagates, like the reference's), frozen, and decoded; rows
/// whose blob is not `bytes`, not an object, or not a valid envelope are skipped. `None` when
/// the domain twin is unbound.
#[pyfunction]
pub fn observation_envelopes_from_rows<'py>(py: Python<'py>, rows: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyTuple>>> {
    // Unbound when the domain module kept its Python implementations: run the reference loop.
    let Some(reference) = REFERENCE.get(py) else {
        return Ok(None);
    };
    let mut result = Vec::new();
    for row in rows.try_iter()? {
        let blob = row?.get_item(0)?;
        if unsafe { ffi::PyBytes_CheckExact(blob.as_ptr()) } == 0 {
            continue;
        }
        let parsed = crate::canonical::strict_json_parse(py, &blob, true)?;
        // ``strict_json_parse`` builds exact ``dict`` objects, the only ``Mapping`` it returns.
        if unsafe { ffi::PyDict_CheckExact(parsed.as_ptr()) } == 0 {
            continue;
        }
        let decoded = crate::values::freeze_json(py, &parsed).and_then(|frozen| match envelope_from_json(py, &frozen)? {
            Some(envelope) => Ok(envelope),
            None => reference.call1((frozen,)),
        });
        match decoded {
            Ok(envelope) => result.push(envelope),
            Err(error) if error.is_instance_of::<PyException>(py) => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(Some(PyTuple::new(py, result)?))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(envelope_bind, module)?)?;
    module.add_function(wrap_pyfunction!(envelope_from_json_py, module)?)?;
    module.add_function(wrap_pyfunction!(observation_envelopes_from_rows, module)?)?;
    Ok(())
}
