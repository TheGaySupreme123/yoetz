//! Filesystem and Git walks plus integration digests for `yoetz.adapters` (package `fswalks`).
//!
//! The descriptor-relative walks need POSIX `openat`/`fstatat`; Yoetz supports macOS, Linux, and
//! Windows only through WSL 2, so they are compiled for Unix targets only.

pub mod pytext;

#[cfg(unix)]
pub mod git_subject_state;

pub mod git_change_capture;

#[cfg(unix)]
pub mod cursor_mcp_runtime;

pub mod managed_tree;
pub mod toml_tables;
