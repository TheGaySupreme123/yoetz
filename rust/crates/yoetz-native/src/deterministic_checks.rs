//! `yoetz.kernel.deterministic_checks` reference validation and coverage folding.
//!
//! Every function here is a fast path that either returns exactly what the Python reference
//! returns or returns `None`, after which the Python wrapper runs the reference itself. A refusal
//! therefore always comes from the reference (same exception, same chained cause, same Python
//! frame); the native path never raises on its own account except for a Python error raised by a
//! callable the reference also calls (`_weaken_ref_coverage`, `weakest`).
//!
//! Identifier grammar is not duplicated here: each reference id is checked by calling the
//! `validate_id` the reference's `event_id`/`claim_id`/... helpers reach (read from
//! `yoetz.domain.values` at call time). Those helpers return the very string object they were
//! given when it is an exact `str`, so a validated exact `str` stands for itself.

use std::collections::{HashMap, HashSet};
use std::sync::Mutex;

use pyo3::exceptions::PyValueError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyFrozenSet, PyString, PyTuple};

use crate::registry::Slot;

/// The seven `IdKind` members `_basis_ref` dispatches to, in its prefix order.
static ID_KINDS: Slot = Slot::new();
/// `yoetz.domain.values`, whose `validate_id` the typed id helpers call.
static VALUES_MODULE: Slot = Slot::new();

/// `_basis_ref` prefix order: event, obligation, claim, action, result, evidence, finding.
const PREFIXES: [&str; 7] = ["evt_", "obl_", "clm_", "act_", "res_", "evd_", "fnd_"];
const EVENT: usize = 0;
const CLAIM: usize = 2;
const EVIDENCE: usize = 5;
/// Bounded equality de-duplication while folding coverages; identity de-duplication is unbounded.
const MAX_EQUALITY_PROBES: usize = 16;

/// Bind the `IdKind` members (in `PREFIXES` order) and the module holding `validate_id`.
#[pyfunction]
pub fn checks_bind(kinds: Bound<'_, PyTuple>, values_module: Bound<'_, PyAny>) -> PyResult<()> {
    if kinds.len() != PREFIXES.len() {
        return Err(PyValueError::new_err("policy_wiring_invalid"));
    }
    ID_KINDS.set(kinds.into_any().unbind());
    VALUES_MODULE.set(values_module.unbind());
    Ok(())
}

#[inline]
fn is_exact_str(value: &Bound<'_, PyAny>) -> bool {
    unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) != 0 }
}

/// The UTF-8 text of an exact `str`, or `None` (not exact, or holds a lone surrogate).
#[inline]
fn exact_text<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a str> {
    if !is_exact_str(value) {
        return None;
    }
    unsafe { value.cast_unchecked::<PyString>() }.to_str().ok()
}

fn prefix_index(text: &str) -> Option<usize> {
    PREFIXES.iter().position(|prefix| text.starts_with(prefix))
}

/// Id texts `validate_id` accepted as themselves, each for the kind its prefix selects (the only
/// kind this module ever asks about). Validation is a pure function of an exact `str`'s content,
/// so a verdict is reused until `validate_id` itself is replaced (a test patch, a rebinding) or
/// the bound is reached. Refusals are never cached: the reference reruns and raises them.
struct AcceptedIds {
    validator: Option<Py<PyAny>>,
    texts: HashSet<Box<str>>,
}

const MAX_ACCEPTED_IDS: usize = 32_768;

static ACCEPTED_IDS: Mutex<Option<AcceptedIds>> = Mutex::new(None);

/// Run `action` on the cache for `validate`, resetting it when `validate` is a different object.
/// The lock is never held while Python code runs.
fn with_accepted_ids<T>(
    validate: &Bound<'_, PyAny>,
    action: impl FnOnce(&mut HashSet<Box<str>>) -> T,
) -> T {
    let mut guard = ACCEPTED_IDS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let cache = guard.get_or_insert_with(|| AcceptedIds {
        validator: None,
        texts: HashSet::new(),
    });
    if !cache
        .validator
        .as_ref()
        .is_some_and(|known| known.as_ptr() == validate.as_ptr())
    {
        cache.texts.clear();
        cache.validator = Some(validate.clone().unbind());
    }
    action(&mut cache.texts)
}

/// Validates reference ids through the reference's own `validate_id`.
struct RefValidator<'py> {
    validate: Bound<'py, PyAny>,
    kinds: Bound<'py, PyTuple>,
    seen: HashSet<usize>,
}

