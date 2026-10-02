//! Strict JSON Pointer decoding for result-leaf classification, the Rust twin of
//! `yoetz.protocol.models._decode_pointer`, plus the leaf-pointer escaping of
//! `yoetz.application.service._escape_pointer`.
//!
//! Unicode normalization is the caller's: the reference refuses a pointer or a decoded segment
//! that is not NFC, and every ASCII string is NFC, so the caller's `is_nfc` is only asked about
//! non-ASCII text.

use super::canonical::Reason;

/// The largest pointer, in UTF-8 bytes, a projection names.
pub const MAX_PROJECTION_POINTER_BYTES: usize = 256;

pub const INVALID_JSON_POINTER: Reason = "invalid_json_pointer";

/// Decode `pointer` into its segments, or refuse it with `invalid_json_pointer`.
///
/// `is_nfc` answers `unicodedata.normalize("NFC", text) == text` for non-ASCII text; the whole
/// pointer's normalization is checked before anything else, as in the reference.
pub fn decode_pointer<F>(
    pointer: &str,
    max_bytes: usize,
    mut is_nfc: F,
) -> Result<Vec<String>, Reason>
where
    F: FnMut(&str) -> bool,
{
    if !pointer.is_ascii() && !is_nfc(pointer) {
        return Err(INVALID_JSON_POINTER);
    }
    if !pointer.starts_with('/') || pointer.len() > max_bytes {
        return Err(INVALID_JSON_POINTER);
    }
    let mut decoded = Vec::new();
    for raw in pointer[1..].split('/') {
        let bytes = raw.as_bytes();
        let mut index = 0;
        while index < bytes.len() {
            if bytes[index] == b'~' {
                if index + 1 >= bytes.len() || !matches!(bytes[index + 1], b'0' | b'1') {
                    return Err(INVALID_JSON_POINTER);
                }
                index += 2;
            } else {
                index += 1;
            }
        }
        let segment = raw.replace("~1", "/").replace("~0", "~");
        let canonical = segment.replace('~', "~0").replace('/', "~1");
        if canonical != raw || (!segment.is_ascii() && !is_nfc(&segment)) {
            return Err(INVALID_JSON_POINTER);
        }
        decoded.push(segment);
    }
    Ok(decoded)
}

/// `segment.isascii() and segment.isdecimal()` and no leading zero: an array index the strict
/// traversal accepts. Returns the index, saturated at `usize::MAX` (always out of range).
pub fn array_index(segment: &str) -> Option<usize> {
    let bytes = segment.as_bytes();
    if bytes.is_empty() || !bytes.iter().all(u8::is_ascii_digit) {
        return None;
    }
    if bytes.len() > 1 && bytes[0] == b'0' {
        return None;
    }
    let mut value: usize = 0;
    for &byte in bytes {
        value = value
            .saturating_mul(10)
            .saturating_add(usize::from(byte - b'0'));
    }
    Some(value)
}

/// `value.replace("~", "~0").replace("/", "~1")` appended to `out`.
pub fn push_escaped(out: &mut String, value: &str) {
    for character in value.chars() {
        match character {
            '~' => out.push_str("~0"),
            '/' => out.push_str("~1"),
            other => out.push(other),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn decode(pointer: &str) -> Result<Vec<String>, Reason> {
        decode_pointer(pointer, MAX_PROJECTION_POINTER_BYTES, |_| true)
    }

    #[test]
    fn decodes_escapes() {
        assert_eq!(decode("/a~1b/~0c/0").unwrap(), vec!["a/b", "~c", "0"]);
        assert_eq!(decode("/").unwrap(), vec![""]);
        assert_eq!(decode("/~01").unwrap(), vec!["~1"]);
    }

    #[test]
    fn refuses_bad_pointers() {
        assert_eq!(decode(""), Err(INVALID_JSON_POINTER));
        assert_eq!(decode("a"), Err(INVALID_JSON_POINTER));
        assert_eq!(decode("/~"), Err(INVALID_JSON_POINTER));
        assert_eq!(decode("/~2"), Err(INVALID_JSON_POINTER));
        assert_eq!(
            decode(&format!("/{}", "a".repeat(256))),
            Err(INVALID_JSON_POINTER)
        );
        assert!(decode(&format!("/{}", "a".repeat(255))).is_ok());
        assert_eq!(
            decode_pointer("/\u{e9}", 256, |_| false),
            Err(INVALID_JSON_POINTER)
        );
    }

    #[test]
    fn array_indexes() {
        assert_eq!(array_index("0"), Some(0));
        assert_eq!(array_index("12"), Some(12));
        assert_eq!(array_index("012"), None);
        assert_eq!(array_index(""), None);
        assert_eq!(array_index("1a"), None);
        assert_eq!(array_index(&"9".repeat(60)), Some(usize::MAX));
    }

    #[test]
    fn escapes() {
        let mut out = String::new();
        push_escaped(&mut out, "a/~b");
        assert_eq!(out, "a~1~0b");
    }
}
