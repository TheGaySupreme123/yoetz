//! `yoetz.protocol.models` tree walks over live Python objects: `_strip_optional_non_null_fields`
//! and `classify_result_leaf`.
//!
//! `_classify_leaf_shape` stays the Python `functools.lru_cache` function: the twin looks it up
//! in the module globals on every call, so a replaced or instrumented cache is still the one
//! consulted. Anything other than the exact built-in containers (and `JsonObject`) is handed to
//! the Python reference before any rule is consulted.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use pyo3::exceptions::{PyAttributeError, PyValueError};
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::pyclass::CompareOp;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyDict, PyFrozenSet, PyList, PyString, PyTuple, PyType};
use yoetz_core::protocol::pointer::{self as core_pointer, INVALID_JSON_POINTER};

use crate::registry::{Slot, protocol_error};
use crate::walk::{
    JSON_OBJECT, NATIVE_RECURSION_LIMIT, is_exact, is_mapping_instance, is_plain_scalar, is_sequence_instance, is_type,
    json_object_index,
};

// ---------------------------------------------------------------------------------------------
// _strip_optional_non_null_fields
// ---------------------------------------------------------------------------------------------

static BASE_MODEL: Slot = Slot::new();
static STRIP_REFERENCE: Slot = Slot::new();
static BASE_MODEL_GETATTR: Slot = Slot::new();
static OBJECT_GETATTRIBUTE: Slot = Slot::new();

/// What one model class contributes to the strip, cached by class identity and revalidated
/// against the identity of `__pydantic_fields__` and `optional_non_null_fields` on every use.
struct ModelInfo {
    _class: Py<PyAny>,
    fields: Py<PyAny>,
    field_count: usize,
    declared: Option<Py<PyAny>>,
    /// `optional_non_null_fields` when it is a `frozenset` (the reference ignores anything else).
    optional: Option<Py<PyAny>>,
    /// Dump key (serialization alias, alias, or name) to field name.
    by_dump_key: Py<PyDict>,
    /// `getattr(model, "root", None)` reduces to the instance dict and `__pydantic_extra__`.
    plain_root: bool,
    root_is_field: bool,
}

static MODEL_INFO: Mutex<Option<HashMap<usize, ModelInfo>>> = Mutex::new(None);

/// Bind `BaseModel`, `BaseModel.__getattr__`, and the Python reference strip.
#[pyfunction]
#[pyo3(name = "models_bind_strip")]
pub fn bind_strip(base_model: Bound<'_, PyAny>, reference: Bound<'_, PyAny>) -> PyResult<()> {
    let py = base_model.py();
    BASE_MODEL_GETATTR.set(base_model.getattr("__getattr__")?.unbind());
    OBJECT_GETATTRIBUTE.set(py.import("builtins")?.getattr("object")?.getattr("__getattribute__")?.unbind());
    BASE_MODEL.set(base_model.unbind());
    STRIP_REFERENCE.set(reference.unbind());
    *MODEL_INFO.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = None;
    Ok(())
}

fn class_attr<'py>(class: &Bound<'py, PyType>, name: &Bound<'py, PyString>) -> PyResult<Option<Bound<'py, PyAny>>> {
    match class.getattr(name) {
        Ok(value) => Ok(Some(value)),
        Err(error) if error.is_instance_of::<PyAttributeError>(class.py()) => Ok(None),
        Err(error) => Err(error),
    }
}

