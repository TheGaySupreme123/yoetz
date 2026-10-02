//! Advice identities of `yoetz.application.observation_advice`: `stable_advice_finding_id`,
//! `canonical_material`, `_suppression_identity`, `_delivery_condition_identity`, and the
//! digest of `advice_delivery_identity`.
//!
//! Each twin answers only for exact `str` inputs it can encode byte-identically and returns
//! `None` otherwise, so the Python wrapper runs the reference and raises its own refusal.

use std::sync::Mutex;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use yoetz_core::application::observation_materialize::{prefixed_hex48, stable_uuid4};
use yoetz_core::protocol::canonical::{self as canonical, Value};

use crate::observation_materialize::{exact_str, exact_strs};

struct Domains {
    finding: Vec<u8>,
    finding_prefix: String,
    suppression: Vec<u8>,
    delivery: Vec<u8>,
}

static DOMAINS: Mutex<Option<Domains>> = Mutex::new(None);

/// Bind the finding, suppression, and delivery domains and the finding-id prefix.
#[pyfunction]
pub fn advice_identity_bind(
    finding: &Bound<'_, PyBytes>,
    finding_prefix: &str,
    suppression: &Bound<'_, PyBytes>,
    delivery: &Bound<'_, PyBytes>,
) {
    *DOMAINS.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(Domains {
        finding: finding.as_bytes().to_vec(),
        finding_prefix: finding_prefix.to_owned(),
        suppression: suppression.as_bytes().to_vec(),
        delivery: delivery.as_bytes().to_vec(),
    });
}

fn with_domains<T>(body: impl FnOnce(&Domains) -> Option<T>) -> PyResult<Option<T>> {
    let guard = DOMAINS.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let Some(domains) = guard.as_ref() else {
        return Err(PyValueError::new_err("advice_identity_unbound"));
    };
    Ok(body(domains))
}

/// `stable_advice_finding_id(rule_code, detail_token, evidence_digest)`.
#[pyfunction]
pub fn advice_stable_finding_id(
    rule_code: &Bound<'_, PyAny>,
    detail_token: &Bound<'_, PyAny>,
    evidence_digest: &Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    let (Some(rule), Some(detail), Some(evidence)) = (exact_str(rule_code), exact_str(detail_token), exact_str(evidence_digest)) else {
        return Ok(None);
    };
    with_domains(|domains| {
        let parts: [&[u8]; 6] = [&domains.finding, rule.as_bytes(), b"\0", detail.as_bytes(), b"\0", evidence.as_bytes()];
        Some(domains.finding_prefix.clone() + &stable_uuid4(parts))
    })
}

fn material(finding_ids: &Bound<'_, PyAny>, evidence_digest: &Bound<'_, PyAny>, next_action: &Bound<'_, PyAny>) -> Option<Vec<u8>> {
    let ids = exact_strs(finding_ids, true)?;
    let evidence = exact_str(evidence_digest)?;
    let action = exact_str(next_action)?;
    let mut out = Vec::with_capacity(ids.iter().map(|id| id.len() + 1).sum::<usize>() + evidence.len() + action.len() + 2);
    for (index, id) in ids.iter().enumerate() {
        if index > 0 {
            out.push(b',');
        }
        out.extend_from_slice(id.as_bytes());
    }
    out.push(0);
    out.extend_from_slice(evidence.as_bytes());
    out.push(0);
    out.extend_from_slice(action.as_bytes());
    Some(out)
}

/// `canonical_material(finding_ids, evidence_digest, next_action)`.
#[pyfunction]
pub fn advice_canonical_material<'py>(
    py: Python<'py>,
    finding_ids: &Bound<'py, PyAny>,
    evidence_digest: &Bound<'py, PyAny>,
    next_action: &Bound<'py, PyAny>,
) -> Option<Bound<'py, PyBytes>> {
    material(finding_ids, evidence_digest, next_action).map(|bytes| PyBytes::new(py, &bytes))
}

/// `_suppression_identity(finding_ids, evidence_digest, next_action)`.
#[pyfunction]
pub fn advice_suppression_identity(
    finding_ids: &Bound<'_, PyAny>,
    evidence_digest: &Bound<'_, PyAny>,
    next_action: &Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    let Some(bytes) = material(finding_ids, evidence_digest, next_action) else {
        return Ok(None);
    };
    with_domains(|domains| Some(prefixed_hex48("suppress-", [domains.suppression.as_slice(), bytes.as_slice()])))
}

fn text_members(pairs: &[(&str, &Bound<'_, PyAny>)]) -> Option<Vec<(String, Value)>> {
    pairs
        .iter()
        .map(|(key, value)| exact_str(value).map(|text| ((*key).to_owned(), Value::Str(text.to_owned()))))
        .collect()
}

/// `_delivery_condition_identity(candidate)` over the candidate's two fields.
#[pyfunction]
pub fn advice_condition_identity(detail_token: &Bound<'_, PyAny>, rule_code: &Bound<'_, PyAny>) -> Option<String> {
    let members = text_members(&[("detail_token", detail_token), ("rule_code", rule_code)])?;
    let encoded = canonical::encode(&Value::Object(members)).ok()?;
    Some(prefixed_hex48("condition-", [encoded.as_slice()]))
}

/// The `advice_delivery_identity` digest of one condition; `condition_identity` is `None` for
/// the item-less form, whose condition has no such member.
#[pyfunction]
pub fn advice_delivery_identity(
    condition_identity: &Bound<'_, PyAny>,
    detail: &Bound<'_, PyAny>,
    next_action: &Bound<'_, PyAny>,
    rule_code: &Bound<'_, PyAny>,
    summary: &Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    let mut pairs = vec![("detail", detail), ("next_action", next_action), ("rule_code", rule_code), ("summary", summary)];
    if !condition_identity.is_none() {
        pairs.push(("condition_identity", condition_identity));
    }
    let Some(members) = text_members(&pairs) else {
        return Ok(None);
    };
    let condition = Value::Object(vec![("condition".to_owned(), Value::Object(members))]);
    let Ok(encoded) = canonical::encode(&condition) else {
        return Ok(None);
    };
    with_domains(|domains| Some(prefixed_hex48("deliver-", [domains.delivery.as_slice(), encoded.as_slice()])))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(advice_identity_bind, module)?)?;
    module.add_function(wrap_pyfunction!(advice_stable_finding_id, module)?)?;
    module.add_function(wrap_pyfunction!(advice_canonical_material, module)?)?;
    module.add_function(wrap_pyfunction!(advice_suppression_identity, module)?)?;
    module.add_function(wrap_pyfunction!(advice_condition_identity, module)?)?;
    module.add_function(wrap_pyfunction!(advice_delivery_identity, module)?)?;
    Ok(())
}
