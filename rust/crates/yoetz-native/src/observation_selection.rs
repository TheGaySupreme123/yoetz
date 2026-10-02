//! `yoetz.domain.observation_selection` twins: `classify_observation`,
//! `is_routine_read_candidate`, `envelope_outcome_state`, and `is_edit_tool_name`.
//!
//! Only exact `dict` payloads are walked natively. A value the reference tests with
//! `isinstance(value, Mapping)` that is a mapping but not an exact `dict` (or a token whose
//! folding needs Python's view of a lone surrogate) stops the native walk, and the whole call is
//! answered by the Python reference instead; the walk has no side effects to repeat. Set
//! membership on caller values, `==` against the event name, and `value not in (None, False, "")`
//! go through Python's own protocols, so unhashable or custom-equality values behave exactly as
//! in the reference. `strict_json_parse` is read from the module namespace at call time.

use pyo3::exceptions::{PyNameError, PyTypeError, PyValueError};
use pyo3::ffi;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBytes, PyDict, PyFrozenSet, PyString, PyTuple};
use yoetz_core::domain::observation_selection as core;
use yoetz_core::domain::observation_selection::{ContentRole, Phase, RoutineFacts, ToolDecision};

use crate::registry::{PROTOCOL_VALUE_ERROR, Slot};

static GLOBALS: Slot = Slot::new();
static CLASSIFICATION: Slot = Slot::new();
static JSON_OBJECT: Slot = Slot::new();
static ROLE_NONE: Slot = Slot::new();
static ROLE_TOOL_INPUT: Slot = Slot::new();
static ROLE_TOOL_OUTPUT: Slot = Slot::new();
static ROLE_BOTH: Slot = Slot::new();
static FALLBACK_CLASSIFY: Slot = Slot::new();
static FALLBACK_CANDIDATE: Slot = Slot::new();
static FALLBACK_OUTCOME: Slot = Slot::new();
static FALLBACK_EDIT: Slot = Slot::new();

static MAPPING_ABC: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static FAILURE_HOOK_EVENTS: PyOnceLock<Py<PyFrozenSet>> = PyOnceLock::new();
static FALSY_ERRORS: PyOnceLock<Py<PyTuple>> = PyOnceLock::new();

/// Why a native walk stopped.
enum Stop {
    /// The reference raises this error.
    Raise(PyErr),
    /// The input needs the Python reference.
    Defer,
}

impl From<PyErr> for Stop {
    fn from(error: PyErr) -> Self {
        Stop::Raise(error)
    }
}

type Flow<T> = Result<T, Stop>;

/// Bind the classification class, its role members, the module namespace, and the Python
/// implementations each twin defers to.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn bind_observation_selection(
    globals: Bound<'_, PyDict>,
    classification: Bound<'_, PyAny>,
    role: Bound<'_, PyAny>,
    json_object: Bound<'_, PyAny>,
    classify: Bound<'_, PyAny>,
    candidate: Bound<'_, PyAny>,
    outcome: Bound<'_, PyAny>,
    edit: Bound<'_, PyAny>,
) -> PyResult<()> {
    ROLE_NONE.set(role.getattr("NONE")?.unbind());
    ROLE_TOOL_INPUT.set(role.getattr("TOOL_INPUT")?.unbind());
    ROLE_TOOL_OUTPUT.set(role.getattr("TOOL_OUTPUT")?.unbind());
    ROLE_BOTH.set(role.getattr("BOTH")?.unbind());
    GLOBALS.set(globals.into_any().unbind());
    CLASSIFICATION.set(classification.unbind());
    JSON_OBJECT.set(json_object.unbind());
    FALLBACK_CLASSIFY.set(classify.unbind());
    FALLBACK_CANDIDATE.set(candidate.unbind());
    FALLBACK_OUTCOME.set(outcome.unbind());
    FALLBACK_EDIT.set(edit.unbind());
    Ok(())
}

