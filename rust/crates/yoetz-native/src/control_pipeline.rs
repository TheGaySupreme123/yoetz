//! The fused frame pipeline of `yoetz.service.control_protocol`: `_validated_wire`,
//! `validate_request`, `validate_result`, `_decode_control_payload`, `encode_control_frame`, and
//! `_plain_mapping_for_model`.
//!
//! Every dependency is called through the module's own globals, read at call time, so a test that
//! patches one of them (for example `_decode_control_payload` or `validate_schema_instance`)
//! still sees every call the reference makes. The twins only *skip* work whose result is already
//! determined, and only while every dependency involved is the original bound at import:
//!
//! * a wire tree that is already plain (exact `dict` with `str` keys, exact `list`, scalars) is
//!   not copied again by `_plain_wire_value`: the copy would be structurally identical;
//! * an exact `JsonObject` is deeply frozen by construction, so `freeze_json` of its plain copy
//!   is structurally that same object, and the object itself is returned;
//! * when the caller discards the frozen frame (`validate_request`, `validate_result`, and
//!   `encode_control_frame`, which only needs its canonical bytes), a plain tree that the schema
//!   check accepted is not frozen at all: `validate_schema_instance` first canonical-encodes the
//!   value, which enforces every rule `freeze_json` would (safe integers, no floats, valid
//!   strings and keys, nesting depth), so the freeze could neither fail nor change the bytes;
//! * `_plain_mapping_for_model` thaws an exact `JsonObject` tree directly, keys in canonical
//!   order, instead of encoding and re-parsing it.
//!
//! Refusals keep the reference's identity: `ControlProtocolError` passes through, and a
//! `TypeError`/`ValueError` (which includes `ProtocolValueError`) becomes
//! `ControlProtocolError("frame_invalid")` raised `from None` with the original as its context.

use pyo3::exceptions::{PyRuntimeError, PyTypeError, PyValueError};
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyString, PyTuple};
use yoetz_core::protocol::canonical::utf16_cmp;

use crate::registry::Slot;
use crate::walk::{JSON_OBJECT, NATIVE_RECURSION_LIMIT, is_exact, is_mapping_instance, is_plain_scalar, is_type, json_object_items};

/// The `control_protocol` module namespace (its `globals()`).
static NAMESPACE: Slot = Slot::new();
/// `ControlProtocolError`.
static ERROR: Slot = Slot::new();
/// `struct.error`, which `encode_control_frame` also folds into `frame_invalid`.
static STRUCT_ERROR: Slot = Slot::new();
/// The Python `_validated_wire`, for a schema name that is not an exact `str`.
static REFERENCE_VALIDATED_WIRE: Slot = Slot::new();

// The dependencies as bound at import; a skip applies only while the global is still this object.
static ORIGINAL_PLAIN: Slot = Slot::new();
static ORIGINAL_VALIDATED_WIRE: Slot = Slot::new();
static ORIGINAL_FREEZE: Slot = Slot::new();
static ORIGINAL_ENCODE: Slot = Slot::new();
static ORIGINAL_PARSE: Slot = Slot::new();
static ORIGINAL_VALIDATE: Slot = Slot::new();
static ORIGINAL_FRAME_SIZE: Slot = Slot::new();

/// Bind the module namespace, its error classes, the Python `_validated_wire`, and the original
/// dependency objects (`originals` maps each global name to the object bound at import).
#[pyfunction]
#[pyo3(name = "control_bind_pipeline")]
pub fn bind_pipeline(
    namespace: Bound<'_, PyDict>,
    error: Bound<'_, PyAny>,
    struct_error: Bound<'_, PyAny>,
    reference_validated_wire: Bound<'_, PyAny>,
    originals: Bound<'_, PyDict>,
) -> PyResult<()> {
    let slot = |name: &str, target: &Slot| -> PyResult<()> {
        let value = originals
            .get_item(name)?
            .ok_or_else(|| PyRuntimeError::new_err("control_pipeline_original_missing"))?;
        target.set(value.unbind());
        Ok(())
    };
    slot("_plain_wire_value", &ORIGINAL_PLAIN)?;
    slot("_validated_wire", &ORIGINAL_VALIDATED_WIRE)?;
    slot("freeze_json", &ORIGINAL_FREEZE)?;
    slot("canonical_encode", &ORIGINAL_ENCODE)?;
    slot("strict_json_parse", &ORIGINAL_PARSE)?;
    slot("validate_schema_instance", &ORIGINAL_VALIDATE)?;
    slot("_validate_frame_size", &ORIGINAL_FRAME_SIZE)?;
    NAMESPACE.set(namespace.into_any().unbind());
    ERROR.set(error.unbind());
    STRUCT_ERROR.set(struct_error.unbind());
    REFERENCE_VALIDATED_WIRE.set(reference_validated_wire.unbind());
    Ok(())
}

