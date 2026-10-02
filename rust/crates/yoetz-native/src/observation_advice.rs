//! `yoetz.kernel.policies.observation_advice` envelope rules over live envelopes.
//!
//! One pass flattens every `ObservationEnvelope` into `yoetz_core::observation_advice::Envelope`
//! (each payload field read once), the core runs every envelope-walking rule, and this binding
//! hands back the cited envelopes' Python objects so the Python wrapper builds the reference's
//! candidates (and digests) in the reference's order. Any value the flattening cannot represent
//! exactly makes the scan return `None`, and the Python reference runs instead.

use std::collections::HashSet;
use std::sync::{Arc, Mutex};

use pyo3::exceptions::PyValueError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyList, PyString, PyTuple};
use yoetz_core::observation_advice::{
    self as core, CheckFact, CorrelationKey, Envelope, Vocabulary,
};

use crate::registry::Slot;

/// Payload field names, in `FIELDS` order, bound from the module's `_FIELD_*` constants.
static FIELD_NAMES: Slot = Slot::new();
static VOCABULARY: Mutex<Option<Arc<Vocabulary>>> = Mutex::new(None);

const TOOL_NAME: usize = 0;
const EXIT_STATUS: usize = 1;
const SUCCESS: usize = 2;
const CLAIM_KIND: usize = 3;
const RESULT_STATUS: usize = 4;
const CHANGED_PATHS_DIGEST: usize = 5;
const MAPPING_HINT: usize = 6;
const SUBAGENT_ID: usize = 7;
const ACTION: usize = 8;
const ATTEMPT: usize = 9;
const DENIED: usize = 10;
const COMMAND_COMMITMENT: usize = 11;
const CORRELATION_ID: usize = 12;
const TOOL_CALL_ID: usize = 13;
const FIELD_COUNT: usize = 14;

fn texts(values: &Bound<'_, PyAny>) -> PyResult<Vec<String>> {
    let mut out = Vec::new();
    for item in values.try_iter()? {
        out.push(item?.cast_into::<PyString>()?.to_str()?.to_owned());
    }
    Ok(out)
}

/// Bind the module constants the rules read: payload field names (in `FIELDS` order) and the
/// tool, action and hint vocabularies.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn advice_bind(
    field_names: Bound<'_, PyTuple>,
    edit_tools: Bound<'_, PyAny>,
    verification_tools: Bound<'_, PyAny>,
    command_tools: Bound<'_, PyAny>,
    routine_read_actions: Bound<'_, PyAny>,
    static_check_hints: Bound<'_, PyAny>,
    live_claim_hints: Bound<'_, PyAny>,
    post_tool_event_kinds: Bound<'_, PyAny>,
    originating_tool_actions: Bound<'_, PyAny>,
) -> PyResult<()> {
    if field_names.len() != FIELD_COUNT {
        return Err(PyValueError::new_err("observation_advice_invalid"));
    }
    let set = |values: &Bound<'_, PyAny>| -> PyResult<HashSet<String>> {
        Ok(texts(values)?.into_iter().collect())
    };
    let vocabulary = Vocabulary {
        edit_tools: set(&edit_tools)?,
        verification_tools: set(&verification_tools)?,
        command_tools: set(&command_tools)?,
        routine_read_actions: set(&routine_read_actions)?,
        static_check_hints: texts(&static_check_hints)?,
        live_claim_hints: texts(&live_claim_hints)?,
        post_tool_event_kinds: set(&post_tool_event_kinds)?,
        originating_tool_actions: set(&originating_tool_actions)?,
    };
    FIELD_NAMES.set(field_names.into_any().unbind());
    *VOCABULARY
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(Arc::new(vocabulary));
    Ok(())
}

#[inline]
fn is_exact_str(value: &Bound<'_, PyAny>) -> bool {
    unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) != 0 }
}

/// An exact `str`'s text; `Err(())` for a `str` the flattening cannot hold (lone surrogate).
fn exact_text(value: &Bound<'_, PyAny>) -> Result<Option<String>, ()> {
    if !is_exact_str(value) {
        return Ok(None);
    }
    match unsafe { value.cast_unchecked::<PyString>() }.to_str() {
        Ok(text) => Ok(Some(text.to_owned())),
        Err(_) => Err(()),
    }
}