fn build_info(py: Python<'_>, class: &Bound<'_, PyType>, fields: &Bound<'_, PyAny>, declared: Option<&Bound<'_, PyAny>>) -> PyResult<ModelInfo> {
    let by_dump_key = PyDict::new(py);
    let fields_dict = fields.cast::<PyDict>()?;
    for (name, field) in fields_dict.iter() {
        let mut key = field.getattr(pyo3::intern!(py, "serialization_alias"))?;
        if !key.is_truthy()? {
            key = field.getattr(pyo3::intern!(py, "alias"))?;
        }
        if !key.is_truthy()? {
            key = name.clone();
        }
        by_dump_key.set_item(key, name)?;
    }
    let optional = match declared {
        Some(value) if value.is_instance_of::<PyFrozenSet>() => Some(value.clone().unbind()),
        _ => None,
    };
    let getattr_hook = class_attr(class, pyo3::intern!(py, "__getattr__"))?;
    let getattribute = class_attr(class, pyo3::intern!(py, "__getattribute__"))?;
    let private: Option<Bound<'_, PyAny>> = class_attr(class, pyo3::intern!(py, "__private_attributes__"))?;
    let root_name = pyo3::intern!(py, "root");
    let private_has_root = match &private {
        Some(mapping) => mapping.contains(root_name)?,
        None => true,
    };
    let plain_root = matches!((&getattr_hook, BASE_MODEL_GETATTR.get(py)), (Some(hook), Some(expected)) if hook.is(&expected))
        && matches!((&getattribute, OBJECT_GETATTRIBUTE.get(py)), (Some(found), Some(expected)) if found.is(&expected))
        && !private_has_root
        && class_attr(class, root_name)?.is_none();
    Ok(ModelInfo {
        _class: class.clone().into_any().unbind(),
        fields: fields.clone().unbind(),
        field_count: fields_dict.len(),
        declared: declared.map(|value| value.clone().unbind()),
        optional,
        by_dump_key: by_dump_key.unbind(),
        plain_root,
        root_is_field: fields_dict.contains(root_name)?,
    })
}

struct InfoView<'py> {
    optional: Option<Bound<'py, PyAny>>,
    by_dump_key: Bound<'py, PyDict>,
    plain_root: bool,
    root_is_field: bool,
}

fn model_info<'py>(py: Python<'py>, class: &Bound<'py, PyType>) -> PyResult<InfoView<'py>> {
    let fields = class.getattr(pyo3::intern!(py, "__pydantic_fields__"))?;
    let declared = class_attr(class, pyo3::intern!(py, "optional_non_null_fields"))?;
    let key = class.as_ptr() as usize;
    {
        let guard = MODEL_INFO.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        if let Some(info) = guard.as_ref().and_then(|cache| cache.get(&key)) {
            let same_declared = match (&info.declared, &declared) {
                (Some(cached), Some(current)) => cached.as_ptr() == current.as_ptr(),
                (None, None) => true,
                _ => false,
            };
            let same_fields = info.fields.as_ptr() == fields.as_ptr()
                && fields.cast::<PyDict>().map(|dict| dict.len() == info.field_count).unwrap_or(false);
            if same_declared && same_fields {
                return Ok(InfoView {
                    optional: info.optional.as_ref().map(|value| value.bind(py).clone()),
                    by_dump_key: info.by_dump_key.bind(py).clone(),
                    plain_root: info.plain_root,
                    root_is_field: info.root_is_field,
                });
            }
        }
    }
    let info = build_info(py, class, &fields, declared.as_ref())?;
    let view = InfoView {
        optional: info.optional.as_ref().map(|value| value.bind(py).clone()),
        by_dump_key: info.by_dump_key.bind(py).clone(),
        plain_root: info.plain_root,
        root_is_field: info.root_is_field,
    };
    let mut guard = MODEL_INFO.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    guard.get_or_insert_with(HashMap::new).insert(key, info);
    Ok(view)
}

/// `isinstance(value, BaseModel)`.
fn is_base_model(base: &Bound<'_, PyAny>, value: &Bound<'_, PyAny>) -> PyResult<bool> {
    if unsafe { ffi::PyType_IsSubtype(value.get_type().as_ptr().cast(), base.as_ptr().cast()) } != 0 {
        return Ok(true);
    }
    if is_plain_scalar(value)
        || is_exact(value, ffi::PyDict_CheckExact)
        || is_exact(value, ffi::PyList_CheckExact)
        || is_exact(value, ffi::PyTuple_CheckExact)
    {
        return Ok(false);
    }
    value.is_instance(base)
}

/// `getattr(attribute_owner, name, None)` restricted to `AttributeError`.
fn getattr_or_none<'py>(value: &Bound<'py, PyAny>, name: &Bound<'py, PyString>) -> PyResult<Option<Bound<'py, PyAny>>> {
    match value.getattr(name) {
        Ok(found) => Ok(Some(found)),
        Err(error) if error.is_instance_of::<PyAttributeError>(value.py()) => Ok(None),
        Err(error) => Err(error),
    }
}

