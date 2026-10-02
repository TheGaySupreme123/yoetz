//! `yoetz.mcp.descriptors` schema bundling tree transforms, and the `_mutable_json` thaw it
//! shares with `yoetz.mcp.server`.
//!
//! Each twin reproduces its Python reference's output (insertion order included) for the shapes
//! the catalog produces: exact `dict`s, `MappingProxyType`s over a `dict`, exact `list`s and
//! `tuple`s, and exact `str` keys. Any other node (a custom `Mapping`, a `list`/`tuple` or `str`
//! subclass, a key that is not an exact `str`, a non-ASCII URI the reference would fail to
//! encode) and any nesting past `NATIVE_RECURSION_LIMIT` goes to the Python reference passed in
//! as `fallback`, so its own behavior and recursion limit decide. Catalog lookups stay in Python
//! callbacks, so their errors are the reference's own.

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyIterator, PyList, PySet, PyString, PyTuple};
use sha2::{Digest, Sha256};

use crate::walk::{NATIVE_RECURSION_LIMIT, is_exact, is_mapping_instance, is_plain_scalar};

type Entries<'py> = Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)>;

#[inline]
fn is_mapping_proxy(value: &Bound<'_, PyAny>) -> bool {
    unsafe { ffi::Py_TYPE(value.as_ptr()) == std::ptr::addr_of_mut!(ffi::PyDictProxy_Type) }
}

/// The `(key, value)` pairs `source.items()` yields, for an exact `dict` or a mapping proxy
/// whose `items()` is a `dict` view; `None` for any other mapping.
fn dict_entries<'py>(value: &Bound<'py, PyAny>) -> PyResult<Option<Entries<'py>>> {
    if is_exact(value, ffi::PyDict_CheckExact) {
        let dict = unsafe { value.cast_unchecked::<PyDict>() };
        return Ok(Some(dict.iter().collect()));
    }
    if !is_mapping_proxy(value) {
        return Ok(None);
    }
    let items = value.call_method0(pyo3::intern!(value.py(), "items"))?;
    if unsafe { ffi::PyDictItems_Check(items.as_ptr()) } == 0 {
        return Ok(None);
    }
    let mut entries = Vec::new();
    for pair in items.try_iter()? {
        let pair = pair?;
        let pair = pair.cast_into::<PyTuple>()?;
        entries.push((pair.get_item(0)?, pair.get_item(1)?));
    }
    Ok(Some(entries))
}

/// The members of an exact `list` or `tuple`, read the way the reference's iteration reads them.
fn sequence_members<'py>(value: &Bound<'py, PyAny>) -> PyResult<Option<Vec<Bound<'py, PyAny>>>> {
    if is_exact(value, ffi::PyList_CheckExact) {
        let list = unsafe { value.cast_unchecked::<PyList>() };
        let mut members = Vec::with_capacity(list.len());
        let mut index = 0;
        while index < list.len() {
            members.push(list.get_item(index)?);
            index += 1;
        }
        return Ok(Some(members));
    }
    if is_exact(value, ffi::PyTuple_CheckExact) {
        return Ok(Some(unsafe { value.cast_unchecked::<PyTuple>() }.iter().collect()));
    }
    Ok(None)
}

