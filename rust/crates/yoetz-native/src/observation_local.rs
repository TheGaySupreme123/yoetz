//! `yoetz.adapters.integrations.observation_local` twins.
//!
//! The store's orchestration (locks, batches, atomic writes, clocks, and every `_MAX_*` limit)
//! stays in Python. These are the pure steps its hot paths repeat:
//!
//! - `IdentityMemo`: the `_EnvelopeCodec` identity memo (fragments, pressure facts, dedup keys)
//!   with the reference's exact LRU behavior. Entries hold their item, so an identity cannot be
//!   reused while cached; factories run without the memo lock, exactly like the reference.
//! - the dedup ring order, fair eviction choice, and per-key lane column;
//! - the canonical digests of the module's small flat key objects.
//!
//! Every function answers `None` (or `(False, None)`) for input it does not model, and the
//! Python wrapper then runs the reference, so a refusal is always the reference's own.

use std::collections::HashMap;
use std::sync::Mutex;

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PySet, PyString, PyTuple};
use yoetz_core::integrations::observation_local as core;
use yoetz_core::protocol::canonical::{self as canonical_core, MAX_SAFE_INTEGER};

const NIL: usize = usize::MAX;

#[derive(Clone, PartialEq, Eq, Hash)]
struct MemoKey {
    id: usize,
    tag: Option<Box<str>>,
}

struct Node {
    key: MemoKey,
    item: Py<PyAny>,
    value: Py<PyAny>,
    prev: usize,
    next: usize,
}

/// An insertion-ordered map with `OrderedDict` semantics (assignment keeps position).
struct Ordered {
    map: HashMap<MemoKey, usize>,
    nodes: Vec<Option<Node>>,
    free: Vec<usize>,
    head: usize,
    tail: usize,
}

impl Ordered {
    fn new() -> Self {
        Ordered {
            map: HashMap::new(),
            nodes: Vec::new(),
            free: Vec::new(),
            head: NIL,
            tail: NIL,
        }
    }

    fn len(&self) -> usize {
        self.map.len()
    }

    fn node(&self, index: usize) -> &Node {
        self.nodes[index].as_ref().expect("linked node")
    }

    fn node_mut(&mut self, index: usize) -> &mut Node {
        self.nodes[index].as_mut().expect("linked node")
    }

    fn unlink(&mut self, index: usize) {
        let (prev, next) = {
            let node = self.node(index);
            (node.prev, node.next)
        };
        if prev == NIL {
            self.head = next;
        } else {
            self.node_mut(prev).next = next;
        }
        if next == NIL {
            self.tail = prev;
        } else {
            self.node_mut(next).prev = prev;
        }
    }

    fn link_last(&mut self, index: usize) {
        let tail = self.tail;
        {
            let node = self.node_mut(index);
            node.prev = tail;
            node.next = NIL;
        }
        if tail == NIL {
            self.head = index;
        } else {
            self.node_mut(tail).next = index;
        }
        self.tail = index;
    }

    fn move_to_end(&mut self, index: usize) {
        if self.tail != index {
            self.unlink(index);
            self.link_last(index);
        }
    }

    /// `od[key] = (item, value)`: replace in place, or append. Returns the replaced pair.
    fn assign(
        &mut self,
        key: MemoKey,
        item: Py<PyAny>,
        value: Py<PyAny>,
    ) -> Option<(Py<PyAny>, Py<PyAny>)> {
        if let Some(&index) = self.map.get(&key) {
            let node = self.node_mut(index);
            let old_item = std::mem::replace(&mut node.item, item);
            let old_value = std::mem::replace(&mut node.value, value);
            return Some((old_item, old_value));
        }
        let node = Node {
            key: key.clone(),
            item,
            value,
            prev: NIL,
            next: NIL,
        };
        let index = match self.free.pop() {
            Some(index) => {
                self.nodes[index] = Some(node);
                index
            }
            None => {
                self.nodes.push(Some(node));
                self.nodes.len() - 1
            }
        };
        self.map.insert(key, index);
        self.link_last(index);
        None
    }

