//! `yoetz.adapters.integrations.codex_session_stream` twins.
//!
//! `_token`, `_consistent_alias_token`, `structural_from_stream_record` (with `_structural_body`,
//! `_mcp_item_failed`, and `_child_session_header` inlined), the `_rollout_item_decisions` fold,
//! the keyed oversized-line partial helpers, `rollout_filename_matches_token`, and
//! `_source_file_identity`.
//!
//! Module globals the reference reads (`JsonObject`, the vocabularies, `normalize_observed_command`,
//! `observed_command_commitment`) are looked up in the bound module namespace at call time. The
//! Python wrappers only call a twin while every helper it inlines is still the bound original,
//! and a twin answers "defer" for input it does not model, so the reference handles the rest.

use pyo3::exceptions::PyNameError;
use pyo3::ffi;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBytes, PyDict, PyString, PyTuple};
use yoetz_core::integrations::codex_session_stream as core;

use crate::registry::Slot;

static GLOBALS: Slot = Slot::new();
/// `(UNSUPPORTED_EVENT, MISSING_SUBAGENT_IDENTITY)` gap code values.
static GAP_CODES: Slot = Slot::new();
/// `(_ITEM_CARRIER, _ITEM_COPY, _ITEM_PENDING, _ITEM_UNPAIRED)`.
static DECISIONS: Slot = Slot::new();
static FALSY_ERRORS: PyOnceLock<Py<PyTuple>> = PyOnceLock::new();

/// Bind the reference module namespace and the constant strings the twins emit.
#[pyfunction]
pub fn bind_codex_session_stream(globals: Bound<'_, PyDict>, gap_codes: Bound<'_, PyTuple>, decisions: Bound<'_, PyTuple>) {
    GLOBALS.set(globals.into_any().unbind());
    GAP_CODES.set(gap_codes.into_any().unbind());
    DECISIONS.set(decisions.into_any().unbind());
}

fn slot<'py>(py: Python<'py>, slot: &Slot) -> PyResult<Bound<'py, PyAny>> {
    slot.get(py).ok_or_else(|| PyNameError::new_err("yoetz_native_codex_session_stream_unbound"))
}

/// A global of the stream module, read at call time like the reference does.
fn global<'py>(py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    let globals = slot(py, &GLOBALS)?;
    let globals = globals.cast::<PyDict>()?;
    globals.get_item(name)?.ok_or_else(|| PyNameError::new_err(name.to_owned()))
}

fn bound_item<'py>(py: Python<'py>, slot_ref: &Slot, index: usize) -> PyResult<Bound<'py, PyAny>> {
    slot(py, slot_ref)?.cast::<PyTuple>()?.get_item(index)
}

#[inline]
fn exact_str<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a str> {
    if unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    unsafe { value.cast_unchecked::<PyString>() }.to_str().ok()
}

/// `_token(value)`: the same object when it is a bounded token, else `None`.
fn token<'py>(value: Bound<'py, PyAny>) -> Option<Bound<'py, PyAny>> {
    // A lone surrogate is never in the token alphabet, so a failed UTF-8 view is not a token.
    if exact_str(&value).is_some_and(core::token) { Some(value) } else { None }
}

#[pyfunction]
pub fn codex_stream_token<'py>(value: Bound<'py, PyAny>) -> Option<Bound<'py, PyAny>> {
    token(value)
}

/// Mapping reads with the reference's `JsonObject` semantics: `.get(key)` and `key in mapping`.
///
/// A `JsonObject` answers both from its private index (exactly what its inherited `Mapping`
/// methods do, without their Python-level frames); any other mapping uses its own protocol.
struct Reader<'py> {
    target: Bound<'py, PyAny>,
}

impl<'py> Reader<'py> {
    fn new(py: Python<'py>, value: &Bound<'py, PyAny>, json_object: &Bound<'py, PyAny>) -> PyResult<Self> {
        if value.get_type().as_ptr() == json_object.as_ptr() {
            if let Ok(index) = value.getattr(intern!(py, "_index")) {
                return Ok(Reader { target: index });
            }
        }
        Ok(Reader { target: value.clone() })
    }

