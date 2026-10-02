//! `yoetz.kernel.reducers` hot loops over live Python objects.
//!
//! Each twin either reproduces its Python reference's result for the inputs it is given or
//! returns `None` before it has done anything observable, and the caller then runs the
//! reference. Records are never built here except through the Python callables the reference
//! itself uses (`dataclasses.replace`, the contradiction classes, the id validators).

use std::collections::{BTreeMap, HashMap, HashSet};
use std::hash::{BuildHasherDefault, Hash, Hasher};

use pyo3::ffi;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyFrozenSet, PyList, PyString, PyTuple};

use crate::kernel_projections::{dict_items, trusted_dict};

// ---------------------------------------------------------------------------------------------
// Exact-`str` keys hashed by CPython's own (cached, randomized) string hash.

#[derive(Default)]
struct PassHasher(u64);

impl Hasher for PassHasher {
    fn finish(&self) -> u64 {
        self.0
    }

    fn write(&mut self, bytes: &[u8]) {
        for byte in bytes {
            self.0 = (self.0.rotate_left(8) ^ u64::from(*byte)).wrapping_mul(0x9E37_79B9_7F4A_7C15);
        }
    }

    fn write_isize(&mut self, value: isize) {
        self.0 = (self.0.rotate_left(29) ^ (value as u64)).wrapping_mul(0x9E37_79B9_7F4A_7C15);
    }
}

type PassBuild = BuildHasherDefault<PassHasher>;

/// An exact `str` borrowed from a container that outlives the check.
#[derive(Clone, Copy)]
struct StrKey {
    hash: isize,
    pointer: *mut ffi::PyObject,
}

impl StrKey {
    /// `None` unless *value* is an exact `str`.
    fn of(value: &Bound<'_, PyAny>) -> Option<StrKey> {
        let pointer = value.as_ptr();
        if unsafe { ffi::PyUnicode_CheckExact(pointer) } == 0 {
            return None;
        }
        // An exact `str` always hashes; the hash is cached on the object.
        let hash = unsafe { ffi::PyObject_Hash(pointer) };
        Some(StrKey { hash: hash as isize, pointer })
    }
}

impl Hash for StrKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        state.write_isize(self.hash);
    }
}

impl PartialEq for StrKey {
    fn eq(&self, other: &Self) -> bool {
        self.pointer == other.pointer
            || (self.hash == other.hash && unsafe { ffi::PyUnicode_Compare(self.pointer, other.pointer) } == 0)
    }
}

impl Eq for StrKey {}

fn is_exact_str(value: &Bound<'_, PyAny>) -> bool {
    unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) != 0 }
}

/// The UTF-8 bytes of an exact `str`; `None` for anything else or a lone surrogate.
fn str_bytes<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a [u8]> {
    if !is_exact_str(value) {
        return None;
    }
    let mut size: ffi::Py_ssize_t = 0;
    let data = unsafe { ffi::PyUnicode_AsUTF8AndSize(value.as_ptr(), &mut size) };
    if data.is_null() {
        unsafe { ffi::PyErr_Clear() };
        return None;
    }
    Some(unsafe { std::slice::from_raw_parts(data.cast::<u8>(), size as usize) })
}

/// The `_ascii_key` bytes of an exact `str`; `None` when the reference would refuse it.
fn ascii_bytes<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a [u8]> {
    str_bytes(value).filter(|bytes| bytes.is_ascii())
}

