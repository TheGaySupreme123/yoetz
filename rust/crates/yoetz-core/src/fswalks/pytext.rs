//! Python text-codec and `str` method semantics the filesystem adapters rely on.
//!
//! Each helper reproduces one CPython behavior exactly for the inputs it accepts, so a port can
//! decode or split bytes the way its Python reference does.

use std::borrow::Cow;

/// `data.decode("utf-8", errors="replace")`.
///
/// CPython and Rust both substitute one U+FFFD per maximal invalid subpart (the Unicode
/// "best practice" for UTF-8 decoding), so the lossy conversion is byte-identical.
pub fn utf8_replace(data: &[u8]) -> Cow<'_, str> {
    String::from_utf8_lossy(data)
}

/// `data.decode("utf-8", errors="ignore")`: the same maximal invalid subparts, dropped.
pub fn utf8_ignore(data: &[u8]) -> Cow<'_, str> {
    let mut chunks = data.utf8_chunks();
    let Some(first) = chunks.next() else {
        return Cow::Borrowed("");
    };
    if first.invalid().is_empty() {
        // `utf8_chunks` yields a single chunk with an empty invalid part for valid input.
        return Cow::Borrowed(first.valid());
    }
    let mut out = String::with_capacity(data.len());
    out.push_str(first.valid());
    for chunk in chunks {
        out.push_str(chunk.valid());
    }
    Cow::Owned(out)
}

/// `data.decode("ascii", errors="ignore")`: every byte >= 0x80 dropped.
pub fn ascii_ignore(data: &[u8]) -> Cow<'_, str> {
    if data.is_ascii() {
        // ASCII is valid UTF-8.
        return Cow::Borrowed(std::str::from_utf8(data).unwrap_or_default());
    }
    let kept: Vec<u8> = data.iter().copied().filter(u8::is_ascii).collect();
    Cow::Owned(String::from_utf8(kept).unwrap_or_default())
}

/// Text-mode universal-newline translation (`newline=None`): `\r\n` and `\r` become `\n`.
pub fn universal_newlines(text: &str) -> Cow<'_, str> {
    if !text.contains('\r') {
        return Cow::Borrowed(text);
    }
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(character) = chars.next() {
        if character == '\r' {
            if chars.peek() == Some(&'\n') {
                chars.next();
            }
            out.push('\n');
        } else {
            out.push(character);
        }
    }
    Cow::Owned(out)
}

/// `str.isspace()` for one ASCII character.
#[inline]
pub fn is_ascii_py_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | 0x0B | 0x0C | b'\r' | 0x1C..=0x1F)
}

/// `str.strip()` for an ASCII-only string.
pub fn ascii_strip(text: &str) -> &str {
    let bytes = text.as_bytes();
    let mut start = 0;
    let mut end = bytes.len();
    while start < end && is_ascii_py_space(bytes[start]) {
        start += 1;
    }
    while end > start && is_ascii_py_space(bytes[end - 1]) {
        end -= 1;
    }
    &text[start..end]
}

/// `str.splitlines()` for an ASCII-only string (boundaries `\n`, `\r`, `\r\n`, `\x0b`, `\x0c`,
/// `\x1c`, `\x1d`, `\x1e`).
pub fn ascii_splitlines(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut lines = Vec::new();
    let mut start = 0;
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        if matches!(byte, b'\n' | b'\r' | 0x0B | 0x0C | 0x1C | 0x1D | 0x1E) {
            lines.push(&text[start..index]);
            if byte == b'\r' && index + 1 < bytes.len() && bytes[index + 1] == b'\n' {
                index += 1;
            }
            start = index + 1;
        }
        index += 1;
    }
    if start < bytes.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// The value of `int(text)` for an ASCII-only string, or `None` where `int()` raises