    fn get(&self, key: &Bound<'py, PyString>) -> PyResult<Bound<'py, PyAny>> {
        self.target.call_method1(intern!(self.target.py(), "get"), (key,))
    }

    fn contains(&self, key: &Bound<'py, PyString>) -> PyResult<bool> {
        self.target.contains(key)
    }
}

fn is_instance(value: &Bound<'_, PyAny>, class: &Bound<'_, PyAny>) -> PyResult<bool> {
    value.is_instance(class)
}

/// `_consistent_alias_token(body, names)`.
fn consistent_alias_token<'py>(
    reader: &Reader<'py>,
    names: &[&Bound<'py, PyString>],
) -> PyResult<(Option<Bound<'py, PyAny>>, bool)> {
    let mut values: Vec<Bound<'py, PyAny>> = Vec::new();
    let mut supplied = false;
    for name in names {
        if !reader.contains(name)? {
            continue;
        }
        supplied = true;
        let raw = reader.get(name)?;
        if raw.is_none() {
            continue;
        }
        let Some(value) = token(raw) else {
            return Ok((None, supplied));
        };
        values.push(value);
    }
    if let Some(first) = values.first() {
        let first_text = exact_str(first).unwrap_or_default();
        if values[1..].iter().any(|value| exact_str(value).unwrap_or_default() != first_text) {
            return Ok((None, supplied));
        }
    }
    Ok((values.into_iter().next(), supplied))
}

/// `_consistent_alias_token(body, names)`, or `None` to defer (a name that is not a `str`).
#[pyfunction]
pub fn codex_stream_consistent_alias_token<'py>(
    py: Python<'py>,
    body: &Bound<'py, PyAny>,
    names: &Bound<'py, PyTuple>,
) -> PyResult<Option<(Option<Bound<'py, PyAny>>, bool)>> {
    let json_object = global(py, "JsonObject")?;
    let reader = Reader::new(py, body, &json_object)?;
    let mut keys = Vec::with_capacity(names.len());
    for name in names.iter() {
        if exact_str(&name).is_none() {
            return Ok(None);
        }
        keys.push(unsafe { name.cast_into_unchecked::<PyString>() });
    }
    let refs: Vec<&Bound<'py, PyString>> = keys.iter().collect();
    Ok(Some(consistent_alias_token(&reader, &refs)?))
}

/// `_structural_body(record)`.
fn structural_body<'py>(
    py: Python<'py>,
    value: &Reader<'py>,
    json_object: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let payload = value.get(intern!(py, "payload"))?;
    if is_instance(&payload, json_object)? {
        let payload_reader = Reader::new(py, &payload, json_object)?;
        let inner = payload_reader.get(intern!(py, "item"))?;
        if is_instance(&inner, json_object)? {
            return Ok(Some(inner));
        }
        return Ok(Some(payload));
    }
    let item = value.get(intern!(py, "item"))?;
    if is_instance(&item, json_object)? {
        return Ok(Some(item));
    }
    Ok(None)
}

/// `_mcp_item_failed(body)`.
fn mcp_item_failed<'py>(py: Python<'py>, body: &Reader<'py>, json_object: &Bound<'py, PyAny>) -> PyResult<bool> {
    let falsy = FALSY_ERRORS
        .get_or_try_init(py, || -> PyResult<Py<PyTuple>> {
            Ok(PyTuple::new(py, [py.None().into_bound(py), false.into_pyobject(py)?.to_owned().into_any(), PyString::new(py, "").into_any()])?.unbind())
        })?
        .bind(py);
    let error = body.get(intern!(py, "error"))?;
    if !falsy.contains(error)? {
        return Ok(true);
    }
    let result = body.get(intern!(py, "result"))?;
    if !is_instance(&result, json_object)? {
        return Ok(false);
    }
    let reader = Reader::new(py, &result, json_object)?;
    let flag = reader.get(intern!(py, "isError"))?;
    Ok(flag.as_ptr() == unsafe { ffi::Py_True() })
}