    /// `od.popitem(last=False)`.
    fn pop_first(&mut self) -> Option<(Py<PyAny>, Py<PyAny>)> {
        let index = self.head;
        if index == NIL {
            return None;
        }
        self.unlink(index);
        let node = self.nodes[index].take().expect("linked node");
        self.free.push(index);
        self.map.remove(&node.key);
        Some((node.item, node.value))
    }

    /// `od.clear()`, handing back the members so they are released outside the lock.
    fn clear(&mut self) -> Vec<Option<Node>> {
        self.map.clear();
        self.free.clear();
        self.head = NIL;
        self.tail = NIL;
        std::mem::take(&mut self.nodes)
    }
}

/// The `_EnvelopeCodec` identity memo.
///
/// `lru=True` is an `OrderedDict` LRU bounded at `capacity` (hits move to the end, inserts
/// evict from the front). `lru=False` is the pressure-facts dict: hits do not reorder, and an
/// insert into a full memo clears it first.
#[pyclass(frozen, module = "yoetz_native")]
pub struct IdentityMemo {
    capacity: usize,
    lru: bool,
    entries: Mutex<Ordered>,
}

impl IdentityMemo {
    fn lock(&self) -> std::sync::MutexGuard<'_, Ordered> {
        self.entries
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn lookup<'py>(
        &self,
        py: Python<'py>,
        key: &MemoKey,
        item: &Bound<'py, PyAny>,
    ) -> Option<Bound<'py, PyAny>> {
        let mut entries = self.lock();
        let index = *entries.map.get(key)?;
        let node = entries.node(index);
        if node.item.as_ptr() != item.as_ptr() {
            return None;
        }
        let value = node.value.bind(py).clone();
        if self.lru {
            entries.move_to_end(index);
        }
        Some(value)
    }

    fn store(&self, key: MemoKey, item: &Bound<'_, PyAny>, value: &Bound<'_, PyAny>) {
        // Released only after the lock is dropped: a release may run arbitrary Python.
        let mut released: Vec<(Py<PyAny>, Py<PyAny>)> = Vec::new();
        let mut cleared: Vec<Option<Node>> = Vec::new();
        {
            let mut entries = self.lock();
            if !self.lru && entries.len() >= self.capacity {
                cleared = entries.clear();
            }
            if let Some(old) = entries.assign(key, item.clone().unbind(), value.clone().unbind()) {
                released.push(old);
            }
            if self.lru {
                while entries.len() > self.capacity {
                    match entries.pop_first() {
                        Some(old) => released.push(old),
                        None => break,
                    }
                }
            }
        }
        drop(released);
        drop(cleared);
    }

    fn get_or_build<'py>(
        &self,
        py: Python<'py>,
        key: MemoKey,
        item: &Bound<'py, PyAny>,
        build: impl FnOnce() -> PyResult<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        if let Some(value) = self.lookup(py, &key, item) {
            return Ok(value);
        }
        let value = build()?;
        self.store(key, item, &value);
        Ok(value)
    }
}

fn identity_key(item: &Bound<'_, PyAny>) -> MemoKey {
    MemoKey {
        id: item.as_ptr() as usize,
        tag: None,
    }
}

#[pymethods]
impl IdentityMemo {
    #[new]
    #[pyo3(signature = (capacity, *, lru = true))]
    fn new(capacity: usize, lru: bool) -> Self {
        IdentityMemo {
            capacity,
            lru,
            entries: Mutex::new(Ordered::new()),
        }
    }

    /// The cached value for `item` (moving it to the end of an LRU memo), else `None`.
    fn get<'py>(&self, py: Python<'py>, item: &Bound<'py, PyAny>) -> Option<Bound<'py, PyAny>> {
        self.lookup(py, &identity_key(item), item)
    }