/// What `_validated_wire` produced: the frozen frame (absent when skipped), the validated plain
/// wire tree (absent when another callable produced the frame), and whether that tree is plain.
struct Validated<'py> {
    frozen: Option<Bound<'py, PyAny>>,
    wire: Option<Bound<'py, PyAny>>,
    plain_tree: bool,
}

struct Pipeline<'py> {
    py: Python<'py>,
    namespace: Bound<'py, PyDict>,
    error: Bound<'py, PyAny>,
}

fn unbound() -> PyErr {
    PyRuntimeError::new_err("control_pipeline_unbound")
}

impl<'py> Pipeline<'py> {
    fn new(py: Python<'py>) -> PyResult<Self> {
        let namespace = NAMESPACE.get(py).ok_or_else(unbound)?.cast_into::<PyDict>()?;
        let error = ERROR.get(py).ok_or_else(unbound)?;
        Ok(Pipeline { py, namespace, error })
    }

    /// The module global `name`, read now (a `NameError` if it was deleted, like the reference).
    fn global(&self, name: &str) -> PyResult<Bound<'py, PyAny>> {
        match self.namespace.get_item(PyString::intern(self.py, name))? {
            Some(value) => Ok(value),
            None => Err(pyo3::exceptions::PyNameError::new_err(format!("name '{name}' is not defined"))),
        }
    }

    /// Whether the module global `name` is still the object `original` holds.
    fn is_original(&self, name: &str, original: &Slot) -> bool {
        match (self.namespace.get_item(PyString::intern(self.py, name)), original.get(self.py)) {
            (Ok(Some(current)), Some(original)) => current.is(&original),
            _ => {
                let _ = PyErr::take(self.py);
                false
            }
        }
    }

    /// `ControlProtocolError(reason)`.
    fn fail(&self, reason: &str) -> PyErr {
        match self.error.call1((reason,)) {
            Ok(instance) => PyErr::from_value(instance),
            Err(error) => error,
        }
    }

    fn is_control_error(&self, error: &PyErr) -> bool {
        error.matches(self.py, &self.error).unwrap_or(false)
    }

    /// The reference's `except ControlProtocolError: raise` / `except (...): raise
    /// ControlProtocolError("frame_invalid") from None`, with the struct error folded in when
    /// `with_struct` is set.
    fn map_error(&self, error: PyErr, with_struct: bool) -> PyErr {
        if self.is_control_error(&error) {
            return error;
        }
        let py = self.py;
        let folded = error.is_instance_of::<PyTypeError>(py)
            || error.is_instance_of::<PyValueError>(py)
            || (with_struct && STRUCT_ERROR.get(py).is_some_and(|class| error.matches(py, &class).unwrap_or(false)));
        if !folded {
            return error;
        }
        let replacement = self.fail("frame_invalid");
        if self.is_control_error(&replacement) {
            // `from None` sets `__cause__` to None and suppresses the context, which stays the
            // exception the reference's handler caught.
            let value = replacement.value(py);
            let original = error.into_value(py);
            unsafe {
                ffi::PyException_SetContext(value.as_ptr(), original.into_ptr());
            }
            replacement.set_cause(py, None);
        }
        replacement
    }

    /// `_plain_wire_value(value)` through the module global, or `value` itself when the global
    /// is the native twin and `value` is already a plain tree. The flag says whether the result
    /// is known to be a plain tree.
    fn plain(&self, value: &Bound<'py, PyAny>) -> PyResult<(Bound<'py, PyAny>, bool)> {
        let function = self.global("_plain_wire_value")?;
        if ORIGINAL_PLAIN.get(self.py).is_some_and(|original| function.is(&original)) && is_plain_tree(value, 0) {
            return Ok((value.clone(), true));
        }
        Ok((function.call1((value,))?, false))
    }

    /// `_validated_wire`'s schema-version choice.
    fn schema_version(&self, schema_name: &str) -> PyResult<Bound<'py, PyAny>> {
        let name = match schema_name {
            "control-result" => "_CONTROL_RESULT_SCHEMA_VERSION",
            "control-request" => "_CONTROL_REQUEST_SCHEMA_VERSION",
            "control-hello" | "control-hello-result" => "_CONTROL_SCHEMA_VERSION",
            _ => "_SCHEMA_VERSION",
        };
        self.global(name)
    }

    /// The body of `_validated_wire(value, schema_name)` without its error mapping. `None`
    /// means the frozen frame was not built because the caller discards it and building it
    /// could neither fail nor change anything (see the module comment).
    fn validated_body(
        &self,
        value: &Bound<'py, PyAny>,
        schema_name: &Bound<'py, PyString>,
        name: &str,
        need_frozen: bool,
    ) -> PyResult<(Option<Bound<'py, PyAny>>, Bound<'py, PyAny>, bool)> {
        let py = self.py;
        let (wire, mut plain_tree) = self.plain(value)?;
        if !is_mapping_instance(py, &wire)? {
            return Err(self.fail("frame_invalid"));
        }
        let version = self.schema_version(name)?;
        self.global("validate_schema_instance")?.call1((schema_name, version, &wire))?;
        let freeze = self.global("freeze_json")?;
        let freeze_original = ORIGINAL_FREEZE.get(py).is_some_and(|original| freeze.is(&original));
        let json_object = JSON_OBJECT.get(py).ok_or_else(unbound)?;
        if freeze_original && is_type(value, &json_object) && self.is_original("_plain_wire_value", &ORIGINAL_PLAIN) {
            return Ok((Some(value.clone()), wire, true));
        }
        if !need_frozen && freeze_original && self.is_original("validate_schema_instance", &ORIGINAL_VALIDATE) {
            if !plain_tree {
                plain_tree = is_plain_tree(&wire, 0);
            }
            if plain_tree {
                return Ok((None, wire, true));
            }
        }
        let frozen = freeze.call1((&wire,))?;
        if !is_type(&frozen, &json_object) {
            return Err(self.fail("frame_invalid"));
        }
        Ok((Some(frozen), wire, plain_tree))
    }

    /// `_validated_wire(value, schema_name)` with its own error mapping. The second member is the
    /// validated plain wire tree, and the third says whether it is a plain tree.
    fn validated(
        &self,
        value: &Bound<'py, PyAny>,
        schema_name: &Bound<'py, PyAny>,
        need_frozen: bool,
    ) -> PyResult<Validated<'py>> {
        let exact_name = if is_exact(schema_name, ffi::PyUnicode_CheckExact) {
            let text = unsafe { schema_name.cast_unchecked::<PyString>() };
            text.to_str().ok().map(|name| (text.clone(), name.to_owned()))
        } else {
            None
        };
        let Some((text, name)) = exact_name else {
            // Only the module's own literals reach here in practice; anything else takes the
            // reference, whose set membership test decides an unhashable name.
            let reference = REFERENCE_VALIDATED_WIRE.get(self.py).ok_or_else(unbound)?;
            return Ok(Validated { frozen: Some(reference.call1((value, schema_name))?), wire: None, plain_tree: false });
        };
        match self.validated_body(value, &text, &name, need_frozen) {
            Ok((frozen, wire, plain_tree)) => Ok(Validated { frozen, wire: Some(wire), plain_tree }),
            Err(error) => Err(self.map_error(error, false)),
        }
    }

    /// `_validated_wire` through the module global: the native body when the global is still
    /// the twin, otherwise whatever the global now is (whose result is always frozen).
    fn validated_global(
        &self,
        value: &Bound<'py, PyAny>,
        schema_name: &Bound<'py, PyAny>,
        need_frozen: bool,
    ) -> PyResult<Validated<'py>> {
        let function = self.global("_validated_wire")?;
        if ORIGINAL_VALIDATED_WIRE.get(self.py).is_some_and(|original| function.is(&original)) {
            return self.validated(value, schema_name, need_frozen);
        }
        Ok(Validated { frozen: Some(function.call1((value, schema_name))?), wire: None, plain_tree: false })
    }

    fn frame_limit(&self, name: &str) -> Option<usize> {
        let value = self.global(name).ok()?;
        if !is_exact(&value, ffi::PyLong_CheckExact) {
            return None;
        }
        value.extract::<usize>().ok()
    }

    /// `_validate_frame_size(payload, frame)`; `frame` is built by `build` only when the size
    /// check actually reads it.
    fn frame_size(
        &self,
        payload: &Bound<'py, PyAny>,
        length: usize,
        frame: &mut Option<Bound<'py, PyAny>>,
        build: &dyn Fn() -> PyResult<Bound<'py, PyAny>>,
    ) -> PyResult<()> {
        let function = self.global("_validate_frame_size")?;
        let limits = if ORIGINAL_FRAME_SIZE.get(self.py).is_some_and(|original| function.is(&original)) {
            self.frame_limit("MAX_CONTROL_FRAME_BYTES").zip(self.frame_limit("MAX_ORDINARY_CONTROL_FRAME_BYTES"))
        } else {
            None
        };
        let Some((maximum, ordinary)) = limits else {
            if frame.is_none() {
                *frame = Some(build()?);
            }
            function.call1((payload, frame.as_ref()))?;
            return Ok(());
        };
        if length > maximum {
            return Err(self.fail("frame_too_large"));
        }
        if length > ordinary {
            if frame.is_none() {
                *frame = Some(build()?);
            }
            if !self.global("_is_bounded_import")?.call1((frame.as_ref(),))?.is_truthy()? {
                return Err(self.fail("frame_too_large"));
            }
        }
        Ok(())
    }

    fn decode_body(&self, payload: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let parse = self.global("strict_json_parse")?;
        let parsed = parse.call1((payload,))?;
        let encode = self.global("canonical_encode")?;
        let canonical = if is_exact(payload, ffi::PyBytes_CheckExact)
            && ORIGINAL_PARSE.get(py).is_some_and(|original| parse.is(&original))
            && ORIGINAL_ENCODE.get(py).is_some_and(|original| encode.is(&original))
        {
            // `canonical_encode(strict_json_parse(data)) == data` decided over the bytes alone;
            // the parse above already succeeded, so only the comparison remains.
            let raw = unsafe { payload.cast_unchecked::<PyBytes>() }.as_bytes();
            yoetz_core::protocol::canonical_check::is_canonical_json_bytes(raw)
        } else {
            encode.call1((&parsed,))?.eq(payload)?
        };
        if !canonical || !is_mapping_instance(py, &parsed)? {
            return Err(self.fail("frame_invalid"));
        }
        let schema_name = self.global("_schema_name_for_frame")?.call1((&parsed,))?;
        let value = self.validated_global(&parsed, &schema_name, true)?.frozen.ok_or_else(unbound)?;
        let length = payload.len()?;
        let mut frame = Some(value.clone());
        self.frame_size(payload, length, &mut frame, &|| Ok(value.clone()))?;
        Ok(value)
    }

    fn encode_body(&self, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyBytes>> {
        let py = self.py;
        let wire = self.global("_plain_wire_value")?.call1((value,))?;
        if !is_mapping_instance(py, &wire)? {
            return Err(self.fail("frame_invalid"));
        }
        let schema_name = self.global("_schema_name_for_frame")?.call1((&wire,))?;
        let Validated { frozen: mut frame, wire: validated_wire, plain_tree } = self.validated_global(&wire, &schema_name, false)?;
        let encode = self.global("canonical_encode")?;
        let payload = match (&frame, &validated_wire) {
            (Some(frame), _) => encode.call1((frame,))?,
            (None, Some(validated_wire)) if plain_tree && ORIGINAL_ENCODE.get(py).is_some_and(|original| encode.is(&original)) => {
                // The frozen frame is structurally this plain tree: same canonical bytes.
                encode.call1((validated_wire,))?
            }
            (None, Some(validated_wire)) => {
                let frozen = self.global("freeze_json")?.call1((validated_wire,))?;
                let bytes = encode.call1((&frozen,))?;
                frame = Some(frozen);
                bytes
            }
            (None, None) => return Err(unbound()),
        };
        let length = payload.len()?;
        // Only an oversized frame needs the frozen frame (`_is_bounded_import` reads it).
        let build = || -> PyResult<Bound<'py, PyAny>> {
            let source = validated_wire.as_ref().ok_or_else(unbound)?;
            self.global("freeze_json")?.call1((source,))
        };
        self.frame_size(&payload, length, &mut frame, &build)?;
        let raw = payload.cast::<PyBytes>()?.as_bytes();
        let length = u32::try_from(raw.len()).map_err(|_| self.fail("frame_invalid"))?;
        PyBytes::new_with(py, raw.len() + 4, |out| {
            out[..4].copy_from_slice(&length.to_be_bytes());
            out[4..].copy_from_slice(raw);
            Ok(())
        })
    }
}