/// `getattr(model, "root", None)`.
fn model_root<'py>(py: Python<'py>, model: &Bound<'py, PyAny>, info: &InfoView<'py>) -> PyResult<Option<Bound<'py, PyAny>>> {
    let name = pyo3::intern!(py, "root");
    if info.plain_root && !info.root_is_field {
        // No class attribute, private attribute, or custom hook can supply ``root``: only the
        // instance dict (a field would live there) or ``__pydantic_extra__`` can.
        let extra = getattr_or_none(model, pyo3::intern!(py, "__pydantic_extra__"))?;
        let extra_has_root = match extra {
            Some(extra) if extra.is_truthy()? => extra.contains(name)?,
            _ => false,
        };
        if !extra_has_root {
            if let Some(dict) = getattr_or_none(model, pyo3::intern!(py, "__dict__"))? {
                if let Ok(dict) = dict.cast::<PyDict>() {
                    return dict.get_item(name);
                }
            }
        }
    }
    getattr_or_none(model, name)
}

struct Stripper<'py> {
    py: Python<'py>,
    base: Bound<'py, PyAny>,
    reference: Bound<'py, PyAny>,
    /// `models._strip_optional_non_null_fields` (root unwrap, dump aliases) when true;
    /// `application.status._strip_optional_non_null_nulls` (dump keys are attribute names)
    /// when false.
    public: bool,
}

impl<'py> Stripper<'py> {
    fn strip(&self, model: &Bound<'py, PyAny>, dumped: &Bound<'py, PyAny>, depth: usize) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        if depth > NATIVE_RECURSION_LIMIT {
            return self.reference.call1((model, dumped));
        }
        let (optional, by_dump_key) = if self.public {
            let info = model_info(py, &model.get_type())?;
            if let Some(root) = model_root(py, model, &info)? {
                if is_base_model(&self.base, &root)? {
                    return self.strip(&root, dumped, depth + 1);
                }
            }
            (info.optional, Some(info.by_dump_key))
        } else {
            let declared = class_attr(&model.get_type(), pyo3::intern!(py, "optional_non_null_fields"))?;
            (declared.filter(|value| value.is_instance_of::<PyFrozenSet>()), None)
        };
        let result = PyDict::new(py);
        let pairs: Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)> = if is_exact(dumped, ffi::PyDict_CheckExact) {
            unsafe { dumped.cast_unchecked::<PyDict>() }.iter().collect()
        } else {
            let mut collected = Vec::new();
            for pair in dumped.call_method0(pyo3::intern!(py, "items"))?.try_iter()? {
                collected.push(pair?.extract()?);
            }
            collected
        };
        for (key, value) in pairs {
            let field_name = match by_dump_key.as_ref().map(|map| map.get_item(&key)).transpose()?.flatten() {
                Some(name) => name,
                None => key.clone(),
            };
            if value.is_none() {
                if let Some(optional) = &optional {
                    if optional.contains(&field_name)? {
                        continue;
                    }
                }
            }
            let name = field_name.cast::<PyString>().map_err(|_| {
                pyo3::exceptions::PyTypeError::new_err("attribute name must be string")
            })?;
            let attribute = model.getattr(name)?;
            if is_base_model(&self.base, &attribute)? && is_mapping_instance(py, &value)? {
                result.set_item(&key, self.strip(&attribute, &value, depth + 1)?)?;
            } else if unsafe { ffi::PyList_Check(value.as_ptr()) } != 0
                && is_sequence_instance(py, &attribute)?
                && unsafe { ffi::PyUnicode_Check(attribute.as_ptr()) == 0 && ffi::PyBytes_Check(attribute.as_ptr()) == 0 }
            {
                result.set_item(&key, self.children(&attribute, &value, depth)?)?;
            } else {
                result.set_item(&key, &value)?;
            }
        }
        Ok(result.into_any())
    }

    /// The `zip(attribute, value, strict=True)` loop.
    fn children(&self, attribute: &Bound<'py, PyAny>, value: &Bound<'py, PyAny>, depth: usize) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        let children = PyList::empty(py);
        let mut left = attribute.try_iter()?;
        let mut right = value.try_iter()?;
        loop {
            let Some(child) = left.next().transpose()? else {
                if right.next().transpose()?.is_some() {
                    return Err(PyValueError::new_err("zip() argument 2 is longer than argument 1"));
                }
                break;
            };
            let Some(child_dump) = right.next().transpose()? else {
                return Err(PyValueError::new_err("zip() argument 2 is shorter than argument 1"));
            };
            if is_base_model(&self.base, &child)? && is_mapping_instance(py, &child_dump)? {
                children.append(self.strip(&child, &child_dump, depth + 1)?)?;
            } else {
                children.append(child_dump)?;
            }
        }
        Ok(children.into_any())
    }
}

