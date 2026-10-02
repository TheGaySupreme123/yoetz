//! `yoetz.kernel.policies.work_integrity._unresolved_action_scan` over live projection maps.
//!
//! The reference compares every unlinked action with every later action (quadratic in Python
//! objects). This twin reads each action once, interns its subject strings, and runs the same
//! comparison over integers. It returns exactly the reference's `(action_id, later_ids)` pairs in
//! the reference's order, or `None` whenever an input is not the plain shape it can read
//! exactly; the Python reference then runs and raises whatever it raises.

use std::collections::{HashMap, HashSet};

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyList, PyString, PyTuple};

fn exact_text<'a>(value: &'a Bound<'_, PyAny>) -> Option<&'a str> {
    if unsafe { ffi::PyUnicode_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    unsafe { value.cast_unchecked::<PyString>() }.to_str().ok()
}

/// The values of a mapping through its own `.values()` / `.items()`, as the reference reads it.
fn mapping_view<'py>(mapping: &Bound<'py, PyAny>, method: &str) -> Option<Vec<Bound<'py, PyAny>>> {
    let mut out = Vec::new();
    for item in mapping.call_method0(method).ok()?.try_iter().ok()? {
        out.push(item.ok()?);
    }
    Some(out)
}

/// `_action_subject_key`: which subject family an action names and its interned members.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Family {
    Obligations,
    RequestedItems,
}

struct Action<'py> {
    id: Bound<'py, PyAny>,
    id_text: String,
    frontier: i64,
    subjects: Option<(Family, Vec<u32>)>,
}

/// Exact `str` members of an exact tuple, interned; `None` when any member is not plain.
fn interned_members(value: &Bound<'_, PyAny>, interner: &mut HashMap<String, u32>) -> Option<Vec<u32>> {
    if unsafe { ffi::PyTuple_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    let tuple = unsafe { value.cast_unchecked::<PyTuple>() };
    let mut members = Vec::with_capacity(tuple.len());
    for item in tuple.iter() {
        let text = exact_text(&item)?;
        let next = interner.len() as u32;
        members.push(*interner.entry(text.to_owned()).or_insert(next));
    }
    Some(members)
}

#[pyfunction]
pub fn integrity_unresolved_action_scan<'py>(
    py: Python<'py>,
    actions: &Bound<'py, PyAny>,
    results: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyList>> {
    let mut linked: HashSet<String> = HashSet::new();
    for record in mapping_view(results, "values")? {
        let payload = record.getattr("payload").ok()?;
        if payload.is_none() {
            continue;
        }
        let action_id = payload.getattr("action_id").ok()?;
        linked.insert(exact_text(&action_id)?.to_owned());
    }
    let mut interner: HashMap<String, u32> = HashMap::new();
    let mut ordered: Vec<(Action<'py>, bool)> = Vec::new();
    for pair in mapping_view(actions, "items")? {
        let (id, record): (Bound<'py, PyAny>, Bound<'py, PyAny>) = pair.extract().ok()?;
        let payload = record.getattr("payload").ok()?;
        if payload.is_none() {
            continue;
        }
        // `_ascii(item[0])` in the sort key: the reference raises for a non-ASCII id.
        let id_text = exact_text(&id)?.to_owned();
        if !id_text.is_ascii() {
            return None;
        }
        let frontier_object = record.getattr("source_frontier").ok()?;
        if unsafe { ffi::PyLong_CheckExact(frontier_object.as_ptr()) } == 0 {
            return None;
        }
        let frontier: i64 = frontier_object.extract().ok()?;
        let obligations = interned_members(&payload.getattr("obligation_refs").ok()?, &mut interner)?;
        let items = interned_members(&payload.getattr("attempted_items").ok()?, &mut interner)?;
        let subjects = if !obligations.is_empty() {
            Some((Family::Obligations, obligations))
        } else if !items.is_empty() {
            Some((Family::RequestedItems, items))
        } else {
            None
        };
        let is_linked = linked.contains(&id_text);
        ordered.push((Action { id, id_text, frontier, subjects }, is_linked));
    }
    ordered.sort_by(|(left, _), (right, _)| {
        (left.frontier, left.id_text.as_bytes()).cmp(&(right.frontier, right.id_text.as_bytes()))
    });
    // Only an action naming subjects can be disjoint from another, so only those are candidates.
    let keyed: Vec<usize> = (0..ordered.len()).filter(|index| ordered[*index].0.subjects.is_some()).collect();
    let output = PyList::empty(py);
    for (position, &index) in keyed.iter().enumerate() {
        let (action, is_linked) = &ordered[index];
        if *is_linked {
            continue;
        }
        let Some((family, members)) = &action.subjects else {
            continue;
        };
        let own: HashSet<u32> = members.iter().copied().collect();
        let mut later: Vec<&Bound<'py, PyAny>> = Vec::new();
        // `keyed` follows the sorted order, so every later frontier sits after this position.
        for &other in &keyed[position + 1..] {
            let candidate = &ordered[other].0;
            if candidate.frontier <= action.frontier {
                continue;
            }
            if let Some((other_family, other_members)) = &candidate.subjects {
                if other_family == family && other_members.iter().all(|member| !own.contains(member)) {
                    later.push(&candidate.id);
                }
            }
        }
        if later.is_empty() {
            continue;
        }
        let later = PyTuple::new(py, later).ok()?;
        output.append((&action.id, later)).ok()?;
    }
    Some(output)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(integrity_unresolved_action_scan, module)?)?;
    Ok(())
}
