//! `yoetz.domain.observation` twins: `normalize_observed_command`, `_structural_payload`, and
//! the sorted-set validators (`_sorted_unique_tokens`, `_sorted_unique_gap_codes`,
//! `_evidence_refs`, `_content_object_refs`).
//!
//! Collaborators the reference reaches through its module globals (`JsonObject`,
//! `canonical_encode`, `validate_commitment`, `validate_observation_protection_reference`) are
//! looked up in that module's namespace at call time, so a patched global still intercepts. Input
//! the port does not model (a lone surrogate, a `JsonObject` without its item tuple) goes to the
//! Python reference bound at import time.

use pyo3::exceptions::PyNameError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyString, PyTuple};
use yoetz_core::domain::observation as core;

use crate::registry::{PROTOCOL_VALUE_ERROR, Slot, protocol_error};
use crate::shlex::exact_utf8;

static GLOBALS: Slot = Slot::new();
/// The Python reference implementations, by name.
static FALLBACKS: Slot = Slot::new();

const INVALID: &str = "invalid_event_value_type";
const UNKNOWN_FIELD: &str = "unknown_payload_field";

/// Bind the reference module namespace and its Python implementations (name -> function).
#[pyfunction]
pub fn bind_observation(globals: Bound<'_, PyDict>, fallbacks: Bound<'_, PyDict>) {
    GLOBALS.set(globals.into_any().unbind());
    FALLBACKS.set(fallbacks.into_any().unbind());
}

/// The constants and tables the twins hard-code, for the import-time drift check.
#[pyfunction]
pub fn observation_tables(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let tables = PyDict::new(py);
    tables.set_item(
        "_MAX_OBSERVED_COMMAND_CHARS",
        core::MAX_OBSERVED_COMMAND_CHARS,
    )?;
    tables.set_item("_MAX_OBSERVED_ARGV", core::MAX_OBSERVED_ARGV)?;
    tables.set_item("_MAX_STRUCTURAL_BYTES", core::MAX_STRUCTURAL_BYTES)?;
    tables.set_item("_STRUCTURAL_KEYS", core::STRUCTURAL_KEYS.to_vec())?;
    tables.set_item(
        "_STRUCTURAL_TOKEN_KEYS",
        core::STRUCTURAL_TOKEN_KEYS.to_vec(),
    )?;
    tables.set_item("_PROSE_KEYS", core::PROSE_KEYS.to_vec())?;
    tables.set_item("_SHELL_WRAPPERS", core::SHELL_WRAPPERS.to_vec())?;
    tables.set_item("_SHELL_COMMAND_FLAG_RE", core::SHELL_COMMAND_FLAG_PATTERN)?;
    tables.set_item("_TOKEN_RE", core::TOKEN_PATTERN)?;
    tables.set_item("_GAP_RE", core::GAP_PATTERN)?;
    tables.set_item("_MAX_GAP_CODES", core::MAX_GAP_CODES)?;
    tables.set_item(
        "OBSERVATION_WORKSPACE_DOMAIN",
        PyBytes::new(py, core::WORKSPACE_DOMAIN),
    )?;
    tables.set_item(
        "OBSERVATION_STREAM_LINE_DOMAIN",
        PyBytes::new(py, core::STREAM_LINE_DOMAIN),
    )?;
    tables.set_item(
        "OBSERVATION_HOOK_COMMITMENT_DOMAIN",
        PyBytes::new(py, core::HOOK_COMMITMENT_DOMAIN),
    )?;
    tables.set_item(
        "OBSERVATION_COMMAND_COMMITMENT_DOMAIN",
        PyBytes::new(py, core::COMMAND_COMMITMENT_DOMAIN),
    )?;
    Ok(tables)
}

fn slot<'py>(py: Python<'py>, slot: &Slot) -> PyResult<Bound<'py, PyAny>> {
    slot.get(py)
        .ok_or_else(|| PyNameError::new_err("yoetz_native_observation_unbound"))
}