    /// Record `value` for `item`.
    fn put(&self, item: &Bound<'_, PyAny>, value: &Bound<'_, PyAny>) {
        self.store(identity_key(item), item, value);
    }

    /// The cached value for `item`, else `factory(item)` recorded.
    fn get_or_call<'py>(
        &self,
        py: Python<'py>,
        item: &Bound<'py, PyAny>,
        factory: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.get_or_build(py, identity_key(item), item, || factory.call1((item,)))
    }

    /// A callable `item -> value` over this memo that builds misses with `factory(item)`.
    fn bind(slf: Bound<'_, Self>, factory: Bound<'_, PyAny>) -> MemoCall {
        MemoCall {
            memo: slf.unbind(),
            factory: factory.unbind(),
        }
    }

    /// A callable `(tag, item) -> value` over this memo, keyed by `(tag, id(item))`, that builds
    /// misses with `factory(tag, item)`.
    fn bind_tagged(slf: Bound<'_, Self>, factory: Bound<'_, PyAny>) -> TaggedMemoCall {
        TaggedMemoCall {
            memo: slf.unbind(),
            factory: factory.unbind(),
        }
    }

    fn __len__(&self) -> usize {
        self.lock().len()
    }
}

/// `memo.bind(factory)`.
#[pyclass(frozen, module = "yoetz_native")]
pub struct MemoCall {
    memo: Py<IdentityMemo>,
    factory: Py<PyAny>,
}

#[pymethods]
impl MemoCall {
    fn __call__<'py>(
        &self,
        py: Python<'py>,
        item: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let factory = self.factory.bind(py);
        self.memo
            .get()
            .get_or_build(py, identity_key(item), item, || factory.call1((item,)))
    }

    /// `tuple(self(item) for item in items)`.
    fn many<'py>(
        &self,
        py: Python<'py>,
        items: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyTuple>> {
        let memo = self.memo.get();
        let factory = self.factory.bind(py);
        let mut values = Vec::new();
        for item in items.try_iter()? {
            let item = item?;
            values.push(
                memo.get_or_build(py, identity_key(&item), &item, || factory.call1((&item,)))?,
            );
        }
        PyTuple::new(py, values)
    }
}

/// `memo.bind_tagged(factory)`.
#[pyclass(frozen, module = "yoetz_native")]
pub struct TaggedMemoCall {
    memo: Py<IdentityMemo>,
    factory: Py<PyAny>,
}

#[pymethods]
impl TaggedMemoCall {
    fn __call__<'py>(
        &self,
        py: Python<'py>,
        tag: &Bound<'py, PyAny>,
        item: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let factory = self.factory.bind(py);
        // The reference keys by `(tag, id(item))`; a tag that is not plain text is not memoized.
        let Some(text) = exact_str(tag) else {
            return factory.call1((tag, item));
        };
        let key = MemoKey {
            id: item.as_ptr() as usize,
            tag: Some(text.into()),
        };
        self.memo
            .get()
            .get_or_build(py, key, item, || factory.call1((tag, item)))
    }
}

#[inline]
fn exact_str<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a str> {
    if unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    unsafe { value.cast_unchecked::<PyString>() }.to_str().ok()
}

#[inline]
fn is_exact_list(value: &Bound<'_, PyAny>) -> bool {
    unsafe { ffi::PyList_CheckExact(value.as_ptr()) != 0 }
}

/// `hash(text)` for an exact `str`, which CPython caches on the object.
fn str_hash(value: &Bound<'_, PyAny>) -> PyResult<u64> {
    Ok(value.hash()? as u64)
}

