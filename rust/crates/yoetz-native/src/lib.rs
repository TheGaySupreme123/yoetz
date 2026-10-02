//! `yoetz_native`: the optional Rust accelerator for the Yoetz Python package.
//!
//! The Python package never requires this module. `yoetz/_native.py` imports it when present,
//! checks `INTERFACE_VERSION`, and each pure Python module then binds its public functions to
//! the native twins registered here. Every native function reproduces its Python reference's
//! output bytes and refusal identity exactly; the Python test suite is the parity oracle.
//!
//! One binding module per Python module it accelerates; each exposes `register`.

use pyo3::prelude::*;

mod registry;

mod canonical;
mod observability_privacy;
mod missing_for_assessment;
mod observation;
mod observation_selection;
mod shlex;
mod jsonframes;
mod control_protocol;
mod ids;
mod models;
mod service;
mod values;
mod walk;

#[pymodule]
fn yoetz_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("INTERFACE_VERSION", yoetz_core::INTERFACE_VERSION)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add_function(wrap_pyfunction!(registry::bind_protocol_value_error, module)?)?;

    canonical::register(module)?;
    observability_privacy::register(module)?;
    shlex::register(module)?;
    observation::register(module)?;
    observation_selection::register(module)?;
    missing_for_assessment::register(module)?;
    jsonframes::register(module)?;
    control_protocol::register(module)?;
    ids::register(module)?;
    models::register(module)?;
    service::register(module)?;
    values::register(module)?;
    Ok(())
}