/// `isinstance(value, Mapping) or isinstance(value, tuple | list)` for a value that is none of
/// the exact shapes: such a node belongs to the reference.
fn is_foreign_container(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<bool> {
    if is_plain_scalar(value) {
        return Ok(false);
    }
    let pointer = value.as_ptr();
    if unsafe { ffi::PyList_Check(pointer) != 0 || ffi::PyTuple_Check(pointer) != 0 } {
        return Ok(true);
    }
    is_mapping_instance(py, value)
}

/// Exact `str` content, or `None` for anything else (including lone surrogates).
fn exact_text<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a str> {
    if !is_exact(value, ffi::PyUnicode_CheckExact) {
        return None;
    }
    unsafe { value.cast_unchecked::<PyString>() }.to_str().ok()
}

fn thaw<'py>(py: Python<'py>, value: &Bound<'py, PyAny>, fallback: &Bound<'py, PyAny>, stringify: bool, depth: usize) -> PyResult<Bound<'py, PyAny>> {
    if is_plain_scalar(value) {
        return Ok(value.clone());
    }
    if depth >= NATIVE_RECURSION_LIMIT {
        return fallback.call1((value,));
    }
    if let Some(entries) = dict_entries(value)? {
        let thawed = PyDict::new(py);
        for (key, item) in entries {
            let key = if stringify && !is_exact(&key, ffi::PyUnicode_CheckExact) { key.str()?.into_any() } else { key };
            thawed.set_item(key, thaw(py, &item, fallback, stringify, depth + 1)?)?;
        }
        return Ok(thawed.into_any());
    }
    if let Some(members) = sequence_members(value)? {
        let mut thawed = Vec::with_capacity(members.len());
        for member in members {
            thawed.push(thaw(py, &member, fallback, stringify, depth + 1)?);
        }
        return Ok(PyList::new(py, thawed)?.into_any());
    }
    if is_foreign_container(py, value)? {
        return fallback.call1((value,));
    }
    Ok(value.clone())
}

/// `_mutable_json(value)` of `yoetz.mcp.descriptors` (`stringify_keys=False`) or of
/// `yoetz.mcp.server` (`stringify_keys=True`, which applies `str` to every key).
#[pyfunction]
#[pyo3(name = "mcp_thaw_json", signature = (value, fallback, stringify_keys = false))]
pub fn thaw_json<'py>(py: Python<'py>, value: &Bound<'py, PyAny>, fallback: &Bound<'py, PyAny>, stringify_keys: bool) -> PyResult<Bound<'py, PyAny>> {
    thaw(py, value, fallback, stringify_keys, 0)
}

/// `_bundle_key(uri)` for an ASCII `uri`.
fn bundle_key(uri: &str) -> String {
    let digest = Sha256::digest(uri.as_bytes());
    let mut key = String::with_capacity(24);
    key.push_str("__yoetz_");
    key.push_str(&hex::encode(&digest[..8]));
    key
}

struct Rewrite<'py> {
    namespace: String,
    root_uri: Bound<'py, PyString>,
    root_text: String,
    inline_uris: Bound<'py, PyAny>,
    inline_document: Bound<'py, PyAny>,
    fallback: Bound<'py, PyAny>,
}