/// The tables and limits the twins hard-code, for the import-time drift check.
#[pyfunction]
pub fn observation_selection_tables(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let tables = PyDict::new(py);
    tables.set_item("_MAX_RESULT_JSON_BYTES", core::MAX_RESULT_JSON_BYTES)?;
    tables.set_item("_MAX_COMMAND_CHARS", core::MAX_COMMAND_CHARS)?;
    for (name, table) in [
        ("ROUTINE_READ_TOOLS", core::ROUTINE_READ_TOOLS),
        ("SHELL_TOOLS", core::SHELL_TOOLS),
        ("READ_ONLY_COMMANDS", core::READ_ONLY_COMMANDS),
        ("_PRE_EVENTS", core::PRE_EVENTS),
        ("_POST_EVENTS", core::POST_EVENTS),
        ("_SUCCESS_STATUSES", core::SUCCESS_STATUSES),
        ("_PARTIAL_STATUSES", core::PARTIAL_STATUSES),
        ("_RG_PRE_OPTIONS", core::RG_PRE_OPTIONS),
        ("_GIT_SIDE_EFFECT_PREFIXES", core::GIT_SIDE_EFFECT_PREFIXES),
        ("_GIT_SIDE_EFFECT_OPTIONS", core::GIT_SIDE_EFFECT_OPTIONS),
        ("_EDIT_TOOL_HINTS", core::EDIT_TOOL_HINTS),
        ("_TEST_TOOL_HINTS", core::TEST_TOOL_HINTS),
        ("_VERIFICATION_TOOL_HINTS", core::VERIFICATION_TOOL_HINTS),
        ("_EDIT_COMMANDS", core::EDIT_COMMANDS),
        ("_TEST_COMMANDS", core::TEST_COMMANDS),
    ] {
        tables.set_item(name, table.to_vec())?;
    }
    let failures = PyDict::new(py);
    for (status, state) in core::FAILURE_STATUSES {
        failures.set_item(status, state)?;
    }
    tables.set_item("_FAILURE_STATUSES", failures)?;
    Ok(tables)
}

fn slot<'py>(py: Python<'py>, slot: &Slot) -> PyResult<Bound<'py, PyAny>> {
    slot.get(py).ok_or_else(|| PyNameError::new_err("yoetz_native_observation_selection_unbound"))
}

fn mapping_abc(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    MAPPING_ABC
        .get_or_try_init(py, || -> PyResult<Py<PyAny>> {
            Ok(py.import("collections.abc")?.getattr("Mapping")?.unbind())
        })
        .map(|value| value.bind(py))
}

/// `isinstance(value, Mapping)`: `Some(dict)` for an exact `dict`, `None` for a non-mapping,
/// and `Defer` for any other mapping.
fn as_dict<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> Flow<Option<Bound<'py, PyDict>>> {
    let pointer = value.as_ptr();
    unsafe {
        if ffi::PyDict_CheckExact(pointer) != 0 {
            return Ok(Some(value.cast_unchecked::<PyDict>().clone()));
        }
        if value.is_none()
            || ffi::PyUnicode_CheckExact(pointer) != 0
            || ffi::PyLong_CheckExact(pointer) != 0
            || ffi::PyBool_Check(pointer) != 0
            || ffi::PyList_CheckExact(pointer) != 0
            || ffi::PyTuple_CheckExact(pointer) != 0
            || ffi::PyFloat_CheckExact(pointer) != 0
        {
            return Ok(None);
        }
    }
    if value.is_instance(mapping_abc(py)?)? { Err(Stop::Defer) } else { Ok(None) }
}

/// `mapping.get(key)` with `None` for a missing key.
fn get<'py>(py: Python<'py>, mapping: &Bound<'py, PyDict>, key: &Bound<'py, PyString>) -> PyResult<Bound<'py, PyAny>> {
    Ok(mapping.get_item(key)?.unwrap_or_else(|| py.None().into_bound(py)))
}

