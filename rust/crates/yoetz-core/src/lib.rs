//! Pure Rust core of Yoetz.
//!
//! Every module here is a behavior-identical twin of a pure Python module under `src/yoetz/`.
//! The Python package stays the authority (ADRs, schemas, and the Python test suite lock the
//! contract); this crate exists so the same computation runs natively, both behind the
//! `yoetz_native` accelerator and inside native command-line tools.

pub mod application;
pub mod domain;
pub mod importers;
pub mod observability;
pub mod fswalks;
pub mod objects;
pub mod protocol;
pub mod shlex;

/// Interface revision shared with `yoetz/_native.py`. A mismatch disables the accelerator.
pub const INTERFACE_VERSION: u32 = 1;