/// A required exact `str` attribute.
fn required_text(value: &Bound<'_, PyAny>) -> Option<String> {
    exact_text(value).ok().flatten()
}

/// An exact `int` that fits `i64`.
fn exact_int(value: &Bound<'_, PyAny>) -> Option<Option<i64>> {
    if unsafe { ffi::PyLong_CheckExact(value.as_ptr()) } == 0 {
        return Some(None);
    }
    value.extract::<i64>().ok().map(Some)
}

/// Python `str.lower()`: ASCII here, anything else through Python itself.
fn python_lower(value: &Bound<'_, PyAny>, text: &str) -> Option<String> {
    if text.is_ascii() {
        return Some(text.to_ascii_lowercase());
    }
    let lowered = value.call_method0("lower").ok()?;
    Some(
        lowered
            .cast_into::<PyString>()
            .ok()?
            .to_str()
            .ok()?
            .to_owned(),
    )
}

/// Reads one structural payload with `Mapping.get` semantics (missing and JSON null are `None`).
struct Payload<'py> {
    payload: Bound<'py, PyAny>,
    index: Option<Bound<'py, PyAny>>,
}

impl<'py> Payload<'py> {
    fn new(payload: Bound<'py, PyAny>, json_object: &Bound<'py, PyAny>) -> Self {
        // `JsonObject.get` is `Mapping.get` over `__getitem__`, which reads `_index`. Only the
        // exact class gets the direct read; anything else goes through its own `.get`.
        let index = if payload.get_type().is(json_object) {
            payload.getattr("_index").ok().filter(|index| unsafe {
                ffi::Py_TYPE(index.as_ptr()) == std::ptr::addr_of_mut!(ffi::PyDictProxy_Type)
            })
        } else {
            None
        };
        Payload { payload, index }
    }

    fn get(&self, key: &Bound<'py, PyAny>) -> Option<Option<Bound<'py, PyAny>>> {
        let value = match &self.index {
            Some(index) => match index.get_item(key) {
                Ok(value) => value,
                Err(error) if error.is_instance_of::<pyo3::exceptions::PyKeyError>(index.py()) => {
                    return Some(None);
                }
                Err(_) => return None,
            },
            None => self.payload.call_method1("get", (key,)).ok()?,
        };
        Some(if value.is_none() { None } else { Some(value) })
    }
}

/// The Python objects behind one flattened envelope that candidates may cite.
struct Cited<'py> {
    source_identity: Bound<'py, PyAny>,
    key: Option<Bound<'py, PyTuple>>,
    subagent_id: Option<Bound<'py, PyAny>>,
    changed_paths_digest: Option<Bound<'py, PyAny>>,
}