/// `_strip_optional_non_null_fields(model, dumped)`.
#[pyfunction]
#[pyo3(name = "models_strip_optional_non_null_fields")]
pub fn strip_optional_non_null_fields<'py>(py: Python<'py>, model: &Bound<'py, PyAny>, dumped: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let (Some(base), Some(reference)) = (BASE_MODEL.get(py), STRIP_REFERENCE.get(py)) else {
        return Err(pyo3::exceptions::PyRuntimeError::new_err("strip_unbound"));
    };
    Stripper { py, base, reference, public: true }.strip(model, dumped, 0)
}

static STATUS_STRIP_REFERENCE: Slot = Slot::new();

/// Bind `BaseModel` and the Python reference `application.status._strip_optional_non_null_nulls`.
#[pyfunction]
#[pyo3(name = "status_bind_strip")]
pub fn bind_status_strip(base_model: Bound<'_, PyAny>, reference: Bound<'_, PyAny>) {
    BASE_MODEL.set(base_model.unbind());
    STATUS_STRIP_REFERENCE.set(reference.unbind());
}

/// `yoetz.application.status._strip_optional_non_null_nulls(model, dumped)`.
#[pyfunction]
#[pyo3(name = "status_strip_optional_non_null_nulls")]
pub fn strip_optional_non_null_nulls<'py>(py: Python<'py>, model: &Bound<'py, PyAny>, dumped: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let (Some(base), Some(reference)) = (BASE_MODEL.get(py), STATUS_STRIP_REFERENCE.get(py)) else {
        return Err(pyo3::exceptions::PyRuntimeError::new_err("strip_unbound"));
    };
    Stripper { py, base, reference, public: false }.strip(model, dumped, 0)
}

// ---------------------------------------------------------------------------------------------
// _ClosedModel input adaptation and wire validators
// ---------------------------------------------------------------------------------------------

static ADAPT_REFERENCE: Slot = Slot::new();
static ACCEPTS_TUPLE: Slot = Slot::new();

/// Per model class: the fields (in declaration order) whose annotation accepts a tuple, cached
/// by class identity and revalidated against `__pydantic_fields__`.
struct TupleFields {
    _class: Py<PyAny>,
    fields: Py<PyAny>,
    field_count: usize,
    names: Vec<Py<PyString>>,
}

static TUPLE_FIELDS: Mutex<Option<HashMap<usize, Arc<TupleFields>>>> = Mutex::new(None);

/// Bind the Python reference adaptation and `_annotation_accepts_tuple`.
#[pyfunction]
#[pyo3(name = "models_bind_adapt")]
pub fn bind_adapt(reference: Bound<'_, PyAny>, accepts_tuple: Bound<'_, PyAny>) {
    ADAPT_REFERENCE.set(reference.unbind());
    ACCEPTS_TUPLE.set(accepts_tuple.unbind());
    *TUPLE_FIELDS.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = None;
}

