//! `yoetz.application.semantic_case` hot paths over Python values.
//!
//! Each function answers `None` (or status `0`) whenever its input is not plain JSON data or the
//! reference would take a path the twin does not reproduce; the Python wrapper then runs the
//! reference, so every refusal keeps its exact class, reason code and evaluation order.
//!
//! The envelope is read straight from the reference's own freshly built `dict`: exact `dict`,
//! `list`, `tuple`, `str`, `int`, `bool` and `None` only, with strings borrowed rather than copied.
//! Every Python object a call needs (content, approvals, results) is read or created outside the
//! span in which the borrowed tree is alive, so no Python code can run while it borrows.

use std::borrow::Cow;
use std::collections::{HashMap, HashSet};

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBytes, PyDict, PyFrozenSet, PyList, PyString, PyTuple};
use yoetz_core::application::semantic_case::{
    self as core, Bounded, Clipped, Node, Partition, Step,
};
use yoetz_core::protocol::canonical::MAX_JSON_DEPTH;

const DEFER: u8 = 0;
const DONE: u8 = 1;
const REFUSED: u8 = 2;

type Outcome<'py> = (u8, Option<Bound<'py, PyAny>>);

#[inline]
fn exact(value: &Bound<'_, PyAny>, check: unsafe fn(*mut ffi::PyObject) -> i32) -> bool {
    unsafe { check(value.as_ptr()) != 0 }
}

/// An exact `str` as UTF-8, or `None` (a subclass or a lone surrogate).
fn exact_str<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a str> {
    if !exact(value, ffi::PyUnicode_CheckExact) {
        return None;
    }
    unsafe { value.cast_unchecked::<PyString>() }.to_str().ok()
}

fn small_int(value: &Bound<'_, PyAny>) -> Option<i64> {
    if !exact(value, ffi::PyLong_CheckExact) {
        return None;
    }
    let mut overflow: std::os::raw::c_int = 0;
    let number = unsafe { ffi::PyLong_AsLongLongAndOverflow(value.as_ptr(), &mut overflow) };
    if overflow != 0 {
        return None;
    }
    Some(number)
}

/// Whether trees may borrow string data: only where the GIL serializes every thread.
static BORROW_STRINGS: PyOnceLock<bool> = PyOnceLock::new();

fn borrow_strings(py: Python<'_>) -> bool {
    *BORROW_STRINGS.get_or_init(py, || {
        // A free-threaded build lets another thread replace a string a tree still points at.
        py.import("sysconfig")
            .and_then(|sysconfig| sysconfig.call_method1("get_config_var", ("Py_GIL_DISABLED",)))
            .and_then(|flag| flag.is_truthy())
            .map(|free_threaded| !free_threaded)
            .unwrap_or(false)
    })
}

/// The UTF-8 text of an exact `str`: borrowed for the lifetime of the tree that reaches it when
/// `borrow` is set, copied otherwise.
fn tree_str<'a>(value: &Bound<'_, PyAny>, borrow: bool) -> Option<Cow<'a, str>> {
    if !exact(value, ffi::PyUnicode_CheckExact) {
        return None;
    }
    if !borrow {
        let text = unsafe { value.cast_unchecked::<PyString>() }
            .to_str()
            .ok()?;
        return Some(Cow::Owned(text.to_owned()));
    }
    let mut size: ffi::Py_ssize_t = 0;
    let data = unsafe { ffi::PyUnicode_AsUTF8AndSize(value.as_ptr(), &mut size) };
    if data.is_null() {
        // A lone surrogate: discard the encode error; the reference handles this input.
        let _ = PyErr::take(value.py());
        return None;
    }
    // SAFETY: CPython keeps a string's UTF-8 buffer for as long as the string object lives. The
    // string is owned by a container of the root the tree was built from, the caller holds that
    // root for the tree's whole lifetime, and with the GIL held no Python code runs while the tree
    // is alive (every Python object a call needs is created before the tree or after it is
    // dropped), so nothing can release or replace the string meanwhile.
    let bytes = unsafe { std::slice::from_raw_parts(data.cast::<u8>(), size as usize) };
    Some(Cow::Borrowed(unsafe {
        std::str::from_utf8_unchecked(bytes)
    }))
}