/// `_classification_token(value)`.
fn classification_token<'py>(value: &Bound<'py, PyAny>) -> Option<Bound<'py, PyString>> {
    if unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    let length = unsafe { ffi::PyUnicode_GetLength(value.as_ptr()) };
    if !(1..=256).contains(&length) {
        return None;
    }
    Some(unsafe { value.cast_unchecked::<PyString>() }.clone())
}

/// `text.casefold()` for valid UTF-8 text: ASCII in Rust, anything else through Python.
fn casefold_str(py: Python<'_>, text: &str) -> PyResult<String> {
    if text.is_ascii() {
        return Ok(text.to_ascii_lowercase());
    }
    PyString::new(py, text).call_method0("casefold")?.extract()
}

/// `token.casefold()`; `None` when the token holds a lone surrogate (its fold then cannot equal
/// any ASCII table entry).
fn casefold_token(py: Python<'_>, token: &Bound<'_, PyString>) -> PyResult<Option<String>> {
    match token.to_str() {
        Ok(text) => casefold_str(py, text).map(Some),
        Err(_) => Ok(None),
    }
}

fn is_true(value: &Bound<'_, PyAny>) -> bool {
    value.as_ptr() == unsafe { ffi::Py_True() }
}

fn is_exact_bool(value: &Bound<'_, PyAny>) -> bool {
    unsafe { ffi::PyBool_Check(value.as_ptr()) != 0 }
}

/// `_routine_shell_facts(payload)`.
fn routine_shell_facts<'py>(py: Python<'py>, payload: &Bound<'py, PyDict>) -> Flow<RoutineFacts> {
    const AMBIGUOUS: RoutineFacts = RoutineFacts { candidate: false, reason: "ambiguous_shell" };
    let Some(nested) = as_dict(py, &get(py, payload, intern!(py, "tool_input"))?)? else {
        return Ok(AMBIGUOUS);
    };
    let mut raw = get(py, &nested, intern!(py, "cmd"))?;
    if classification_nonempty_str(&raw).is_none() {
        raw = get(py, &nested, intern!(py, "command"))?;
    }
    let Some(raw) = classification_nonempty_str(&raw) else {
        return Ok(AMBIGUOUS);
    };
    let Ok(text) = raw.to_str() else {
        return Err(Stop::Defer);
    };
    let mut fold = |text: &str| casefold_str(py, text);
    Ok(core::shell_command_facts(text, &mut fold)?)
}

/// `type(raw) is str and raw` (non-empty exact `str`).
fn classification_nonempty_str<'py>(value: &Bound<'py, PyAny>) -> Option<Bound<'py, PyString>> {
    if unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    let text = unsafe { value.cast_unchecked::<PyString>() };
    if unsafe { ffi::PyUnicode_GetLength(value.as_ptr()) } == 0 {
        return None;
    }
    Some(text.clone())
}

/// `_routine_facts(payload)`.
fn routine_facts<'py>(py: Python<'py>, payload: &Bound<'py, PyDict>) -> Flow<RoutineFacts> {
    let Some(tool) = classification_token(&get(py, payload, intern!(py, "tool_name"))?) else {
        return Ok(RoutineFacts { candidate: false, reason: "unknown_tool" });
    };
    // Substring hints over a fold that keeps a lone surrogate need the reference.
    let Some(lowered) = casefold_token(py, &tool)? else {
        return Err(Stop::Defer);
    };
    match core::tool_decision(&lowered) {
        ToolDecision::Facts(facts) => Ok(facts),
        ToolDecision::Shell => routine_shell_facts(py, payload),
    }
}