/// A tree `_plain_wire_value` would copy without change: exact `dict` with exact `str` keys,
/// exact `list`, and plain scalars. Deeper than the native bound answers `false`, so the caller
/// takes the reference path.
fn is_plain_tree(value: &Bound<'_, PyAny>, depth: usize) -> bool {
    if is_plain_scalar(value) {
        return true;
    }
    if depth > NATIVE_RECURSION_LIMIT {
        return false;
    }
    if is_exact(value, ffi::PyDict_CheckExact) {
        let dict = unsafe { value.cast_unchecked::<PyDict>() };
        return dict
            .iter()
            .all(|(key, member)| is_exact(&key, ffi::PyUnicode_CheckExact) && is_plain_tree(&member, depth + 1));
    }
    if is_exact(value, ffi::PyList_CheckExact) {
        let list = unsafe { value.cast_unchecked::<PyList>() };
        return list.iter().all(|member| is_plain_tree(&member, depth + 1));
    }
    false
}

/// `_validated_wire(value, schema_name)`.
#[pyfunction]
#[pyo3(name = "control_validated_wire")]
pub fn validated_wire<'py>(py: Python<'py>, value: &Bound<'py, PyAny>, schema_name: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let pipeline = Pipeline::new(py)?;
    pipeline.validated(value, schema_name, true)?.frozen.ok_or_else(unbound)
}

