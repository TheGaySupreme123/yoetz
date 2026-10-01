//! Restricted-JCS canonical encoding, the Rust twin of `yoetz.protocol.canonical`.
//!
//! The byte output and the reason code of every refusal must equal the Python reference
//! exactly: canonical bytes are load-bearing for digests, manifests, and stored identities.
//! Object members sort by the UTF-16 code units of their keys (the reference sorts by the
//! UTF-16-BE encoding), strings escape only `"`, `\` and C0 controls, and the nesting bound is
//! checked before a container's members are visited.

use std::cmp::Ordering;

use sha2::{Digest, Sha256};

/// Containers may nest at most this deep (a container at depth 64 is refused).
pub const MAX_JSON_DEPTH: usize = 64;
/// The largest integer magnitude the profile admits (`2**53 - 1`).
pub const MAX_SAFE_INTEGER: i64 = (1_i64 << 53) - 1;
/// The SQLite signed integer bounds used by the canonical integer-string helpers.
pub const MAX_SQLITE_SIGNED_INTEGER: i64 = i64::MAX;

/// A registered `ProtocolValueError` reason code.
pub type Reason = &'static str;

pub const NUL_BYTE_FORBIDDEN: Reason = "nul_byte_forbidden";
pub const LONE_SURROGATE: Reason = "lone_surrogate";
pub const NESTING_TOO_DEEP: Reason = "nesting_too_deep";
pub const INTEGER_OUT_OF_SAFE_RANGE: Reason = "integer_out_of_safe_range";
pub const FLOAT_FORBIDDEN: Reason = "float_forbidden";
pub const UNSUPPORTED_JSON_TYPE: Reason = "unsupported_json_type";
pub const OBJECT_KEY_NOT_STRING: Reason = "object_key_not_string";
pub const MALFORMED_JSON: Reason = "malformed_json";
pub const DUPLICATE_OBJECT_KEY: Reason = "duplicate_object_key";
pub const INVALID_UTF8: Reason = "invalid_utf8";
pub const BYTE_ORDER_MARK_FORBIDDEN: Reason = "byte_order_mark_forbidden";
pub const INPUT_NOT_BYTES: Reason = "input_not_bytes";
pub const SET_MEMBER_NOT_ASCII: Reason = "set_member_not_ascii";
pub const DUPLICATE_SET_MEMBER: Reason = "duplicate_set_member";
pub const UNSORTED_SET_FIELD: Reason = "unsorted_set_field";
pub const INTEGER_OUT_OF_SQLITE_RANGE: Reason = "integer_out_of_sqlite_range";
pub const NONCANONICAL_INTEGER_STRING: Reason = "noncanonical_integer_string";
pub const LEDGER_ASSIGNED_FIELD: Reason = "ledger_assigned_field_in_request_identity";
pub const NOT_AN_ACCEPTED_ENVELOPE: Reason = "not_an_accepted_envelope";

/// Keys a logical request identity may never carry (ledger-assigned on acceptance).
pub const REQUEST_DIGEST_FENCE_KEYS: [&str; 5] = [
    "ingestion_sequence",
    "accepted_at",
    "previous_entry_digest",
    "object_id",
    "ledger",
];

/// The exact top-level key set of an accepted-entry preimage.
pub const ACCEPTED_ENTRY_PREIMAGE_KEYS: [&str; 18] = [
    "artifact_refs",
    "author",
    "causal_parents",
    "coverage",
    "event_id",
    "evidence_refs",
    "ledger",
    "occurred_at",
    "operation_id",
    "payload_ref",
    "protocol",
    "protocol_version",
    "publication_channel",
    "redaction",
    "schema",
    "session_id",
    "task_id",
    "writer",
];

/// Byte classes that force the escaping path: `"`, `\` and C0 controls.
static NEEDS_ESCAPE: [bool; 256] = {
    let mut table = [false; 256];
    let mut index = 0;
    while index < 0x20 {
        table[index] = true;
        index += 1;
    }
    table[b'"' as usize] = true;
    table[b'\\' as usize] = true;
    table
};

const HEX: &[u8; 16] = b"0123456789abcdef";

/// Return whether `text` contains a byte the encoder must escape.
#[inline]
pub fn needs_escape(text: &str) -> bool {
    text.as_bytes().iter().any(|byte| NEEDS_ESCAPE[*byte as usize])
}

/// Refuse a NUL character; a Rust `str` cannot hold a lone surrogate.
#[inline]
pub fn validate_str(text: &str) -> Result<(), Reason> {
    if memchr::memchr(0, text.as_bytes()).is_some() {
        Err(NUL_BYTE_FORBIDDEN)
    } else {
        Ok(())
    }
}