impl<'py> RefValidator<'py> {
    fn new(py: Python<'py>) -> Option<Self> {
        let kinds = ID_KINDS.get(py)?.cast_into::<PyTuple>().ok()?;
        let validate = VALUES_MODULE.get(py)?.getattr("validate_id").ok()?;
        Some(RefValidator {
            validate,
            kinds,
            seen: HashSet::new(),
        })
    }

    /// Whether `value` (an exact `str` whose prefix selects `kind`) validates to itself.
    fn accepts(&mut self, value: &Bound<'py, PyAny>, kind: usize) -> bool {
        let pointer = value.as_ptr() as usize;
        if self.seen.contains(&pointer) {
            return true;
        }
        let Some(text) = exact_text(value) else {
            return false;
        };
        if with_accepted_ids(&self.validate, |texts| texts.contains(text)) {
            self.seen.insert(pointer);
            return true;
        }
        let Ok(kind_member) = self.kinds.get_item(kind) else {
            return false;
        };
        match self.validate.call1((kind_member, value)) {
            Ok(result) if result.is(value) => {
                self.seen.insert(pointer);
                with_accepted_ids(&self.validate, |texts| {
                    if texts.len() >= MAX_ACCEPTED_IDS {
                        texts.clear();
                    }
                    texts.insert(text.into());
                });
                true
            }
            Ok(_) => false,
            Err(_) => false,
        }
    }

    /// `_basis_ref(value) is value`, or `None` when the reference must decide.
    fn basis_ref(&mut self, value: &Bound<'py, PyAny>) -> Option<usize> {
        let kind = prefix_index(exact_text(value)?)?;
        self.accepts(value, kind).then_some(kind)
    }
}

/// `_basis_ref` fast path: the validated ref itself, or `None` to run the reference.
#[pyfunction]
pub fn checks_basis_ref<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyAny>> {
    let mut validator = RefValidator::new(py)?;
    validator.basis_ref(value)?;
    Some(value.clone())
}

/// `_validated_ref_tuple` fast path.
#[pyfunction]
pub fn checks_validated_ref_tuple<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    public_only: bool,
    allow_empty: bool,
    max_ref_list: usize,
) -> Option<Bound<'py, PyTuple>> {
    if unsafe { ffi::PyTuple_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    let raw = unsafe { value.cast_unchecked::<PyTuple>() };
    let minimum = if allow_empty { 0 } else { 1 };
    if raw.len() < minimum || raw.len() > max_ref_list {
        return None;
    }
    let mut validator = RefValidator::new(py)?;
    let mut previous: Option<&str> = None;
    let items: Vec<Bound<'py, PyAny>> = raw.iter().collect();
    for item in &items {
        let kind = validator.basis_ref(item)?;
        if public_only && kind > CLAIM {
            return None;
        }
        // Validated ids are printable ASCII, so byte order is the reference's `_ascii_key` order;
        // the reference requires the tuple to already be sorted and unique.
        let text = exact_text(item)?;
        if previous.is_some_and(|prior| prior.as_bytes() >= text.as_bytes()) {
            return None;
        }
        previous = Some(text);
    }
    PyTuple::new(py, items).ok()
}

/// `_sorted_unique` fast path over a materialized tuple of exact ASCII `str`s.
#[pyfunction]
pub fn checks_sorted_unique<'py>(
    py: Python<'py>,
    values: &Bound<'py, PyTuple>,
) -> Option<Bound<'py, PyTuple>> {
    let mut members: Vec<(&[u8], Bound<'py, PyAny>)> = Vec::with_capacity(values.len());
    let items: Vec<Bound<'py, PyAny>> = values.iter().collect();
    let mut seen: HashSet<&[u8]> = HashSet::with_capacity(items.len());
    for item in &items {
        let text = exact_text(item)?;
        if !text.is_ascii() {
            return None;
        }
        // `set()` keeps the first of equal members.
        if seen.insert(text.as_bytes()) {
            members.push((text.as_bytes(), item.clone()));
        }
    }
    members.sort_by(|left, right| left.0.cmp(right.0));
    PyTuple::new(py, members.into_iter().map(|(_, item)| item)).ok()
}

