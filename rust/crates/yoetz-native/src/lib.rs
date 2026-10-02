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
mod objects_envelope;
mod secret_memory;
mod sqlite_connection;
mod sqlite_observation;

#[pymodule]
fn yoetz_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("INTERFACE_VERSION", yoetz_core::INTERFACE_VERSION)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add_function(wrap_pyfunction!(registry::bind_protocol_value_error, module)?)?;

    canonical::register(module)?;
    objects_envelope::register(module)?;
    secret_memory::register(module)?;
    sqlite_connection::register(module)?;
    sqlite_observation::register(module)?;
    Ok(())
}
