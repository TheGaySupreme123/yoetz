//! `yoetz.observability.privacy`'s sensitive-content scanner over Python bytes.
//!
//! The Python wrappers validate inputs, read the module's limit constants at call time, and
//! defer to the reference whenever a scanner dependency was replaced; these functions only run
//! the scan. Large inputs are scanned with the interpreter released: `bytes` is immutable, so
//! the borrowed buffer cannot change underneath the scan.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyTuple};
use yoetz_core::observability::privacy::{
    self as core, Finding, FindingKind, PATTERN_SOURCES, PATTERNS, PRIVATE_KEY_MARKERS,
    RedactPasses, ScanLimits,
};

use crate::registry::Slot;

static SCAN_FINDING: Slot = Slot::new();
static SEVERITY_SECRET: Slot = Slot::new();
static SEVERITY_KEY_MATERIAL: Slot = Slot::new();

/// Inputs at least this large are scanned without holding the interpreter.
const DETACH_BYTES: usize = 16 * 1024;

/// Bind `ScanFinding` and the two `Sensitivity` members findings carry.
#[pyfunction]
pub fn bind_privacy_scan(
    scan_finding: Bound<'_, PyAny>,
    secret: Bound<'_, PyAny>,
    key_material: Bound<'_, PyAny>,
) {
    SCAN_FINDING.set(scan_finding.unbind());
    SEVERITY_SECRET.set(secret.unbind());
    SEVERITY_KEY_MATERIAL.set(key_material.unbind());
}

/// The marker bytes and `(regex source, flags)` pairs the native scanner implements.
#[pyfunction]
pub fn privacy_scan_profile<'py>(
    py: Python<'py>,
) -> (Vec<Bound<'py, PyBytes>>, Vec<(Bound<'py, PyBytes>, u32)>) {
    let markers = PRIVATE_KEY_MARKERS.iter().map(|marker| PyBytes::new(py, marker)).collect();
    let patterns = PATTERN_SOURCES
        .iter()
        .map(|(source, flags)| (PyBytes::new(py, source), *flags))
        .collect();
    (markers, patterns)
}

fn limits(max_findings: i64, chunk_bytes: i64, overlap_bytes: i64) -> PyResult<ScanLimits> {
    ScanLimits::new(max_findings, chunk_bytes, overlap_bytes)
        .ok_or_else(|| PyValueError::new_err("privacy_scan_limits_invalid"))
}

fn run_scan<T: Send>(py: Python<'_>, data: &[u8], work: impl FnOnce(&[u8]) -> T + Send) -> T {
    if data.len() >= DETACH_BYTES {
        py.detach(|| work(data))
    } else {
        work(data)
    }
}

fn finding_object<'py>(py: Python<'py>, finding: &Finding) -> PyResult<Bound<'py, PyAny>> {
    let class = SCAN_FINDING
        .get(py)
        .ok_or_else(|| PyValueError::new_err("privacy_scan_unbound"))?;
    let severity = match finding.kind {
        FindingKind::PrivateKeyMarker => SEVERITY_KEY_MATERIAL.get(py),
        FindingKind::Canary | FindingKind::CredentialPattern => SEVERITY_SECRET.get(py),
    }
    .ok_or_else(|| PyValueError::new_err("privacy_scan_unbound"))?;
    class.call1((finding.kind.as_str(), finding.start, finding.end, severity))
}

/// `scan_for_sensitive_content` after validation: a tuple of `ScanFinding`.
#[pyfunction]
pub fn privacy_scan<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyBytes>,
    canaries: &Bound<'py, PyTuple>,
    max_findings: i64,
    chunk_bytes: i64,
    overlap_bytes: i64,
) -> PyResult<Bound<'py, PyTuple>> {
    let limits = limits(max_findings, chunk_bytes, overlap_bytes)?;
    let canary_objects: Vec<Bound<'py, PyBytes>> = canaries
        .iter()
        .map(|canary| canary.cast_into::<PyBytes>().map_err(PyErr::from))
        .collect::<PyResult<_>>()?;
    let canary_bytes: Vec<&[u8]> = canary_objects
        .iter()
        .map(|canary| canary.as_bytes())
        .collect();
    let findings = run_scan(py, data.as_bytes(), |bytes| {
        core::scan(bytes, &canary_bytes, limits)
    });
    let objects = findings
        .iter()
        .map(|finding| finding_object(py, finding))
        .collect::<PyResult<Vec<_>>>()?;
    PyTuple::new(py, objects)
}