/// `tuple(sorted(facts, key=_fact_key))` fast path (stable, like `sorted`).
#[pyfunction]
pub fn checks_sorted_facts<'py>(
    py: Python<'py>,
    facts: &Bound<'py, PyTuple>,
) -> Option<Bound<'py, PyTuple>> {
    let items: Vec<Bound<'py, PyAny>> = facts.iter().collect();
    let mut codes: Vec<Bound<'py, PyAny>> = Vec::with_capacity(items.len());
    let mut refs: Vec<Vec<Bound<'py, PyAny>>> = Vec::with_capacity(items.len());
    for fact in &items {
        let code = fact.getattr("fact_code").ok()?;
        exact_text(&code).filter(|text| text.is_ascii())?;
        let subject_refs = fact.getattr("subject_refs").ok()?;
        if unsafe { ffi::PyTuple_CheckExact(subject_refs.as_ptr()) } == 0 {
            return None;
        }
        let subject_refs: Vec<Bound<'py, PyAny>> =
            unsafe { subject_refs.cast_unchecked::<PyTuple>() }
                .iter()
                .collect();
        for item in &subject_refs {
            exact_text(item).filter(|text| text.is_ascii())?;
        }
        codes.push(code);
        refs.push(subject_refs);
    }
    let key = |index: usize| -> (&[u8], Vec<&[u8]>) {
        let code = exact_text(&codes[index]).unwrap_or_default().as_bytes();
        let subjects = refs[index]
            .iter()
            .map(|item| exact_text(item).unwrap_or_default().as_bytes())
            .collect();
        (code, subjects)
    };
    let keys: Vec<(&[u8], Vec<&[u8]>)> = (0..items.len()).map(key).collect();
    let mut order: Vec<usize> = (0..items.len()).collect();
    order.sort_by(|left, right| keys[*left].cmp(&keys[*right]));
    PyTuple::new(py, order.into_iter().map(|index| items[index].clone())).ok()
}

/// The `(key, value)` pairs of an exact `dict` or `mappingproxy`, or `None` for anything else.
fn exact_mapping_items<'py>(
    mapping: &Bound<'py, PyAny>,
) -> Option<Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)>> {
    let pointer = mapping.as_ptr();
    if unsafe { ffi::PyDict_CheckExact(pointer) } != 0 {
        return Some(
            unsafe { mapping.cast_unchecked::<PyDict>() }
                .iter()
                .collect(),
        );
    }
    let is_proxy =
        unsafe { ffi::Py_TYPE(pointer) == std::ptr::addr_of_mut!(ffi::PyDictProxy_Type) };
    if !is_proxy {
        return None;
    }
    let mut pairs = Vec::new();
    for pair in mapping.call_method0("items").ok()?.try_iter().ok()? {
        pairs.push(
            pair.ok()?
                .extract::<(Bound<'py, PyAny>, Bound<'py, PyAny>)>()
                .ok()?,
        );
    }
    Some(pairs)
}

/// `_case_ref_coverage` fast path: `(allowed, coverage)` or `None` to run the reference.
#[pyfunction]
pub fn checks_case_ref_coverage<'py>(
    py: Python<'py>,
    allowed_ids: &Bound<'py, PyAny>,
    coverage_by_ref: &Bound<'py, PyAny>,
    coverage_class: &Bound<'py, PyAny>,
) -> Option<(Bound<'py, PyFrozenSet>, Bound<'py, PyDict>)> {
    if unsafe { ffi::PyFrozenSet_CheckExact(allowed_ids.as_ptr()) } == 0 {
        return None;
    }
    let mut validator = RefValidator::new(py)?;
    let mut members: Vec<Bound<'py, PyAny>> = Vec::new();
    for item in allowed_ids.try_iter().ok()? {
        let item = item.ok()?;
        validator.basis_ref(&item)?;
        members.push(item);
    }
    let pairs = exact_mapping_items(coverage_by_ref)?;
    let coverage = PyDict::new(py);
    for (raw_ref, value) in pairs {
        validator.basis_ref(&raw_ref)?;
        if !value.get_type().is(coverage_class) {
            return None;
        }
        coverage.set_item(&raw_ref, &value).ok()?;
    }
    let allowed = PyFrozenSet::new(py, members.iter()).ok()?;
    if coverage.len() != allowed.len() {
        return None;
    }
    for key in coverage.keys() {
        if !allowed.contains(&key).ok()? {
            return None;
        }
    }
    Some((allowed, coverage))
}

#[inline]
fn contains(container: &Bound<'_, PyAny>, item: &Bound<'_, PyAny>) -> Option<bool> {
    container.contains(item).ok()
}