/// `validate_request(request)`.
#[pyfunction]
#[pyo3(name = "control_validate_request")]
pub fn validate_request(py: Python<'_>, request: &Bound<'_, PyAny>) -> PyResult<()> {
    let pipeline = Pipeline::new(py)?;
    pipeline.validated_global(request, PyString::intern(py, "control-request").as_any(), false)?;
    Ok(())
}

/// `validate_result(result)`.
#[pyfunction]
#[pyo3(name = "control_validate_result")]
pub fn validate_result(py: Python<'_>, result: &Bound<'_, PyAny>) -> PyResult<()> {
    let pipeline = Pipeline::new(py)?;
    let class = pipeline.global("ControlResult")?;
    if !is_type(result, &class) {
        return Err(PyTypeError::new_err("control_result_invalid"));
    }
    pipeline.validated_global(result, PyString::intern(py, "control-result").as_any(), false)?;
    Ok(())
}

/// `_decode_control_payload(payload)`.
#[pyfunction]
#[pyo3(name = "control_decode_control_payload")]
pub fn decode_control_payload<'py>(py: Python<'py>, payload: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let pipeline = Pipeline::new(py)?;
    pipeline.decode_body(payload).map_err(|error| pipeline.map_error(error, false))
}

/// `encode_control_frame(value)`.
#[pyfunction]
#[pyo3(name = "control_encode_control_frame")]
pub fn encode_control_frame<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyBytes>> {
    let pipeline = Pipeline::new(py)?;
    pipeline.encode_body(value).map_err(|error| pipeline.map_error(error, true))
}