/// `_spawn_parent_thread(payload)`.
fn spawn_parent_thread<'py>(
    py: Python<'py>,
    payload: &Reader<'py>,
    json_object: &Bound<'py, PyAny>,
    thread_source: &Bound<'py, PyAny>,
) -> PyResult<(Option<Bound<'py, PyAny>>, bool)> {
    let source = payload.get(intern!(py, "source"))?;
    if !is_instance(&source, json_object)? {
        return Ok((None, false));
    }
    let subagent = Reader::new(py, &source, json_object)?.target.call_method1(intern!(py, "get"), (thread_source,))?;
    if !is_instance(&subagent, json_object)? {
        return Ok((None, false));
    }
    let spawn = Reader::new(py, &subagent, json_object)?.get(intern!(py, "thread_spawn"))?;
    if !is_instance(&spawn, json_object)? {
        return Ok((None, false));
    }
    let spawn = Reader::new(py, &spawn, json_object)?;
    if !spawn.contains(intern!(py, "parent_thread_id"))? {
        return Ok((None, false));
    }
    Ok((token(spawn.get(intern!(py, "parent_thread_id"))?), true))
}

fn same_token(left: &Bound<'_, PyAny>, right: &Bound<'_, PyAny>) -> bool {
    exact_str(left) == exact_str(right)
}

type Header<'py> = (Option<Bound<'py, PyAny>>, Vec<Bound<'py, PyAny>>, bool);

/// `_child_session_header(record)`: `(child, spawning, declared)`.
fn child_session_header<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyAny>,
    value: &Reader<'py>,
    json_object: &Bound<'py, PyAny>,
) -> PyResult<Header<'py>> {
    let wrapper = record.getattr(intern!(py, "wrapper_type"))?;
    if wrapper.ne("session_meta")? {
        return Ok((None, Vec::new(), false));
    }
    let Some(payload) = structural_body(py, value, json_object)? else {
        return Ok((None, Vec::new(), false));
    };
    let thread_source = global(py, "_SUBAGENT_THREAD_SOURCE")?;
    let payload = Reader::new(py, &payload, json_object)?;
    if payload.get(intern!(py, "thread_source"))?.ne(&thread_source)? {
        return Ok((None, Vec::new(), false));
    }
    let (parent, spawn_supplied) = spawn_parent_thread(py, &payload, json_object, &thread_source)?;
    if spawn_supplied && parent.is_none() {
        return Ok((None, Vec::new(), true));
    }
    let declared = token(payload.get(intern!(py, "parent_thread_id"))?);
    if payload.contains(intern!(py, "parent_thread_id"))? && declared.is_none() {
        return Ok((None, Vec::new(), true));
    }
    if let (Some(parent), Some(declared)) = (&parent, &declared) {
        if !same_token(parent, declared) {
            return Ok((None, Vec::new(), true));
        }
    }
    let parent = declared.or(parent);
    let child = token(payload.get(intern!(py, "id"))?);
    let (Some(child), Some(parent)) = (child, parent) else {
        return Ok((None, Vec::new(), true));
    };
    if same_token(&child, &parent) {
        return Ok((None, Vec::new(), true));
    }
    let mut spawning: Vec<Bound<'py, PyAny>> = Vec::with_capacity(2);
    for candidate in [token(payload.get(intern!(py, "session_id"))?), Some(parent)].into_iter().flatten() {
        if !same_token(&candidate, &child) && !spawning.iter().any(|known| same_token(known, &candidate)) {
            spawning.push(candidate);
        }
    }
    Ok((Some(child), spawning, true))
}

/// `_child_session_header(record)`.
#[pyfunction]
pub fn codex_stream_child_session_header<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyAny>,
) -> PyResult<(Option<Bound<'py, PyAny>>, Bound<'py, PyTuple>, bool)> {
    let json_object = global(py, "JsonObject")?;
    let value = record.getattr(intern!(py, "value"))?;
    let reader = Reader::new(py, &value, &json_object)?;
    let (child, spawning, declared) = child_session_header(py, record, &reader, &json_object)?;
    Ok((child, PyTuple::new(py, spawning)?, declared))
}

