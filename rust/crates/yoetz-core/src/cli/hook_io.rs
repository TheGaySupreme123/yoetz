//! Cursor hook ingress parsing, the twin of `yoetz.cli.hook_io._parse_cursor_hook_document`,
//! `_normalize_cursor_value`, and `_cursor_identity_payload`.
//!
//! The reference runs `json.loads` with an `object_pairs_hook` (duplicate keys), `parse_float=
//! Decimal`, a bounded `parse_int`, and a refusing `parse_constant`, then walks the result twice:
//! once to replace vendor decimals (keeping only a top-level `duration`, truncated to integer
//! milliseconds) and once through `ensure_canonical_value`. Its refusals therefore come in three
//! phases: anything the scan raises (first in document order), then the first normalization
//! refusal (pre-order, which is document order), then the first canonical-profile refusal (keys
//! in insertion order before values in UTF-16 key order). This module reproduces all three in
//! one scan: scan refusals abort immediately, normalization refusals are remembered and raised
//! after a clean scan, and only a document holding an invalid string pays for the canonical-order
//! walk.

use std::borrow::Cow;
use std::cmp::Ordering;
use std::collections::HashSet;

use crate::protocol::canonical::{
    self, DUPLICATE_OBJECT_KEY, FLOAT_FORBIDDEN, INTEGER_OUT_OF_SAFE_RANGE, LONE_SURROGATE,
    MAX_JSON_DEPTH, MAX_SAFE_INTEGER, NESTING_TOO_DEEP, NUL_BYTE_FORBIDDEN, Reason,
    UNSUPPORTED_JSON_TYPE,
};
use crate::protocol::json::{self, JsonSink, JsonText};

pub const INVALID_EVENT_VALUE_TYPE: Reason = "invalid_event_value_type";
pub const INVALID_DURATION: Reason = "invalid_duration";

const MAX_IDENTITY_STRING_BYTES: usize = 8_192;
const MAX_IDENTITY_DEPTH: usize = 6;
const MAX_IDENTITY_ITEMS: usize = 32;

const IDENTITY_STRINGS: [&str; 27] = [
    "action",
    "claim_kind",
    "conversation_id",
    "cursor_version",
    "decision",
    "failure_type",
    "filePath",
    "file_path",
    "generation_id",
    "hook_event_name",
    "id",
    "mapping_hint",
    "model",
    "model_id",
    "outcome",
    "path",
    "permission_decision",
    "permission_kind",
    "result_status",
    "session_id",
    "source",
    "status",
    "target_file",
    "tool_call_id",
    "tool_name",
    "tool_use_id",
    "value",
];
const IDENTITY_BOOLS: [&str; 16] = [
    "canceled",
    "cancelled",
    "denied",
    "failed",
    "interrupted",
    "isCanceled",
    "isError",
    "isInterrupted",
    "is_cancelled",
    "is_denied",
    "is_error",
    "is_interrupt",
    "is_interrupted",
    "ok",
    "permission_denied",
    "success",
];
const IDENTITY_INTS: [&str; 5] = ["duration", "exitCode", "exitStatus", "exit_code", "exit_status"];
const IDENTITY_OBJECTS: [&str; 8] = [
    "data",
    "result",
    "result_json",
    "structuredContent",
    "structured_content",
    "tool_input",
    "tool_output",
    "tool_response",
];

/// An object key. `Wide` holds a key with at least one lone surrogate, as code points.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum CursorKey<'a> {
    Text(Cow<'a, str>),
    Wide(Vec<u32>),
}

impl CursorKey<'_> {
    /// The profile refusal this key draws (`_validate_string`), if any.
    fn fault(&self) -> Option<Reason> {
        match self {
            CursorKey::Text(text) => text.contains('\0').then_some(NUL_BYTE_FORBIDDEN),
            CursorKey::Wide(points) => wide_fault(points),
        }
    }
}