fn tuple_fields(py: Python<'_>, class: &Bound<'_, PyAny>, accepts_tuple: &Bound<'_, PyAny>) -> PyResult<Option<Arc<TupleFields>>> {
    let Some(fields) = getattr_or_none(class, pyo3::intern!(py, "__pydantic_fields__"))? else {
        return Ok(None);
    };
    let Ok(fields_dict) = fields.cast::<PyDict>() else {
        return Ok(None);
    };
    let key = class.as_ptr() as usize;
    let cached = TUPLE_FIELDS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .as_ref()
        .and_then(|cache| cache.get(&key).cloned());
    if let Some(entry) = cached {
        if entry.fields.as_ptr() == fields.as_ptr() && entry.field_count == fields_dict.len() {
            return Ok(Some(entry));
        }
    }
    let mut names = Vec::new();
    for (name, field) in fields_dict.iter() {
        let annotation = field.getattr(pyo3::intern!(py, "annotation"))?;
        if accepts_tuple.call1((annotation,))?.is_truthy()? {
            names.push(name.cast_into::<PyString>()?.unbind());
        }
    }
    let entry = Arc::new(TupleFields {
        _class: class.clone().unbind(),
        fields: fields.clone().unbind(),
        field_count: fields_dict.len(),
        names,
    });
    TUPLE_FIELDS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .get_or_insert_with(HashMap::new)
        .insert(key, entry.clone());
    Ok(Some(entry))
}

/// `_ClosedModel._adapt_json_arrays_and_reject_forbidden_nulls(cls, value)`.
#[pyfunction]
#[pyo3(name = "models_adapt_closed_input")]
pub fn adapt_closed_input<'py>(py: Python<'py>, class: &Bound<'py, PyAny>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let (Some(reference), Some(accepts_tuple)) = (ADAPT_REFERENCE.get(py), ACCEPTS_TUPLE.get(py)) else {
        return Err(pyo3::exceptions::PyRuntimeError::new_err("adapt_unbound"));
    };
    if !is_exact(value, ffi::PyDict_CheckExact) {
        if is_mapping_instance(py, value)? {
            return reference.call1((class, value));
        }
        return Ok(value.clone());
    }
    let source = unsafe { value.cast_unchecked::<PyDict>() };
    let declared = class.getattr(pyo3::intern!(py, "optional_non_null_fields"))?;
    if !(is_exact(&declared, ffi::PyFrozenSet_CheckExact) || is_exact(&declared, ffi::PySet_CheckExact)) {
        return reference.call1((class, value));
    }
    for field_name in declared.try_iter()? {
        let field_name = field_name?;
        if let Some(found) = source.get_item(&field_name)? {
            if found.is_none() {
                return Err(PyValueError::new_err("optional_field_must_not_be_null"));
            }
        }
    }
    let Some(entry) = tuple_fields(py, class, &accepts_tuple)? else {
        return reference.call1((class, value));
    };
    let mut adapted: Option<Bound<'py, PyDict>> = None;
    for name in &entry.names {
        let name = name.bind(py);
        let Some(raw) = source.get_item(name)? else {
            continue;
        };
        if is_exact(&raw, ffi::PyList_CheckExact) {
            let target = match &adapted {
                Some(target) => target.clone(),
                None => {
                    let copy = source.copy()?;
                    adapted = Some(copy.clone());
                    copy
                }
            };
            let members = unsafe { raw.cast_unchecked::<PyList>() }.to_tuple();
            target.set_item(name, members)?;
        }
    }
    Ok(match adapted {
        Some(adapted) => adapted.into_any(),
        None => value.clone(),
    })
}

/// `_timestamp_wire(value)`.
#[pyfunction]
#[pyo3(name = "models_timestamp_wire")]
pub fn timestamp_wire<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    if is_exact(value, ffi::PyUnicode_CheckExact) {
        if let Ok(text) = unsafe { value.cast_unchecked::<PyString>() }.to_str() {
            if yoetz_core::protocol::timestamp::is_wire_timestamp(text) {
                return Ok(value.clone());
            }
        }
    }
    Err(protocol_error(py, "invalid_timestamp"))
}

// ---------------------------------------------------------------------------------------------
// classify_result_leaf
// ---------------------------------------------------------------------------------------------

struct LeafBindings {
    module_globals: Py<PyDict>,
    result_methods: Py<PyAny>,
    status_views: Py<PyAny>,
    known_selectors: Py<PyAny>,
    receipt_text_formats: Py<PyAny>,
    reference: Py<PyAny>,
    max_pointer_bytes: usize,
}