/// `_structural_body(record)`.
#[pyfunction]
pub fn codex_stream_structural_body<'py>(py: Python<'py>, record: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyAny>>> {
    let json_object = global(py, "JsonObject")?;
    let value = record.getattr(intern!(py, "value"))?;
    let reader = Reader::new(py, &value, &json_object)?;
    structural_body(py, &reader, &json_object)
}

/// `_mcp_item_failed(body)`.
#[pyfunction]
pub fn codex_stream_mcp_item_failed(py: Python<'_>, body: &Bound<'_, PyAny>) -> PyResult<bool> {
    let json_object = global(py, "JsonObject")?;
    let reader = Reader::new(py, body, &json_object)?;
    mcp_item_failed(py, &reader, &json_object)
}

/// The constants the twins hard-code, for the import-time drift check.
#[pyfunction]
pub fn codex_stream_tables(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let tables = PyDict::new(py);
    tables.set_item("_OVERSIZED_PARTIAL_PREFIX", PyBytes::new(py, core::OVERSIZED_PARTIAL_PREFIX))?;
    tables.set_item("_OVERSIZED_PARTIAL_DOMAIN", PyBytes::new(py, core::OVERSIZED_PARTIAL_DOMAIN))?;
    tables.set_item("_OVERSIZED_LINE_DOMAIN", PyBytes::new(py, core::OVERSIZED_LINE_DOMAIN))?;
    tables.set_item("_MAX_CANONICAL_INTEGER", core::MAX_CANONICAL_INTEGER as i64)?;
    tables.set_item("_JSONL_SUFFIXES", PyTuple::new(py, core::JSONL_SUFFIXES)?)?;
    tables.set_item("_PAIRING_CLOSE_EVENTS", PyTuple::new(py, ["SessionEnd", "Stop"])?)?;
    tables.set_item("_TOKEN_ALPHABET", "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/+-")?;
    tables.set_item("_TOKEN_LEADING_REFUSED", "._:/+-")?;
    tables.set_item("_TOKEN_MAX_CHARS", 128)?;
    Ok(tables)
}