/// An exact `int` that fits `i64`.
fn exact_i64(value: &Bound<'_, PyAny>) -> Option<i64> {
    if unsafe { ffi::PyLong_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    value.extract::<i64>().ok()
}

fn exact_dict<'a, 'py>(value: &'a Bound<'py, PyAny>) -> Option<&'a Bound<'py, PyDict>> {
    value.cast_exact::<PyDict>().ok()
}

// ---------------------------------------------------------------------------------------------
// ReplayIndex

/// Twin of `reducers._carry_id_set`: an exact `str` the validated prior set already holds is
/// carried (validation is a pure function of its text and returns the same object); every other
/// member goes through `validate` in iteration order.
#[pyfunction]
fn reducers_carry_id_set<'py>(
    source: &Bound<'py, PyAny>,
    trusted: &Bound<'py, PyAny>,
    validate: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyFrozenSet>>> {
    let py = source.py();
    if source.cast_exact::<PyFrozenSet>().is_err() || trusted.cast_exact::<PyFrozenSet>().is_err() {
        return Ok(None);
    }
    let members = PyList::empty(py);
    for item in source.try_iter()? {
        let item = item?;
        let known = is_exact_str(&item) && unsafe { ffi::PySet_Contains(trusted.as_ptr(), item.as_ptr()) } == 1;
        if known {
            members.append(item)?;
        } else {
            members.append(validate.call1((item,))?)?;
        }
    }
    Ok(Some(PyFrozenSet::new(py, members.iter())?))
}

/// Twin of the checks in `reducers._index_invariants` once the observation-finding ids are
/// copied: `Some(true)` when every invariant holds, `Some(false)` when the reference raises
/// `projection_corrupt`, `None` for inputs outside the exact shapes a constructed index holds.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn reducers_index_invariants(
    frontier: &Bound<'_, PyAny>,
    payloads: &Bound<'_, PyAny>,
    evidence: &Bound<'_, PyAny>,
    roots: &Bound<'_, PyAny>,
    observed: &Bound<'_, PyAny>,
    observation_findings: &Bound<'_, PyAny>,
    association_type: &Bound<'_, PyAny>,
) -> PyResult<Option<bool>> {
    let py = frontier.py();
    let (Some(frontier), Some(payloads), Some(evidence), Some(roots)) =
        (exact_i64(frontier), exact_dict(payloads), exact_dict(evidence), exact_dict(roots))
    else {
        return Ok(None);
    };
    if observation_findings.cast_exact::<PyFrozenSet>().is_err() {
        return Ok(None);
    }
    let payload_items = dict_items(payloads);
    let mut accepted: HashSet<StrKey, PassBuild> = HashSet::with_capacity_and_hasher(payload_items.len(), PassBuild::default());
    for (_, value) in &payload_items {
        let Some(key) = StrKey::of(value) else {
            return Ok(None);
        };
        accepted.insert(key);
    }
    if payload_items.len() as i64 != frontier || accepted.len() != payload_items.len() {
        return Ok(Some(false));
    }
    let evidence_name = intern!(py, "evidence_id");
    let source_name = intern!(py, "source_event_id");
    let evidence_items = dict_items(evidence);
    let mut seen: HashSet<(StrKey, StrKey), PassBuild> = HashSet::default();
    // Keep every attribute value alive while its borrowed key sits in `seen`.
    let mut held: Vec<Bound<'_, PyAny>> = Vec::new();
    for (_, associations) in &evidence_items {
        let Ok(associations) = associations.cast_exact::<PyTuple>() else {
            return Ok(None);
        };
        for association in associations.iter() {
            if association.get_type().as_ptr() != association_type.as_ptr() {
                return Ok(None);
            }
            let evidence_id = association.getattr(evidence_name)?;
            let source_event_id = association.getattr(source_name)?;
            let (Some(evidence_key), Some(source_key)) = (StrKey::of(&evidence_id), StrKey::of(&source_event_id))
            else {
                return Ok(None);
            };
            if !accepted.contains(&source_key) || !seen.insert((evidence_key, source_key)) {
                return Ok(Some(false));
            }
            held.push(evidence_id);
            held.push(source_event_id);
        }
    }
    for (_, root) in dict_items(roots) {
        let Some(key) = StrKey::of(&root) else {
            return Ok(None);
        };
        if !accepted.contains(&key) {
            return Ok(Some(false));
        }
    }
    if observed.cast_exact::<PyFrozenSet>().is_err() {
        return Ok(Some(false));
    }
    for set in [observed, observation_findings] {
        for item in set.try_iter()? {
            let item = item?;
            let Some(key) = StrKey::of(&item) else {
                return Ok(None);
            };
            if !accepted.contains(&key) {
                return Ok(Some(false));
            }
        }
    }
    drop(held);
    Ok(Some(true))
}

// ---------------------------------------------------------------------------------------------
// _recompute_secondary_effects

