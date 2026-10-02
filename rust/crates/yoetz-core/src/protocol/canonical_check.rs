//! Single-pass canonical round-trip check over raw bytes.
//!
//! [`is_canonical_json_bytes`] answers `canonical_encode(strict_json_parse(data)) == data`
//! without building a value: the bytes must already be exactly the restricted-JCS text the
//! encoder would emit. That form is a strict subset of what the strict parser admits, so a
//! `true` answer implies the parse succeeds (valid UTF-8, no NUL or BOM, no duplicate key, no
//! float, safe integers, bounded nesting, no lone surrogate) and that re-encoding reproduces the
//! input byte for byte. Any doubt answers `false`; callers then run the reference expression,
//! which reports the exact refusal.
//!
//! The canonical text has no whitespace; object members appear in strictly increasing UTF-16
//! key order (so no duplicates); strings escape exactly `"`, `\` and C0 controls, using the
//! short forms `\b \t \n \f \r` and lowercase `\u00xx` otherwise, and never contain U+0000;
//! integers are `0` or `-?[1-9][0-9]*` within `±(2**53 - 1)`; containers nest below
//! [`MAX_JSON_DEPTH`].

use std::borrow::Cow;
use std::cmp::Ordering;

use super::canonical::{MAX_JSON_DEPTH, MAX_SAFE_INTEGER, utf16_cmp};

/// Return whether `data` is exactly the canonical encoding of the value it parses to.
pub fn is_canonical_json_bytes(data: &[u8]) -> bool {
    // Outside string literals the grammar admits only ASCII, and every string body is checked
    // as UTF-8, so a `true` answer implies the whole input is valid UTF-8 (hence no BOM: U+FEFF
    // cannot start a value).
    let mut checker = Checker {
        bytes: data,
        pos: 0,
    };
    checker.value(0) && checker.pos == data.len()
}

/// Bytes that end the plain run of a string: `"`, `\` and C0 controls.
static STRING_STOP: [bool; 256] = {
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

const ONES: u64 = u64::from_ne_bytes([0x01; 8]);
const HIGHS: u64 = u64::from_ne_bytes([0x80; 8]);

/// Whether any byte of `word` is zero (exact for presence).
#[inline]
fn has_zero_byte(word: u64) -> bool {
    word.wrapping_sub(ONES) & !word & HIGHS != 0
}

/// Advance from `index` over whole 8-byte words holding no string stop byte; the byte loop
/// finishes the partial word. Exact presence tests, so a stop byte is never skipped.
#[inline]
fn skip_plain_words(bytes: &[u8], mut index: usize) -> usize {
    while let Some(chunk) = bytes.get(index..index + 8) {
        let word = u64::from_ne_bytes([
            chunk[0], chunk[1], chunk[2], chunk[3], chunk[4], chunk[5], chunk[6], chunk[7],
        ]);
        let control = word.wrapping_sub(ONES * 0x20) & !word & HIGHS != 0;
        if control
            || has_zero_byte(word ^ (ONES * u64::from(b'"')))
            || has_zero_byte(word ^ (ONES * u64::from(b'\\')))
        {
            break;
        }
        index += 8;
    }
    index
}

struct Checker<'a> {
    bytes: &'a [u8],
    pos: usize,
}