/// `structural_from_stream_record(record, profile=profile, key_material=key_material)`.
#[pyfunction]
#[pyo3(signature = (record, profile, key_material))]
pub fn codex_stream_structural<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyAny>,
    profile: &Bound<'py, PyAny>,
    key_material: &Bound<'py, PyAny>,
) -> PyResult<(Bound<'py, PyAny>, Bound<'py, PyTuple>)> {
    let json_object = global(py, "JsonObject")?;
    let (item_types, known_wrappers) = if profile.is_none() {
        (global(py, "_ROLLOUT_ITEM_TYPES")?, global(py, "_ROLLOUT_WRAPPER_TYPES")?)
    } else {
        let frozen = |name: &Bound<'py, PyString>| -> PyResult<Bound<'py, PyAny>> {
            Ok(pyo3::types::PyFrozenSet::new(py, profile.getattr(name)?.try_iter()?.collect::<PyResult<Vec<_>>>()?)?.into_any())
        };
        (frozen(intern!(py, "item_types"))?, frozen(intern!(py, "wrapper_types"))?)
    };
    let unsupported = bound_item(py, &GAP_CODES, 0)?;
    let missing_subagent = bound_item(py, &GAP_CODES, 1)?;
    let mut unsupported_gap = false;
    let mut missing_gap = false;
    let fields = PyDict::new(py);
    let wrapper_type = record.getattr(intern!(py, "wrapper_type"))?;
    fields.set_item(intern!(py, "stream_kind"), &wrapper_type)?;
    let item_type = record.getattr(intern!(py, "item_type"))?;
    if !item_type.is_none() {
        match token(item_type.clone()) {
            Some(action) => fields.set_item(intern!(py, "action"), action)?,
            None => unsupported_gap = true,
        }
    }
    let item_is = |name: &str| -> PyResult<bool> { item_type.eq(name) };
    let value = record.getattr(intern!(py, "value"))?;
    let value_reader = Reader::new(py, &value, &json_object)?;
    if let Some(body_object) = structural_body(py, &value_reader, &json_object)? {
        let body = Reader::new(py, &body_object, &json_object)?;
        let mut tool = token(body.get(intern!(py, "tool"))?);
        if tool.is_none() {
            tool = token(body.get(intern!(py, "name"))?);
        }
        let type_token = token(body.get(intern!(py, "type"))?);
        if tool.is_none() {
            if let Some(type_token) = type_token {
                if !item_types.contains(&type_token)? {
                    tool = Some(type_token);
                }
            }
        }
        if tool.is_none() && !item_type.is_none() {
            let names = global(py, "_STREAM_TOOL_ITEM_NAMES")?;
            let named = names.call_method1(intern!(py, "get"), (&item_type,))?;
            if !named.is_none() {
                tool = Some(named);
            }
        }
        if let Some(tool) = tool {
            fields.set_item(intern!(py, "tool_name"), tool)?;
        }
        let mut status = token(body.get(intern!(py, "status"))?);
        if status.is_none() {
            status = token(body.get(intern!(py, "result_status"))?);
        }
        if item_is("McpToolCall")? && mcp_item_failed(py, &body, &json_object)? {
            status = Some(intern!(py, "failed").clone().into_any());
        }
        if let Some(status) = status {
            fields.set_item(intern!(py, "result_status"), status)?;
        }
        let exit_code = body.get(intern!(py, "exit_code"))?;
        if body.contains(intern!(py, "exit_code"))? && !exit_code.is_none() {
            let in_range = unsafe { ffi::PyLong_CheckExact(exit_code.as_ptr()) } != 0
                && exit_code.ge(-1)?
                && exit_code.le(255)?;
            if in_range {
                fields.set_item(intern!(py, "exit_status"), &exit_code)?;
            } else {
                unsupported_gap = true;
            }
        }
        if !key_material.is_none() && item_is("CommandExecution")? {
            let normalized = global(py, "normalize_observed_command")?.call1((body.get(intern!(py, "command"))?,))?;
            if !normalized.is_none() {
                let commitment = global(py, "observed_command_commitment")?.call1((key_material, normalized))?;
                fields.set_item(intern!(py, "command_commitment"), commitment)?;
            }
        }
        let subagent_activity = item_is("SubAgentActivity")?;
        if !subagent_activity {
            let mut call_id = token(body.get(intern!(py, "id"))?);
            if call_id.is_none() {
                call_id = token(body.get(intern!(py, "call_id"))?);
            }
            if let Some(call_id) = call_id {
                fields.set_item(intern!(py, "tool_call_id"), call_id)?;
            }
        }
        let (subagent_id, _supplied) = consistent_alias_token(
            &body,
            &[intern!(py, "subagent_id"), intern!(py, "agent_id"), intern!(py, "agent_thread_id")],
        )?;
        if let Some(subagent_id) = subagent_id {
            fields.set_item(intern!(py, "subagent_id"), subagent_id)?;
        }
        if subagent_activity {
            let (parent, supplied) = consistent_alias_token(
                &body,
                &[intern!(py, "parent_tool_call_id"), intern!(py, "tool_call_id"), intern!(py, "tool_use_id")],
            )?;
            if let Some(parent) = parent {
                fields.set_item(intern!(py, "parent_tool_call_id"), parent)?;
            } else if supplied {
                pop(&fields, intern!(py, "subagent_id"))?;
                missing_gap = true;
            }
            pop(&fields, intern!(py, "tool_call_id"))?;
            let activity_kind = token(body.get(intern!(py, "kind"))?).unwrap_or_else(|| py.None().into_bound(py));
            if !global(py, "_SUBAGENT_ACTIVITY_KINDS")?.contains(activity_kind)? {
                unsupported_gap = true;
            }
        }
    }
    let (child, _spawning, header) = child_session_header(py, record, &value_reader, &json_object)?;
    if header {
        pop(&fields, intern!(py, "tool_call_id"))?;
        match child {
            None => {
                pop(&fields, intern!(py, "subagent_id"))?;
                missing_gap = true;
            }
            Some(child) => fields.set_item(intern!(py, "subagent_id"), child)?,
        }
    }
    if !known_wrappers.contains(&wrapper_type)? {
        unsupported_gap = true;
    }
    let structural = json_object.call1((fields,))?;
    let mut gaps: Vec<Bound<'py, PyAny>> = Vec::with_capacity(2);
    if missing_gap {
        gaps.push(missing_subagent);
    }
    if unsupported_gap {
        gaps.push(unsupported);
    }
    gaps.sort_by(|left, right| exact_str(left).unwrap_or_default().as_bytes().cmp(exact_str(right).unwrap_or_default().as_bytes()));
    Ok((structural, PyTuple::new(py, gaps)?))
}