/// Plain JSON data as a tree, or `None` for anything else (the reference keeps its own handling).
fn to_node<'a>(value: &Bound<'_, PyAny>, depth: usize, borrow: bool) -> Option<Node<'a>> {
    let pointer = value.as_ptr();
    if value.is_none() {
        return Some(Node::Null);
    }
    if unsafe { ffi::PyBool_Check(pointer) } != 0 {
        return Some(Node::Bool(pointer == unsafe { ffi::Py_True() }));
    }
    if exact(value, ffi::PyLong_CheckExact) {
        return small_int(value).map(Node::Int);
    }
    if exact(value, ffi::PyUnicode_CheckExact) {
        return tree_str(value, borrow).map(Node::Str);
    }
    let is_list = exact(value, ffi::PyList_CheckExact);
    if is_list || exact(value, ffi::PyTuple_CheckExact) {
        if depth >= MAX_JSON_DEPTH {
            return None;
        }
        let mut items = Vec::new();
        if is_list {
            let list = unsafe { value.cast_unchecked::<PyList>() };
            items.reserve(list.len());
            for item in list.iter() {
                items.push(to_node(&item, depth + 1, borrow)?);
            }
            return Some(Node::Array(items));
        }
        for item in unsafe { value.cast_unchecked::<PyTuple>() }.iter() {
            items.push(to_node(&item, depth + 1, borrow)?);
        }
        return Some(Node::Tuple(items));
    }
    if exact(value, ffi::PyDict_CheckExact) {
        if depth >= MAX_JSON_DEPTH {
            return None;
        }
        let dict = unsafe { value.cast_unchecked::<PyDict>() };
        let mut members = Vec::with_capacity(dict.len());
        for (key, item) in dict.iter() {
            members.push((tree_str(&key, borrow)?, to_node(&item, depth + 1, borrow)?));
        }
        return Some(Node::Object(members));
    }
    None
}

fn bytes_arg<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a [u8]> {
    if !exact(value, ffi::PyBytes_CheckExact) {
        return None;
    }
    Some(unsafe { value.cast_unchecked::<PyBytes>() }.as_bytes())
}

fn bound_arg(value: &Bound<'_, PyAny>) -> Option<usize> {
    // A negative bound admits nothing, exactly like comparing a length with it.
    small_int(value).map(|bound| usize::try_from(bound).unwrap_or(0))
}

/// `content_by_id` and `included_item_ids` collected up front (exact `str` and `bytes` only).
struct Approvals<'py> {
    content: Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)>,
    included: Vec<Bound<'py, PyAny>>,
}

impl<'py> Approvals<'py> {
    fn collect(content: &Bound<'py, PyAny>, included: &Bound<'py, PyAny>) -> Option<Self> {
        if !exact(content, ffi::PyDict_CheckExact) {
            return None;
        }
        let dict = unsafe { content.cast_unchecked::<PyDict>() };
        let mut members = Vec::with_capacity(dict.len());
        for (key, item) in dict.iter() {
            if !exact(&key, ffi::PyUnicode_CheckExact) || !exact(&item, ffi::PyBytes_CheckExact) {
                return None;
            }
            members.push((key, item));
        }
        // Only containers that can be read twice: the reference must still see every member when
        // the twin defers.
        let pointer = included.as_ptr();
        let rereadable = unsafe {
            ffi::PyFrozenSet_CheckExact(pointer) != 0
                || ffi::PyAnySet_CheckExact(pointer) != 0
                || ffi::PyList_CheckExact(pointer) != 0
                || ffi::PyTuple_CheckExact(pointer) != 0
        };
        if !rereadable {
            return None;
        }
        let mut approved = Vec::new();
        for member in included.try_iter().ok()? {
            let member = member.ok()?;
            if !exact(&member, ffi::PyUnicode_CheckExact) {
                return None;
            }
            approved.push(member);
        }
        Some(Approvals {
            content: members,
            included: approved,
        })
    }