static LEAF_BINDINGS: Mutex<Option<Arc<LeafBindings>>> = Mutex::new(None);
static NORMALIZE: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// Bind the models module globals, the closed rule sets, and the Python reference.
#[pyfunction]
#[pyo3(name = "models_bind_classify")]
pub fn bind_classify(
    module_globals: Bound<'_, PyDict>,
    result_methods: Bound<'_, PyAny>,
    status_views: Bound<'_, PyAny>,
    known_selectors: Bound<'_, PyAny>,
    reference: Bound<'_, PyAny>,
    max_pointer_bytes: usize,
) -> PyResult<()> {
    let py = module_globals.py();
    let receipt_text_formats = PyFrozenSet::new(py, ["markdown", "text"])?.into_any().unbind();
    *LEAF_BINDINGS.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(Arc::new(LeafBindings {
        module_globals: module_globals.unbind(),
        result_methods: result_methods.unbind(),
        status_views: status_views.unbind(),
        known_selectors: known_selectors.unbind(),
        receipt_text_formats,
        reference: reference.unbind(),
        max_pointer_bytes,
    }));
    Ok(())
}

enum Step<T> {
    Done(T),
    Refused,
    Defer,
}

fn invalid(py: Python<'_>) -> PyErr {
    protocol_error(py, INVALID_JSON_POINTER)
}

fn is_nfc(py: Python<'_>, text: &str) -> bool {
    let normalize = NORMALIZE.get_or_try_init(py, || -> PyResult<Py<PyAny>> {
        Ok(py.import("unicodedata")?.getattr("normalize")?.unbind())
    });
    let Ok(normalize) = normalize else {
        return false;
    };
    match normalize.bind(py).call1(("NFC", text)) {
        Ok(normalized) => normalized.extract::<&str>().map(|value| value == text).unwrap_or(false),
        Err(_) => false,
    }
}

/// `mapping.get(key)` for an exact dict or `JsonObject` (`None` when absent).
fn fast_get<'py>(container: &Bound<'py, PyAny>, json_object: &Bound<'py, PyAny>, key: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyAny>>> {
    if is_exact(container, ffi::PyDict_CheckExact) {
        return unsafe { container.cast_unchecked::<PyDict>() }.get_item(key);
    }
    debug_assert!(is_type(container, json_object));
    let index = json_object_index(container)?;
    if !index.contains(key)? {
        return Ok(None);
    }
    Ok(Some(index.get_item(key)?))
}

fn is_fast_mapping(value: &Bound<'_, PyAny>, json_object: &Bound<'_, PyAny>) -> bool {
    is_exact(value, ffi::PyDict_CheckExact) || is_type(value, json_object)
}

/// The traversal's leaf, which segments indexed arrays, and the container visited per segment.
type Traversal<'py> = (Bound<'py, PyAny>, Vec<bool>, Vec<Bound<'py, PyAny>>);

struct Classifier<'py> {
    py: Python<'py>,
    json_object: Bound<'py, PyAny>,
}