/// `fields.pop(key, None)`.
fn pop(fields: &Bound<'_, PyDict>, key: &Bound<'_, PyString>) -> PyResult<()> {
    if fields.contains(key)? {
        fields.del_item(key)?;
    }
    Ok(())
}

/// What the fold reads from one stored row: source lane, event kind, source identity, call id,
/// command commitment, integer exit, stated post, hooked rollout item.
type Facts<'py> = (
    core::RowSource,
    Bound<'py, PyAny>,
    Bound<'py, PyAny>,
    Option<Bound<'py, PyAny>>,
    Option<Bound<'py, PyAny>>,
    Option<i64>,
    bool,
    bool,
);

/// One stored row's facts for the fold, or `None` to defer the whole call.
fn row_facts<'py>(
    py: Python<'py>,
    envelope: &Bound<'py, PyAny>,
    json_object: &Bound<'py, PyAny>,
    hook: &Bound<'py, PyAny>,
    stream: &Bound<'py, PyAny>,
    completed_items: &Bound<'py, PyAny>,
) -> PyResult<Option<Facts<'py>>> {
    let structural = envelope.getattr(intern!(py, "structural_payload"))?;
    let reader = Reader::new(py, &structural, json_object)?;
    let call_id = token(reader.get(intern!(py, "tool_call_id"))?);
    let commitment = token(reader.get(intern!(py, "command_commitment"))?);
    let exit_status = reader.get(intern!(py, "exit_status"))?;
    let exit_is_int = unsafe { ffi::PyLong_CheckExact(exit_status.as_ptr()) } != 0;
    let exit_fact = if exit_is_int {
        match exit_status.extract::<i64>() {
            Ok(number) => Some(number),
            Err(_) => return Ok(None),
        }
    } else {
        None
    };
    let source_object = envelope.getattr(intern!(py, "source"))?;
    let source = if source_object.as_ptr() == hook.as_ptr() {
        core::RowSource::CodexHook
    } else if source_object.as_ptr() == stream.as_ptr() {
        core::RowSource::CodexSessionStream
    } else {
        core::RowSource::Other
    };
    let event_kind = envelope.getattr(intern!(py, "event_kind"))?;
    let identity = envelope.getattr(intern!(py, "source_identity"))?;
    if exact_str(&event_kind).is_none() || exact_str(&identity).is_none() {
        return Ok(None);
    }
    let mut stated = false;
    let mut hooked_item = false;
    match source {
        core::RowSource::CodexHook => {
            let success = reader.get(intern!(py, "success"))?;
            let denied = reader.get(intern!(py, "denied"))?;
            stated = exit_is_int
                || unsafe { ffi::PyBool_Check(success.as_ptr()) } != 0
                || denied.as_ptr() == unsafe { ffi::Py_True() };
        }
        core::RowSource::CodexSessionStream => {
            hooked_item = exact_str(&event_kind) == Some("item_completed")
                && completed_items.contains(reader.get(intern!(py, "action"))?)?;
        }
        core::RowSource::Other => {}
    }
    Ok(Some((source, event_kind, identity, call_id, commitment, exit_fact, stated, hooked_item)))
}