/// The reference compares freshly replaced records by value; a derived field is "unchanged" when
/// it is the identical object or an exact `str`/`int`/`tuple` of equal value, which also proves
/// `dataclasses.replace` would validate it exactly as it validated the original record.
fn same_value(left: &Bound<'_, PyAny>, right: &Bound<'_, PyAny>) -> bool {
    if left.as_ptr() == right.as_ptr() {
        return true;
    }
    unsafe {
        let (a, b) = (left.as_ptr(), right.as_ptr());
        if ffi::PyUnicode_CheckExact(a) != 0 && ffi::PyUnicode_CheckExact(b) != 0 {
            return ffi::PyUnicode_Compare(a, b) == 0;
        }
        if ffi::PyLong_CheckExact(a) != 0 && ffi::PyLong_CheckExact(b) != 0 {
            return ffi::PyObject_RichCompareBool(a, b, ffi::Py_EQ) == 1;
        }
        if ffi::PyTuple_CheckExact(a) != 0 && ffi::PyTuple_CheckExact(b) != 0 {
            let left = left.cast_unchecked::<PyTuple>();
            let right = right.cast_unchecked::<PyTuple>();
            if left.len() != right.len() {
                return false;
            }
            return left.iter().zip(right.iter()).all(|(x, y)| same_value(&x, &y));
        }
    }
    false
}

enum Derived<'py> {
    /// Equal to the record's own fields: keep the record object.
    Original,
    /// The reset values of the reference's first pass.
    Reset,
    /// A record `dataclasses.replace` built for a write that changed a field.
    Written(Bound<'py, PyAny>),
}

struct Slot<'py> {
    key: Bound<'py, PyAny>,
    record: Bound<'py, PyAny>,
    fields: Vec<Bound<'py, PyAny>>,
    derived: Derived<'py>,
}

struct Family<'py> {
    dict: Bound<'py, PyDict>,
    names: Vec<Bound<'py, PyString>>,
    reset: Vec<Bound<'py, PyAny>>,
    slots: Vec<Slot<'py>>,
    index: Option<Bound<'py, PyDict>>,
}