/// The thawed twin of an exact `JsonObject` tree: what `strict_json_parse(canonical_encode(x))`
/// returns, keys in canonical (UTF-16) order. `None` for anything that is not such a tree.
fn thaw<'py>(py: Python<'py>, class: &Bound<'py, PyAny>, value: &Bound<'py, PyAny>, depth: usize) -> PyResult<Option<Bound<'py, PyAny>>> {
    if value.is_none()
        || is_exact(value, ffi::PyBool_Check)
        || is_exact(value, ffi::PyLong_CheckExact)
        || is_exact(value, ffi::PyUnicode_CheckExact)
    {
        return Ok(Some(value.clone()));
    }
    if depth > NATIVE_RECURSION_LIMIT {
        return Ok(None);
    }
    if is_type(value, class) {
        let items = json_object_items(value)?;
        let mut pairs: Vec<(Bound<'py, PyString>, Bound<'py, PyAny>)> = Vec::with_capacity(items.len());
        for pair in items.iter() {
            let pair = pair.cast_into::<PyTuple>()?;
            let key = pair.get_item(0)?;
            if !is_exact(&key, ffi::PyUnicode_CheckExact) {
                return Ok(None);
            }
            let Some(member) = thaw(py, class, &pair.get_item(1)?, depth + 1)? else {
                return Ok(None);
            };
            pairs.push((key.cast_into::<PyString>()?, member));
        }
        let mut keyed = Vec::with_capacity(pairs.len());
        for (key, member) in pairs {
            let Ok(text) = key.to_str().map(str::to_owned) else {
                return Ok(None);
            };
            keyed.push((text, key, member));
        }
        keyed.sort_by(|left, right| utf16_cmp(&left.0, &right.0));
        let out = PyDict::new(py);
        for (_, key, member) in keyed {
            out.set_item(key, member)?;
        }
        return Ok(Some(out.into_any()));
    }
    if is_exact(value, ffi::PyTuple_CheckExact) {
        let tuple = unsafe { value.cast_unchecked::<PyTuple>() };
        let mut members = Vec::with_capacity(tuple.len());
        for member in tuple.iter() {
            let Some(member) = thaw(py, class, &member, depth + 1)? else {
                return Ok(None);
            };
            members.push(member);
        }
        return Ok(Some(PyList::new(py, members)?.into_any()));
    }
    Ok(None)
}