/// A normalized Cursor value. Vendor decimals are already `Null` (or the integer duration).
#[derive(Debug, Clone, PartialEq)]
pub enum CursorValue<'a> {
    Null,
    Bool(bool),
    Int(i64),
    Str(Cow<'a, str>),
    /// A string the canonical profile refuses, with the refusal it draws.
    BadStr(Reason),
    Array(Vec<CursorValue<'a>>),
    Object(Vec<(CursorKey<'a>, CursorValue<'a>)>),
}

fn wide_fault(points: &[u32]) -> Option<Reason> {
    points.iter().find_map(|point| match *point {
        0 => Some(NUL_BYTE_FORBIDDEN),
        0xD800..=0xDFFF => Some(LONE_SURROGATE),
        _ => None,
    })
}

/// `Decimal(literal)` acceptance under the stdlib's exact-conversion rule, and the value's
/// sign, significant digits (no leading zeros), and exponent.
struct DecimalLiteral {
    negative: bool,
    /// Significant coefficient digits; empty for zero.
    digits: String,
    exponent: i128,
}

/// libmpdec `MAX_EMAX` and `MIN_ETINY` (64-bit): `Decimal(str)` raises `InvalidOperation` for a
/// literal whose conversion would be inexact, rounded, or clamped under the max context.
const DECIMAL_EMAX: i128 = 999_999_999_999_999_999;
const DECIMAL_ETINY: i128 = -1_999_999_999_999_999_997;
/// Any exponent this large is already far outside `[ETINY, EMAX]`.
const EXPONENT_CEILING: i128 = 1_000_000_000_000_000_000_000_000;

fn decimal_literal(literal: &str) -> Option<DecimalLiteral> {
    let (negative, body) = match literal.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, literal),
    };
    let (mantissa, exponent_text) = match body.find(['e', 'E']) {
        Some(at) => (&body[..at], Some(&body[at + 1..])),
        None => (body, None),
    };
    let (integer, fraction) = match mantissa.find('.') {
        Some(at) => (&mantissa[..at], &mantissa[at + 1..]),
        None => (mantissa, ""),
    };
    let mut exponent: i128 = 0;
    if let Some(text) = exponent_text {
        let (sign, magnitude) = match text.as_bytes().first() {
            Some(b'-') => (-1, &text[1..]),
            Some(b'+') => (1, &text[1..]),
            _ => (1, text),
        };
        let magnitude = magnitude.trim_start_matches('0');
        let value = if magnitude.len() > 24 {
            EXPONENT_CEILING
        } else if magnitude.is_empty() {
            0
        } else {
            magnitude.parse::<i128>().ok()?
        };
        exponent = sign * value;
    }
    exponent -= fraction.len() as i128;
    let mut digits = String::with_capacity(integer.len() + fraction.len());
    digits.push_str(integer);
    digits.push_str(fraction);
    let significant = digits.trim_start_matches('0').to_owned();
    if significant.is_empty() {
        if !(DECIMAL_ETINY..=DECIMAL_EMAX).contains(&exponent) {
            return None;
        }
    } else if exponent + significant.len() as i128 - 1 > DECIMAL_EMAX || exponent < DECIMAL_ETINY {
        return None;
    }
    Some(DecimalLiteral { negative, digits: significant, exponent })
}

/// `_normalize_cursor_duration` for an accepted decimal literal.
fn cursor_duration(value: &DecimalLiteral) -> Result<i64, Reason> {
    if value.negative {
        // Negative nonzero, or negative zero: both are refused before the range check.
        return Err(INVALID_DURATION);
    }
    if value.digits.is_empty() {
        return Ok(0);
    }
    let whole_digits = value.digits.len() as i128 + value.exponent;
    if whole_digits <= 0 {
        return Ok(0);
    }
    if whole_digits > 16 {
        return Err(INTEGER_OUT_OF_SAFE_RANGE);
    }
    let whole_digits = whole_digits as usize;
    let mut whole: i64 = 0;
    for index in 0..whole_digits {
        let digit = value.digits.as_bytes().get(index).map_or(0, |byte| i64::from(byte - b'0'));
        whole = whole * 10 + digit;
    }
    let fraction_nonzero = value.digits.len() > whole_digits
        && value.digits.as_bytes()[whole_digits..].iter().any(|byte| *byte != b'0');
    if whole > MAX_SAFE_INTEGER || (whole == MAX_SAFE_INTEGER && fraction_nonzero) {
        return Err(INTEGER_OUT_OF_SAFE_RANGE);
    }
    Ok(whole)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Container {
    Array,
    Object,
}

struct CursorSink<'a> {
    text: &'a str,
    /// Open containers, innermost last.
    stack: Vec<Container>,
    /// The member key at the root object's level is `duration`.
    root_duration_key: bool,
    /// The first normalization refusal, raised only after a clean scan.
    deferred: Option<Reason>,
    /// Some string or key draws a canonical-profile refusal.
    faulty: bool,
}