/// A global of `yoetz.domain.observation`, read at call time like the reference does.
fn global<'py>(py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    let globals = slot(py, &GLOBALS)?;
    let globals = globals.cast::<PyDict>()?;
    globals
        .get_item(name)?
        .ok_or_else(|| PyNameError::new_err(name.to_owned()))
}

/// The Python reference implementation of `name`.
fn fallback<'py>(py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    let fallbacks = slot(py, &FALLBACKS)?;
    let fallbacks = fallbacks.cast::<PyDict>()?;
    fallbacks
        .get_item(name)?
        .ok_or_else(|| PyNameError::new_err(name.to_owned()))
}

#[inline]
fn is_exact_tuple(value: &Bound<'_, PyAny>) -> bool {
    unsafe { ffi::PyTuple_CheckExact(value.as_ptr()) != 0 }
}

#[inline]
fn is_exact_str(value: &Bound<'_, PyAny>) -> bool {
    unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) != 0 }
}

/// `normalize_observed_command(value)`.
#[pyfunction]
pub fn normalize_observed_command<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let pointer = value.as_ptr();
    let normalized = if is_exact_str(value) {
        match exact_utf8(value) {
            Some(text) => core::normalize_observed_command_str(text),
            None => return fallback(py, "normalize_observed_command")?.call1((value,)),
        }
    } else if unsafe { ffi::PyList_CheckExact(pointer) != 0 } || is_exact_tuple(value) {
        let items: Vec<Bound<'py, PyAny>> = value.try_iter()?.collect::<PyResult<_>>()?;
        if items.is_empty()
            || items.len() > core::MAX_OBSERVED_ARGV
            || !items.iter().all(is_exact_str)
        {
            return Ok(py.None().into_bound(py));
        }
        let mut argv: Vec<&str> = Vec::with_capacity(items.len());
        for item in &items {
            match exact_utf8(item) {
                Some(text) => argv.push(text),
                None => return fallback(py, "normalize_observed_command")?.call1((value,)),
            }
        }
        core::normalize_observed_command_argv(&argv)
    } else {
        None
    };
    Ok(match normalized {
        Some(text) => PyString::new(py, &text).into_any(),
        None => py.None().into_bound(py),
    })
}

/// A `JsonObject`'s `(key, value)` pairs from its frozen item tuple, or `None` when the object
/// does not carry one in the expected shape.
fn json_object_items<'py>(
    object: &Bound<'py, PyAny>,
) -> Option<Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)>> {
    let items = object.getattr("_items").ok()?;
    if !is_exact_tuple(&items) {
        return None;
    }
    let items = unsafe { items.cast_unchecked::<PyTuple>() };
    let mut pairs = Vec::with_capacity(items.len());
    for pair in items.iter() {
        if !is_exact_tuple(&pair) {
            return None;
        }
        let pair = unsafe { pair.cast_unchecked::<PyTuple>() };
        if pair.len() != 2 {
            return None;
        }
        let key = pair.get_item(0).ok()?;
        if !is_exact_str(&key) {
            return None;
        }
        pairs.push((key, pair.get_item(1).ok()?));
    }
    Some(pairs)
}

/// `raise _invalid(reason) from cause`.
fn invalid_from(py: Python<'_>, reason: &str, cause: PyErr) -> PyErr {
    crate::registry::protocol_error_from(py, reason, cause)
}

fn is_protocol_error(py: Python<'_>, error: &PyErr) -> bool {
    PROTOCOL_VALUE_ERROR
        .get(py)
        .is_some_and(|class| error.matches(py, class).unwrap_or(false))
}

/// `_looks_like_path(text)` for one exact `str`.
fn str_looks_like_path(py: Python<'_>, text: &Bound<'_, PyAny>) -> PyResult<bool> {
    let Some(slice) = exact_utf8(text) else {
        return global(py, "_looks_like_path")?.call1((text,))?.is_truthy();
    };
    match core::looks_like_path(slice) {
        Some(answer) => Ok(answer),
        None => {
            // `value[0].isalpha()` for a non-ASCII first character.
            let first: String = slice.chars().take(1).collect();
            PyString::new(py, &first)
                .call_method0("isalpha")?
                .is_truthy()
        }
    }
}