    fn assemble(&self, envelope: &Node<'_>) -> Option<Vec<u8>> {
        let mut content: HashMap<&str, &[u8]> = HashMap::with_capacity(self.content.len());
        for (key, item) in &self.content {
            content.insert(exact_str(key)?, bytes_arg(item)?);
        }
        let mut included: HashSet<&str> = HashSet::with_capacity(self.included.len());
        for member in &self.included {
            included.insert(exact_str(member)?);
        }
        match core::assemble_filtered_review_packet(
            envelope,
            |item_id| content.get(item_id).copied(),
            &included,
        ) {
            Step::Done(bytes) => Some(bytes),
            Step::Defer => None,
        }
    }
}

/// The constants the twins hard-code, for the import-time drift check.
#[pyfunction]
pub fn semantic_case_tables(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let tables = PyDict::new(py);
    tables.set_item("_PACKET_SCHEMA", core::PACKET_SCHEMA)?;
    tables.set_item("REVIEW_PACKET_ITEM_ID", core::REVIEW_PACKET_ITEM_ID)?;
    tables.set_item(
        "SEMANTIC_PRIOR_FINDINGS_OVER_LIMIT_GAP",
        core::PRIOR_FINDINGS_OVER_LIMIT_GAP,
    )?;
    tables.set_item(
        "_PACKET_ID_LIST_KEYS",
        PyTuple::new(py, core::PACKET_ID_LIST_KEYS)?,
    )?;
    let labels = PyDict::new(py);
    for (section, label) in core::SECTION_LABELS {
        labels.set_item(section, label)?;
    }
    tables.set_item("_SECTION_LABELS", labels)?;
    let [open, middle, close] = core::ELISION_MARKER_PARTS;
    tables.set_item(
        "_ELISION_MARKER",
        format!("{open}{{elided}}{middle}{{total}}{close}"),
    )?;
    tables.set_item("_MIN_HEAD_TAIL_SIDE_BYTES", core::MIN_HEAD_TAIL_SIDE_BYTES)?;
    tables.set_item(
        "_MIN_CLIPPABLE_PROSE_BYTES",
        core::MIN_CLIPPABLE_PROSE_BYTES,
    )?;
    Ok(tables)
}

/// The ladder of `bounded_case_envelope` over the freshly built envelope `dict`.
///
/// Returns `(1, bytes)`, `(2, None)` for `SemanticCaseTooLarge`, or `(0, None)` to defer.
#[pyfunction]
pub fn bound_case_envelope<'py>(
    py: Python<'py>,
    envelope: &Bound<'py, PyAny>,
    maximum: &Bound<'py, PyAny>,
) -> Outcome<'py> {
    let Some(maximum) = bound_arg(maximum) else {
        return (DEFER, None);
    };
    let outcome = {
        let Some(mut tree) = exact_dict_node(envelope) else {
            return (DEFER, None);
        };
        core::bound_envelope(&mut tree, maximum, true)
    };
    match outcome {
        Step::Done(Bounded::Fits(bytes)) => (DONE, Some(PyBytes::new(py, &bytes).into_any())),
        Step::Done(Bounded::TooLarge) => (REFUSED, None),
        Step::Defer => (DEFER, None),
    }
}

/// The tree of an exact `dict` root. Its strings may borrow from `envelope`, which the caller
/// holds for as long as the tree lives.
fn exact_dict_node<'a>(envelope: &'a Bound<'_, PyAny>) -> Option<Node<'a>> {
    if !exact(envelope, ffi::PyDict_CheckExact) {
        return None;
    }
    let borrow = borrow_strings(envelope.py());
    to_node(envelope, 0, borrow)
}