/// `_plain_mapping_for_model(value)`.
#[pyfunction]
#[pyo3(name = "control_plain_mapping_for_model")]
pub fn plain_mapping_for_model<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let pipeline = Pipeline::new(py)?;
    if !is_mapping_instance(py, value)? {
        return Err(pipeline.fail("frame_invalid"));
    }
    let mut thawed = None;
    if let Some(class) = JSON_OBJECT.get(py) {
        if is_type(value, &class)
            && pipeline.is_original("strict_json_parse", &ORIGINAL_PARSE)
            && pipeline.is_original("canonical_encode", &ORIGINAL_ENCODE)
        {
            thawed = thaw(py, &class, value, 0)?;
        }
    }
    let thawed = match thawed {
        Some(thawed) => thawed,
        None => {
            let encoded = pipeline.global("canonical_encode")?.call1((value,))?;
            pipeline.global("strict_json_parse")?.call1((encoded,))?
        }
    };
    if !is_exact(&thawed, ffi::PyDict_CheckExact) {
        return Err(pipeline.fail("frame_invalid"));
    }
    Ok(thawed)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_pipeline, module)?)?;
    module.add_function(wrap_pyfunction!(validated_wire, module)?)?;
    module.add_function(wrap_pyfunction!(validate_request, module)?)?;
    module.add_function(wrap_pyfunction!(validate_result, module)?)?;
    module.add_function(wrap_pyfunction!(decode_control_payload, module)?)?;
    module.add_function(wrap_pyfunction!(encode_control_frame, module)?)?;
    module.add_function(wrap_pyfunction!(plain_mapping_for_model, module)?)?;
    Ok(())
}