/// `_reject_path_like(value)`, iteratively.
fn reject_path_like<'py>(
    py: Python<'py>,
    value: Bound<'py, PyAny>,
    json_object: &Bound<'py, PyAny>,
) -> PyResult<()> {
    let mut stack = vec![value];
    while let Some(value) = stack.pop() {
        if is_exact_str(&value) {
            if str_looks_like_path(py, &value)? {
                return Err(protocol_error(py, INVALID));
            }
        } else if is_exact_tuple(&value) {
            let tuple = unsafe { value.cast_unchecked::<PyTuple>() };
            stack.extend(tuple.iter().rev());
        } else if value.get_type().is(json_object) {
            match json_object_items(&value) {
                Some(pairs) => stack.extend(pairs.into_iter().rev().map(|(_, item)| item)),
                None => {
                    global(py, "_reject_path_like")?.call1((value,))?;
                }
            }
        }
    }
    Ok(())
}

/// `_structural_payload(value)`.
#[pyfunction]
pub fn structural_payload<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let json_object = global(py, "JsonObject")?;
    let payload = if value.get_type().is(&json_object) {
        value.clone()
    } else {
        match json_object.call1((value,)) {
            Ok(payload) => payload,
            Err(error) if is_protocol_error(py, &error) => {
                return Err(invalid_from(py, INVALID, error));
            }
            Err(error) => return Err(error),
        }
    };
    let Some(pairs) = json_object_items(&payload) else {
        return fallback(py, "_structural_payload")?.call1((payload,));
    };
    let keys: Vec<Option<&str>> = pairs
        .iter()
        .map(|(key, _)| unsafe { key.cast_unchecked::<PyString>() }.to_str().ok())
        .collect();
    if keys
        .iter()
        .any(|key| key.is_some_and(|key| core::PROSE_KEYS.contains(&key)))
    {
        return Err(protocol_error(py, UNKNOWN_FIELD));
    }
    if keys
        .iter()
        .any(|key| !key.is_some_and(|key| core::STRUCTURAL_KEYS.contains(&key)))
    {
        return Err(protocol_error(py, UNKNOWN_FIELD));
    }
    for ((_, item), key) in pairs.iter().zip(keys) {
        // Every key is a structural key by now.
        let key = key.unwrap_or_default();
        if core::has_path_suffix(key) {
            return Err(protocol_error(py, UNKNOWN_FIELD));
        }
        if core::STRUCTURAL_TOKEN_KEYS.contains(&key)
            && !exact_utf8(item).is_some_and(core::is_token)
        {
            return Err(protocol_error(py, INVALID));
        }
        if key == "protection_reference" {
            global(py, "validate_observation_protection_reference")?.call1((item,))?;
        }
        if key == "command_commitment" {
            if !is_exact_str(item) {
                return Err(protocol_error(py, INVALID));
            }
            match global(py, "validate_commitment")?.call1((item,)) {
                Ok(_) => {}
                Err(error) if is_protocol_error(py, &error) => {
                    return Err(invalid_from(py, INVALID, error));
                }
                Err(error) => return Err(error),
            }
        }
        reject_path_like(py, item.clone(), &json_object)?;
    }
    let encoded = global(py, "canonical_encode")?.call1((&payload,))?;
    let length = match encoded.cast::<PyBytes>() {
        Ok(bytes) => bytes.as_bytes().len(),
        Err(_) => encoded.len()?,
    };
    if length > core::MAX_STRUCTURAL_BYTES {
        return Err(protocol_error(py, INVALID));
    }
    Ok(payload)
}

/// One validated member of a sorted ASCII set, or `None` when the reference must decide.
type MemberResult<'py> = PyResult<Option<Bound<'py, PyString>>>;