/// `_bounded_result_mapping(value)`.
fn bounded_result_mapping<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> Flow<Option<Bound<'py, PyDict>>> {
    if let Some(dict) = as_dict(py, value)? {
        return Ok(Some(dict));
    }
    let Some(text) = classification_nonempty_str(value) else {
        return Ok(None);
    };
    let Ok(text) = text.to_str() else {
        // UnicodeEncodeError
        return Ok(None);
    };
    if text.len() > core::MAX_RESULT_JSON_BYTES {
        return Ok(None);
    }
    let parse = global(py, "strict_json_parse")?;
    let parsed = match parse.call1((PyBytes::new(py, text.as_bytes()),)) {
        Ok(parsed) => parsed,
        Err(error) => {
            let protocol = PROTOCOL_VALUE_ERROR.get(py);
            let swallowed = error.is_instance_of::<PyTypeError>(py)
                || error.is_instance_of::<PyValueError>(py)
                || protocol.is_some_and(|class| error.matches(py, class).unwrap_or(false));
            if swallowed {
                return Ok(None);
            }
            return Err(Stop::Raise(error));
        }
    };
    as_dict(py, &parsed)
}

fn global<'py>(py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    let globals = slot(py, &GLOBALS)?;
    let globals = globals.cast::<PyDict>()?;
    globals.get_item(name)?.ok_or_else(|| PyNameError::new_err(name.to_owned()))
}

/// `_classification_result_mappings(payload)`.
fn result_mappings<'py>(py: Python<'py>, payload: &Bound<'py, PyDict>) -> Flow<Vec<Bound<'py, PyDict>>> {
    let mut mappings = vec![payload.clone()];
    for key in
        [intern!(py, "tool_response"), intern!(py, "tool_output"), intern!(py, "result"), intern!(py, "result_json")]
    {
        let Some(mapping) = bounded_result_mapping(py, &get(py, payload, key)?)? else {
            continue;
        };
        mappings.push(mapping.clone());
        for nested_key in [
            intern!(py, "structuredContent"),
            intern!(py, "structured_content"),
            intern!(py, "data"),
            intern!(py, "result"),
        ] {
            if let Some(nested) = bounded_result_mapping(py, &get(py, &mapping, nested_key)?)? {
                mappings.push(nested);
            }
        }
    }
    Ok(mappings)
}