/// Append the canonical JSON string literal for `text` to `out`.
pub fn encode_str_into(out: &mut Vec<u8>, text: &str) -> Result<(), Reason> {
    let bytes = text.as_bytes();
    out.reserve(bytes.len() + 2);
    out.push(b'"');
    let mut start = 0;
    for (index, &byte) in bytes.iter().enumerate() {
        if !NEEDS_ESCAPE[byte as usize] {
            continue;
        }
        if byte == 0 {
            return Err(NUL_BYTE_FORBIDDEN);
        }
        out.extend_from_slice(&bytes[start..index]);
        match byte {
            b'"' => out.extend_from_slice(b"\\\""),
            b'\\' => out.extend_from_slice(b"\\\\"),
            0x08 => out.extend_from_slice(b"\\b"),
            0x09 => out.extend_from_slice(b"\\t"),
            0x0A => out.extend_from_slice(b"\\n"),
            0x0C => out.extend_from_slice(b"\\f"),
            0x0D => out.extend_from_slice(b"\\r"),
            _ => {
                out.extend_from_slice(b"\\u00");
                out.push(HEX[(byte >> 4) as usize]);
                out.push(HEX[(byte & 0x0F) as usize]);
            }
        }
        start = index + 1;
    }
    out.extend_from_slice(&bytes[start..]);
    out.push(b'"');
    Ok(())
}

/// Compare two keys by their UTF-16 code units (the reference's UTF-16-BE byte order).
#[inline]
pub fn utf16_cmp(left: &str, right: &str) -> Ordering {
    // UTF-8 byte order equals code point order, which equals UTF-16 order except where a
    // supplementary character meets one in U+E000..=U+FFFF. Compare bytes until the first
    // difference and only consult UTF-16 there.
    let (lb, rb) = (left.as_bytes(), right.as_bytes());
    let common = lb.iter().zip(rb.iter()).take_while(|(l, r)| l == r).count();
    if common == lb.len() || common == rb.len() {
        return lb.len().cmp(&rb.len());
    }
    // Back up to the start of the differing character.
    let mut boundary = common;
    while boundary > 0 && !left.is_char_boundary(boundary) {
        boundary -= 1;
    }
    let lc = left[boundary..].chars().next().map_or(0, u32::from);
    let rc = right[boundary..].chars().next().map_or(0, u32::from);
    utf16_unit_key(lc).cmp(&utf16_unit_key(rc))
}

#[inline]
fn utf16_unit_key(codepoint: u32) -> (u32, u32) {
    if codepoint >= 0x1_0000 {
        let offset = codepoint - 0x1_0000;
        (0xD800 + (offset >> 10), 0xDC00 + (offset & 0x3FF))
    } else {
        (codepoint, 0)
    }
}

/// Compare UTF-16 code unit sequences given as code points (surrogates allowed).
pub fn utf16_cmp_codepoints(left: &[u32], right: &[u32]) -> Ordering {
    let expand = |points: &[u32]| -> Vec<u32> {
        let mut units = Vec::with_capacity(points.len());
        for &point in points {
            let (first, second) = utf16_unit_key(point);
            units.push(first);
            if second != 0 {
                units.push(second);
            }
        }
        units
    };
    expand(left).cmp(&expand(right))
}

/// `"sha256:" + hex(sha256(bytes))`.
pub fn sha256_prefixed(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut out = String::with_capacity(7 + 64);
    out.push_str("sha256:");
    out.push_str(&hex::encode(digest));
    out
}

/// Render an integer the way Python's `str(int)` does.
#[inline]
pub fn push_int(out: &mut Vec<u8>, value: i64) {
    let mut buffer = itoa::Buffer::new();
    out.extend_from_slice(buffer.format(value).as_bytes());
}

/// Check the canonical integer profile.
#[inline]
pub fn check_safe_integer(value: i64) -> Result<(), Reason> {
    if (-MAX_SAFE_INTEGER..=MAX_SAFE_INTEGER).contains(&value) {
        Ok(())
    } else {
        Err(INTEGER_OUT_OF_SAFE_RANGE)
    }
}

/// `canonical_integer_string` for an already-integral value.
pub fn canonical_integer_string(value: i64) -> Result<String, Reason> {
    if value < 0 {
        return Err(INTEGER_OUT_OF_SQLITE_RANGE);
    }
    Ok(value.to_string())
}