impl<'a> CursorSink<'a> {
    fn defer(&mut self, reason: Reason) {
        if self.deferred.is_none() {
            self.deferred = Some(reason);
        }
    }

    /// Recover the input-lifetime slice for a borrowed string (always a subslice of `text`).
    fn borrowed(&self, slice: &str) -> Cow<'a, str> {
        let base = self.text.as_ptr() as usize;
        let at = slice.as_ptr() as usize;
        if at >= base && at + slice.len() <= base + self.text.len() {
            if let Some(original) = self.text.get(at - base..at - base + slice.len()) {
                return Cow::Borrowed(original);
            }
        }
        Cow::Owned(slice.to_owned())
    }

    fn open(&mut self, kind: Container) {
        // ``_normalize_cursor_value`` refuses a container at depth >= MAX_JSON_DEPTH.
        if self.stack.len() >= MAX_JSON_DEPTH {
            self.defer(NESTING_TOO_DEEP);
        }
        self.stack.push(kind);
    }

    /// Close a container; one past the depth bound is already a deferred refusal, so its
    /// content is dropped here and the kept tree never nests deeper than the bound.
    fn close(&mut self) -> bool {
        self.stack.pop();
        self.stack.len() < MAX_JSON_DEPTH
    }
}

fn has_duplicate(members: &[(CursorKey<'_>, CursorValue<'_>)]) -> bool {
    if members.len() < 2 {
        return false;
    }
    if members.len() <= 8 {
        for (index, (key, _)) in members.iter().enumerate() {
            if members[..index].iter().any(|(prior, _)| prior == key) {
                return true;
            }
        }
        return false;
    }
    let mut seen: HashSet<&CursorKey<'_>> = HashSet::with_capacity(members.len());
    members.iter().any(|(key, _)| !seen.insert(key))
}

impl<'a> JsonSink for CursorSink<'a> {
    type Value = CursorValue<'a>;
    type Key = CursorKey<'a>;
    type Array = Vec<CursorValue<'a>>;
    type Object = Vec<(CursorKey<'a>, CursorValue<'a>)>;
    type Error = Reason;