fn failure_hook_events(py: Python<'_>) -> PyResult<&Bound<'_, PyFrozenSet>> {
    FAILURE_HOOK_EVENTS
        .get_or_try_init(py, || -> PyResult<Py<PyFrozenSet>> {
            Ok(PyFrozenSet::new(py, ["PostToolUseFailure", "postToolUseFailure", "StopFailure"])?.unbind())
        })
        .map(|value| value.bind(py))
}

fn falsy_errors(py: Python<'_>) -> PyResult<&Bound<'_, PyTuple>> {
    FALSY_ERRORS
        .get_or_try_init(py, || -> PyResult<Py<PyTuple>> {
            let empty = PyString::new(py, "").into_any();
            let items: [Bound<'_, PyAny>; 3] =
                [py.None().into_bound(py), pyo3::types::PyBool::new(py, false).to_owned().into_any(), empty];
            Ok(PyTuple::new(py, items)?.unbind())
        })
        .map(|value| value.bind(py))
}

/// `_classification_outcome(payload, event_name).state`.
fn classification_outcome<'py>(
    py: Python<'py>,
    payload: &Bound<'py, PyDict>,
    event_name: &Bound<'py, PyString>,
    event: &str,
) -> Flow<Option<&'static str>> {
    let hook_event = get(py, payload, intern!(py, "hook_event_name"))?;
    let mut outcome =
        core::Outcome { denied: matches!(event, "PermissionDenied" | "permission_denied"), ..core::Outcome::default() };
    outcome.failure = matches!(event, "PostToolUseFailure" | "postToolUseFailure" | "StopFailure")
        || failure_hook_events(py)?.as_any().contains(&hook_event)?;

    for mapping in result_mappings(py, payload)? {
        for key in [intern!(py, "denied"), intern!(py, "is_denied"), intern!(py, "permission_denied")] {
            if is_true(&get(py, &mapping, key)?) {
                outcome.denied = true;
            }
        }
        for key in [
            intern!(py, "interrupted"),
            intern!(py, "is_interrupted"),
            intern!(py, "is_interrupt"),
            intern!(py, "isInterrupted"),
            intern!(py, "cancelled"),
            intern!(py, "canceled"),
            intern!(py, "is_cancelled"),
            intern!(py, "isCanceled"),
        ] {
            if is_true(&get(py, &mapping, key)?) {
                outcome.cancelled = true;
            }
        }
        for (index, key) in
            [intern!(py, "is_error"), intern!(py, "isError"), intern!(py, "failed")].into_iter().enumerate()
        {
            let value = get(py, &mapping, key)?;
            if is_exact_bool(&value) {
                if is_true(&value) {
                    outcome.failure = true;
                } else if index < 2 {
                    // A protocol-level false error bit is a closed success fact.
                    outcome.success = true;
                }
            }
        }
        for key in [intern!(py, "success"), intern!(py, "ok")] {
            let value = get(py, &mapping, key)?;
            if is_exact_bool(&value) {
                if is_true(&value) {
                    outcome.success = true;
                } else {
                    outcome.failure = true;
                }
            }
        }
        for key in
            [intern!(py, "exit_code"), intern!(py, "exitCode"), intern!(py, "exit_status"), intern!(py, "exitStatus")]
        {
            let Some(value) = mapping.get_item(key)? else {
                continue;
            };
            let mut valid = None;
            if unsafe { ffi::PyLong_CheckExact(value.as_ptr()) } != 0 {
                let mut overflow: std::os::raw::c_int = 0;
                let number = unsafe { ffi::PyLong_AsLongLongAndOverflow(value.as_ptr(), &mut overflow) };
                if overflow == 0 && (-1..=255).contains(&number) {
                    valid = Some(number);
                }
            }
            match valid {
                Some(number) => outcome.exit(number),
                None => outcome.invalid_exit = true,
            }
        }
        for key in [
            intern!(py, "result_status"),
            intern!(py, "status"),
            intern!(py, "outcome"),
            intern!(py, "failure_type"),
            intern!(py, "permission_decision"),
        ] {
            let Some(raw) = mapping.get_item(key)? else {
                continue;
            };
            let Some(token) = classification_token(&raw) else {
                if !raw.is_none() {
                    outcome.unknown = true;
                }
                continue;
            };
            match casefold_token(py, &token)? {
                Some(lowered) => outcome.status(core::status_class(&lowered)),
                None => outcome.unknown = true,
            }
        }
        if mapping.contains(intern!(py, "error"))? {
            let value = get(py, &mapping, intern!(py, "error"))?;
            if !falsy_errors(py)?.as_any().contains(&value)? {
                outcome.failure = true;
            }
        }
    }

    // A background launch has no terminal result at this event boundary.
    if let Some(tool_input) = as_dict(py, &get(py, payload, intern!(py, "tool_input"))?)? {
        if is_true(&get(py, &tool_input, intern!(py, "run_in_background"))?) {
            outcome.partial = true;
        }
    }

    let native_post_success = matches!(event, "PostToolUse" | "postToolUse")
        && get(py, payload, intern!(py, "hook_event_name"))?.eq(event_name)?;
    Ok(outcome.state(native_post_success))
}

/// `_has_untrusted_routine_label(payload)`.
fn has_untrusted_routine_label<'py>(py: Python<'py>, payload: &Bound<'py, PyDict>) -> Flow<bool> {
    if let Some(token) = classification_token(&get(py, payload, intern!(py, "action"))?) {
        if casefold_token(py, &token)?.as_deref() == Some("routine_read") {
            return Ok(true);
        }
    }
    if let Some(nested) = as_dict(py, &get(py, payload, intern!(py, "tool_input"))?)? {
        if let Some(token) = classification_token(&get(py, &nested, intern!(py, "action"))?) {
            if casefold_token(py, &token)?.as_deref() == Some("routine_read") {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

/// Exact `dict` payload and exact UTF-8 `str` event name, or `None` (use the reference).
fn native_args<'a, 'py>(
    payload: &'a Bound<'py, PyAny>,
    event_name: &'a Bound<'py, PyAny>,
) -> Option<(&'a Bound<'py, PyDict>, &'a Bound<'py, PyString>, &'a str)> {
    if unsafe { ffi::PyDict_CheckExact(payload.as_ptr()) } == 0 {
        return None;
    }
    if unsafe { ffi::PyUnicode_CheckExact(event_name.as_ptr()) } == 0 {
        return None;
    }
    let event_name = unsafe { event_name.cast_unchecked::<PyString>() };
    let event = event_name.to_str().ok()?;
    Some((unsafe { payload.cast_unchecked::<PyDict>() }, event_name, event))
}

fn classify<'py>(
    py: Python<'py>,
    payload: &Bound<'py, PyDict>,
    event_name: &Bound<'py, PyString>,
    event: &str,
) -> Flow<Bound<'py, PyAny>> {
    let phase = core::phase(event);
    let routine = if phase == Phase::Other {
        RoutineFacts { candidate: false, reason: "unknown_operation" }
    } else {
        routine_facts(py, payload)?
    };
    let outcome = classification_outcome(py, payload, event_name, event)?;
    let untrusted = has_untrusted_routine_label(py, payload)?;
    let assembled = core::assemble(phase, routine, outcome, untrusted);
    let role = slot(
        py,
        match assembled.content_role {
            ContentRole::None => &ROLE_NONE,
            ContentRole::ToolInput => &ROLE_TOOL_INPUT,
            ContentRole::ToolOutput => &ROLE_TOOL_OUTPUT,
            ContentRole::Both => &ROLE_BOTH,
        },
    )?;
    let reasons = PyTuple::new(py, assembled.reason_tokens.iter().map(|reason| PyString::intern(py, reason)))?;
    Ok(slot(py, &CLASSIFICATION)?.call1((
        assembled.protected,
        assembled.routine_candidate,
        assembled.proven_routine_success,
        role,
        reasons,
    ))?)
}

/// `classify_observation(payload, event_name)`.
#[pyfunction]
pub fn classify_observation<'py>(
    py: Python<'py>,
    payload: &Bound<'py, PyAny>,
    event_name: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    if let Some((dict, name, event)) = native_args(payload, event_name) {
        match classify(py, dict, name, event) {
            Ok(result) => return Ok(result),
            Err(Stop::Raise(error)) => return Err(error),
            Err(Stop::Defer) => {}
        }
    }
    slot(py, &FALLBACK_CLASSIFY)?.call1((payload, event_name))
}

/// `is_routine_read_candidate(payload)`.
#[pyfunction]
pub fn is_routine_read_candidate<'py>(py: Python<'py>, payload: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    if unsafe { ffi::PyDict_CheckExact(payload.as_ptr()) } != 0 {
        match routine_facts(py, unsafe { payload.cast_unchecked::<PyDict>() }) {
            Ok(facts) => return Ok(pyo3::types::PyBool::new(py, facts.candidate).to_owned().into_any()),
            Err(Stop::Raise(error)) => return Err(error),
            Err(Stop::Defer) => {}
        }
    }
    slot(py, &FALLBACK_CANDIDATE)?.call1((payload,))
}