/// `_rollout_item_decisions(envelopes, session_commitment, evicted_open_calls)`, or `None` to
/// defer to the reference.
#[pyfunction]
pub fn codex_stream_rollout_item_decisions<'py>(
    py: Python<'py>,
    envelopes: &Bound<'py, PyAny>,
    session_commitment: &Bound<'py, PyAny>,
    evicted_open_calls: &Bound<'py, PyAny>,
    hook: &Bound<'py, PyAny>,
    stream: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Some(session) = exact_str(session_commitment) else {
        return Ok(None);
    };
    let json_object = global(py, "JsonObject")?;
    let completed_items = global(py, "_STREAM_COMPLETED_TOOL_ITEMS")?;
    let mut evicted: Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)> = Vec::new();
    for pair in evicted_open_calls.try_iter()? {
        let pair = pair?;
        let Ok(pair) = pair.cast_into::<PyTuple>() else {
            return Ok(None);
        };
        if pair.len() != 2 {
            return Ok(None);
        }
        let (commitment, call) = (pair.get_item(0)?, pair.get_item(1)?);
        if exact_str(&commitment).is_none() || exact_str(&call).is_none() {
            return Ok(None);
        }
        evicted.push((commitment, call));
    }
    let mut facts: Vec<Facts<'py>> = Vec::new();
    for envelope in envelopes.try_iter()? {
        let envelope = envelope?;
        let row_session = envelope.getattr(intern!(py, "session_commitment"))?;
        let in_session = match exact_str(&row_session) {
            Some(text) => text == session,
            None => !row_session.ne(session_commitment)?,
        };
        if !in_session {
            continue;
        }
        match row_facts(py, &envelope, &json_object, hook, stream, &completed_items)? {
            Some(row) => facts.push(row),
            None => return Ok(None),
        }
    }
    let rows: Vec<core::RolloutRow<'_>> = facts
        .iter()
        .map(|(source, kind, identity, call_id, commitment, exit_fact, stated, hooked_item)| core::RolloutRow {
            source: *source,
            event_kind: exact_str(kind).unwrap_or_default(),
            source_identity: exact_str(identity).unwrap_or_default(),
            call_id: call_id.as_ref().and_then(exact_str),
            commitment: commitment.as_ref().and_then(exact_str),
            exit_fact: *exit_fact,
            stated: *stated,
            hooked_item: *hooked_item,
        })
        .collect();
    let evicted_text: Vec<(&str, &str)> = evicted
        .iter()
        .map(|(commitment, call)| (exact_str(commitment).unwrap_or_default(), exact_str(call).unwrap_or_default()))
        .collect();
    let decided = core::rollout_item_decisions(&rows, &evicted_text);
    let names = slot(py, &DECISIONS)?;
    let names = names.cast::<PyTuple>()?;
    let decisions = PyDict::new(py);
    for (row, decision) in decided {
        let name = names.get_item(match decision {
            core::Decision::Carrier => 0,
            core::Decision::Copy => 1,
            core::Decision::Pending => 2,
            core::Decision::Unpaired => 3,
        })?;
        decisions.set_item(&facts[row].2, name)?;
    }
    Ok(Some(decisions))
}

/// `rollout_filename_matches_token(filename, token)` for an ASCII `str` name, else `None`.
#[pyfunction]
pub fn codex_stream_filename_matches(filename: &Bound<'_, PyAny>, token_value: &Bound<'_, PyAny>) -> Option<bool> {
    // `type(token) is not str or _token(token) is None` answers before the name is read.
    let Some(token_text) = exact_str(token_value).filter(|text| core::token(text)) else {
        return Some(false);
    };
    let name = exact_str(filename).filter(|name| name.is_ascii())?;
    Some(core::rollout_filename_matches(core::posix_name(name), token_text))
}

fn ascii<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a str> {
    exact_str(value).filter(|text| text.is_ascii())
}