/// The ordered dedup ring as the reference's own key objects, or `None` to defer.
///
/// Membership runs through Python sets so every key's cached hash is reused; only keys missing
/// from the durable order are read as text, to sort them by their UTF-8 bytes.
fn ordered_dedup<'py>(
    py: Python<'py>,
    dedup_order: &Bound<'py, PyAny>,
    dedup: &Bound<'py, PyAny>,
) -> PyResult<Option<Vec<Bound<'py, PyAny>>>> {
    if !is_exact_list(dedup_order) || unsafe { ffi::PySet_CheckExact(dedup.as_ptr()) } == 0 {
        return Ok(None);
    }
    let list = unsafe { dedup_order.cast_unchecked::<PyList>() };
    let set = unsafe { dedup.cast_unchecked::<PySet>() };
    let seen = PySet::empty(py)?;
    let mut ordered = Vec::with_capacity(list.len());
    for key in list.iter() {
        // Only exact strings: their hash and equality run no Python code.
        if exact_str(&key).is_none() {
            return Ok(None);
        }
        if set.contains(&key)? && !seen.contains(&key)? {
            seen.add(&key)?;
            ordered.push(key);
        }
    }
    // `seen` is a subset of `dedup`, so equal sizes mean nothing is left over.
    if seen.len() != set.len() {
        let mut rest = Vec::new();
        for member in set.iter() {
            if seen.contains(&member)? {
                continue;
            }
            if exact_str(&member).is_none() {
                return Ok(None);
            }
            rest.push(member);
        }
        rest.sort_by(|left, right| {
            let left = exact_str(left).unwrap_or_default();
            let right = exact_str(right).unwrap_or_default();
            left.as_bytes().cmp(right.as_bytes())
        });
        ordered.extend(rest);
    }
    Ok(Some(ordered))
}

/// `LocalObservationStore._ordered_dedup_keys(state)` given `state.dedup_order` and
/// `state.dedup`; `None` defers to the reference.
#[pyfunction]
pub fn observation_local_ordered_dedup_keys<'py>(
    py: Python<'py>,
    dedup_order: &Bound<'py, PyAny>,
    dedup: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyList>>> {
    match ordered_dedup(py, dedup_order, dedup)? {
        Some(ordered) => Ok(Some(PyList::new(py, ordered)?)),
        None => Ok(None),
    }
}

/// `LocalObservationStore._select_dedup_eviction_key(state)` as `(True, key)`, or
/// `(False, None)` to defer to the reference.
#[pyfunction]
pub fn observation_local_dedup_eviction_key<'py>(
    py: Python<'py>,
    dedup_order: &Bound<'py, PyAny>,
    dedup: &Bound<'py, PyAny>,
    dedup_lanes: &Bound<'py, PyAny>,
) -> PyResult<(bool, Option<Bound<'py, PyAny>>)> {
    if unsafe { ffi::PyDict_CheckExact(dedup_lanes.as_ptr()) } == 0 {
        return Ok((false, None));
    }
    let Some(ordered) = ordered_dedup(py, dedup_order, dedup)? else {
        return Ok((false, None));
    };
    let lanes_dict = unsafe { dedup_lanes.cast_unchecked::<PyDict>() };
    let mut lane_objects = Vec::with_capacity(ordered.len());
    for key in &ordered {
        let lane = match lanes_dict.get_item(key)? {
            Some(lane) => lane,
            // `f"_unknown:{key}"`, which can coincide with a real lane of that spelling.
            None => PyString::new(
                py,
                &format!("_unknown:{}", exact_str(key).unwrap_or_default()),
            )
            .into_any(),
        };
        if exact_str(&lane).is_none() {
            return Ok((false, None));
        }
        lane_objects.push(lane);
    }
    let mut lanes = Vec::with_capacity(lane_objects.len());
    for lane in &lane_objects {
        lanes.push(core::HashedStr {
            hash: str_hash(lane)?,
            text: exact_str(lane).unwrap_or_default(),
        });
    }
    let position = core::dedup_eviction_position(&lanes);
    Ok((true, position.map(|index| ordered[index].clone())))
}