fn is_ascii_digits(value: &str) -> bool {
    value.bytes().all(|byte| byte.is_ascii_digit())
}

fn matches_integer_pattern(value: &str, signed: bool) -> bool {
    if signed {
        if value == "0" {
            return true;
        }
        let digits = value.strip_prefix('-').unwrap_or(value);
        return !digits.is_empty() && !digits.starts_with('0') && is_ascii_digits(digits);
    }
    value == "0" || (!value.starts_with('0') && is_ascii_digits(value))
}

/// `parse_canonical_integer_string` (string input already established).
pub fn parse_canonical_integer_string(value: &str, signed: bool) -> Result<i64, Reason> {
    let limit = if signed { 20 } else { 19 };
    // The reference measures length in characters; non-ASCII input fails the pattern anyway.
    if value.is_empty() || value.chars().count() > limit {
        return Err(NONCANONICAL_INTEGER_STRING);
    }
    if signed {
        if value == "-0" || !matches_integer_pattern(value, true) {
            return Err(NONCANONICAL_INTEGER_STRING);
        }
    } else if !matches_integer_pattern(value, false) {
        return Err(NONCANONICAL_INTEGER_STRING);
    }
    let parsed: i128 = value.parse().map_err(|_| NONCANONICAL_INTEGER_STRING)?;
    let low = if signed { i64::MIN as i128 } else { 0 };
    if parsed < low || parsed > i64::MAX as i128 {
        return Err(NONCANONICAL_INTEGER_STRING);
    }
    Ok(parsed as i64)
}

/// A JSON value in the Yoetz profile (strings are always valid Unicode here).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Array(Vec<Value>),
    /// Members in insertion order; encoding sorts them.
    Object(Vec<(String, Value)>),
}

impl Value {
    /// Look up an object member by key.
    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Object(members) => members.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(text) => Some(text),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Value::Int(value) => Some(*value),
            _ => None,
        }
    }
}

/// Encode `value` to canonical bytes.
pub fn encode(value: &Value) -> Result<Vec<u8>, Reason> {
    let mut out = Vec::with_capacity(256);
    encode_into(&mut out, value, 0)?;
    Ok(out)
}

/// Encode `value`, sitting at `depth` inside an enclosing document, onto `out`.
pub fn encode_into(out: &mut Vec<u8>, value: &Value, depth: usize) -> Result<(), Reason> {
    match value {
        Value::Null => out.extend_from_slice(b"null"),
        Value::Bool(true) => out.extend_from_slice(b"true"),
        Value::Bool(false) => out.extend_from_slice(b"false"),
        Value::Int(number) => {
            check_safe_integer(*number)?;
            push_int(out, *number);
        }
        Value::Str(text) => encode_str_into(out, text)?,
        Value::Array(items) => {
            if depth >= MAX_JSON_DEPTH {
                return Err(NESTING_TOO_DEEP);
            }
            out.push(b'[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(b',');
                }
                encode_into(out, item, depth + 1)?;
            }
            out.push(b']');
        }
        Value::Object(members) => {
            if depth >= MAX_JSON_DEPTH {
                return Err(NESTING_TOO_DEEP);
            }
            for (key, _) in members {
                validate_str(key)?;
            }
            let mut order: Vec<&(String, Value)> = members.iter().collect();
            order.sort_by(|left, right| utf16_cmp(&left.0, &right.0));
            out.push(b'{');
            for (index, (key, item)) in order.into_iter().enumerate() {
                if index > 0 {
                    out.push(b',');
                }
                encode_str_into(out, key)?;
                out.push(b':');
                encode_into(out, item, depth + 1)?;
            }
            out.push(b'}');
        }
    }
    Ok(())
}

/// The canonical digest of `value`.
pub fn digest(value: &Value) -> Result<String, Reason> {
    Ok(sha256_prefixed(&encode(value)?))
}

/// The deepest relative level holding a container, or `-1` for a scalar.
pub fn container_levels(value: &Value) -> i64 {
    match value {
        Value::Array(items) => 1 + items.iter().map(container_levels).max().unwrap_or(-1),
        Value::Object(members) => {
            1 + members.iter().map(|(_, item)| container_levels(item)).max().unwrap_or(-1)
        }
        _ => -1,
    }
}