/// `semantic_case_to_prepared_payload` from the freshly built envelope `dict`: bound it, then
/// assemble from the bounded tree as the reference assembles from its parsed bytes.
#[pyfunction]
pub fn prepared_review_payload<'py>(
    py: Python<'py>,
    envelope: &Bound<'py, PyAny>,
    maximum: &Bound<'py, PyAny>,
    content_by_id: &Bound<'py, PyAny>,
    included_item_ids: &Bound<'py, PyAny>,
) -> Outcome<'py> {
    let (Some(maximum), Some(approvals)) = (
        bound_arg(maximum),
        Approvals::collect(content_by_id, included_item_ids),
    ) else {
        return (DEFER, None);
    };
    let packet = {
        let Some(mut tree) = exact_dict_node(envelope) else {
            return (DEFER, None);
        };
        match core::bound_envelope(&mut tree, maximum, false) {
            Step::Done(Bounded::Fits(_)) => {
                // The reference assembles from `strict_json_parse` of the bounded bytes.
                core::normalize_tuples(&mut tree);
                approvals.assemble(&tree)
            }
            Step::Done(Bounded::TooLarge) => return (REFUSED, None),
            Step::Defer => None,
        }
    };
    match packet {
        Some(bytes) => (DONE, Some(PyBytes::new(py, &bytes).into_any())),
        None => (DEFER, None),
    }
}

/// `assemble_filtered_review_packet` for a plain-JSON `dict` envelope, or `None` to defer.
#[pyfunction]
pub fn assemble_review_packet<'py>(
    py: Python<'py>,
    envelope: &Bound<'py, PyAny>,
    content_by_id: &Bound<'py, PyAny>,
    included_item_ids: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyBytes>> {
    let approvals = Approvals::collect(content_by_id, included_item_ids)?;
    let packet = {
        let tree = exact_dict_node(envelope)?;
        approvals.assemble(&tree)?
    };
    Some(PyBytes::new(py, &packet))
}

/// `_catalog_item_ids(strict_json_parse(encoded))`, or `None` to defer.
#[pyfunction]
pub fn catalog_item_ids<'py>(
    py: Python<'py>,
    encoded: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyFrozenSet>> {
    let (envelope, _length) = core::parse_node(bytes_arg(encoded)?).ok()?;
    if !matches!(envelope, Node::Object(_)) {
        return None;
    }
    let ids: HashSet<&str> = core::catalog_item_ids(&envelope).into_iter().collect();
    PyFrozenSet::new(py, ids).ok()
}

/// `_clip_json_prose(value, limit)` re-encoded: `(1, bytes)`, `(2, None)` for the reference's
/// `None`, or `(0, None)` to defer.
#[pyfunction]
pub fn clip_json_prose<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    limit: &Bound<'py, PyAny>,
) -> Outcome<'py> {
    let Some(limit) = small_int(limit).and_then(|limit| usize::try_from(limit).ok()) else {
        return (DEFER, None);
    };
    let outcome = {
        let Some(tree) = exact_dict_node(value) else {
            return (DEFER, None);
        };
        if core::checked_len(&tree, 0).is_none() {
            return (DEFER, None);
        }
        core::clip_json_prose(tree, limit)
    };
    match outcome {
        Step::Done(Clipped::Fits(bytes)) => (DONE, Some(PyBytes::new(py, &bytes).into_any())),
        Step::Done(Clipped::CannotFit) => (REFUSED, None),
        Step::Defer => (DEFER, None),
    }
}

/// `_head_tail(raw, limit)`, or `None` to defer (input that is not valid UTF-8 included).
#[pyfunction]
pub fn head_tail<'py>(
    py: Python<'py>,
    raw: &Bound<'py, PyAny>,
    limit: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyString>> {
    let text = std::str::from_utf8(bytes_arg(raw)?).ok()?;
    match core::head_tail(text, small_int(limit)?) {
        Step::Done(kept) => Some(PyString::new(py, &kept)),
        Step::Defer => None,
    }
}