impl<'py> Rewrite<'py> {
    fn defer(&self, value: &Bound<'py, PyAny>, current_uri: &Bound<'py, PyString>) -> PyResult<Bound<'py, PyAny>> {
        self.fallback.call1((value, current_uri, &self.root_uri, &self.inline_uris))
    }

    fn rewrite(&self, py: Python<'py>, value: &Bound<'py, PyAny>, current_uri: &Bound<'py, PyString>, depth: usize) -> PyResult<Bound<'py, PyAny>> {
        if is_plain_scalar(value) {
            return Ok(value.clone());
        }
        if depth >= NATIVE_RECURSION_LIMIT {
            return self.defer(value, current_uri);
        }
        if let Some(entries) = dict_entries(value)? {
            let mut reference: Option<&Bound<'py, PyAny>> = None;
            for (key, item) in &entries {
                if !is_exact(key, ffi::PyUnicode_CheckExact) {
                    return self.defer(value, current_uri);
                }
                if exact_text(key) == Some("$ref") {
                    reference = Some(item);
                }
            }
            // `None` keeps the member; `Some` replaces it with a rewritten reference.
            let mut rewritten: Option<Bound<'py, PyString>> = None;
            let mut keep_reference = false;
            if let Some(reference) = reference {
                let pointer = reference.as_ptr();
                if unsafe { ffi::PyUnicode_Check(pointer) } != 0 {
                    let Some(text) = exact_text(reference) else {
                        return self.defer(value, current_uri);
                    };
                    keep_reference = true;
                    if text.starts_with(self.namespace.as_str()) {
                        let (uri, fragment) = match text.split_once('#') {
                            Some((uri, fragment)) => (uri, Some(fragment)),
                            None => (text, None),
                        };
                        let uri_object = PyString::new(py, uri);
                        if fragment.is_none() && entries.len() == 1 && self.inline_uris.contains(&uri_object)? {
                            let document = self.inline_document.call1((&uri_object,))?;
                            return self.rewrite(py, &document, &uri_object, depth + 1);
                        }
                        if !uri.is_ascii() {
                            return self.defer(value, current_uri);
                        }
                        let mut target = format!("#/$defs/{}", bundle_key(uri));
                        if let Some(fragment) = fragment {
                            target.push_str(fragment);
                        }
                        rewritten = Some(PyString::new(py, &target));
                    } else if let Some(rest) = text.strip_prefix('#') {
                        let current = current_uri.to_str()?;
                        if current != self.root_text {
                            if !current.is_ascii() {
                                return self.defer(value, current_uri);
                            }
                            rewritten = Some(PyString::new(py, &format!("#/$defs/{}{}", bundle_key(current), rest)));
                        }
                    }
                }
            }
            let result = PyDict::new(py);
            for (key, item) in &entries {
                let text = exact_text(key);
                if matches!(text, Some("$id" | "$schema")) {
                    continue;
                }
                if keep_reference && text == Some("$ref") {
                    match &rewritten {
                        Some(target) => result.set_item(key, target)?,
                        None => result.set_item(key, item)?,
                    }
                    continue;
                }
                result.set_item(key, self.rewrite(py, item, current_uri, depth + 1)?)?;
            }
            return Ok(result.into_any());
        }
        if let Some(members) = sequence_members(value)? {
            let mut rewritten = Vec::with_capacity(members.len());
            for member in members {
                rewritten.push(self.rewrite(py, &member, current_uri, depth + 1)?);
            }
            return Ok(PyList::new(py, rewritten)?.into_any());
        }
        if is_foreign_container(py, value)? {
            return self.defer(value, current_uri);
        }
        Ok(value.clone())
    }
}

/// `_rewrite_schema_refs(value, current_uri=..., root_uri=..., inline_uris=...)`.
///
/// `inline_document(uri)` is the reference's `_strip_schema_metadata` of the catalog document
/// (raising as the reference raises); `fallback(value, current_uri, root_uri, inline_uris)` is the
/// Python reference.
#[pyfunction]
#[pyo3(name = "mcp_rewrite_schema_refs")]
#[allow(clippy::too_many_arguments)]
pub fn rewrite_schema_refs<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    current_uri: &Bound<'py, PyString>,
    root_uri: &Bound<'py, PyString>,
    inline_uris: &Bound<'py, PyAny>,
    namespace: &str,
    inline_document: &Bound<'py, PyAny>,
    fallback: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let (Ok(_), Ok(root_text)) = (current_uri.to_str(), root_uri.to_str()) else {
        return fallback.call1((value, current_uri, root_uri, inline_uris));
    };
    let rewrite = Rewrite {
        namespace: namespace.to_owned(),
        root_uri: root_uri.clone(),
        root_text: root_text.to_owned(),
        inline_uris: inline_uris.clone(),
        inline_document: inline_document.clone(),
        fallback: fallback.clone(),
    };
    rewrite.rewrite(py, value, current_uri, 0)
}