/// `request_digest`: refuse ledger-assigned fields anywhere, then digest.
pub fn request_digest(identity: &Value) -> Result<String, Reason> {
    fn walk(node: &Value, depth: usize) -> Result<(), Reason> {
        match node {
            Value::Object(members) => {
                if depth >= MAX_JSON_DEPTH {
                    return Err(NESTING_TOO_DEEP);
                }
                for (key, item) in members {
                    if REQUEST_DIGEST_FENCE_KEYS.contains(&key.as_str()) {
                        return Err(LEDGER_ASSIGNED_FIELD);
                    }
                    walk(item, depth + 1)?;
                }
            }
            Value::Array(items) => {
                if depth >= MAX_JSON_DEPTH {
                    return Err(NESTING_TOO_DEEP);
                }
                for item in items {
                    walk(item, depth + 1)?;
                }
            }
            _ => {}
        }
        Ok(())
    }
    walk(identity, 0)?;
    digest(identity)
}

/// `entry_digest`: gate the exact accepted-envelope key set, then digest.
pub fn entry_digest(preimage: &Value) -> Result<String, Reason> {
    let Value::Object(members) = preimage else {
        return Err(NOT_AN_ACCEPTED_ENVELOPE);
    };
    let mut keys: Vec<&str> = members.iter().map(|(key, _)| key.as_str()).collect();
    keys.sort_unstable();
    keys.dedup();
    if keys.len() != ACCEPTED_ENTRY_PREIMAGE_KEYS.len()
        || keys.iter().any(|key| !ACCEPTED_ENTRY_PREIMAGE_KEYS.contains(key))
    {
        return Err(NOT_AN_ACCEPTED_ENVELOPE);
    }
    match preimage.get("protocol") {
        Some(Value::Str(protocol)) if protocol == "yoetz.event" => {}
        _ => return Err(NOT_AN_ACCEPTED_ENVELOPE),
    }
    digest(preimage)
}

/// `ensure_canonical_set` over already-extracted members.
pub fn ensure_canonical_set<'a, I>(members: I) -> Result<(), Reason>
where
    I: IntoIterator<Item = Option<&'a str>>,
{
    let mut previous: Option<&[u8]> = None;
    for member in members {
        let Some(member) = member else {
            return Err(SET_MEMBER_NOT_ASCII);
        };
        if !member.is_ascii() {
            return Err(SET_MEMBER_NOT_ASCII);
        }
        let encoded = member.as_bytes();
        if let Some(prior) = previous {
            if encoded == prior {
                return Err(DUPLICATE_SET_MEMBER);
            }
            if encoded < prior {
                return Err(UNSORTED_SET_FIELD);
            }
        }
        previous = Some(encoded);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escapes_match_reference() {
        let mut out = Vec::new();
        encode_str_into(&mut out, "a\"b\\c\u{8}\t\n\u{c}\r\u{1}\u{1f}\u{7f}é").unwrap();
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "\"a\\\"b\\\\c\\b\\t\\n\\f\\r\\u0001\\u001f\u{7f}é\""
        );
        assert_eq!(encode_str_into(&mut Vec::new(), "a\0"), Err(NUL_BYTE_FORBIDDEN));
    }

    #[test]
    fn keys_sort_by_utf16() {
        // U+FB00 (BMP, high) sorts after U+1D306 in UTF-16 (surrogates are lower).
        let value = Value::Object(vec![
            ("\u{fb00}".into(), Value::Int(3)),
            ("a".into(), Value::Int(1)),
            ("\u{1d306}".into(), Value::Int(2)),
        ]);
        assert_eq!(
            String::from_utf8(encode(&value).unwrap()).unwrap(),
            "{\"a\":1,\"\u{1d306}\":2,\"\u{fb00}\":3}"
        );
        assert_eq!(utf16_cmp("ab", "abc"), Ordering::Less);
        assert_eq!(utf16_cmp("abc", "abc"), Ordering::Equal);
    }

    #[test]
    fn depth_bound_is_exact() {
        let mut value = Value::Int(0);
        for _ in 0..MAX_JSON_DEPTH {
            value = Value::Array(vec![value]);
        }
        assert!(encode(&value).is_ok());
        assert_eq!(encode(&Value::Array(vec![value])), Err(NESTING_TOO_DEEP));
    }

    #[test]
    fn integer_strings() {
        assert_eq!(parse_canonical_integer_string("0", false), Ok(0));
        assert_eq!(parse_canonical_integer_string("-5", true), Ok(-5));
        assert_eq!(parse_canonical_integer_string("-0", true), Err(NONCANONICAL_INTEGER_STRING));
        assert_eq!(parse_canonical_integer_string("01", false), Err(NONCANONICAL_INTEGER_STRING));
        assert_eq!(
            parse_canonical_integer_string("9223372036854775808", false),
            Err(NONCANONICAL_INTEGER_STRING)
        );
    }
}