/// The pairs of a `JsonObject`'s frozen item tuple, or `None` when it does not carry one.
fn json_object_dict<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Ok(items) = value.getattr("_items") else {
        return Ok(None);
    };
    if unsafe { ffi::PyTuple_CheckExact(items.as_ptr()) } == 0 {
        return Ok(None);
    }
    let dict = PyDict::new(py);
    for pair in unsafe { items.cast_unchecked::<PyTuple>() }.iter() {
        if unsafe { ffi::PyTuple_CheckExact(pair.as_ptr()) } == 0 {
            return Ok(None);
        }
        let pair = unsafe { pair.cast_unchecked::<PyTuple>() };
        if pair.len() != 2 {
            return Ok(None);
        }
        let key = pair.get_item(0)?;
        if unsafe { ffi::PyUnicode_CheckExact(key.as_ptr()) } == 0 {
            return Ok(None);
        }
        dict.set_item(key, pair.get_item(1)?)?;
    }
    Ok(Some(dict))
}

/// `dict(structural_payload)`: a copy of an exact `dict`, a `JsonObject`'s frozen pairs, or the
/// `dict` constructor itself for anything else.
fn structural_dict<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyDict>> {
    if unsafe { ffi::PyDict_CheckExact(value.as_ptr()) } != 0 {
        return unsafe { value.cast_unchecked::<PyDict>() }.copy();
    }
    if let Some(json_object) = JSON_OBJECT.get(py) {
        if value.get_type().is(&json_object) {
            if let Some(dict) = json_object_dict(py, value)? {
                return Ok(dict);
            }
        }
    }
    let dict = py.get_type::<PyDict>().call1((value,))?;
    Ok(dict.cast_into::<PyDict>()?)
}