enum Frame<'py> {
    Node(Bound<'py, PyAny>),
    /// A mapping whose values are visited once its referenced document has been.
    ValuesOf(Bound<'py, PyAny>),
    Members(Vec<Bound<'py, PyAny>>, usize),
    Iter(Bound<'py, PyIterator>),
}

/// `_external_schema_documents(value, project_ordinary_event_draft=project)`.
///
/// `resolve(uri)` is the reference's `_resolved_external_document` for the same projection. The
/// walk keeps the reference's depth-first order on an explicit stack.
#[pyfunction]
#[pyo3(name = "mcp_external_schema_documents")]
pub fn external_schema_documents<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    namespace: &Bound<'py, PyString>,
    opaque_uri: &str,
    project: bool,
    resolve: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    let namespace_text = namespace.to_str()?.to_owned();
    let documents = PyDict::new(py);
    let get = pyo3::intern!(py, "get");
    let reference_key = pyo3::intern!(py, "$ref");
    let mut stack: Vec<Frame<'py>> = vec![Frame::Node(value.clone())];
    while let Some(frame) = stack.pop() {
        let candidate = match frame {
            Frame::Node(candidate) => candidate,
            Frame::ValuesOf(mapping) => {
                stack.push(Frame::Iter(mapping.call_method0(pyo3::intern!(py, "values"))?.try_iter()?));
                continue;
            }
            Frame::Members(members, index) => {
                if index >= members.len() {
                    continue;
                }
                let candidate = members[index].clone();
                stack.push(Frame::Members(members, index + 1));
                candidate
            }
            Frame::Iter(iterator) => match iterator.clone().next() {
                Some(candidate) => {
                    stack.push(Frame::Iter(iterator));
                    candidate?
                }
                None => continue,
            },
        };
        if is_plain_scalar(&candidate) {
            continue;
        }
        let exact_dict = is_exact(&candidate, ffi::PyDict_CheckExact);
        if exact_dict || is_mapping_proxy(&candidate) || is_mapping_instance(py, &candidate)? {
            let reference = if exact_dict {
                unsafe { candidate.cast_unchecked::<PyDict>() }.get_item(reference_key)?
            } else {
                Some(candidate.call_method1(get, (reference_key,))?)
            };
            let mut nested: Option<Bound<'py, PyAny>> = None;
            if let Some(reference) = reference {
                if unsafe { ffi::PyUnicode_Check(reference.as_ptr()) } != 0 {
                    let uri: Option<Bound<'py, PyAny>> = match exact_text(&reference) {
                        Some(text) => text
                            .starts_with(namespace_text.as_str())
                            .then(|| PyString::new(py, text.split_once('#').map_or(text, |(uri, _)| uri)).into_any()),
                        None => {
                            if reference.call_method1(pyo3::intern!(py, "startswith"), (namespace,))?.is_truthy()? {
                                Some(reference.call_method1(pyo3::intern!(py, "partition"), ("#",))?.get_item(0)?)
                            } else {
                                None
                            }
                        }
                    };
                    if let Some(uri) = uri {
                        if project && uri.eq(opaque_uri)? {
                            continue;
                        }
                        if !documents.contains(&uri)? {
                            let document = resolve.call1((&uri,))?;
                            documents.set_item(&uri, &document)?;
                            nested = Some(document);
                        }
                    }
                }
            }
            if exact_dict {
                let members: Vec<Bound<'py, PyAny>> = unsafe { candidate.cast_unchecked::<PyDict>() }.values().iter().collect();
                stack.push(Frame::Members(members, 0));
            } else {
                stack.push(Frame::ValuesOf(candidate));
            }
            if let Some(document) = nested {
                stack.push(Frame::Node(document));
            }
            continue;
        }
        if let Some(members) = sequence_members(&candidate)? {
            stack.push(Frame::Members(members, 0));
            continue;
        }
        let pointer = candidate.as_ptr();
        if unsafe { ffi::PyList_Check(pointer) != 0 || ffi::PyTuple_Check(pointer) != 0 } {
            stack.push(Frame::Iter(candidate.try_iter()?));
        }
    }
    Ok(documents)
}

fn legacy_arrays<'py>(py: Python<'py>, candidate: &Bound<'py, PyAny>, fallback: &Bound<'py, PyAny>, depth: usize) -> PyResult<Bound<'py, PyAny>> {
    if is_plain_scalar(candidate) {
        return Ok(candidate.clone());
    }
    if depth >= NATIVE_RECURSION_LIMIT {
        return fallback.call1((candidate,));
    }
    if let Some(entries) = dict_entries(candidate)? {
        let mapping = PyDict::new(py);
        for (key, item) in entries {
            mapping.set_item(key, legacy_arrays(py, &item, fallback, depth + 1)?)?;
        }
        let prefix_key = pyo3::intern!(py, "prefixItems");
        let Some(prefix_items) = mapping.get_item(prefix_key)? else {
            return Ok(mapping.into_any());
        };
        mapping.del_item(prefix_key)?;
        if unsafe { ffi::PyList_Check(prefix_items.as_ptr()) } == 0 || !prefix_items.is_truthy()? {
            return Ok(mapping.into_any());
        }
        let items_key = pyo3::intern!(py, "items");
        let raw_items = mapping.get_item(items_key)?;
        let max_items = mapping.get_item(pyo3::intern!(py, "maxItems"))?;
        let fixed_prefix = match &max_items {
            Some(max_items) if is_exact(max_items, ffi::PyLong_CheckExact) => max_items.eq(prefix_items.len()?)?,
            _ => false,
        };
        let alternatives = unsafe { Bound::from_owned_ptr_or_err(py, ffi::PySequence_List(prefix_items.as_ptr()))?.cast_into_unchecked::<PyList>() };
        let raw_is_mapping = match &raw_items {
            Some(raw_items) => is_mapping_instance(py, raw_items)?,
            None => false,
        };
        if !fixed_prefix && raw_is_mapping {
            if let Some(raw_items) = &raw_items {
                alternatives.append(raw_items)?;
            }
        }
        let raw_is_false = raw_items.as_ref().is_some_and(|raw| raw.as_ptr() == unsafe { ffi::Py_False() });
        if fixed_prefix || raw_is_false || raw_is_mapping {
            if alternatives.len() == 1 {
                mapping.set_item(items_key, alternatives.get_item(0)?)?;
            } else {
                let any_of = PyDict::new(py);
                any_of.set_item(pyo3::intern!(py, "anyOf"), &alternatives)?;
                mapping.set_item(items_key, any_of)?;
            }
        } else {
            mapping.set_item(items_key, PyDict::new(py))?;
        }
        return Ok(mapping.into_any());
    }
    if let Some(members) = sequence_members(candidate)? {
        let mut projected = Vec::with_capacity(members.len());
        for member in members {
            projected.push(legacy_arrays(py, &member, fallback, depth + 1)?);
        }
        return Ok(PyList::new(py, projected)?.into_any());
    }
    if is_foreign_container(py, candidate)? {
        return fallback.call1((candidate,));
    }
    Ok(candidate.clone())
}