impl<'py> Classifier<'py> {
    /// `_traverse_result_leaf`, recording each container visited.
    fn traverse(&self, result: &Bound<'py, PyAny>, segments: &[Bound<'py, PyString>]) -> PyResult<Step<Traversal<'py>>> {
        let mut current = result.clone();
        let mut arrays = Vec::with_capacity(segments.len());
        let mut path = Vec::with_capacity(segments.len());
        for segment in segments {
            path.push(current.clone());
            if is_fast_mapping(&current, &self.json_object) {
                match fast_get(&current, &self.json_object, segment.as_any())? {
                    Some(next) => current = next,
                    None => return Ok(Step::Refused),
                }
                arrays.push(false);
            } else if is_exact(&current, ffi::PyList_CheckExact) || is_exact(&current, ffi::PyTuple_CheckExact) {
                let Some(index) = core_pointer::array_index(segment.to_str()?) else {
                    return Ok(Step::Refused);
                };
                let length = current.len()?;
                if index >= length {
                    return Ok(Step::Refused);
                }
                current = current.get_item(index)?;
                arrays.push(true);
            } else if is_plain_scalar(&current) {
                return Ok(Step::Refused);
            } else {
                return Ok(Step::Defer);
            }
        }
        let leaf_type_ok = current.is_none()
            || is_exact(&current, ffi::PyBool_Check)
            || is_exact(&current, ffi::PyLong_CheckExact)
            || is_exact(&current, ffi::PyUnicode_CheckExact);
        if !leaf_type_ok {
            return Ok(Step::Refused);
        }
        Ok(Step::Done((current, arrays, path)))
    }

    /// `_publish_event_selector` given the traversal's visited containers.
    fn publish_selector(
        &self,
        bindings: &LeafBindings,
        segments: &[Bound<'py, PyString>],
        path: &[Bound<'py, PyAny>],
        leaf: &Bound<'py, PyAny>,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        let py = self.py;
        if segments.len() != 3 || segments[0].to_str()? != "accepted_events" || segments[2].to_str()? != "summary" {
            return Ok(None);
        }
        // The traversal resolved ``accepted_events`` (path[1]) and the event (path[2]); the
        // reference re-reads both and refuses anything but a sequence holding a mapping, which
        // is exactly when the traversal indexed path[1] as an array.
        let raw_events = &path[1];
        if !(is_exact(raw_events, ffi::PyList_CheckExact) || is_exact(raw_events, ffi::PyTuple_CheckExact)) {
            return Err(invalid(py));
        }
        let event = &path[2];
        let schema_name = fast_get(event, &self.json_object, pyo3::intern!(py, "schema_name").as_any())?;
        let schema_version = fast_get(event, &self.json_object, pyo3::intern!(py, "schema_version").as_any())?;
        let (Some(schema_name), Some(schema_version)) = (schema_name, schema_version) else {
            return Err(invalid(py));
        };
        if !is_exact(&schema_name, ffi::PyUnicode_CheckExact) || !is_exact(&schema_version, ffi::PyUnicode_CheckExact) {
            return Err(invalid(py));
        }
        let selector = PyTuple::new(py, [schema_name, schema_version])?;
        if bindings.known_selectors.bind(py).contains(&selector)? {
            return Ok(Some(selector.into_any()));
        }
        // ``event_map.get("summary")`` is the leaf the traversal reached.
        if leaf.rich_compare("opaque_unknown", CompareOp::Ne)?.is_truthy()? {
            return Err(invalid(py));
        }
        Ok(Some(pyo3::intern!(py, "<opaque>").clone().into_any()))
    }

    fn classify(
        &self,
        bindings: &LeafBindings,
        method: &Bound<'py, PyAny>,
        result: &Bound<'py, PyAny>,
        pointer: &Bound<'py, PyAny>,
    ) -> PyResult<Step<Bound<'py, PyAny>>> {
        let py = self.py;
        if !is_exact(method, ffi::PyUnicode_CheckExact) || !bindings.result_methods.bind(py).contains(method)? {
            return Ok(Step::Refused);
        }
        if !is_fast_mapping(result, &self.json_object) {
            return Ok(Step::Defer);
        }
        match fast_get(result, &self.json_object, pyo3::intern!(py, "ok").as_any())? {
            Some(ok) if ok.as_ptr() == unsafe { ffi::Py_True() } => {}
            _ => return Ok(Step::Refused),
        }
        if !is_exact(pointer, ffi::PyUnicode_CheckExact) {
            return Ok(Step::Refused);
        }
        let Ok(pointer_text) = unsafe { pointer.cast_unchecked::<PyString>() }.to_str() else {
            return Ok(Step::Refused);
        };
        let Ok(decoded) = core_pointer::decode_pointer(pointer_text, bindings.max_pointer_bytes, |text| is_nfc(py, text)) else {
            return Ok(Step::Refused);
        };
        let segments: Vec<Bound<'py, PyString>> = decoded.iter().map(|segment| PyString::new(py, segment)).collect();
        let (leaf, arrays, path) = match self.traverse(result, &segments)? {
            Step::Done(found) => found,
            Step::Refused => return Ok(Step::Refused),
            Step::Defer => return Ok(Step::Defer),
        };
        let method_text = unsafe { method.cast_unchecked::<PyString>() }.to_str()?;
        if method_text == "receipt" && decoded.len() == 1 && decoded[0] == "human_text" {
            let receipt_format = fast_get(result, &self.json_object, pyo3::intern!(py, "format").as_any())?
                .unwrap_or_else(|| py.None().into_bound(py));
            if leaf.is_none() {
                if receipt_format.rich_compare("json", CompareOp::Ne)?.is_truthy()? {
                    return Ok(Step::Refused);
                }
                return Ok(Step::Done(pyo3::intern!(py, "public_structural").clone().into_any()));
            }
            if !is_exact(&leaf, ffi::PyUnicode_CheckExact) || !bindings.receipt_text_formats.bind(py).contains(&receipt_format)? {
                return Ok(Step::Refused);
            }
        }
        let mut status_view = py.None().into_bound(py);
        if method_text == "status" {
            let candidate = fast_get(result, &self.json_object, pyo3::intern!(py, "view").as_any())?;
            match candidate {
                Some(view) if is_exact(&view, ffi::PyUnicode_CheckExact) && bindings.status_views.bind(py).contains(&view)? => {
                    status_view = view;
                }
                _ => return Ok(Step::Refused),
            }
        }
        let event_selector = if method_text == "publish_work" {
            match self.publish_selector(bindings, &segments, &path, &leaf)? {
                Some(selector) => selector,
                None => py.None().into_bound(py),
            }
        } else {
            py.None().into_bound(py)
        };
        let shape = PyTuple::new(
            py,
            segments.iter().zip(arrays.iter()).map(|(segment, &is_array)| {
                if is_array { py.None().into_bound(py) } else { segment.clone().into_any() }
            }),
        )?;
        let globals = bindings.module_globals.bind(py);
        let Some(shape_rule) = globals.get_item(pyo3::intern!(py, "_classify_leaf_shape"))? else {
            return Err(pyo3::exceptions::PyNameError::new_err("name '_classify_leaf_shape' is not defined"));
        };
        let classification = shape_rule.call1((method, status_view, event_selector, shape))?;
        if classification.is_none() {
            return Ok(Step::Refused);
        }
        Ok(Step::Done(classification))
    }
}