/// `tuple(dedup_lanes.get(key) for key in ordered)`, or `None` to defer.
#[pyfunction]
pub fn observation_local_dedup_sessions<'py>(
    py: Python<'py>,
    dedup_lanes: &Bound<'py, PyAny>,
    ordered: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyTuple>>> {
    if unsafe { ffi::PyDict_CheckExact(dedup_lanes.as_ptr()) } == 0 || !is_exact_list(ordered) {
        return Ok(None);
    }
    let lanes = unsafe { dedup_lanes.cast_unchecked::<PyDict>() };
    let keys = unsafe { ordered.cast_unchecked::<PyList>() };
    let mut values = Vec::with_capacity(keys.len());
    for key in keys.iter() {
        if exact_str(&key).is_none() {
            return Ok(None);
        }
        values.push(
            lanes
                .get_item(&key)?
                .unwrap_or_else(|| py.None().into_bound(py)),
        );
    }
    Ok(Some(PyTuple::new(py, values)?))
}

/// The canonical text of one already-validated scalar member, or `None` when the value is
/// outside what this fast path models (the reference then validates and refuses it).
fn scalar_text(value: &Bound<'_, PyAny>, out: &mut Vec<u8>) -> Option<()> {
    let pointer = value.as_ptr();
    if value.is_none() {
        out.extend_from_slice(b"null");
        return Some(());
    }
    if unsafe { ffi::PyBool_Check(pointer) } != 0 {
        let truth = pointer == unsafe { ffi::Py_True() };
        out.extend_from_slice(if truth { b"true" } else { b"false" });
        return Some(());
    }
    if unsafe { ffi::PyLong_CheckExact(pointer) } != 0 {
        let mut overflow: std::os::raw::c_int = 0;
        let number = unsafe { ffi::PyLong_AsLongLongAndOverflow(pointer, &mut overflow) };
        if overflow != 0 || !(-MAX_SAFE_INTEGER..=MAX_SAFE_INTEGER).contains(&number) {
            return None;
        }
        canonical_core::push_int(out, number);
        return Some(());
    }
    let text = exact_str(value)?;
    canonical_core::encode_str_into(out, text).ok()
}

/// One member's canonical text: a scalar, or a nested object of the bound `JsonObject` class.
fn member_text(
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
    json_object: &Bound<'_, PyAny>,
) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    if value.get_type().as_ptr() == json_object.as_ptr() {
        // A frozen object is already validated; only its nesting depth is checked again, and
        // the encoder refuses exactly where the reference's depth check does.
        let text = crate::canonical::canonical_text(py, value, 1).ok()?;
        out.extend_from_slice(text.to_str().ok()?.as_bytes());
        return Some(out);
    }
    scalar_text(value, &mut out)?;
    Some(out)
}

fn digest_members(members: &mut [(&str, Vec<u8>)]) -> Option<String> {
    for index in 1..members.len() {
        if members[..index]
            .iter()
            .any(|(key, _)| *key == members[index].0)
        {
            return None;
        }
    }
    core::flat_object_digest(members).ok()
}

/// `canonical_digest(JsonObject(dict(items)))` for a flat object, or `None` to defer.
///
/// Values may be `None`, `bool`, exact `int`, exact `str`, or an instance of `json_object`
/// (the caller's `JsonObject` class). Anything else, or any value the reference would refuse,
/// defers so the reference raises its own error in its own order.
#[pyfunction]
pub fn observation_local_flat_digest(
    py: Python<'_>,
    items: &Bound<'_, PyTuple>,
    json_object: &Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    let mut keys = Vec::with_capacity(items.len());
    let mut values = Vec::with_capacity(items.len());
    for pair in items.iter() {
        let Ok(pair) = pair.cast_into::<PyTuple>() else {
            return Ok(None);
        };
        if pair.len() != 2 {
            return Ok(None);
        }
        let key = pair.get_item(0)?;
        let value = pair.get_item(1)?;
        if exact_str(&key).is_none_or(|text| canonical_core::validate_str(text).is_err()) {
            return Ok(None);
        }
        let Some(text) = member_text(py, &value, json_object) else {
            return Ok(None);
        };
        keys.push(key);
        values.push(text);
    }
    let mut members: Vec<(&str, Vec<u8>)> = keys
        .iter()
        .zip(values)
        .map(|(key, value)| (exact_str(key).unwrap_or_default(), value))
        .collect();
    Ok(digest_members(&mut members))
}