impl<'py> Family<'py> {
    /// Snapshot an exact dict whose every value is an exact `record_type`.
    fn load(
        dict: &Bound<'py, PyAny>,
        record_type: &Bound<'py, PyAny>,
        names: &[&str],
        reset: Vec<Bound<'py, PyAny>>,
    ) -> PyResult<Option<Family<'py>>> {
        let py = dict.py();
        let Some(dict) = exact_dict(dict) else {
            return Ok(None);
        };
        let names: Vec<Bound<'py, PyString>> = names.iter().map(|name| PyString::intern(py, name)).collect();
        let mut slots = Vec::with_capacity(dict.len());
        for (key, record) in dict_items(dict) {
            if record.get_type().as_ptr() != record_type.as_ptr() {
                return Ok(None);
            }
            let mut fields = Vec::with_capacity(names.len());
            for name in &names {
                fields.push(record.getattr(name)?);
            }
            slots.push(Slot { key, record, fields, derived: Derived::Reset });
        }
        Ok(Some(Family { dict: dict.clone(), names, reset, slots, index: None }))
    }

    /// `dict.get(key)` for the slot it names (keys never change during the recompute).
    fn find(&mut self, key: &Bound<'py, PyAny>) -> PyResult<Option<usize>> {
        let py = self.dict.py();
        let index = match &self.index {
            Some(index) => index.clone(),
            None => {
                let index = PyDict::new(py);
                for (position, slot) in self.slots.iter().enumerate() {
                    index.set_item(&slot.key, position)?;
                }
                self.index = Some(index.clone());
                index
            }
        };
        match index.get_item(key)? {
            Some(position) => Ok(Some(position.extract::<usize>()?)),
            None => Ok(None),
        }
    }

    fn replace(&self, replace: &Bound<'py, PyAny>, record: &Bound<'py, PyAny>, values: &[Bound<'py, PyAny>]) -> PyResult<Bound<'py, PyAny>> {
        let kwargs = PyDict::new(self.dict.py());
        for (name, value) in self.names.iter().zip(values) {
            kwargs.set_item(name, value)?;
        }
        replace.call((record,), Some(&kwargs))
    }

    /// One reference write `dict[key] = replace(dict[key], **values)`. `replace` runs (and may
    /// refuse, exactly as the reference's would) unless the values are the record's own.
    fn write(&mut self, position: usize, values: Vec<Bound<'py, PyAny>>, replace: &Bound<'py, PyAny>) -> PyResult<()> {
        let slot = &self.slots[position];
        let unchanged = slot.fields.iter().zip(&values).all(|(field, value)| same_value(field, value));
        let derived = if unchanged {
            Derived::Original
        } else {
            Derived::Written(self.replace(replace, &slot.record, &values)?)
        };
        self.slots[position].derived = derived;
        Ok(())
    }

    /// Store every record whose derived fields differ from its own.
    fn finish(self, replace: &Bound<'py, PyAny>) -> PyResult<()> {
        for slot in &self.slots {
            match &slot.derived {
                Derived::Original => {}
                Derived::Written(record) => self.dict.set_item(&slot.key, record)?,
                Derived::Reset => {
                    let unchanged = slot.fields.iter().zip(&self.reset).all(|(field, value)| same_value(field, value));
                    if !unchanged {
                        let record = self.replace(replace, &slot.record, &self.reset)?;
                        self.dict.set_item(&slot.key, record)?;
                    }
                }
            }
        }
        Ok(())
    }
}

/// A prior `(ContradictionKey, ContradictionRecord)` pair.
type PriorContradiction<'py> = (Bound<'py, PyAny>, Bound<'py, PyAny>);

/// `(source_frontier, _ascii_key(source_event_id))`; `None` where the reference's key differs.
fn fold_order(record: &Bound<'_, PyAny>) -> PyResult<Option<(i64, Vec<u8>)>> {
    let py = record.py();
    let frontier = record.getattr(intern!(py, "source_frontier"))?;
    let event = record.getattr(intern!(py, "source_event_id"))?;
    Ok(match (exact_i64(&frontier), ascii_bytes(&event)) {
        (Some(frontier), Some(bytes)) => Some((frontier, bytes.to_vec())),
        _ => None,
    })
}

/// Records of *family* whose payload is exactly *payload_type*, in the reference's stable sort.
fn sorted_by_fold_order<'py>(
    records: impl Iterator<Item = Bound<'py, PyAny>>,
    payload_type: Option<&Bound<'py, PyAny>>,
) -> PyResult<Option<Vec<Bound<'py, PyAny>>>> {
    let mut keyed = Vec::new();
    for record in records {
        if let Some(payload_type) = payload_type {
            let payload = record.getattr(intern!(record.py(), "payload"))?;
            if payload.get_type().as_ptr() != payload_type.as_ptr() {
                continue;
            }
        }
        let Some(order) = fold_order(&record)? else {
            return Ok(None);
        };
        keyed.push((order, record));
    }
    keyed.sort_by(|left, right| left.0.cmp(&right.0));
    Ok(Some(keyed.into_iter().map(|(_, record)| record).collect()))
}

