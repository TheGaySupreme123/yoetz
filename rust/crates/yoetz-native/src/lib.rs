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
mod control_pipeline;
mod bundle_upgrade;
mod ids;
mod models;
mod service;
mod values;
mod walk;
mod schemas;
mod schemas_catalog;
mod mcp_descriptors;
mod fswalks;
mod objects_envelope;
mod secret_memory;
mod sqlite_connection;
mod sqlite_observation;
mod hookcli;
mod observation_local;
mod codex_session_stream;
mod kernel_projections;
mod kernel_reducers;
mod deterministic_checks;
mod observation_advice;
mod work_integrity;
mod events;
mod semantic_case;

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
    bundle_upgrade::register(module)?;
    ids::register(module)?;
    models::register(module)?;
    service::register(module)?;
    values::register(module)?;
    schemas::register(module)?;
    schemas_catalog::register(module)?;
    mcp_descriptors::register(module)?;
    fswalks::register(module)?;
    objects_envelope::register(module)?;
    secret_memory::register(module)?;
    sqlite_connection::register(module)?;
    sqlite_observation::register(module)?;
    hookcli::register(module)?;
    observation_local::register(module)?;
    codex_session_stream::register(module)?;
    kernel_projections::register(module)?;
    kernel_reducers::register(module)?;
    deterministic_checks::register(module)?;
    observation_advice::register(module)?;
    work_integrity::register(module)?;
    events::register(module)?;
    semantic_case::register(module)?;
    Ok(())
}