impl<'a> Checker<'a> {
    #[inline]
    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.pos).copied()
    }

    #[inline]
    fn literal(&mut self, word: &[u8]) -> bool {
        if self.bytes[self.pos..].starts_with(word) {
            self.pos += word.len();
            true
        } else {
            false
        }
    }

    /// One value starting at `self.pos`, sitting at container depth `depth`. Recursion is
    /// bounded: a container at `MAX_JSON_DEPTH` is refused before it is entered.
    fn value(&mut self, depth: usize) -> bool {
        match self.peek() {
            Some(b'{') => self.object(depth),
            Some(b'[') => self.array(depth),
            Some(b'"') => self.string().is_some(),
            Some(b'n') => self.literal(b"null"),
            Some(b't') => self.literal(b"true"),
            Some(b'f') => self.literal(b"false"),
            Some(b'-' | b'0'..=b'9') => self.integer(),
            _ => false,
        }
    }

    fn integer(&mut self) -> bool {
        let start = self.pos;
        if self.peek() == Some(b'-') {
            self.pos += 1;
        }
        let digits_start = self.pos;
        match self.peek() {
            // `0` is canonical only alone and unsigned (`-0` is a refused float spelling).
            Some(b'0') => {
                self.pos += 1;
                return digits_start == start;
            }
            Some(b'1'..=b'9') => {}
            _ => return false,
        }
        let mut magnitude: i64 = 0;
        while let Some(byte @ b'0'..=b'9') = self.peek() {
            magnitude = magnitude * 10 + i64::from(byte - b'0');
            if magnitude > MAX_SAFE_INTEGER {
                return false;
            }
            self.pos += 1;
        }
        true
    }

    /// One string literal: its raw body (UTF-8 checked, escapes still spelled out) and whether
    /// it holds an escape. Every escape is already proven to be the encoder's own spelling.
    fn string(&mut self) -> Option<(&'a str, bool)> {
        debug_assert_eq!(self.peek(), Some(b'"'));
        self.pos += 1;
        let start = self.pos;
        let mut escaped = false;
        loop {
            let mut index = skip_plain_words(self.bytes, self.pos);
            while index < self.bytes.len() && !STRING_STOP[self.bytes[index] as usize] {
                index += 1;
            }
            self.pos = index;
            match self.peek()? {
                b'"' => {
                    let body = std::str::from_utf8(&self.bytes[start..index]).ok()?;
                    self.pos += 1;
                    return Some((body, escaped));
                }
                b'\\' => {
                    self.escape()?;
                    escaped = true;
                }
                // A raw C0 control (including NUL): strict parsing refuses it.
                _ => return None,
            }
        }
    }

    /// One object key, decoded (borrowed when it has no escapes).
    fn key(&mut self) -> Option<Cow<'a, str>> {
        let (body, escaped) = self.string()?;
        if !escaped {
            return Some(Cow::Borrowed(body));
        }
        let mut decoded = String::with_capacity(body.len());
        let mut rest = body;
        while let Some(at) = rest.find('\\') {
            decoded.push_str(&rest[..at]);
            let mut escape = Checker {
                bytes: &rest.as_bytes()[at..],
                pos: 0,
            };
            decoded.push(escape.escape()?);
            rest = &rest[at + escape.pos..];
        }
        decoded.push_str(rest);
        Some(Cow::Owned(decoded))
    }

    /// One escape sequence at `self.pos` (the backslash), in exactly the encoder's spelling.
    fn escape(&mut self) -> Option<char> {
        let next = *self.bytes.get(self.pos + 1)?;
        let character = match next {
            b'"' => '"',
            b'\\' => '\\',
            b'b' => '\u{8}',
            b't' => '\t',
            b'n' => '\n',
            b'f' => '\u{c}',
            b'r' => '\r',
            b'u' => {
                let hex = self.bytes.get(self.pos + 2..self.pos + 6)?;
                let high = match hex[2] {
                    b'0' => 0,
                    b'1' => 1,
                    _ => return None,
                };
                let low = match hex[3] {
                    digit @ b'0'..=b'9' => digit - b'0',
                    letter @ b'a'..=b'f' => letter - b'a' + 10,
                    _ => return None,
                };
                let point = (high << 4) | low;
                // Only the controls without a short form, and never U+0000.
                if hex[0] != b'0' || hex[1] != b'0' || point == 0 {
                    return None;
                }
                if matches!(point, 0x08 | 0x09 | 0x0A | 0x0C | 0x0D) {
                    return None;
                }
                self.pos += 6;
                return Some(char::from(point));
            }
            _ => return None,
        };
        self.pos += 2;
        Some(character)
    }

    fn array(&mut self, depth: usize) -> bool {
        if depth >= MAX_JSON_DEPTH {
            return false;
        }
        self.pos += 1;
        if self.peek() == Some(b']') {
            self.pos += 1;
            return true;
        }
        loop {
            if !self.value(depth + 1) {
                return false;
            }
            match self.peek() {
                Some(b',') => self.pos += 1,
                Some(b']') => {
                    self.pos += 1;
                    return true;
                }
                _ => return false,
            }
        }
    }

    fn object(&mut self, depth: usize) -> bool {
        if depth >= MAX_JSON_DEPTH {
            return false;
        }
        self.pos += 1;
        if self.peek() == Some(b'}') {
            self.pos += 1;
            return true;
        }
        let mut previous: Option<Cow<'a, str>> = None;
        loop {
            if self.peek() != Some(b'"') {
                return false;
            }
            let Some(key) = self.key() else {
                return false;
            };
            if let Some(prior) = &previous {
                // Strictly increasing UTF-16 order: sorted, and no duplicate key.
                if utf16_cmp(prior, &key) != Ordering::Less {
                    return false;
                }
            }
            previous = Some(key);
            if self.peek() != Some(b':') {
                return false;
            }
            self.pos += 1;
            if !self.value(depth + 1) {
                return false;
            }
            match self.peek() {
                Some(b',') => self.pos += 1,
                Some(b'}') => {
                    self.pos += 1;
                    return true;
                }
                _ => return false,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::canonical::encode;
    use crate::protocol::json::parse;

    /// The reference relation: parse succeeds and re-encoding reproduces the bytes.
    fn reference(data: &[u8]) -> bool {
        match parse(data) {
            Ok(value) => encode(&value).is_ok_and(|bytes| bytes == data),
            Err(_) => false,
        }
    }

    fn agree(data: &[u8]) {
        assert_eq!(
            is_canonical_json_bytes(data),
            reference(data),
            "{:?}",
            String::from_utf8_lossy(data)
        );
    }

    #[test]
    fn accepts_canonical_text() {
        for sample in [
            &b"null"[..],
            b"true",
            b"false",
            b"0",
            b"-1",
            b"9007199254740991",
            b"-9007199254740991",
            b"\"\"",
            b"[]",
            b"{}",
            b"[1,[2,{}],\"x\"]",
            b"{\"a\":1,\"b\":{\"c\":[null,true]}}",
            "\"\\\"\\\\\\b\\t\\n\\f\\r\\u0001\\u001f\u{7f}é\"".as_bytes(),
            "{\"a\":1,\"\u{1d306}\":2,\"\u{fb00}\":3}".as_bytes(),
            b"{\"\\n\":0,\"\\\"\":1,\"#\":2}",
        ] {
            assert!(
                is_canonical_json_bytes(sample),
                "{:?}",
                String::from_utf8_lossy(sample)
            );
            agree(sample);
        }
    }

    #[test]
    fn refuses_everything_else() {
        for sample in [
            &b""[..],
            b" null",
            b"null ",
            b"[1, 2]",
            b"{\"a\" :1}",
            b"-0",
            b"01",
            b"1.0",
            b"1e3",
            b"9007199254740992",
            b"-9007199254740992",
            b"123456789012345678901234567890",
            b"NaN",
            b"\"\\u0000\"",
            b"\"\\u000a\"",
            b"\"\\u001F\"",
            b"\"\\u0041\"",
            b"\"\\/\"",
            b"\"\\ud800\"",
            b"\"a\x01\"",
            b"\"\x00\"",
            b"{\"b\":1,\"a\":2}",
            b"{\"a\":1,\"a\":2}",
            "{\"\u{fb00}\":3,\"\u{1d306}\":2}".as_bytes(),
            b"{\"#\":2,\"\\\"\":1}",
            b"\xef\xbb\xbfnull",
            b"\xff",
            b"[1,]",
            b"{\"a\":1,}",
            b"[",
            b"\"abc",
            b"\"\\u00",
            b"tru",
            b"[1]]",
        ] {
            assert!(
                !is_canonical_json_bytes(sample),
                "{:?}",
                String::from_utf8_lossy(sample)
            );
            agree(sample);
        }
    }

    #[test]
    fn nesting_bound_is_exact() {
        let deep = |levels: usize| {
            let mut text = "[".repeat(levels);
            text.push_str(&"]".repeat(levels));
            text.into_bytes()
        };
        assert!(is_canonical_json_bytes(&deep(MAX_JSON_DEPTH)));
        assert!(!is_canonical_json_bytes(&deep(MAX_JSON_DEPTH + 1)));
        assert!(!is_canonical_json_bytes(&deep(100_000)));
        agree(&deep(MAX_JSON_DEPTH));
        agree(&deep(MAX_JSON_DEPTH + 1));
    }

    #[test]
    fn word_scan_never_skips_a_stop_byte() {
        for length in 0..24 {
            for position in 0..length {
                for stop in ['"', '\\', '\n', '\u{1}', '\u{1f}', '\u{7f}', 'é', ' '] {
                    let mut text: String =
                        "abcdefghijklmnopqrstuvwxyz".chars().take(length).collect();
                    text.replace_range(
                        text.char_indices()
                            .nth(position)
                            .map(|(at, ch)| at..at + ch.len_utf8())
                            .unwrap(),
                        &stop.to_string(),
                    );
                    let mut encoded = Vec::new();
                    crate::protocol::canonical::encode_str_into(&mut encoded, &text).unwrap();
                    assert!(is_canonical_json_bytes(&encoded));
                    let raw = format!("\"{text}\"");
                    agree(raw.as_bytes());
                    let mut keyed = b"{\"a\":".to_vec();
                    keyed.extend_from_slice(raw.as_bytes());
                    keyed.push(b'}');
                    agree(&keyed);
                }
            }
        }
    }

    #[test]
    fn every_short_control_escape_agrees() {
        for point in 0u8..0x20 {
            let mut short = Vec::new();
            crate::protocol::canonical::encode_str_into(&mut short, &char::from(point).to_string())
                .ok();
            agree(&short);
            agree(format!("\"\\u{point:04x}\"").as_bytes());
            agree(format!("\"\\u{point:04X}\"").as_bytes());
        }
    }
}
