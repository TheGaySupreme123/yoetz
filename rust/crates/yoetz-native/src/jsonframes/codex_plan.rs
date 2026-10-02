//! `yoetz.adapters.importers.codex_plan._batch_partition` over live port records.

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyList, PyTuple};
use yoetz_core::importers::codex_plan::{self, Candidate};

/// An exact `int` that fits `i64`, else `None` (the reference then runs).
fn exact_i64(value: &Bound<'_, PyAny>) -> Option<i64> {
    if unsafe { ffi::PyLong_CheckExact(value.as_ptr()) } == 0 {
        return None;
    }
    let mut overflow: std::os::raw::c_int = 0;
    let number = unsafe { ffi::PyLong_AsLongLongAndOverflow(value.as_ptr(), &mut overflow) };
    // An exact int cannot fail conversion except by overflow, which is flagged, not raised.
    (overflow == 0).then_some(number)
}

fn attribute_i64(record: &Bound<'_, PyAny>, name: &str) -> PyResult<Option<i64>> {
    Ok(exact_i64(&record.getattr(name)?))
}

/// The members of an exact `tuple` or `list`, else `None`.
fn sequence<'py>(value: &Bound<'py, PyAny>) -> Option<Vec<Bound<'py, PyAny>>> {
    let pointer = value.as_ptr();
    if unsafe { ffi::PyTuple_CheckExact(pointer) } != 0 {
        return Some(unsafe { value.cast_unchecked::<PyTuple>() }.iter().collect());
    }
    if unsafe { ffi::PyList_CheckExact(pointer) } != 0 {
        return Some(unsafe { value.cast_unchecked::<PyList>() }.iter().collect());
    }
    None
}

/// Per batch, `(outcomes, gaps)` tuples selected exactly as the reference selects them, or
/// `None` when a record holds anything but exact integers where the sweep reads them.
#[pyfunction]
pub fn codex_plan_batch_partition<'py>(
    py: Python<'py>,
    candidates: &Bound<'py, PyAny>,
    outcomes: &Bound<'py, PyAny>,
    gaps: &Bound<'py, PyAny>,
    draft_count: i64,
    batch_size: i64,
) -> PyResult<Option<Bound<'py, PyList>>> {
    let (Some(candidate_rows), Some(outcome_rows), Some(gap_rows)) =
        (sequence(candidates), sequence(outcomes), sequence(gaps))
    else {
        return Ok(None);
    };
    let Ok(draft_count) = usize::try_from(draft_count) else {
        return Ok(None);
    };
    let mut spans: Vec<Candidate> = Vec::with_capacity(candidate_rows.len());
    for row in &candidate_rows {
        let (Some(index), Some(start), Some(end)) = (
            attribute_i64(row, "candidate_index")?,
            attribute_i64(row, "byte_start")?,
            attribute_i64(row, "byte_end")?,
        ) else {
            return Ok(None);
        };
        spans.push((index, start, end));
    }
    let mut outcome_indexes: Vec<Vec<i64>> = Vec::with_capacity(outcome_rows.len());
    for row in &outcome_rows {
        let indexes = row.getattr("candidate_indexes")?;
        if unsafe { ffi::PyTuple_CheckExact(indexes.as_ptr()) } == 0 {
            return Ok(None);
        }
        let indexes = unsafe { indexes.cast_unchecked::<PyTuple>() };
        let mut values = Vec::with_capacity(indexes.len());
        for item in indexes.iter() {
            let Some(value) = exact_i64(&item) else {
                return Ok(None);
            };
            values.push(value);
        }
        outcome_indexes.push(values);
    }
    let mut gap_spans: Vec<(i64, i64)> = Vec::with_capacity(gap_rows.len());
    for row in &gap_rows {
        let (Some(start), Some(end)) = (attribute_i64(row, "byte_start")?, attribute_i64(row, "byte_end")?)
        else {
            return Ok(None);
        };
        gap_spans.push((start, end));
    }
    let Some(selections) =
        codex_plan::partition_batches(&spans, draft_count, batch_size, &outcome_indexes, &gap_spans)
    else {
        return Ok(None);
    };
    let result = PyList::empty(py);
    for selection in selections {
        let selected_outcomes =
            PyTuple::new(py, selection.outcomes.iter().map(|&position| &outcome_rows[position]))?;
        let selected_gaps = PyTuple::new(py, selection.gaps.iter().map(|&position| &gap_rows[position]))?;
        result.append((selected_outcomes, selected_gaps))?;
    }
    Ok(Some(result))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(codex_plan_batch_partition, module)?)?;
    Ok(())
}
