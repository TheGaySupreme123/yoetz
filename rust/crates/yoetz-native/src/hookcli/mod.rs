//! Per-hook CLI kernels: `yoetz.cli.hook_io`, `yoetz.cli.observe_hooks`, `yoetz.cli.hook_timing`.
//!
//! Functions that may need the Python reference return `NotImplemented`; the module's
//! `_bind_native` wrapper then runs its reference with the same arguments.

use pyo3::prelude::*;

mod hook_io;
mod hook_timing;
mod observe_hooks;

/// The "run the Python reference" answer.
fn defer(py: Python<'_>) -> Bound<'_, PyAny> {
    py.NotImplemented().into_bound(py)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    hook_io::register(module)?;
    observe_hooks::register(module)?;
    hook_timing::register(module)?;
    Ok(())
}
