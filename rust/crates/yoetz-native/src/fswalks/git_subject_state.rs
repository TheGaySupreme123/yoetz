//! `yoetz.adapters.git_subject_state` walks: `GitSubjectStateAdapter._reject_unsafe_tree_entries`,
//! `_reject_unsupported_index_entries`, and the `_hash_untracked` loop.
//!
//! The Python methods still run Git through their own runner and pass its output here. Every
//! failure is raised as the exception `fail(code, observed)` returns, so the Python module keeps
//! sole ownership of its `_CaptureFailure`/`_GitProcessFailure` classes. The walks run with the
//! GIL released and deliver pending signals (for example `KeyboardInterrupt`) between steps.

use std::collections::HashSet;

use pyo3::prelude::*;
use pyo3::types::{PyByteArray, PyBytes, PyFrozenSet, PySet};
use yoetz_core::fswalks::git_subject_state::{self as core, Failure, Stop};

fn checkpoint() -> Result<(), PyErr> {
    Python::attach(|py| py.check_signals())
}

/// `(code, observed)` for the Python failure factory; `file_limit` names its bound by site.
fn failure_code(failure: Failure, file_limit: &'static str) -> (&'static str, u64) {
    match failure {
        Failure::UnsafeRoot => ("unsafe_root", 0),
        Failure::FileLimit(observed) => (file_limit, observed),
        Failure::ReadLimit => ("read_limit", 0),
        Failure::SymlinkUnsupported => ("symlink_unsupported", 0),
        Failure::SymlinkNotObserved => ("symlink_not_observed", 0),
        Failure::SubmodulePresent => ("submodule_present", 0),
        Failure::InputChanged => ("input_changed", 0),
        Failure::GitFailed => ("git_failed", 0),
        Failure::Os(errno) => ("os_error", u64::try_from(errno).unwrap_or(0)),
    }
}

fn raise(fail: &Bound<'_, PyAny>, failure: Failure, file_limit: &'static str) -> PyErr {
    let (code, observed) = failure_code(failure, file_limit);
    match fail.call1((code, observed)) {
        Ok(exception) => PyErr::from_value(exception),
        Err(error) => error,
    }
}

fn stopped(fail: &Bound<'_, PyAny>, stop: Stop<PyErr>, file_limit: &'static str) -> PyErr {
    match stop {
        Stop::Fail(failure) => raise(fail, failure, file_limit),
        Stop::Abort(error) => error,
    }
}

fn prefix_set(ignored: &Bound<'_, PyAny>) -> PyResult<HashSet<Vec<u8>>> {
    let mut prefixes = HashSet::new();
    let mut admit = |item: Bound<'_, PyAny>| {
        // `bytes in frozenset` only ever matches bytes members.
        if let Ok(bytes) = item.cast::<PyBytes>() {
            prefixes.insert(bytes.as_bytes().to_vec());
        }
    };
    if let Ok(set) = ignored.cast::<PyFrozenSet>() {
        set.iter().for_each(&mut admit);
    } else {
        for item in ignored.cast::<PySet>()?.iter() {
            admit(item);
        }
    }
    Ok(prefixes)
}

/// Copy a Git listing out of its bytearray, then zero the bytearray (the reference's
/// `_overwrite`) once it has parsed: a malformed listing is refused before the overwrite.
fn take_entries(listing: &Bound<'_, PyByteArray>) -> Result<Vec<Vec<u8>>, Failure> {
    let data = listing.to_vec();
    let entries = core::nul_entries(&data).ok_or(Failure::GitFailed)?.into_iter().map(<[u8]>::to_vec).collect();
    // Exclusive access: no other reference reads the buffer while the GIL is held.
    unsafe { listing.as_bytes_mut() }.fill(0);
    Ok(entries)
}

/// `_reject_unsafe_tree_entries` after `_collect_ignored_prefixes`.
#[pyfunction]
pub fn git_reject_tree_entries(
    py: Python<'_>,
    fail: &Bound<'_, PyAny>,
    root: &[u8],
    ignored_prefixes: &Bound<'_, PyAny>,
    max_files: u64,
    path_output_limit: u64,
    expected_uid: u32,
) -> PyResult<()> {
    let prefixes = prefix_set(ignored_prefixes)?;
    let limits = core::TreeLimits { max_files, path_output_limit, expected_uid };
    let root = root.to_vec();
    let result = py.detach(|| core::reject_unsafe_tree_entries(&root, &prefixes, &limits, &mut checkpoint));
    result.map_err(|stop| stopped(fail, stop, "tree_file_limit"))
}

/// `_reject_unsupported_index_entries` after its `ls-files --stage -z` call.
#[pyfunction]
pub fn git_reject_unsupported_index_entries(
    py: Python<'_>,
    fail: &Bound<'_, PyAny>,
    staged: &Bound<'_, PyByteArray>,
    dir_fd: i32,
) -> PyResult<()> {
    let entries = take_entries(staged).map_err(|failure| raise(fail, failure, "tree_file_limit"))?;
    let borrowed: Vec<&[u8]> = entries.iter().map(Vec::as_slice).collect();
    let tracked = core::check_index_entries(&borrowed).map_err(|failure| raise(fail, failure, "tree_file_limit"))?;
    let result = py.detach(|| core::stat_tracked_paths(dir_fd, &tracked, &mut checkpoint));
    result.map_err(|stop| stopped(fail, stop, "tree_file_limit"))
}

/// `_hash_untracked` after its `ls-files --others --exclude-standard -z` call.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn git_hash_untracked(
    py: Python<'_>,
    fail: &Bound<'_, PyAny>,
    inventory: &Bound<'_, PyByteArray>,
    dir_fd: i32,
    domain: &[u8],
    max_files: u64,
    max_hash_bytes: u64,
    already_hashed: u64,
    read_chunk: usize,
    expected_uid: u32,
) -> PyResult<(String, u64, usize)> {
    let entries = take_entries(inventory).map_err(|failure| raise(fail, failure, "untracked_file_limit"))?;
    if entries.len() as u64 > max_files {
        return Err(raise(fail, Failure::FileLimit(entries.len() as u64), "untracked_file_limit"));
    }
    let borrowed: Vec<&[u8]> = entries.iter().map(Vec::as_slice).collect();
    let limits = core::UntrackedLimits { max_hash_bytes, already_hashed, read_chunk, expected_uid };
    let domain = domain.to_vec();
    let result = py.detach(|| core::hash_untracked(dir_fd, &domain, &borrowed, &limits, &mut checkpoint));
    let digest = result.map_err(|stop| stopped(fail, stop, "untracked_file_limit"))?;
    Ok((digest.digest, digest.total_bytes, entries.len()))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(git_reject_tree_entries, module)?)?;
    module.add_function(wrap_pyfunction!(git_reject_unsupported_index_entries, module)?)?;
    module.add_function(wrap_pyfunction!(git_hash_untracked, module)?)?;
    Ok(())
}