/// `_legacy_compatible_output_arrays(candidate)`; `fallback` is the Python reference.
#[pyfunction]
#[pyo3(name = "mcp_legacy_compatible_output_arrays")]
pub fn legacy_compatible_output_arrays<'py>(py: Python<'py>, candidate: &Bound<'py, PyAny>, fallback: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    legacy_arrays(py, candidate, fallback, 0)
}

fn flatten<'py>(py: Python<'py>, candidate: &Bound<'py, PyAny>, fallback: &Bound<'py, PyAny>, thaw_fallback: &Bound<'py, PyAny>, depth: usize) -> PyResult<Bound<'py, PyAny>> {
    if is_plain_scalar(candidate) {
        return Ok(candidate.clone());
    }
    if depth >= NATIVE_RECURSION_LIMIT {
        return fallback.call1((candidate,));
    }
    if let Some(entries) = dict_entries(candidate)? {
        if entries.iter().any(|(key, _)| !is_exact(key, ffi::PyUnicode_CheckExact)) {
            return fallback.call1((candidate,));
        }
        let mapping = PyDict::new(py);
        for (key, item) in entries {
            let projected = if exact_text(&key) == Some("$defs") {
                thaw(py, &item, thaw_fallback, false, 0)?
            } else {
                flatten(py, &item, fallback, thaw_fallback, depth + 1)?
            };
            mapping.set_item(key, projected)?;
        }
        let properties = mapping.get_item(pyo3::intern!(py, "properties"))?;
        let required = mapping.get_item(pyo3::intern!(py, "required"))?;
        let (Some(properties), Some(required)) = (properties, required) else {
            return Ok(mapping.into_any());
        };
        if unsafe { ffi::PyDict_Check(properties.as_ptr()) } == 0 {
            return Ok(mapping.into_any());
        }
        let properties = unsafe { properties.cast_into_unchecked::<PyDict>() };
        let sequence_key = pyo3::intern!(py, "sequence");
        let head_key = pyo3::intern!(py, "head_digest");
        if !properties.contains(sequence_key)? || !properties.contains(head_key)? {
            return Ok(mapping.into_any());
        }
        if unsafe { ffi::PyList_Check(required.as_ptr()) } == 0 {
            return Ok(mapping.into_any());
        }
        let (mut has_sequence, mut has_head) = (false, false);
        for member in required.try_iter()? {
            let member = member?;
            if !(is_plain_scalar(&member)) {
                // `set.issubset` hashes every member; the reference decides an unhashable one.
                return fallback.call1((candidate,));
            }
            match exact_text(&member) {
                Some("sequence") => has_sequence = true,
                Some("head_digest") => has_head = true,
                _ => {}
            }
        }
        let all_of = pyo3::intern!(py, "allOf");
        if !(has_sequence && has_head) || !mapping.contains(all_of)? {
            return Ok(mapping.into_any());
        }
        let head_digest = properties.get_item(head_key)?;
        let Some(head_digest) = head_digest.filter(|head| unsafe { ffi::PyDict_Check(head.as_ptr()) } != 0) else {
            return fallback.call1((candidate,));
        };
        mapping.del_item(all_of)?;
        head_digest.set_item(pyo3::intern!(py, "pattern"), "^(genesis|sha256:[0-9a-f]{64})$")?;
        head_digest.set_item(pyo3::intern!(py, "description"), "Genesis is valid only with sequence 0.")?;
        return Ok(mapping.into_any());
    }
    if let Some(members) = sequence_members(candidate)? {
        let mut projected = Vec::with_capacity(members.len());
        for member in members {
            projected.push(flatten(py, &member, fallback, thaw_fallback, depth + 1)?);
        }
        return Ok(PyList::new(py, projected)?.into_any());
    }
    if is_foreign_container(py, candidate)? {
        return fallback.call1((candidate,));
    }
    Ok(candidate.clone())
}