/// The shared loop of the sorted-set validators: `_exact_tuple(value, maximum=...)`, then each
/// member validated in order and compared by its ASCII bytes with the one before it. `Ok(None)`
/// means a member needs the Python reference.
fn sorted_unique<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    maximum: i64,
    mut member: impl FnMut(&Bound<'py, PyAny>) -> MemberResult<'py>,
) -> PyResult<Option<Bound<'py, PyTuple>>> {
    if !is_exact_tuple(value) {
        return Err(protocol_error(py, INVALID));
    }
    let raw = unsafe { value.cast_unchecked::<PyTuple>() };
    if raw.len() as i64 > maximum {
        return Err(protocol_error(py, INVALID));
    }
    let mut result: Vec<Bound<'py, PyString>> = Vec::with_capacity(raw.len());
    for item in raw.iter() {
        let Some(validated) = member(&item)? else {
            return Ok(None);
        };
        // `member.encode("ascii")` must succeed for the comparison; anything else is the
        // reference's business.
        let Some(current) = validated.to_str().ok().filter(|text| text.is_ascii()) else {
            return Ok(None);
        };
        if let Some(previous) = result.last() {
            let previous = previous.to_str()?;
            if current.as_bytes() <= previous.as_bytes() {
                let reason = if current == previous {
                    "duplicate_set_member"
                } else {
                    "unsorted_set_field"
                };
                return Err(protocol_error(py, reason));
            }
        }
        result.push(validated);
    }
    Ok(Some(PyTuple::new(py, result)?))
}

/// `_token(value)` as a set member.
fn token_member<'py>(py: Python<'py>, item: &Bound<'py, PyAny>) -> MemberResult<'py> {
    if exact_utf8(item).is_some_and(core::is_token) {
        return Ok(Some(unsafe { item.cast_unchecked::<PyString>() }.clone()));
    }
    Err(protocol_error(py, INVALID))
}

/// A limit the reference reads from its module namespace at call time.
fn limit(py: Python<'_>, name: &str) -> PyResult<Option<i64>> {
    let value = global(py, name)?;
    if unsafe { ffi::PyLong_CheckExact(value.as_ptr()) } == 0 {
        return Ok(None);
    }
    Ok(value.extract::<i64>().ok())
}

/// `_sorted_unique_tokens(value, *, maximum)`.
#[pyfunction]
#[pyo3(signature = (value, *, maximum))]
pub fn sorted_unique_tokens<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    maximum: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    if unsafe { ffi::PyLong_CheckExact(maximum.as_ptr()) } != 0 {
        if let Ok(limit) = maximum.extract::<i64>() {
            if let Some(tuple) = sorted_unique(py, value, limit, |item| token_member(py, item))? {
                return Ok(tuple.into_any());
            }
        }
    }
    let keywords = PyDict::new(py);
    keywords.set_item("maximum", maximum)?;
    fallback(py, "_sorted_unique_tokens")?.call((value,), Some(&keywords))
}

/// `_evidence_refs(value)`.
#[pyfunction]
pub fn evidence_refs<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    if let Some(maximum) = limit(py, "_MAX_EVIDENCE_REFS")? {
        if let Some(tuple) = sorted_unique(py, value, maximum, |item| token_member(py, item))? {
            return Ok(tuple.into_any());
        }
    }
    fallback(py, "_evidence_refs")?.call1((value,))
}

/// `_sorted_unique_gap_codes(value, *, maximum=_MAX_GAP_CODES)`.
#[pyfunction]
#[pyo3(signature = (value, *, maximum = None))]
pub fn sorted_unique_gap_codes<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    maximum: Option<&Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyAny>> {
    let limit = match maximum {
        None => Some(core::MAX_GAP_CODES as i64),
        Some(maximum) if unsafe { ffi::PyLong_CheckExact(maximum.as_ptr()) } != 0 => {
            maximum.extract::<i64>().ok()
        }
        Some(_) => None,
    };
    if let Some(limit) = limit {
        let gap_code = global(py, "ObservationGapCode")?;
        let native = sorted_unique(py, value, limit, |item| {
            if item.get_type().is(&gap_code) {
                // `value.value`
                let inner = item.getattr("value")?;
                return Ok(if is_exact_str(&inner) {
                    Some(unsafe { inner.cast_into_unchecked::<PyString>() })
                } else {
                    None
                });
            }
            if exact_utf8(item).is_some_and(core::is_gap_code) {
                return Ok(Some(unsafe { item.cast_unchecked::<PyString>() }.clone()));
            }
            Err(protocol_error(py, "invalid_known_gap"))
        })?;
        if let Some(tuple) = native {
            return Ok(tuple.into_any());
        }
    }
    let keywords = PyDict::new(py);
    if let Some(maximum) = maximum {
        keywords.set_item("maximum", maximum)?;
    }
    fallback(py, "_sorted_unique_gap_codes")?.call((value,), Some(&keywords))
}