fn exact_int(value: &Bound<'_, PyAny>) -> Option<i64> {
    if unsafe { ffi::PyLong_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    value.extract::<i64>().ok()
}

fn exact_bytes<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a [u8]> {
    if unsafe { ffi::PyBytes_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    Some(unsafe { value.cast_unchecked::<PyBytes>() }.as_bytes())
}

/// `_encode_oversized_partial(...)`: `(True, bytes)`, `(True, None)` for the reference's
/// `ValueError("session_stream_partial_invalid")`, or `(False, None)` to defer.
#[pyfunction]
pub fn codex_stream_encode_oversized_partial<'py>(
    py: Python<'py>,
    line_start: &Bound<'py, PyAny>,
    prefix_commitment: &Bound<'py, PyAny>,
    session_commitment: &Bound<'py, PyAny>,
    source_generation: &Bound<'py, PyAny>,
    source_identity: &Bound<'py, PyAny>,
    key_material: &Bound<'py, PyAny>,
) -> (bool, Option<Bound<'py, PyBytes>>) {
    let (Some(line_start), Some(prefix), Some(session), Some(generation), Some(identity), Some(key)) = (
        exact_int(line_start),
        exact_str(prefix_commitment),
        ascii(session_commitment),
        exact_int(source_generation),
        ascii(source_identity),
        exact_bytes(key_material),
    ) else {
        return (false, None);
    };
    match core::encode_oversized_partial(line_start, prefix, session, generation, identity, key) {
        Some(encoded) => (true, Some(PyBytes::new(py, &encoded))),
        None => (true, None),
    }
}

/// `_decode_oversized_partial(...)`: `(0, None)` defer, `(1, None)` not oversized, `(2, None)`
/// invalid, `(3, (line_start, prefix_digest))` decoded.
#[pyfunction]
pub fn codex_stream_decode_oversized_partial(
    value: &Bound<'_, PyAny>,
    session_commitment: &Bound<'_, PyAny>,
    source_generation: &Bound<'_, PyAny>,
    source_identity: &Bound<'_, PyAny>,
    key_material: &Bound<'_, PyAny>,
) -> (u8, Option<(i64, String)>) {
    let (Some(value), Some(session), Some(generation), Some(identity), Some(key)) = (
        exact_bytes(value),
        ascii(session_commitment),
        exact_int(source_generation),
        ascii(source_identity),
        exact_bytes(key_material),
    ) else {
        return (0, None);
    };
    match core::decode_oversized_partial(value, session, generation, identity, key) {
        core::OversizedPartial::Defer => (0, None),
        core::OversizedPartial::NotOversized => (1, None),
        core::OversizedPartial::Invalid => (2, None),
        core::OversizedPartial::State(line_start, digest) => (3, Some((line_start, digest))),
    }
}

/// `_oversized_line_commitment(...)` given the state's fields, or `None` to defer.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn codex_stream_oversized_line_commitment(
    line_start: &Bound<'_, PyAny>,
    prefix_digest: &Bound<'_, PyAny>,
    byte_end: &Bound<'_, PyAny>,
    session_commitment: &Bound<'_, PyAny>,
    source_generation: &Bound<'_, PyAny>,
    source_identity: &Bound<'_, PyAny>,
    key_material: &Bound<'_, PyAny>,
) -> Option<String> {
    Some(core::oversized_line_commitment(
        exact_int(line_start)?,
        ascii(prefix_digest)?,
        exact_int(byte_end)?,
        ascii(session_commitment)?,
        exact_int(source_generation)?,
        ascii(source_identity)?,
        exact_bytes(key_material)?,
    ))
}

/// `_source_file_identity(facts, key_material)`, or `None` to defer.
#[pyfunction]
pub fn codex_stream_source_file_identity(
    py: Python<'_>,
    facts: &Bound<'_, PyAny>,
    key_material: &Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    let device = facts.getattr(intern!(py, "st_dev"))?;
    let inode = facts.getattr(intern!(py, "st_ino"))?;
    let exact = |value: &Bound<'_, PyAny>| -> Option<i128> {
        if unsafe { ffi::PyLong_CheckExact(value.as_ptr()) } == 0 {
            return None;
        }
        value.extract::<i128>().ok()
    };
    let (Some(device), Some(inode), Some(key)) = (exact(&device), exact(&inode), exact_bytes(key_material)) else {
        return Ok(None);
    };
    Ok(Some(core::source_file_identity(&core::bounded(device), &core::bounded(inode), key)))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_codex_session_stream, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_token, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_tables, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_structural_body, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_mcp_item_failed, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_child_session_header, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_consistent_alias_token, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_structural, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_rollout_item_decisions, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_filename_matches, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_encode_oversized_partial, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_decode_oversized_partial, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_oversized_line_commitment, module)?)?;
    module.add_function(wrap_pyfunction!(codex_stream_source_file_identity, module)?)?;
    Ok(())
}