/// `_flatten_frontier_conditions(candidate)`; `fallback` is its Python reference and
/// `thaw_fallback` that of `_mutable_json`.
#[pyfunction]
#[pyo3(name = "mcp_flatten_frontier_conditions")]
pub fn flatten_frontier_conditions<'py>(
    py: Python<'py>,
    candidate: &Bound<'py, PyAny>,
    fallback: &Bound<'py, PyAny>,
    thaw_fallback: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    flatten(py, candidate, fallback, thaw_fallback, 0)
}

/// A whole-call twin stopped: only the Python reference, run from the start, can decide (it
/// raises its own error or handles a shape the twin does not reproduce).
struct Undecided;

impl From<PyErr> for Undecided {
    fn from(_: PyErr) -> Self {
        Undecided
    }
}

type Step<T> = Result<T, Undecided>;

/// The text of an exact `str`; `Err` for one holding a lone surrogate, `Ok(None)` otherwise.
fn exact_str_text<'a>(value: &'a Bound<'_, PyAny>) -> Step<Option<&'a str>> {
    if !is_exact(value, ffi::PyUnicode_CheckExact) {
        return Ok(None);
    }
    match unsafe { value.cast_unchecked::<PyString>() }.to_str() {
        Ok(text) => Ok(Some(text)),
        Err(_) => Err(Undecided),
    }
}

/// `mapping.get(key)` for an exact `dict`, or through the mapping's own `get`.
fn mapping_get<'py>(mapping: &Bound<'py, PyAny>, key: &str) -> Step<Option<Bound<'py, PyAny>>> {
    if is_exact(mapping, ffi::PyDict_CheckExact) {
        return Ok(unsafe { mapping.cast_unchecked::<PyDict>() }.get_item(key)?);
    }
    let value = mapping.call_method1(pyo3::intern!(mapping.py(), "get"), (key,))?;
    Ok((!value.is_none()).then_some(value))
}