/// `envelope_outcome_state(structural_payload, event_kind)`.
#[pyfunction]
pub fn envelope_outcome_state<'py>(
    py: Python<'py>,
    structural_payload: &Bound<'py, PyAny>,
    event_kind: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let event = if unsafe { ffi::PyUnicode_CheckExact(event_kind.as_ptr()) } != 0 {
        let name = unsafe { event_kind.cast_unchecked::<PyString>() };
        name.to_str().ok().map(|event| (name, event))
    } else {
        None
    };
    if let Some((name, event)) = event {
        {
            let payload = structural_dict(py, structural_payload)?;
            let hook_name = get(py, &payload, intern!(py, "hook_name"))?;
            if unsafe { ffi::PyUnicode_CheckExact(hook_name.as_ptr()) } != 0
                && !payload.contains(intern!(py, "hook_event_name"))?
            {
                payload.set_item(intern!(py, "hook_event_name"), hook_name)?;
            }
            match classification_outcome(py, &payload, name, event) {
                Ok(state) => {
                    return Ok(match state {
                        Some(state) => PyString::intern(py, state).into_any(),
                        None => py.None().into_bound(py),
                    });
                }
                Err(Stop::Raise(error)) => return Err(error),
                Err(Stop::Defer) => {}
            }
        }
    }
    slot(py, &FALLBACK_OUTCOME)?.call1((structural_payload, event_kind))
}

/// `is_edit_tool_name(tool_name)`.
#[pyfunction]
pub fn is_edit_tool_name<'py>(py: Python<'py>, tool_name: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let Some(tool) = classification_token(tool_name) else {
        return Ok(pyo3::types::PyBool::new(py, false).to_owned().into_any());
    };
    match casefold_token(py, &tool)? {
        Some(lowered) => Ok(pyo3::types::PyBool::new(py, core::is_edit_tool_lowered(&lowered)).to_owned().into_any()),
        None => slot(py, &FALLBACK_EDIT)?.call1((tool_name,)),
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_observation_selection, module)?)?;
    module.add_function(wrap_pyfunction!(observation_selection_tables, module)?)?;
    module.add_function(wrap_pyfunction!(classify_observation, module)?)?;
    module.add_function(wrap_pyfunction!(is_routine_read_candidate, module)?)?;
    module.add_function(wrap_pyfunction!(envelope_outcome_state, module)?)?;
    module.add_function(wrap_pyfunction!(is_edit_tool_name, module)?)?;
    Ok(())
}