fn flatten<'py>(
    py: Python<'py>,
    envelope: &Bound<'py, PyAny>,
    fields: &[Bound<'py, PyAny>],
    json_object: &Bound<'py, PyAny>,
) -> Option<(Envelope, Cited<'py>)> {
    let event_kind = required_text(&envelope.getattr("event_kind").ok()?)?;
    let source_identity = envelope.getattr("source_identity").ok()?;
    required_text(&source_identity)?;
    let payload = Payload::new(envelope.getattr("structural_payload").ok()?, json_object);
    let cursor = envelope.getattr("cursor").ok()?;
    let event_position = exact_int(&cursor.getattr("event_position").ok()?)??;

    let text_field = |index: usize| -> Option<(Option<String>, Option<Bound<'py, PyAny>>)> {
        match payload.get(&fields[index])? {
            Some(value) => {
                let text = exact_text(&value).ok()?;
                Some((text.clone(), text.map(|_| value)))
            }
            None => Some((None, None)),
        }
    };
    let (tool, tool_object) = text_field(TOOL_NAME)?;
    let (claim_kind, claim_object) = text_field(CLAIM_KIND)?;
    let (result_status, _) = text_field(RESULT_STATUS)?;
    let (changed_paths_digest, changed_object) = text_field(CHANGED_PATHS_DIGEST)?;
    let (mapping_hint, hint_object) = text_field(MAPPING_HINT)?;
    let (subagent_id, subagent_object) = text_field(SUBAGENT_ID)?;
    let (commitment, _) = text_field(COMMAND_COMMITMENT)?;
    let exit_status = match payload.get(&fields[EXIT_STATUS])? {
        Some(value) => exact_int(&value)?,
        None => None,
    };
    let success = match payload.get(&fields[SUCCESS])? {
        Some(value) if unsafe { ffi::PyBool_Check(value.as_ptr()) } != 0 => {
            Some(value.is_truthy().ok()?)
        }
        _ => None,
    };
    let denied = payload
        .get(&fields[DENIED])?
        .is_some_and(|value| value.as_ptr() == unsafe { ffi::Py_True() });
    // Any non-null action that is not an exact `str` meets set membership tests the flattening
    // does not model; the reference decides.
    let action = match payload.get(&fields[ACTION])? {
        Some(value) => Some(exact_text(&value).ok()??),
        None => None,
    };
    let attempt_present = payload.get(&fields[ATTEMPT])?.is_some();

    // `payload.get(correlation_id) or payload.get(tool_call_id) or envelope.source_identity`.
    let mut raw_key = None;
    for field in [CORRELATION_ID, TOOL_CALL_ID] {
        if let Some(value) = payload.get(&fields[field])? {
            if value.is_truthy().ok()? {
                raw_key = Some(value);
                break;
            }
        }
    }
    let raw_key = raw_key.unwrap_or_else(|| source_identity.clone());
    let (key, key_tuple) = match exact_text(&raw_key).ok()? {
        Some(raw) => {
            let source = envelope.getattr("source").ok()?.getattr("value").ok()?;
            let session = envelope.getattr("session_commitment").ok()?;
            let generation = cursor.getattr("source_generation").ok()?;
            let key = CorrelationKey {
                source: required_text(&source)?,
                session: required_text(&session)?,
                generation: exact_int(&generation)??,
                raw,
            };
            let tuple = PyTuple::new(py, [source, session, generation, raw_key]).ok()?;
            (Some(key), Some(tuple))
        }
        None => (None, None),
    };

    let lower = |object: &Option<Bound<'py, PyAny>>, text: &Option<String>| -> Option<String> {
        match (object, text) {
            (Some(object), Some(text)) => python_lower(object, text),
            _ => Some(String::new()),
        }
    };
    let flat = Envelope {
        event_kind,
        key,
        claim_lower: lower(&claim_object, &claim_kind)?,
        hint_lower: lower(&hint_object, &mapping_hint)?,
        tool_lower: lower(&tool_object, &tool)?,
        tool,
        exit_status,
        success,
        claim_kind,
        result_status,
        denied,
        command_commitment: commitment.filter(|value| value.starts_with("hmac-sha256:")),
        changed_paths_digest,
        mapping_hint,
        subagent_id,
        action,
        attempt_present,
        event_position,
    };
    Some((
        flat,
        Cited {
            source_identity,
            key: key_tuple,
            subagent_id: subagent_object,
            changed_paths_digest: changed_object,
        },
    ))
}

fn check_fact(check: &Bound<'_, PyAny>) -> Option<CheckFact> {
    // `check.status == "passed" and check.is_current`, then `check.cursor_event_position`.
    let status = check.getattr("status").ok()?;
    let passed = required_text(&status)? == "passed";
    let passed_current = passed && check.getattr("is_current").ok()?.is_truthy().ok()?;
    let cursor_event_position = if passed_current {
        exact_int(&check.getattr("cursor_event_position").ok()?)??
    } else {
        0
    };
    Some(CheckFact {
        passed_current,
        cursor_event_position,
    })
}