/// The inlining of local `$defs` references shared by `_inline_nested_local_defs` (a `prefix`)
/// and `_inline_presentation_refs` (the `retained` definitions, with cycle detection).
struct LocalDefs<'py> {
    definitions: Bound<'py, PyAny>,
    prefix: Option<String>,
    retained: std::collections::HashSet<String>,
    resolving: Vec<String>,
    thaw_fallback: Bound<'py, PyAny>,
}

impl<'py> LocalDefs<'py> {
    /// The definition key a `$ref` value names, when this pass inlines it.
    fn target_key(&self, reference: Option<&Bound<'py, PyAny>>) -> Step<Option<String>> {
        let Some(reference) = reference else {
            return Ok(None);
        };
        let Some(text) = exact_str_text(reference)? else {
            return Ok(None);
        };
        match &self.prefix {
            Some(prefix) => Ok(text.strip_prefix(prefix.as_str()).filter(|rest| !rest.contains('/')).map(str::to_owned)),
            None => {
                // `_local_def_key`.
                let Some(rest) = text.strip_prefix("#/$defs/") else {
                    return Ok(None);
                };
                if rest.is_empty() || rest.contains('/') || self.retained.contains(rest) {
                    return Ok(None);
                }
                Ok(Some(rest.to_owned()))
            }
        }
    }

    fn resolve(&mut self, py: Python<'py>, candidate: &Bound<'py, PyAny>, depth: usize) -> Step<Bound<'py, PyAny>> {
        if is_plain_scalar(candidate) {
            return Ok(candidate.clone());
        }
        if depth >= NATIVE_RECURSION_LIMIT {
            return Err(Undecided);
        }
        if let Some(entries) = dict_entries(candidate)? {
            let mut texts = Vec::with_capacity(entries.len());
            let mut reference = None;
            for (key, item) in &entries {
                if !is_exact(key, ffi::PyUnicode_CheckExact) {
                    return Err(Undecided);
                }
                let text = unsafe { key.cast_unchecked::<PyString>() }.to_str().ok();
                if text == Some("$ref") {
                    reference = Some(item);
                }
                texts.push(text);
            }
            if let Some(key) = self.target_key(reference)? {
                if self.prefix.is_none() && self.resolving.contains(&key) {
                    return Err(Undecided);
                }
                let Some(target) = mapping_get(&self.definitions, &key)? else {
                    return Err(Undecided);
                };
                self.resolving.push(key);
                let thawed = thaw(py, &target, &self.thaw_fallback, false, 0)?;
                let resolved_target = self.resolve(py, &thawed, depth + 1)?;
                self.resolving.pop();
                if !is_exact(&resolved_target, ffi::PyDict_CheckExact) {
                    return Err(Undecided);
                }
                let merged = unsafe { resolved_target.cast_unchecked::<PyDict>() }.copy()?;
                for ((key, item), text) in entries.iter().zip(&texts) {
                    if *text == Some("$ref") {
                        continue;
                    }
                    let resolved_item = self.resolve(py, item, depth + 1)?;
                    if let Some(existing) = merged.get_item(key)? {
                        if existing.ne(&resolved_item)? {
                            return Err(Undecided);
                        }
                    }
                    merged.set_item(key, resolved_item)?;
                }
                return Ok(merged.into_any());
            }
            let resolved = PyDict::new(py);
            for ((key, item), text) in entries.iter().zip(&texts) {
                if *text == Some("$defs") {
                    continue;
                }
                resolved.set_item(key, self.resolve(py, item, depth + 1)?)?;
            }
            return Ok(resolved.into_any());
        }
        if let Some(members) = sequence_members(candidate)? {
            let mut resolved = Vec::with_capacity(members.len());
            for member in members {
                resolved.push(self.resolve(py, &member, depth + 1)?);
            }
            return Ok(PyList::new(py, resolved)?.into_any());
        }
        if is_foreign_container(py, candidate)? {
            return Err(Undecided);
        }
        Ok(candidate.clone())
    }
}