/// Twin of `reducers._recompute_secondary_effects`.
///
/// `types` is `(PlanProjectionRecord, ObligationProjectionRecord, DecisionProjectionRecord,
/// ClaimProjectionRecord, PlanRevisedPayload, DecisionRecordedPayload, ClaimRecordedPayload,
/// ClaimRecordedPayloadV1_1, ContradictionKey, ContradictionRecord)`.
///
/// The derived fields end exactly as the reference leaves them; a record whose derived fields
/// end equal to its own is kept as the same object (so the next projection carries it as
/// trusted) instead of being replaced by an equal copy, and an equal prior contradiction is
/// reused. Every write that changes a field still goes through `dataclasses.replace` in the
/// reference's order, so a refused write raises exactly where the reference's would.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn reducers_secondary_effects<'py>(
    plans: &Bound<'py, PyAny>,
    obligations: &Bound<'py, PyAny>,
    decisions: &Bound<'py, PyAny>,
    claims: &Bound<'py, PyAny>,
    prior_contradictions: &Bound<'py, PyAny>,
    replace: &Bound<'py, PyAny>,
    types: &Bound<'py, PyTuple>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let py = plans.py();
    if types.len() != 10 {
        return Ok(None);
    }
    let plan_record = types.get_item(0)?;
    let obligation_record = types.get_item(1)?;
    let decision_record = types.get_item(2)?;
    let claim_record = types.get_item(3)?;
    let plan_revised = types.get_item(4)?;
    let decision_payload = types.get_item(5)?;
    let claim_payload = types.get_item(6)?;
    let claim_payload_v1_1 = types.get_item(7)?;
    let contradiction_key = types.get_item(8)?;
    let contradiction_record = types.get_item(9)?;
    let none = py.None().into_bound(py);
    let empty = PyTuple::empty(py).into_any();

    let Some(mut plan_family) = Family::load(plans, &plan_record, &["superseded_by_plan_version"], vec![none.clone()])? else {
        return Ok(None);
    };
    let Some(mut obligation_family) = Family::load(
        obligations,
        &obligation_record,
        &["plan_change", "plan_change_reason", "superseded_by_obligation_ids"],
        vec![none.clone(), none.clone(), empty],
    )?
    else {
        return Ok(None);
    };
    let Some(mut decision_family) = Family::load(decisions, &decision_record, &["superseded_by_event_id"], vec![none.clone()])? else {
        return Ok(None);
    };
    let Some(claims) = exact_dict(claims) else {
        return Ok(None);
    };
    let claim_records: Vec<Bound<'py, PyAny>> = dict_items(claims).into_iter().map(|(_, record)| record).collect();
    if claim_records.iter().any(|record| record.get_type().as_ptr() != claim_record.as_ptr()) {
        return Ok(None);
    }
    let Some(revisions) = sorted_by_fold_order(plan_family.slots.iter().map(|slot| slot.record.clone()), Some(&plan_revised))? else {
        return Ok(None);
    };
    let Some(ordered_decisions) =
        sorted_by_fold_order(decision_family.slots.iter().map(|slot| slot.record.clone()), Some(&decision_payload))?
    else {
        return Ok(None);
    };
    let Some(ordered_claims) = sorted_by_fold_order(claim_records.into_iter(), None)? else {
        return Ok(None);
    };

    let payload_name = intern!(py, "payload");
    for record in &revisions {
        let payload = record.getattr(payload_name)?;
        let superseded = payload.getattr(intern!(py, "supersedes_plan_version"))?;
        if let Some(position) = plan_family.find(&superseded)? {
            let version = payload.getattr(intern!(py, "plan_version"))?;
            plan_family.write(position, vec![version], replace)?;
        }
        for change in payload.getattr(intern!(py, "obligation_changes"))?.try_iter()? {
            let change = change?;
            let obligation = change.getattr(intern!(py, "obligation_id"))?;
            if let Some(position) = obligation_family.find(&obligation)? {
                let values = vec![
                    change.getattr(intern!(py, "change"))?,
                    change.getattr(intern!(py, "reason"))?,
                    change.getattr(intern!(py, "replacement_obligation_ids"))?,
                ];
                obligation_family.write(position, values, replace)?;
            }
        }
    }
    for record in &ordered_decisions {
        let payload = record.getattr(payload_name)?;
        let superseded = payload.getattr(intern!(py, "supersedes_event_id"))?;
        if superseded.is_none() {
            continue;
        }
        if let Some(position) = decision_family.find(&superseded)? {
            let source = record.getattr(intern!(py, "source_event_id"))?;
            decision_family.write(position, vec![source], replace)?;
        }
    }

    // Equal prior contradictions, keyed by their exact-`str` identity fields.
    let mut prior: HashMap<(Vec<u8>, Vec<u8>), PriorContradiction<'py>> = HashMap::new();
    if !prior_contradictions.is_none() {
        if let Some(dict) = trusted_dict(prior_contradictions) {
            let dict = unsafe { Bound::from_borrowed_ptr(py, dict).cast_into_unchecked::<PyDict>() };
            for (key, record) in dict_items(&dict) {
                if key.get_type().as_ptr() != contradiction_key.as_ptr()
                    || record.get_type().as_ptr() != contradiction_record.as_ptr()
                {
                    continue;
                }
                let claim = key.getattr(intern!(py, "disputing_claim_id"))?;
                let disputed = key.getattr(intern!(py, "disputed_ref"))?;
                if let (Some(claim), Some(disputed)) = (str_bytes(&claim), str_bytes(&disputed)) {
                    prior.entry((claim.to_vec(), disputed.to_vec())).or_insert((key.clone(), record));
                }
            }
        }
    }
    let contradictions = PyDict::new(py);
    let claim_types = [claim_payload.as_ptr(), claim_payload_v1_1.as_ptr()];
    for record in &ordered_claims {
        let payload = record.getattr(payload_name)?;
        if !claim_types.contains(&payload.get_type().as_ptr()) {
            continue;
        }
        let claim = payload.getattr(intern!(py, "claim_id"))?;
        let source_event = record.getattr(intern!(py, "source_event_id"))?;
        let source_frontier = record.getattr(intern!(py, "source_frontier"))?;
        for disputed in payload.getattr(intern!(py, "disputes_refs"))?.try_iter()? {
            let disputed = disputed?;
            let mut reused = None;
            if let (Some(claim_bytes), Some(disputed_bytes)) = (str_bytes(&claim), str_bytes(&disputed)) {
                if let Some((key, existing)) = prior.get(&(claim_bytes.to_vec(), disputed_bytes.to_vec())) {
                    if same_value(&existing.getattr(intern!(py, "source_event_id"))?, &source_event)
                        && same_value(&existing.getattr(intern!(py, "source_frontier"))?, &source_frontier)
                    {
                        reused = Some((key.clone(), existing.clone()));
                    }
                }
            }
            let (key, value) = match reused {
                Some(pair) => pair,
                None => {
                    let key = contradiction_key.call1((&claim, &disputed))?;
                    let kwargs = PyDict::new(py);
                    kwargs.set_item(intern!(py, "disputing_claim_id"), &claim)?;
                    kwargs.set_item(intern!(py, "disputed_ref"), &disputed)?;
                    kwargs.set_item(intern!(py, "source_event_id"), &source_event)?;
                    kwargs.set_item(intern!(py, "source_frontier"), &source_frontier)?;
                    let value = contradiction_record.call((), Some(&kwargs))?;
                    (key, value)
                }
            };
            contradictions.set_item(key, value)?;
        }
    }

    plan_family.finish(replace)?;
    obligation_family.finish(replace)?;
    decision_family.finish(replace)?;
    Ok(Some(contradictions))
}