/// `_content_object_refs(value)`.
#[pyfunction]
pub fn content_object_refs<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    if let Some(maximum) = limit(py, "_MAX_CONTENT_REFS")? {
        let native = sorted_unique(py, value, maximum, |item| {
            if !is_exact_str(item) {
                return Err(protocol_error(py, INVALID));
            }
            let text = unsafe { item.cast_unchecked::<PyString>() };
            let Ok(slice) = text.to_str() else {
                return Ok(None);
            };
            let validator = if slice.starts_with("hmac-sha256:") {
                Some("validate_commitment")
            } else if slice.starts_with("sha256:") {
                Some("validate_sha256_digest")
            } else {
                None
            };
            if let Some(validator) = validator {
                let member = global(py, validator)?.call1((item,))?;
                return Ok(if is_exact_str(&member) {
                    Some(unsafe { member.cast_into_unchecked::<PyString>() })
                } else {
                    None
                });
            }
            // Object IDs use the ordinary obj_ prefix; commitments stay path-free.
            let length = slice.chars().count();
            if !slice.is_ascii() || !(1..=128).contains(&length) || str_looks_like_path(py, item)? {
                return Err(protocol_error(py, INVALID));
            }
            Ok(Some(text.clone()))
        })?;
        if let Some(tuple) = native {
            return Ok(tuple.into_any());
        }
    }
    fallback(py, "_content_object_refs")?.call1((value,))
}

/// Whether the stdlib callables the commitment references use (`hmac.new`, `hashlib.sha256`,
/// and optionally `os.fsencode`) are still the originals bound at import, so the native HMAC
/// computes what they would. A replaced one sends the call to the Python reference.
fn stdlib_intact(py: Python<'_>, fsencode: bool) -> PyResult<bool> {
    let pairs: &[(&str, &str, &str)] = if fsencode {
        &[
            ("hmac", "new", "hmac.new"),
            ("hashlib", "sha256", "hashlib.sha256"),
            ("os", "fsencode", "os.fsencode"),
        ]
    } else {
        &[
            ("hmac", "new", "hmac.new"),
            ("hashlib", "sha256", "hashlib.sha256"),
        ]
    };
    for (module, name, original) in pairs {
        let current = global(py, module)?.getattr(*name)?;
        if !current.is(&fallback(py, original)?) {
            return Ok(false);
        }
    }
    if fsencode {
        // `os.fsencode` is UTF-8 with surrogateescape only on such a filesystem encoding.
        return fallback(py, "fs_utf8_surrogateescape")?.is_truthy();
    }
    Ok(true)
}

/// `key_material` when it is an exact `bytes` of 16..=64 bytes, else `invalid_commitment`.
fn commitment_key<'a>(py: Python<'_>, key: &'a Bound<'_, PyAny>) -> PyResult<&'a [u8]> {
    if unsafe { ffi::PyBytes_CheckExact(key.as_ptr()) } != 0 {
        let bytes = unsafe { key.cast_unchecked::<PyBytes>() }.as_bytes();
        if core::is_commitment_key(bytes) {
            return Ok(bytes);
        }
    }
    Err(protocol_error(py, "invalid_commitment"))
}