/// `ValueError`. `Some(None)` is a valid integer outside `i64`.
pub fn ascii_py_int(text: &str) -> Option<Option<i64>> {
    let body = ascii_strip(text).as_bytes();
    let (negative, digits) = match body.first() {
        Some(b'-') => (true, &body[1..]),
        Some(b'+') => (false, &body[1..]),
        _ => (false, body),
    };
    if digits.is_empty() || !digits[0].is_ascii_digit() || !digits[digits.len() - 1].is_ascii_digit() {
        return None;
    }
    let mut value: Option<i64> = Some(0);
    let mut previous_underscore = false;
    for &byte in digits {
        if byte == b'_' {
            if previous_underscore {
                return None;
            }
            previous_underscore = true;
            continue;
        }
        if !byte.is_ascii_digit() {
            return None;
        }
        previous_underscore = false;
        let digit = i64::from(byte - b'0');
        value = value.and_then(|current| current.checked_mul(10)).and_then(|current| {
            if negative { current.checked_sub(digit) } else { current.checked_add(digit) }
        });
    }
    Some(value)
}

/// `bytes.splitlines(keepends=True)`: boundaries are `\n`, `\r\n`, and `\r` only.
pub fn bytes_splitlines_keepends(data: &[u8]) -> Vec<&[u8]> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut index = 0;
    while index < data.len() {
        match data[index] {
            b'\n' => {
                lines.push(&data[start..=index]);
                start = index + 1;
            }
            b'\r' => {
                if index + 1 < data.len() && data[index + 1] == b'\n' {
                    index += 1;
                }
                lines.push(&data[start..=index]);
                start = index + 1;
            }
            _ => {}
        }
        index += 1;
    }
    if start < data.len() {
        lines.push(&data[start..]);
    }
    lines
}

/// `data.rstrip(b"\r\n")`.
pub fn rstrip_crlf(data: &[u8]) -> &[u8] {
    let mut end = data.len();
    while end > 0 && matches!(data[end - 1], b'\r' | b'\n') {
        end -= 1;
    }
    &data[..end]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utf8_ignore_drops_the_maximal_subparts_replace_marks() {
        let cases: &[(&[u8], &str, &str)] = &[
            (b"abc", "abc", "abc"),
            (b"\xf0\x80\x80", "\u{fffd}\u{fffd}\u{fffd}", ""),
            (b"\xed\xa0\x80x", "\u{fffd}\u{fffd}\u{fffd}x", "x"),
            (b"\xe2\x82", "\u{fffd}", ""),
            (b"a\xe2\x82b", "a\u{fffd}b", "ab"),
            (b"\xef\xbf\xbd", "\u{fffd}", "\u{fffd}"),
            (b"\xf4\x90\x80\x80", "\u{fffd}\u{fffd}\u{fffd}\u{fffd}", ""),
            (b"\xc0\xaf", "\u{fffd}\u{fffd}", ""),
            (b"\xf0\x9f\x98", "\u{fffd}", ""),
        ];
        for (input, replaced, ignored) in cases {
            assert_eq!(utf8_replace(input), *replaced);
            assert_eq!(utf8_ignore(input), *ignored);
        }
    }

    #[test]
    fn py_int_matches_int_for_ascii() {
        assert_eq!(ascii_py_int("\t 12 \n"), Some(Some(12)));
        assert_eq!(ascii_py_int("-1_0"), Some(Some(-10)));
        assert_eq!(ascii_py_int("+7"), Some(Some(7)));
        assert_eq!(ascii_py_int("1__0"), None);
        assert_eq!(ascii_py_int("_1"), None);
        assert_eq!(ascii_py_int("1_"), None);
        assert_eq!(ascii_py_int(""), None);
        assert_eq!(ascii_py_int("- 1"), None);
        assert_eq!(ascii_py_int("99999999999999999999"), Some(None));
    }

    #[test]
    fn splitting_matches_python() {
        assert_eq!(ascii_splitlines("a\r\nb\rc\x0bd\n"), vec!["a", "b", "c", "d"]);
        assert_eq!(ascii_splitlines(""), Vec::<&str>::new());
        assert_eq!(bytes_splitlines_keepends(b"a\r\nb\rc\x0bd"), vec![&b"a\r\n"[..], b"b\r", b"c\x0bd"]);
        assert_eq!(universal_newlines("a\r\nb\rc"), "a\nb\nc");
    }
}