// ---------------------------------------------------------------------------------------------
// _recompute_missing_gaps

const PREFIXES: [&[u8]; 6] = [b"obl_", b"act_", b"res_", b"evd_", b"clm_", b"fnd_"];

struct Gaps<'py> {
    /// Sorted by `_ascii_key`; the first object stored for a text wins, as in a `set`.
    markers: BTreeMap<Vec<u8>, Bound<'py, PyAny>>,
    validators: Vec<Bound<'py, PyAny>>,
    /// The visibility mapping for each prefix, in `PREFIXES` order.
    visible: Vec<Bound<'py, PyDict>>,
}

enum Require {
    Done,
    /// An input outside the shapes the twin handles: run the reference instead.
    Defer,
    /// `_target_visible` raised `projection_corrupt`.
    Corrupt,
}

impl<'py> Gaps<'py> {
    fn require(&mut self, source_event: &[u8], target: &Bound<'py, PyAny>) -> PyResult<Require> {
        let Some(text) = str_bytes(target) else {
            return Ok(Require::Defer);
        };
        let Some(kind) = PREFIXES.iter().position(|prefix| text.starts_with(prefix)) else {
            return Ok(Require::Corrupt);
        };
        let validated = self.validators[kind].call1((target,))?;
        if self.visible[kind].contains(&validated)? {
            return Ok(Require::Done);
        }
        let mut marker = Vec::with_capacity(13 + source_event.len() + text.len());
        marker.extend_from_slice(b"missing_ref:");
        marker.extend_from_slice(source_event);
        marker.push(b':');
        marker.extend_from_slice(text);
        if !marker.is_ascii() {
            return Ok(Require::Defer);
        }
        if let std::collections::btree_map::Entry::Vacant(entry) = self.markers.entry(marker) {
            // ASCII was checked above, so the bytes are valid UTF-8.
            let Ok(text) = std::str::from_utf8(entry.key()) else {
                return Ok(Require::Defer);
            };
            let object = PyString::new(target.py(), text).into_any();
            entry.insert(object);
        }
        Ok(Require::Done)
    }
}