/// The reference's `_dedup_key` digest of one envelope, with `observation_cursor_to_json`
/// inlined (the caller only uses this while that global is the original), or `None` to defer.
#[pyfunction]
pub fn observation_local_dedup_digest(
    py: Python<'_>,
    workspace: &Bound<'_, PyAny>,
    envelope: &Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    let session = envelope.getattr(pyo3::intern!(py, "session_commitment"))?;
    let source = envelope
        .getattr(pyo3::intern!(py, "source"))?
        .getattr(pyo3::intern!(py, "value"))?;
    let source_identity = envelope.getattr(pyo3::intern!(py, "source_identity"))?;
    let event_kind = envelope.getattr(pyo3::intern!(py, "event_kind"))?;
    let cursor = envelope.getattr(pyo3::intern!(py, "cursor"))?;
    let cursor_fields = [
        (
            "source_generation",
            cursor.getattr(pyo3::intern!(py, "source_generation"))?,
        ),
        (
            "byte_position",
            cursor.getattr(pyo3::intern!(py, "byte_position"))?,
        ),
        (
            "event_position",
            cursor.getattr(pyo3::intern!(py, "event_position"))?,
        ),
        (
            "last_source_commitment",
            cursor.getattr(pyo3::intern!(py, "last_source_commitment"))?,
        ),
        (
            "mapping_version",
            cursor.getattr(pyo3::intern!(py, "mapping_version"))?,
        ),
    ];
    let mut nested: Vec<(&str, Vec<u8>)> = Vec::with_capacity(5);
    for (key, value) in &cursor_fields {
        let mut text = Vec::new();
        if scalar_text(value, &mut text).is_none() {
            return Ok(None);
        }
        nested.push((key, text));
    }
    nested.sort_by(|left, right| canonical_core::utf16_cmp(left.0, right.0));
    let mut cursor_text = Vec::with_capacity(256);
    cursor_text.push(b'{');
    for (position, (key, value)) in nested.iter().enumerate() {
        if position > 0 {
            cursor_text.push(b',');
        }
        if canonical_core::encode_str_into(&mut cursor_text, key).is_err() {
            return Ok(None);
        }
        cursor_text.push(b':');
        cursor_text.extend_from_slice(value);
    }
    cursor_text.push(b'}');
    let scalars = [
        ("workspace_commitment", workspace.clone()),
        ("session_commitment", session),
        ("source", source),
        ("source_identity", source_identity),
        ("event_kind", event_kind),
    ];
    let mut members: Vec<(&str, Vec<u8>)> = Vec::with_capacity(6);
    for (key, value) in &scalars {
        let mut text = Vec::new();
        if scalar_text(value, &mut text).is_none() {
            return Ok(None);
        }
        members.push((key, text));
    }
    members.push(("cursor", cursor_text));
    Ok(digest_members(&mut members))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<IdentityMemo>()?;
    module.add_class::<MemoCall>()?;
    module.add_class::<TaggedMemoCall>()?;
    module.add_function(wrap_pyfunction!(
        observation_local_ordered_dedup_keys,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        observation_local_dedup_eviction_key,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(observation_local_dedup_sessions, module)?)?;
    module.add_function(wrap_pyfunction!(observation_local_flat_digest, module)?)?;
    module.add_function(wrap_pyfunction!(observation_local_dedup_digest, module)?)?;
    Ok(())
}