    fn fail(&mut self, reason: Reason) -> Reason {
        reason
    }
    fn null(&mut self) -> Result<Self::Value, Reason> {
        Ok(CursorValue::Null)
    }
    fn boolean(&mut self, value: bool) -> Result<Self::Value, Reason> {
        Ok(CursorValue::Bool(value))
    }
    fn integer(&mut self, literal: &str) -> Result<Self::Value, Reason> {
        // ``_parse_cursor_integer``: ``int()`` failures and range both name the safe range.
        if literal == "-0" {
            return Err(FLOAT_FORBIDDEN);
        }
        let digits = literal.strip_prefix('-').unwrap_or(literal);
        if digits.len() > 18 {
            return Err(INTEGER_OUT_OF_SAFE_RANGE);
        }
        let value: i64 = literal.parse().map_err(|_| INTEGER_OUT_OF_SAFE_RANGE)?;
        if !(-MAX_SAFE_INTEGER..=MAX_SAFE_INTEGER).contains(&value) {
            return Err(INTEGER_OUT_OF_SAFE_RANGE);
        }
        Ok(CursorValue::Int(value))
    }
    fn float(&mut self, literal: &str) -> Result<Self::Value, Reason> {
        if matches!(literal, "NaN" | "Infinity" | "-Infinity") {
            return Err(FLOAT_FORBIDDEN);
        }
        // ``Decimal(literal)`` raising ``InvalidOperation`` is a scan-time refusal.
        let decimal = decimal_literal(literal).ok_or(FLOAT_FORBIDDEN)?;
        match self.stack.as_slice() {
            [] => {
                self.defer(FLOAT_FORBIDDEN);
                Ok(CursorValue::Null)
            }
            [Container::Object] if self.root_duration_key => match cursor_duration(&decimal) {
                Ok(value) => Ok(CursorValue::Int(value)),
                Err(reason) => {
                    self.defer(reason);
                    Ok(CursorValue::Null)
                }
            },
            _ => Ok(CursorValue::Null),
        }
    }
    fn string(&mut self, text: JsonText<'_>) -> Result<Self::Value, Reason> {
        Ok(match text {
            JsonText::Borrowed(slice) => CursorValue::Str(self.borrowed(slice)),
            JsonText::Owned(owned) => {
                if owned.contains('\0') {
                    self.faulty = true;
                    CursorValue::BadStr(NUL_BYTE_FORBIDDEN)
                } else {
                    CursorValue::Str(Cow::Owned(owned))
                }
            }
            JsonText::Wide(points) => {
                self.faulty = true;
                CursorValue::BadStr(wide_fault(&points).unwrap_or(LONE_SURROGATE))
            }
        })
    }
    fn key(&mut self, text: JsonText<'_>) -> Result<Self::Key, Reason> {
        let key = match text {
            JsonText::Borrowed(slice) => CursorKey::Text(self.borrowed(slice)),
            JsonText::Owned(owned) => {
                if owned.contains('\0') {
                    self.faulty = true;
                }
                CursorKey::Text(Cow::Owned(owned))
            }
            JsonText::Wide(points) => {
                self.faulty = true;
                CursorKey::Wide(points)
            }
        };
        if self.stack.len() == 1 {
            self.root_duration_key = matches!(&key, CursorKey::Text(text) if text == "duration");
        }
        Ok(key)
    }
    fn begin_array(&mut self) -> Result<Self::Array, Reason> {
        self.open(Container::Array);
        Ok(Vec::new())
    }
    fn push(&mut self, array: &mut Self::Array, value: Self::Value) -> Result<(), Reason> {
        array.push(value);
        Ok(())
    }
    fn end_array(&mut self, array: Self::Array) -> Result<Self::Value, Reason> {
        Ok(if self.close() { CursorValue::Array(array) } else { CursorValue::Null })
    }
    fn begin_object(&mut self) -> Result<Self::Object, Reason> {
        self.open(Container::Object);
        Ok(Vec::new())
    }
    fn insert(&mut self, object: &mut Self::Object, key: Self::Key, value: Self::Value) -> Result<(), Reason> {
        object.push((key, value));
        Ok(())
    }
    fn end_object(&mut self, object: Self::Object) -> Result<Self::Value, Reason> {
        // ``object_pairs_hook``: ``dict(pairs)`` collapsing a key is the duplicate refusal.
        if has_duplicate(&object) {
            return Err(DUPLICATE_OBJECT_KEY);
        }
        Ok(if self.close() { CursorValue::Object(object) } else { CursorValue::Null })
    }
}

fn utf16_key_cmp(left: &CursorKey<'_>, right: &CursorKey<'_>) -> Ordering {
    match (left, right) {
        (CursorKey::Text(left), CursorKey::Text(right)) => canonical::utf16_cmp(left, right),
        // Unreachable after key validation; any total order is fine.
        _ => Ordering::Equal,
    }
}

/// The first refusal `ensure_canonical_value` reports for a normalized tree whose containers
/// all sit within the depth bound (recursion is therefore bounded by `MAX_JSON_DEPTH`).
fn first_profile_fault(value: &CursorValue<'_>) -> Option<Reason> {
    match value {
        CursorValue::BadStr(reason) => Some(*reason),
        CursorValue::Array(items) => items.iter().find_map(first_profile_fault),
        CursorValue::Object(members) => {
            if let Some(reason) = members.iter().find_map(|(key, _)| key.fault()) {
                return Some(reason);
            }
            let mut order: Vec<&(CursorKey<'_>, CursorValue<'_>)> = members.iter().collect();
            order.sort_by(|left, right| utf16_key_cmp(&left.0, &right.0));
            order.into_iter().find_map(|(_, item)| first_profile_fault(item))
        }
        _ => None,
    }
}

/// `_parse_cursor_hook_document(data)`: the normalized root object's members, or the
/// reference's exact refusal.
pub fn parse_cursor_hook_document(raw: &[u8]) -> Result<Vec<(CursorKey<'_>, CursorValue<'_>)>, Reason> {
    if raw.is_empty() {
        return Err(INVALID_EVENT_VALUE_TYPE);
    }
    let text = json::precheck(raw)?;
    let mut sink = CursorSink { text, stack: Vec::new(), root_duration_key: false, deferred: None, faulty: false };
    let value = json::scan(text, &mut sink)?;
    if let Some(reason) = sink.deferred {
        return Err(reason);
    }
    if sink.faulty {
        if let Some(reason) = first_profile_fault(&value) {
            return Err(reason);
        }
    }
    match value {
        CursorValue::Object(members) => Ok(members),
        _ => Err(UNSUPPORTED_JSON_TYPE),
    }
}