/// A non-empty, NUL-free, valid UTF-8 exact `str`: `Ok(Some(text))`; a lone surrogate:
/// `Ok(None)` (the reference decides); anything else: the reference's refusal.
fn commitment_text<'a>(py: Python<'_>, value: &'a Bound<'_, PyAny>) -> PyResult<Option<&'a str>> {
    if !is_exact_str(value) {
        return Err(protocol_error(py, INVALID));
    }
    let Ok(text) = unsafe { value.cast_unchecked::<PyString>() }.to_str() else {
        return Ok(None);
    };
    if text.is_empty() || text.contains('\0') {
        return Err(protocol_error(py, INVALID));
    }
    Ok(Some(text))
}

fn text_commitment<'py>(
    py: Python<'py>,
    name: &str,
    domain: &[u8],
    fsencode: bool,
    key_material: &Bound<'py, PyAny>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    if stdlib_intact(py, fsencode)? {
        let key = commitment_key(py, key_material)?;
        if let Some(text) = commitment_text(py, value)? {
            return Ok(
                PyString::new(py, &core::hmac_commitment(key, domain, text.as_bytes())).into_any(),
            );
        }
    }
    fallback(py, name)?.call1((key_material, value))
}

/// `workspace_commitment_from_path(key_material, path)`.
#[pyfunction]
pub fn workspace_commitment_from_path<'py>(
    py: Python<'py>,
    key_material: &Bound<'py, PyAny>,
    path: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    text_commitment(
        py,
        "workspace_commitment_from_path",
        core::WORKSPACE_DOMAIN,
        true,
        key_material,
        path,
    )
}

/// `hook_source_commitment(key_material, source_identity)`.
#[pyfunction]
pub fn hook_source_commitment<'py>(
    py: Python<'py>,
    key_material: &Bound<'py, PyAny>,
    source_identity: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    text_commitment(
        py,
        "hook_source_commitment",
        core::HOOK_COMMITMENT_DOMAIN,
        false,
        key_material,
        source_identity,
    )
}

/// `observed_command_commitment(key_material, command)`.
#[pyfunction]
pub fn observed_command_commitment<'py>(
    py: Python<'py>,
    key_material: &Bound<'py, PyAny>,
    command: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    text_commitment(
        py,
        "observed_command_commitment",
        core::COMMAND_COMMITMENT_DOMAIN,
        false,
        key_material,
        command,
    )
}

/// `stream_line_commitment(key_material, content)`.
#[pyfunction]
pub fn stream_line_commitment<'py>(
    py: Python<'py>,
    key_material: &Bound<'py, PyAny>,
    content: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    if !stdlib_intact(py, false)? {
        return fallback(py, "stream_line_commitment")?.call1((key_material, content));
    }
    let key = commitment_key(py, key_material)?;
    if unsafe { ffi::PyBytes_CheckExact(content.as_ptr()) } == 0 {
        return Err(protocol_error(py, INVALID));
    }
    let message = unsafe { content.cast_unchecked::<PyBytes>() }.as_bytes();
    Ok(PyString::new(
        py,
        &core::hmac_commitment(key, core::STREAM_LINE_DOMAIN, message),
    )
    .into_any())
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_observation, module)?)?;
    module.add_function(wrap_pyfunction!(observation_tables, module)?)?;
    module.add_function(wrap_pyfunction!(normalize_observed_command, module)?)?;
    module.add_function(wrap_pyfunction!(structural_payload, module)?)?;
    module.add_function(wrap_pyfunction!(sorted_unique_tokens, module)?)?;
    module.add_function(wrap_pyfunction!(sorted_unique_gap_codes, module)?)?;
    module.add_function(wrap_pyfunction!(evidence_refs, module)?)?;
    module.add_function(wrap_pyfunction!(content_object_refs, module)?)?;
    module.add_function(wrap_pyfunction!(workspace_commitment_from_path, module)?)?;
    module.add_function(wrap_pyfunction!(hook_source_commitment, module)?)?;
    module.add_function(wrap_pyfunction!(observed_command_commitment, module)?)?;
    module.add_function(wrap_pyfunction!(stream_line_commitment, module)?)?;
    Ok(())
}