/// `classify_result_leaf(method, validated_result, pointer)`.
#[pyfunction]
#[pyo3(name = "models_classify_result_leaf")]
pub fn classify_result_leaf<'py>(
    py: Python<'py>,
    method: &Bound<'py, PyAny>,
    validated_result: &Bound<'py, PyAny>,
    pointer: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    // Clone the bindings out of the lock: the classification calls back into Python.
    let bound = LEAF_BINDINGS.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).clone();
    let Some(bindings) = bound else {
        return Err(pyo3::exceptions::PyRuntimeError::new_err("classify_unbound"));
    };
    let reference = bindings.reference.bind(py).clone();
    let step = match JSON_OBJECT.get(py) {
        Some(json_object) => Classifier { py, json_object }.classify(&bindings, method, validated_result, pointer)?,
        None => Step::Defer,
    };
    match step {
        Step::Done(classification) => Ok(classification),
        Step::Refused => Err(invalid(py)),
        Step::Defer => reference.call1((method, validated_result, pointer)),
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_strip, module)?)?;
    module.add_function(wrap_pyfunction!(strip_optional_non_null_fields, module)?)?;
    module.add_function(wrap_pyfunction!(bind_classify, module)?)?;
    module.add_function(wrap_pyfunction!(classify_result_leaf, module)?)?;
    module.add_function(wrap_pyfunction!(bind_status_strip, module)?)?;
    module.add_function(wrap_pyfunction!(strip_optional_non_null_nulls, module)?)?;
    module.add_function(wrap_pyfunction!(bind_adapt, module)?)?;
    module.add_function(wrap_pyfunction!(adapt_closed_input, module)?)?;
    module.add_function(wrap_pyfunction!(timestamp_wire, module)?)?;
    Ok(())
}