/// `_ref_coverages` fast path. Refs whose source coverage object and weakening inputs agree share
/// one `_weaken_ref_coverage` result, which is deterministic in exactly those inputs.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn checks_ref_coverages<'py>(
    py: Python<'py>,
    source_by_ref: &Bound<'py, PyAny>,
    records_by_event: &Bound<'py, PyAny>,
    redacted_events: &Bound<'py, PyAny>,
    unavailable_events: &Bound<'py, PyAny>,
    missing_sources: &Bound<'py, PyAny>,
    unknown_events: &Bound<'py, PyAny>,
    redacted_object_by_evidence: &Bound<'py, PyAny>,
    unavailable_object_by_evidence: &Bound<'py, PyAny>,
    gap_codes_by_root: &Bound<'py, PyAny>,
    weaken: &Bound<'py, PyAny>,
    unknown_event_class: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let exact_dict =
        |value: &Bound<'py, PyAny>| unsafe { ffi::PyDict_CheckExact(value.as_ptr()) != 0 };
    if !exact_dict(source_by_ref) || !exact_dict(records_by_event) || !exact_dict(gap_codes_by_root)
    {
        return Ok(None);
    }
    let Some(mut validator) = RefValidator::new(py) else {
        return Ok(None);
    };
    let records = unsafe { records_by_event.cast_unchecked::<PyDict>() };
    let roots = unsafe { gap_codes_by_root.cast_unchecked::<PyDict>() };
    let code = |text: &str| PyString::new(py, text).into_any();
    let redacted_object_code = code("redacted_object");
    let unavailable_object_code = code("captured_object_unavailable");
    let provenance_codes = [
        code("evidence_content_digest_only"),
        code("evidence_content_withheld"),
        code("evidence_digest_subject_legacy_unknown"),
    ];
    let output = PyDict::new(py);
    let mut memo: HashMap<(usize, u16), Bound<'py, PyAny>> = HashMap::new();
    let pairs: Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)> =
        unsafe { source_by_ref.cast_unchecked::<PyDict>() }
            .iter()
            .collect();
    for (reference, source_event) in pairs {
        let Ok(Some(source)) = records.get_item(&source_event) else {
            return Ok(None);
        };
        let Some(text) = exact_text(&reference) else {
            return Ok(None);
        };
        let is_evidence = text.starts_with(PREFIXES[EVIDENCE]);
        let is_event = text.starts_with(PREFIXES[EVENT]);
        if is_evidence && !validator.accepts(&reference, EVIDENCE) {
            return Ok(None);
        }
        if is_event && !validator.accepts(&reference, EVENT) {
            return Ok(None);
        }
        let root_codes = match roots.get_item(&source_event) {
            Ok(codes) => codes,
            Err(_) => return Ok(None),
        };
        let has_code = |item: &Bound<'py, PyAny>| -> Option<bool> {
            match &root_codes {
                Some(codes) => contains(codes, item),
                None => Some(false),
            }
        };
        // Bits 0..=5 are `_weaken_ref_coverage`'s flags in keyword order; 6..=8 the provenance codes.
        let flags = || -> Option<u16> {
            let flags = [
                contains(redacted_events, &source_event)?,
                contains(unavailable_events, &source_event)?,
                (is_evidence && contains(redacted_object_by_evidence, &reference)?)
                    || has_code(&redacted_object_code)?,
                (is_evidence && contains(unavailable_object_by_evidence, &reference)?)
                    || has_code(&unavailable_object_code)?,
                contains(missing_sources, &source_event)?,
                is_event
                    && contains(unknown_events, &reference)?
                    && source.get_type().is(unknown_event_class),
                has_code(&provenance_codes[0])?,
                has_code(&provenance_codes[1])?,
                has_code(&provenance_codes[2])?,
            ];
            Some(flags.iter().enumerate().fold(
                0,
                |mask, (bit, flag)| if *flag { mask | (1 << bit) } else { mask },
            ))
        };
        let Some(mask) = flags() else {
            return Ok(None);
        };
        let Ok(base) = source.getattr("coverage") else {
            return Ok(None);
        };
        let memo_key = (base.as_ptr() as usize, mask);
        let weakened = match memo.get(&memo_key) {
            Some(value) => value.clone(),
            None => {
                let provenance = PyFrozenSet::new(
                    py,
                    provenance_codes
                        .iter()
                        .enumerate()
                        .filter(|(index, _)| mask & (1 << (6 + index)) != 0)
                        .map(|(_, item)| item),
                )?;
                let keywords = PyDict::new(py);
                let names = [
                    "redacted_event",
                    "unavailable_event",
                    "redacted_object",
                    "unavailable_object",
                    "missing_ref",
                    "unknown_event",
                ];
                for (bit, name) in names.iter().enumerate() {
                    keywords.set_item(*name, mask & (1 << bit) != 0)?;
                }
                keywords.set_item("evidence_provenance_gaps", provenance)?;
                let value = weaken.call((&base,), Some(&keywords))?;
                memo.insert(memo_key, value.clone());
                value
            }
        };
        output.set_item(&reference, weakened)?;
    }
    Ok(Some(output))
}