/// `_encoded_prefix(text, budget)`, or `None` to defer.
#[pyfunction]
pub fn encoded_prefix<'py>(
    py: Python<'py>,
    text: &Bound<'py, PyAny>,
    budget: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyAny>> {
    let slice = exact_str(text)?;
    let length = core::encoded_prefix_len(slice, small_int(budget)?);
    if length == slice.len() {
        return Some(text.clone());
    }
    Some(PyString::new(py, &slice[..length]).into_any())
}

/// Each row's canonical text as it sits inside a lineage part (depth 2), or `None` to defer.
fn lineage_rows<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
) -> Option<Vec<Bound<'py, PyString>>> {
    if !(exact(rows, ffi::PyList_CheckExact) || exact(rows, ffi::PyTuple_CheckExact)) {
        return None;
    }
    let mut encoded = Vec::new();
    for row in rows.try_iter().ok()? {
        encoded.push(crate::canonical::canonical_text(py, &row.ok()?, 2).ok()?);
    }
    Some(encoded)
}

/// `_lineage_partition(...)`: `(1, tuple[bytes, ...])`, `(2, None)` for
/// `LineageSemanticCapacityExceeded`, or `(0, None)` to defer.
#[pyfunction]
pub fn lineage_partition<'py>(
    py: Python<'py>,
    children: &Bound<'py, PyAny>,
    gaps: &Bound<'py, PyAny>,
    manifest_digest: &Bound<'py, PyAny>,
    item_limit: &Bound<'py, PyAny>,
    max_parts: &Bound<'py, PyAny>,
    schema: &Bound<'py, PyAny>,
) -> PyResult<Outcome<'py>> {
    let (Some(item_limit), Some(max_parts), Some(schema)) = (
        small_int(item_limit),
        small_int(max_parts),
        exact_str(schema),
    ) else {
        return Ok((DEFER, None));
    };
    let digest = if manifest_digest.is_none() {
        None
    } else {
        match exact_str(manifest_digest) {
            Some(digest) => Some(digest),
            None => return Ok((DEFER, None)),
        }
    };
    let (Some(child_rows), Some(gap_rows)) = (lineage_rows(py, children), lineage_rows(py, gaps))
    else {
        return Ok((DEFER, None));
    };
    let mut child_bytes: Vec<&[u8]> = Vec::with_capacity(child_rows.len());
    for row in &child_rows {
        child_bytes.push(row.to_str()?.as_bytes());
    }
    let mut gap_bytes: Vec<&[u8]> = Vec::with_capacity(gap_rows.len());
    for row in &gap_rows {
        gap_bytes.push(row.to_str()?.as_bytes());
    }
    Ok(
        match core::lineage_partition(
            &child_bytes,
            &gap_bytes,
            digest,
            item_limit,
            max_parts,
            schema,
        ) {
            Step::Done(Partition::Parts(parts)) => {
                let parts: Vec<Bound<'py, PyBytes>> =
                    parts.iter().map(|part| PyBytes::new(py, part)).collect();
                (DONE, Some(PyTuple::new(py, parts)?.into_any()))
            }
            Step::Done(Partition::TooLarge) => (REFUSED, None),
            Step::Defer => (DEFER, None),
        },
    )
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(semantic_case_tables, module)?)?;
    module.add_function(wrap_pyfunction!(bound_case_envelope, module)?)?;
    module.add_function(wrap_pyfunction!(prepared_review_payload, module)?)?;
    module.add_function(wrap_pyfunction!(assemble_review_packet, module)?)?;
    module.add_function(wrap_pyfunction!(catalog_item_ids, module)?)?;
    module.add_function(wrap_pyfunction!(clip_json_prose, module)?)?;
    module.add_function(wrap_pyfunction!(head_tail, module)?)?;
    module.add_function(wrap_pyfunction!(encoded_prefix, module)?)?;
    module.add_function(wrap_pyfunction!(lineage_partition, module)?)?;
    Ok(())
}
