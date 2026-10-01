//! `yoetz_native`: the optional Rust accelerator for the Yoetz Python package.
//!
//! The Python package never requires this module. `yoetz/_native.py` imports it when present,
//! checks [`INTERFACE_VERSION`], and each pure Python module then binds its public functions to
//! the native twins registered here. Every native function reproduces its Python reference's
//! output bytes and refusal identity exactly; the Python test suite is the parity oracle.

use pyo3::prelude::*;

mod canonical;
mod registry;

#[pymodule]
fn yoetz_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("INTERFACE_VERSION", yoetz_core::INTERFACE_VERSION)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add_function(wrap_pyfunction!(registry::bind_protocol_value_error, module)?)?;

    module.add_function(wrap_pyfunction!(canonical::bind_canonical_fragment, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::canonical_encode, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::canonical_text, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::canonical_fragment_parts, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::canonical_digest, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::ensure_canonical_value, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::container_levels, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::validate_string, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::encode_string, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::ensure_canonical_set, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::canonical_integer_string, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::parse_canonical_integer_string, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::request_digest, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::sha256_prefixed, module)?)?;
    module.add_function(wrap_pyfunction!(canonical::strict_json_parse, module)?)?;
    Ok(())
}