/// An identity-view member value; `None` is `_IDENTITY_DROP`.
type Kept<'a> = Option<CursorValue<'a>>;

fn bounded_identity_string<'a>(value: &CursorValue<'a>) -> Option<Cow<'a, str>> {
    match value {
        CursorValue::Str(text) if !text.is_empty() && text.len() <= MAX_IDENTITY_STRING_BYTES => {
            Some(text.clone())
        }
        _ => None,
    }
}

fn identity_field<'a>(key: &str, value: &CursorValue<'a>, depth: usize) -> Kept<'a> {
    if key == "workspace_roots" {
        let CursorValue::Array(roots) = value else {
            return Some(CursorValue::Null);
        };
        if roots.len() > MAX_IDENTITY_ITEMS {
            return Some(CursorValue::Null);
        }
        let mut kept = Vec::with_capacity(roots.len());
        for item in roots {
            match bounded_identity_string(item) {
                Some(text) => kept.push(CursorValue::Str(text)),
                None => return Some(CursorValue::Null),
            }
        }
        return Some(CursorValue::Array(kept));
    }
    if key == "error" {
        // ``value not in (None, False, "")``: ``0 == False`` also drops.
        let unset = matches!(value, CursorValue::Null | CursorValue::Bool(false) | CursorValue::Int(0))
            || matches!(value, CursorValue::Str(text) if text.is_empty());
        return (!unset).then_some(CursorValue::Bool(true));
    }
    if key == "model_params" {
        let CursorValue::Array(items) = value else {
            return None;
        };
        if items.len() > MAX_IDENTITY_ITEMS {
            return None;
        }
        let mut kept = Vec::new();
        for item in items {
            let CursorValue::Object(members) = item else { continue };
            if depth >= MAX_IDENTITY_DEPTH {
                continue;
            }
            let reduced = identity_object(members, depth + 1);
            if !reduced.is_empty() {
                kept.push(CursorValue::Object(reduced));
            }
        }
        return Some(CursorValue::Array(kept));
    }
    if IDENTITY_OBJECTS.contains(&key) {
        if let CursorValue::Object(members) = value {
            if depth >= MAX_IDENTITY_DEPTH {
                return None;
            }
            let reduced = identity_object(members, depth + 1);
            return (!reduced.is_empty()).then_some(CursorValue::Object(reduced));
        }
    }
    if IDENTITY_BOOLS.contains(&key) {
        if let CursorValue::Bool(truth) = value {
            return Some(CursorValue::Bool(*truth));
        }
    }
    if IDENTITY_INTS.contains(&key) {
        if let CursorValue::Int(number) = value {
            return Some(CursorValue::Int(*number));
        }
    }
    if IDENTITY_STRINGS.contains(&key) {
        return bounded_identity_string(value).map(CursorValue::Str);
    }
    None
}

fn identity_object<'a>(members: &[(CursorKey<'a>, CursorValue<'a>)], depth: usize) -> Vec<(CursorKey<'a>, CursorValue<'a>)> {
    let mut kept = Vec::new();
    for (key, item) in members {
        // Keys of an accepted document are valid text.
        let CursorKey::Text(name) = key else { continue };
        if let Some(copied) = identity_field(name, item, depth) {
            kept.push((key.clone(), copied));
        }
    }
    kept
}