/// `redact_sensitive_content`: `(data, False)` with the same object when nothing was found.
#[pyfunction]
pub fn privacy_redact<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyBytes>,
    max_findings: i64,
    chunk_bytes: i64,
    overlap_bytes: i64,
) -> PyResult<(Bound<'py, PyAny>, bool)> {
    let limits = limits(max_findings, chunk_bytes, overlap_bytes)?;
    match run_scan(py, data.as_bytes(), |bytes| core::redact(bytes, limits)) {
        None => Ok((data.clone().into_any(), false)),
        Some(replaced) => Ok((PyBytes::new(py, &replaced).into_any(), true)),
    }
}

/// Redact until a pass finds nothing, at most `passes` times.
///
/// Returns `(data, False)` (the same object) when the first pass is clean, `(redacted, True)`
/// after a later clean pass, and `None` when every pass still found something.
#[pyfunction]
pub fn privacy_redact_passes<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyBytes>,
    passes: u64,
    max_findings: i64,
    chunk_bytes: i64,
    overlap_bytes: i64,
) -> PyResult<Option<(Bound<'py, PyAny>, bool)>> {
    let limits = limits(max_findings, chunk_bytes, overlap_bytes)?;
    Ok(
        match run_scan(py, data.as_bytes(), |bytes| {
            core::redact_passes(bytes, passes, limits)
        }) {
            RedactPasses::Clean => Some((data.clone().into_any(), false)),
            RedactPasses::Redacted(text) => Some((PyBytes::new(py, &text).into_any(), true)),
            RedactPasses::Incomplete => None,
        },
    )
}

/// `(private_key_marker present, credential_pattern present)` for a canary-free scan.
#[pyfunction]
pub fn privacy_scan_kinds(
    py: Python<'_>,
    data: &Bound<'_, PyBytes>,
    max_findings: i64,
    chunk_bytes: i64,
    overlap_bytes: i64,
) -> PyResult<(bool, bool)> {
    let limits = limits(max_findings, chunk_bytes, overlap_bytes)?;
    Ok(run_scan(py, data.as_bytes(), |bytes| {
        core::sensitive_kinds(bytes, limits)
    }))
}

/// Parity probe: one credential pattern's `finditer` spans over `data` (index into the
/// reference's pattern order). Used by the differential harness, never by the package.
#[pyfunction]
pub fn privacy_pattern_spans(
    index: usize,
    data: &Bound<'_, PyBytes>,
) -> PyResult<Vec<(usize, usize)>> {
    let pattern = *PATTERNS
        .get(index)
        .ok_or_else(|| PyValueError::new_err("privacy_pattern_index_invalid"))?;
    let mut spans = Vec::new();
    pattern.find_iter(data.as_bytes(), &mut |start, end| {
        spans.push((start, end));
        true
    });
    Ok(spans)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bind_privacy_scan, module)?)?;
    module.add_function(wrap_pyfunction!(privacy_scan_profile, module)?)?;
    module.add_function(wrap_pyfunction!(privacy_scan, module)?)?;
    module.add_function(wrap_pyfunction!(privacy_redact, module)?)?;
    module.add_function(wrap_pyfunction!(privacy_redact_passes, module)?)?;
    module.add_function(wrap_pyfunction!(privacy_scan_kinds, module)?)?;
    module.add_function(wrap_pyfunction!(privacy_pattern_spans, module)?)?;
    Ok(())
}