/// The `resolve(source)` pass of `_inline_nested_local_defs` (with `prefix`) or of
/// `_inline_presentation_refs` (with `retained`), or `undecided` when only the reference can
/// tell. `thaw_fallback` is the Python `_mutable_json`.
#[pyfunction]
#[pyo3(name = "mcp_inline_local_defs", signature = (source, definitions, prefix, retained, thaw_fallback, undecided))]
pub fn inline_local_defs<'py>(
    py: Python<'py>,
    source: &Bound<'py, PyAny>,
    definitions: &Bound<'py, PyAny>,
    prefix: Option<&Bound<'py, PyAny>>,
    retained: Option<&Bound<'py, PyAny>>,
    thaw_fallback: &Bound<'py, PyAny>,
    undecided: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let prepared = (|| -> Step<LocalDefs<'py>> {
        let prefix = match prefix {
            Some(prefix) => Some(exact_str_text(prefix)?.ok_or(Undecided)?.to_owned()),
            None => None,
        };
        let mut retained_keys = std::collections::HashSet::new();
        if let Some(retained) = retained {
            for key in retained.try_iter()? {
                let key = key?;
                retained_keys.insert(exact_str_text(&key)?.ok_or(Undecided)?.to_owned());
            }
        }
        Ok(LocalDefs { definitions: definitions.clone(), prefix, retained: retained_keys, resolving: Vec::new(), thaw_fallback: thaw_fallback.clone() })
    })();
    let Ok(mut pass) = prepared else {
        return Ok(undecided.clone());
    };
    match pass.resolve(py, source, 0) {
        Ok(resolved) => Ok(resolved),
        Err(Undecided) => Ok(undecided.clone()),
    }
}

fn collect_defs<'py>(candidate: &Bound<'py, PyAny>, found: &Bound<'py, PySet>, depth: usize) -> Step<()> {
    let py = candidate.py();
    if is_plain_scalar(candidate) {
        return Ok(());
    }
    if depth >= NATIVE_RECURSION_LIMIT {
        return Err(Undecided);
    }
    if let Some(entries) = dict_entries(candidate)? {
        for (key, item) in &entries {
            if !is_exact(key, ffi::PyUnicode_CheckExact) {
                return Err(Undecided);
            }
            if unsafe { key.cast_unchecked::<PyString>() }.to_str().ok() == Some("$ref") {
                if let Some(text) = exact_str_text(item)? {
                    if let Some(rest) = text.strip_prefix("#/$defs/") {
                        found.add(rest.split('/').next().unwrap_or(rest))?;
                    }
                }
            }
        }
        for (_, item) in &entries {
            collect_defs(item, found, depth + 1)?;
        }
        return Ok(());
    }
    if let Some(members) = sequence_members(candidate)? {
        for member in members {
            collect_defs(&member, found, depth + 1)?;
        }
        return Ok(());
    }
    if is_foreign_container(py, candidate)? {
        return Err(Undecided);
    }
    Ok(())
}

/// `_referenced_top_level_defs(value)`, or `undecided` when only the reference can tell.
#[pyfunction]
#[pyo3(name = "mcp_referenced_top_level_defs")]
pub fn referenced_top_level_defs<'py>(py: Python<'py>, value: &Bound<'py, PyAny>, undecided: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let found = PySet::empty(py)?;
    match collect_defs(value, &found, 0) {
        Ok(()) => Ok(found.into_any()),
        Err(Undecided) => Ok(undecided.clone()),
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(thaw_json, module)?)?;
    module.add_function(wrap_pyfunction!(rewrite_schema_refs, module)?)?;
    module.add_function(wrap_pyfunction!(external_schema_documents, module)?)?;
    module.add_function(wrap_pyfunction!(legacy_compatible_output_arrays, module)?)?;
    module.add_function(wrap_pyfunction!(flatten_frontier_conditions, module)?)?;
    module.add_function(wrap_pyfunction!(inline_local_defs, module)?)?;
    module.add_function(wrap_pyfunction!(referenced_top_level_defs, module)?)?;
    Ok(())
}