/// `_cursor_identity_payload(parsed)` for an accepted document's members.
pub fn cursor_identity_payload<'a>(members: &[(CursorKey<'a>, CursorValue<'a>)]) -> Vec<(CursorKey<'a>, CursorValue<'a>)> {
    identity_object(members, 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn refusal(source: &str) -> Reason {
        parse_cursor_hook_document(source.as_bytes()).expect_err(source)
    }

    #[test]
    fn duration_truncates_and_vendor_floats_drop() {
        let members = parse_cursor_hook_document(br#"{"duration": 12.9, "meta": {"x": 1.5, "duration": 2.5}, "n": [0.5]}"#).unwrap();
        assert_eq!(members[0].1, CursorValue::Int(12));
        let CursorValue::Object(meta) = &members[1].1 else { panic!() };
        assert_eq!(meta[0].1, CursorValue::Null);
        assert_eq!(meta[1].1, CursorValue::Null);
        assert_eq!(members[2].1, CursorValue::Array(vec![CursorValue::Null]));
    }

    #[test]
    fn refusals_keep_reference_phases() {
        assert_eq!(parse_cursor_hook_document(b""), Err(INVALID_EVENT_VALUE_TYPE));
        assert_eq!(refusal("1.5"), FLOAT_FORBIDDEN);
        assert_eq!(refusal("[1]"), UNSUPPORTED_JSON_TYPE);
        assert_eq!(refusal(r#"{"a": -0}"#), FLOAT_FORBIDDEN);
        assert_eq!(refusal(r#"{"a": NaN}"#), FLOAT_FORBIDDEN);
        assert_eq!(refusal(r#"{"a": 1e999999999999999999999}"#), FLOAT_FORBIDDEN);
        assert_eq!(refusal(r#"{"duration": -0.0}"#), INVALID_DURATION);
        assert_eq!(refusal(r#"{"duration": -1.5}"#), INVALID_DURATION);
        assert_eq!(refusal(r#"{"duration": 9007199254740991.5}"#), INTEGER_OUT_OF_SAFE_RANGE);
        assert_eq!(refusal(r#"{"duration": 1e17}"#), INTEGER_OUT_OF_SAFE_RANGE);
        assert_eq!(refusal(r#"{"a": 9007199254740992}"#), INTEGER_OUT_OF_SAFE_RANGE);
        // A scan refusal outranks an earlier normalization refusal.
        assert_eq!(refusal(r#"{"duration": -1.5, "a": 1, "a": 2}"#), DUPLICATE_OBJECT_KEY);
        // Normalization outranks the profile walk.
        assert_eq!(refusal(r#"{"s": "\u0000", "duration": -1.5}"#), INVALID_DURATION);
        // Keys in insertion order precede values in UTF-16 order.
        assert_eq!(refusal(r#"{"b": "\ud800", "a\u0000": 1}"#), NUL_BYTE_FORBIDDEN);
        assert_eq!(refusal(r#"{"b": "\u0000", "a": "\ud800"}"#), LONE_SURROGATE);
        assert_eq!(refusal(r#"{"a": 1, "\ud800": 1, "\ud800": 2}"#), DUPLICATE_OBJECT_KEY);
    }

    #[test]
    fn duration_edges() {
        let ok = |source: &str| match parse_cursor_hook_document(source.as_bytes()).unwrap()[0].1 {
            CursorValue::Int(value) => value,
            ref other => panic!("{other:?}"),
        };
        assert_eq!(ok(r#"{"duration": 9007199254740991.0}"#), 9_007_199_254_740_991);
        assert_eq!(ok(r#"{"duration": 0.0}"#), 0);
        assert_eq!(ok(r#"{"duration": 1e-999}"#), 0);
        assert_eq!(ok(r#"{"duration": 12.5e1}"#), 125);
        assert_eq!(ok(r#"{"duration": 0.0123e3}"#), 12);
    }

    #[test]
    fn deep_nesting_is_deferred_and_bounded() {
        let depth = 70;
        let source = format!("{{\"a\":{}{}}}", "[".repeat(depth), "]".repeat(depth));
        assert_eq!(refusal(&source), NESTING_TOO_DEEP);
        let hostile = format!("{}{}", "[".repeat(30_000), "]".repeat(30_000));
        assert_eq!(refusal(&hostile), NESTING_TOO_DEEP);
    }

    #[test]
    fn identity_view_keeps_only_allowlisted_fields() {
        let members = parse_cursor_hook_document(
            br#"{"session_id":"s","prompt":"secret","error":0,"tool_input":{"path":"a","content":"x"},"workspace_roots":["/w",""],"duration":3.5,"model_params":[{"id":"m"},{"x":1},2]}"#,
        )
        .unwrap();
        let view = cursor_identity_payload(&members);
        let names: Vec<_> = view.iter().map(|(key, _)| key.clone()).collect();
        assert_eq!(
            names,
            ["session_id", "tool_input", "workspace_roots", "duration", "model_params"]
                .map(|name| CursorKey::Text(Cow::Borrowed(name)))
        );
        assert_eq!(view[2].1, CursorValue::Null);
        assert_eq!(view[3].1, CursorValue::Int(3));
    }
}
