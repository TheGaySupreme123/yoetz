//! `yoetz.ports.objects.ObjectRootSnapshot.live_object_ids` validation: an exact `tuple` of
//! exact `str` in strictly increasing UTF-8 byte order (`_sorted_unique_ascii`), each a valid
//! object id. The twin only accepts; the Python wrapper runs the reference for anything else,
//! which raises its exact refusal and exception chain.

use pyo3::prelude::*;

use crate::ports_importer::{Grammar, exact_str_members};

/// Whether `values` passes `_sorted_unique_ascii` and every member validates for `kind`.
#[pyfunction]
pub fn objects_live_object_ids_valid(values: &Bound<'_, PyAny>, kind: &Bound<'_, PyAny>) -> bool {
    let (Some(grammar), Some(members)) = (Grammar::of(kind), exact_str_members(values, usize::MAX))
    else {
        return false;
    };
    let mut previous: Option<&str> = None;
    for member in &members {
        let Ok(text) = member.to_str() else {
            return false;
        };
        if previous.is_some_and(|before| text.as_bytes() <= before.as_bytes())
            || !grammar.accepts(member)
        {
            return false;
        }
        previous = Some(text);
    }
    true
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(objects_live_object_ids_valid, module)?)?;
    Ok(())
}