/// Fold coverages with `weakest`, in order. Coverage meet is idempotent, commutative and
/// associative, so a coverage equal to one already folded in leaves the accumulator's value
/// unchanged and is skipped: by identity always, by equality against a bounded number of
/// distinct values. The intermediate values (and so any refusal `weakest` raises) are exactly the
/// distinct ones the reference's full fold reaches.
fn fold_distinct<'py>(
    coverages: impl IntoIterator<Item = Bound<'py, PyAny>>,
    weakest: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let mut folded_identities: HashSet<usize> = HashSet::new();
    let mut distinct: Vec<Bound<'py, PyAny>> = Vec::new();
    let mut result: Option<Bound<'py, PyAny>> = None;
    for coverage in coverages {
        if !folded_identities.insert(coverage.as_ptr() as usize) {
            continue;
        }
        let mut equal_seen = false;
        if distinct.len() < MAX_EQUALITY_PROBES {
            for prior in &distinct {
                if coverage.eq(prior)? {
                    equal_seen = true;
                    break;
                }
            }
        }
        if equal_seen {
            continue;
        }
        result = Some(match result {
            None => coverage.clone(),
            Some(accumulated) => weakest.call1((accumulated, &coverage))?,
        });
        distinct.push(coverage);
    }
    Ok(result)
}

/// `_fold_case_coverages` fast path for a non-empty mapping.
#[pyfunction]
pub fn checks_fold_case_coverages<'py>(
    coverage_by_ref: &Bound<'py, PyAny>,
    weakest: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let Some(pairs) = exact_mapping_items(coverage_by_ref) else {
        return Ok(None);
    };
    if pairs.is_empty() {
        return Ok(None);
    }
    let mut keyed: Vec<(&str, &Bound<'py, PyAny>)> = Vec::with_capacity(pairs.len());
    for (key, value) in &pairs {
        // `sorted(key=str)` on exact `str` keys is code-point order, which UTF-8 byte order keeps.
        let Some(text) = exact_text(key) else {
            return Ok(None);
        };
        keyed.push((text, value));
    }
    keyed.sort_by(|left, right| left.0.as_bytes().cmp(right.0.as_bytes()));
    fold_distinct(
        keyed.into_iter().map(|(_, coverage)| coverage.clone()),
        weakest,
    )
}

/// `_fold_ref_coverages` fast path; any ref missing from the mapping defers to the reference,
/// which raises its `KeyError` at the same point.
#[pyfunction]
pub fn checks_fold_ref_coverages<'py>(
    coverage_by_ref: &Bound<'py, PyAny>,
    refs: &Bound<'py, PyTuple>,
    weakest: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let pointer = coverage_by_ref.as_ptr();
    let is_proxy =
        unsafe { ffi::Py_TYPE(pointer) == std::ptr::addr_of_mut!(ffi::PyDictProxy_Type) };
    if refs.is_empty() || !(is_proxy || unsafe { ffi::PyDict_CheckExact(pointer) } != 0) {
        return Ok(None);
    }
    let mut coverages = Vec::with_capacity(refs.len());
    for reference in refs.iter() {
        if !is_exact_str(&reference) {
            return Ok(None);
        }
        match coverage_by_ref.get_item(&reference) {
            Ok(coverage) => coverages.push(coverage),
            Err(_) => return Ok(None),
        }
    }
    fold_distinct(coverages, weakest)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(checks_bind, module)?)?;
    module.add_function(wrap_pyfunction!(checks_basis_ref, module)?)?;
    module.add_function(wrap_pyfunction!(checks_validated_ref_tuple, module)?)?;
    module.add_function(wrap_pyfunction!(checks_sorted_unique, module)?)?;
    module.add_function(wrap_pyfunction!(checks_sorted_facts, module)?)?;
    module.add_function(wrap_pyfunction!(checks_case_ref_coverage, module)?)?;
    module.add_function(wrap_pyfunction!(checks_ref_coverages, module)?)?;
    module.add_function(wrap_pyfunction!(checks_fold_case_coverages, module)?)?;
    module.add_function(wrap_pyfunction!(checks_fold_ref_coverages, module)?)?;
    Ok(())
}
