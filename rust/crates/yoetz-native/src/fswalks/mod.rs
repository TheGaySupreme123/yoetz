//! Package `fswalks`: filesystem and Git walks plus integration digests for `yoetz.adapters`.
//!
//! One binding module per Python module it accelerates.

use pyo3::prelude::*;

#[cfg(unix)]
mod cursor_mcp_runtime;
mod git_change_capture;
#[cfg(unix)]
mod git_subject_state;
mod managed_tree;
mod toml_tables;

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    #[cfg(unix)]
    git_subject_state::register(module)?;
    #[cfg(unix)]
    cursor_mcp_runtime::register(module)?;
    git_change_capture::register(module)?;
    toml_tables::register(module)?;
    managed_tree::register(module)?;
    Ok(())
}