macro_rules! require {
    ($gaps:expr, $source:expr, $target:expr) => {
        match $gaps.require($source, $target)? {
            Require::Done => {}
            Require::Defer => return Ok(None),
            Require::Corrupt => return Err(corrupt()),
        }
    };
}

fn corrupt() -> PyErr {
    pyo3::exceptions::PyValueError::new_err("projection_corrupt")
}

fn each<'py>(value: &Bound<'py, PyAny>) -> PyResult<Vec<Bound<'py, PyAny>>> {
    value.try_iter()?.collect()
}

/// Twin of `reducers._recompute_missing_gaps`.
///
/// `collections` is `(plans, obligations, decisions, assignments, actions, results, evidence,
/// claims, findings, responses, coordination_dispositions)` (exact dicts), `validators` is
/// `(obligation_id, action_id, result_id, evidence_id, claim_id, finding_id)` as the module
/// currently binds them, and `types` is `(PlanPublishedPayload, PlanRevisedPayload,
/// ClaimRecordedPayloadV1_1)`. Targets are visited in the reference's order and validated
/// through the same callables, so a refused id raises exactly where the reference's would.
#[pyfunction]
fn reducers_missing_gaps<'py>(
    retained: &Bound<'py, PyAny>,
    collections: &Bound<'py, PyTuple>,
    validators: &Bound<'py, PyTuple>,
    types: &Bound<'py, PyTuple>,
) -> PyResult<Option<Bound<'py, PyTuple>>> {
    let py = retained.py();
    if collections.len() != 11 || validators.len() != 6 || types.len() != 3 {
        return Ok(None);
    }
    let mut dicts = Vec::with_capacity(11);
    for collection in collections.iter() {
        let Ok(dict) = collection.cast_exact::<PyDict>() else {
            return Ok(None);
        };
        dicts.push(dict.clone());
    }
    let [plans, obligations, decisions, assignments, actions, results, evidence, claims, findings, responses, dispositions] =
        <[Bound<'py, PyDict>; 11]>::try_from(dicts).map_err(|_| corrupt())?;
    let plan_published = types.get_item(0)?;
    let plan_revised = types.get_item(1)?;
    let claim_v1_1 = types.get_item(2)?;

    let mut markers = BTreeMap::new();
    for marker in retained.try_iter()? {
        let marker = marker?;
        let Some(text) = str_bytes(&marker) else {
            return Ok(None);
        };
        if text.starts_with(b"missing_ref:") {
            continue;
        }
        if !text.is_ascii() {
            return Ok(None);
        }
        markers.entry(text.to_vec()).or_insert(marker);
    }
    let mut gaps = Gaps {
        markers,
        validators: validators.iter().collect(),
        visible: vec![obligations.clone(), actions.clone(), results.clone(), evidence.clone(), claims.clone(), findings.clone()],
    };

    let payload_name = intern!(py, "payload");
    let source_name = intern!(py, "source_event_id");
    // (collection, fields, the record's own payload type gate)
    for (index, dict) in [&plans, &obligations, &decisions, &assignments, &actions, &results, &claims, &findings, &responses, &dispositions]
        .into_iter()
        .enumerate()
    {
        for (_, record) in dict_items(dict) {
            let payload = record.getattr(payload_name)?;
            if payload.is_none() && index != 0 {
                continue;
            }
            let source_event = record.getattr(source_name)?;
            let source = match str_bytes(&source_event) {
                Some(bytes) => bytes.to_vec(),
                None => return Ok(None),
            };
            match index {
                0 => {
                    let kind = payload.get_type();
                    if kind.as_ptr() == plan_published.as_ptr() {
                        for target in each(&payload.getattr(intern!(py, "obligation_refs"))?)? {
                            require!(gaps, &source, &target);
                        }
                    } else if kind.as_ptr() == plan_revised.as_ptr() {
                        for change in each(&payload.getattr(intern!(py, "obligation_changes"))?)? {
                            require!(gaps, &source, &change.getattr(intern!(py, "obligation_id"))?);
                            for target in each(&change.getattr(intern!(py, "replacement_obligation_ids"))?)? {
                                require!(gaps, &source, &target);
                            }
                        }
                    }
                }
                1 => {
                    for target in each(&payload.getattr(intern!(py, "resolution_evidence_refs"))?)? {
                        require!(gaps, &source, &target);
                    }
                }
                2 => {
                    for target in each(&payload.getattr(intern!(py, "affected_obligation_ids"))?)? {
                        require!(gaps, &source, &target);
                    }
                }
                3 => {
                    for target in each(&payload.getattr(intern!(py, "obligation_ids"))?)? {
                        require!(gaps, &source, &target);
                    }
                }
                4 => {
                    for target in each(&payload.getattr(intern!(py, "obligation_refs"))?)? {
                        require!(gaps, &source, &target);
                    }
                }
                5 => {
                    require!(gaps, &source, &payload.getattr(intern!(py, "action_id"))?);
                    for target in each(&payload.getattr(intern!(py, "evidence_refs"))?)? {
                        require!(gaps, &source, &target);
                    }
                }
                6 => {
                    for target in each(&payload.getattr(intern!(py, "supporting_refs"))?)? {
                        require!(gaps, &source, &target);
                    }
                    for target in each(&payload.getattr(intern!(py, "obligation_refs"))?)? {
                        require!(gaps, &source, &target);
                    }
                    for target in each(&payload.getattr(intern!(py, "disputes_refs"))?)? {
                        match str_bytes(&target) {
                            Some(text) if text.starts_with(b"clm_") => require!(gaps, &source, &target),
                            Some(_) => {}
                            None => return Ok(None),
                        }
                    }
                    if payload.get_type().as_ptr() == claim_v1_1.as_ptr() {
                        for target in each(&payload.getattr(intern!(py, "limitation_refs"))?)? {
                            require!(gaps, &source, &target);
                        }
                        for target in each(&payload.getattr(intern!(py, "supersedes_claim_refs"))?)? {
                            require!(gaps, &source, &target);
                        }
                    }
                }
                7 => {
                    for target in each(&payload.getattr(intern!(py, "subject_refs"))?)? {
                        match str_bytes(&target) {
                            Some(text) if !text.starts_with(b"evt_") => require!(gaps, &source, &target),
                            Some(_) => {}
                            None => return Ok(None),
                        }
                    }
                }
                8 => {
                    require!(gaps, &source, &payload.getattr(intern!(py, "finding_id"))?);
                    for target in each(&payload.getattr(intern!(py, "evidence_refs"))?)? {
                        require!(gaps, &source, &target);
                    }
                }
                _ => {
                    for target in each(&payload.getattr(intern!(py, "evidence_refs"))?)? {
                        require!(gaps, &source, &target);
                    }
                }
            }
        }
    }
    Ok(Some(PyTuple::new(py, gaps.markers.into_values())?))
}

/// Whether exact tuple *events* starts with the identical objects of exact tuple *prefix*.
#[pyfunction]
fn reducers_identical_prefix(prefix: &Bound<'_, PyAny>, events: &Bound<'_, PyAny>) -> bool {
    let (Ok(prefix), Ok(events)) = (prefix.cast_exact::<PyTuple>(), events.cast_exact::<PyTuple>()) else {
        return false;
    };
    if prefix.as_ptr() == events.as_ptr() {
        return true;
    }
    if prefix.len() > events.len() {
        return false;
    }
    (0..prefix.len()).all(|index| unsafe {
        ffi::PyTuple_GET_ITEM(prefix.as_ptr(), index as ffi::Py_ssize_t)
            == ffi::PyTuple_GET_ITEM(events.as_ptr(), index as ffi::Py_ssize_t)
    })
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(reducers_identical_prefix, module)?)?;
    module.add_function(wrap_pyfunction!(reducers_carry_id_set, module)?)?;
    module.add_function(wrap_pyfunction!(reducers_index_invariants, module)?)?;
    module.add_function(wrap_pyfunction!(reducers_secondary_effects, module)?)?;
    module.add_function(wrap_pyfunction!(reducers_missing_gaps, module)?)?;
    Ok(())
}