/// Sorted unique `str` refs as the reference's `tuple(sorted(set(refs), key=_ascii))`, or the
/// raw list when a ref is not ASCII (the reference's own sort then decides, and may raise).
fn refs<'py>(
    py: Python<'py>,
    cited: &[Cited<'py>],
    positions: &[usize],
) -> PyResult<Bound<'py, PyAny>> {
    let objects: Vec<&Bound<'py, PyAny>> = positions
        .iter()
        .map(|position| &cited[*position].source_identity)
        .collect();
    let mut keyed: Vec<(&str, &Bound<'py, PyAny>)> = Vec::with_capacity(objects.len());
    for object in &objects {
        match unsafe { object.cast_unchecked::<PyString>() }.to_str() {
            Ok(text) if text.is_ascii() => keyed.push((text, object)),
            _ => return Ok(PyList::new(py, objects)?.into_any()),
        }
    }
    keyed.sort_by(|left, right| left.0.cmp(right.0));
    keyed.dedup_by(|right, left| right.0 == left.0);
    Ok(PyTuple::new(py, keyed.into_iter().map(|(_, object)| object))?.into_any())
}

/// Flatten every envelope once and run the envelope rules. Returns `None` (run the reference) or
/// `(failed, edits, completion, static_for_live, subagents, changed, semantic)`:
/// `failed` is `[(key, source_identity)]`, `edits` is `[(refs, key)]`, `subagents` is
/// `[(subagent_id, source_identity)]`, `changed` is `[(source_identity, digest)]`, and the
/// single-candidate rules are refs or `None`.
#[pyfunction]
pub fn advice_scan<'py>(
    py: Python<'py>,
    envelopes: &Bound<'py, PyAny>,
    check_facts: &Bound<'py, PyAny>,
    json_object: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyTuple>>> {
    let Some(vocabulary) = VOCABULARY
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clone()
    else {
        return Ok(None);
    };
    let Some(fields) = FIELD_NAMES.get(py) else {
        return Ok(None);
    };
    let fields: Vec<Bound<'py, PyAny>> = fields.try_iter()?.collect::<PyResult<_>>()?;
    let mut flat = Vec::new();
    let mut cited = Vec::new();
    let Ok(iterator) = envelopes.try_iter() else {
        return Ok(None);
    };
    for envelope in iterator {
        let Ok(envelope) = envelope else {
            return Ok(None);
        };
        let Some((envelope, objects)) = flatten(py, &envelope, &fields, json_object) else {
            return Ok(None);
        };
        flat.push(envelope);
        cited.push(objects);
    }
    let mut checks = Vec::new();
    let Ok(iterator) = check_facts.try_iter() else {
        return Ok(None);
    };
    for check in iterator {
        let Some(fact) = check.ok().as_ref().and_then(check_fact) else {
            return Ok(None);
        };
        checks.push(fact);
    }
    let Some(scan) = core::scan(&flat, &checks, &vocabulary) else {
        return Ok(None);
    };
    let key_of = |position: usize| cited[position].key.clone();
    let failed = PyList::empty(py);
    for position in &scan.failed {
        failed.append((key_of(*position), &cited[*position].source_identity))?;
    }
    let edits = PyList::empty(py);
    for phases in &scan.edits_after_check {
        edits.append((refs(py, &cited, phases)?, key_of(phases[0])))?;
    }
    let optional = |positions: &Option<Vec<usize>>| -> PyResult<Option<Bound<'py, PyAny>>> {
        positions
            .as_deref()
            .map(|positions| refs(py, &cited, positions))
            .transpose()
    };
    let subagents = PyList::empty(py);
    for position in &scan.subagent_unaddressed {
        subagents.append((
            &cited[*position].subagent_id,
            &cited[*position].source_identity,
        ))?;
    }
    let changed = PyList::empty(py);
    for position in &scan.changed_paths {
        changed.append((
            &cited[*position].source_identity,
            &cited[*position].changed_paths_digest,
        ))?;
    }
    Ok(Some(PyTuple::new(
        py,
        [
            failed.into_any(),
            edits.into_any(),
            optional(&scan.completion_without_verification)?
                .unwrap_or_else(|| py.None().into_bound(py)),
            optional(&scan.static_for_live)?.unwrap_or_else(|| py.None().into_bound(py)),
            subagents.into_any(),
            changed.into_any(),
            optional(&scan.semantic_without_attempt)?.unwrap_or_else(|| py.None().into_bound(py)),
        ],
    )?))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(advice_bind, module)?)?;
    module.add_function(wrap_pyfunction!(advice_scan, module)?)?;
    Ok(())
}
