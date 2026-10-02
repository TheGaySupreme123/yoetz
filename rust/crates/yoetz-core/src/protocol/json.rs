//! Strict wire-JSON scanner, the Rust twin of `strict_json_parse`.
//!
//! The scanner reproduces CPython's `json` C scanner token for token (number backtracking,
//! `\uXXXX` surrogate joining, strict control-character refusal) so that the *first* failure
//! a document triggers is the same failure the reference reports: number hooks fire as each
//! number is scanned, and duplicate keys are refused when their object closes. It is iterative,
//! so hostile nesting can never exhaust the native stack.

use super::canonical::{
    BYTE_ORDER_MARK_FORBIDDEN, DUPLICATE_OBJECT_KEY, FLOAT_FORBIDDEN, INTEGER_OUT_OF_SAFE_RANGE,
    INVALID_UTF8, MALFORMED_JSON, MAX_SAFE_INTEGER, NESTING_TOO_DEEP, NUL_BYTE_FORBIDDEN, Reason,
    Value,
};

/// Nesting at which the reference interpreter's recursion guard fires (approximately; CPython
/// 3.14 bounds by native stack, which admits ~20k levels from a shallow caller).
pub const MAX_PARSE_NESTING: usize = 20_000;

/// A decoded JSON string.
pub enum JsonText<'a> {
    /// No escapes: a slice of the input.
    Borrowed(&'a str),
    /// Escapes decoded; every code point is a Unicode scalar value.
    Owned(String),
    /// Contains at least one lone surrogate code point (only `\uD800`-style escapes do this).
    Wide(Vec<u32>),
}

/// Receives scanned tokens and builds values. Errors abort the scan immediately.
pub trait JsonSink {
    type Value;
    type Key;
    type Array;
    type Object;
    type Error;

    fn fail(&mut self, reason: Reason) -> Self::Error;
    fn null(&mut self) -> Result<Self::Value, Self::Error>;
    fn boolean(&mut self, value: bool) -> Result<Self::Value, Self::Error>;
    /// An integer literal (no fraction or exponent). `-0` and range are the sink's to judge.
    fn integer(&mut self, literal: &str) -> Result<Self::Value, Self::Error>;
    /// A float literal or a `NaN`/`Infinity`/`-Infinity` constant.
    fn float(&mut self, literal: &str) -> Result<Self::Value, Self::Error>;
    fn string(&mut self, text: JsonText<'_>) -> Result<Self::Value, Self::Error>;
    fn key(&mut self, text: JsonText<'_>) -> Result<Self::Key, Self::Error>;
    fn begin_array(&mut self) -> Result<Self::Array, Self::Error>;
    fn push(&mut self, array: &mut Self::Array, value: Self::Value) -> Result<(), Self::Error>;
    fn end_array(&mut self, array: Self::Array) -> Result<Self::Value, Self::Error>;
    fn begin_object(&mut self) -> Result<Self::Object, Self::Error>;
    fn insert(
        &mut self,
        object: &mut Self::Object,
        key: Self::Key,
        value: Self::Value,
    ) -> Result<(), Self::Error>;
    fn end_object(&mut self, object: Self::Object) -> Result<Self::Value, Self::Error>;
}

/// The byte-level gates `strict_json_parse` applies before scanning.
pub fn precheck(raw: &[u8]) -> Result<&str, Reason> {
    if memchr::memchr(0, raw).is_some() {
        return Err(NUL_BYTE_FORBIDDEN);
    }
    let text = std::str::from_utf8(raw).map_err(|_| INVALID_UTF8)?;
    if text.starts_with('\u{feff}') {
        return Err(BYTE_ORDER_MARK_FORBIDDEN);
    }
    Ok(text)
}

enum Frame<S: JsonSink> {
    Array(S::Array),
    Object(S::Object, Option<S::Key>),
}

/// Decoded string text; switches to code points only when a lone surrogate appears.
enum TextBuffer {
    Utf8(String),
    Wide(Vec<u32>),
}

impl TextBuffer {
    #[inline]
    fn push_str(&mut self, text: &str) {
        match self {
            TextBuffer::Utf8(buffer) => buffer.push_str(text),
            TextBuffer::Wide(points) => points.extend(text.chars().map(u32::from)),
        }
    }

    #[inline]
    fn push_point(&mut self, point: u32) {
        match self {
            TextBuffer::Utf8(buffer) => match char::from_u32(point) {
                Some(character) => buffer.push(character),
                None => {
                    let mut points: Vec<u32> = buffer.chars().map(u32::from).collect();
                    points.push(point);
                    *self = TextBuffer::Wide(points);
                }
            },
            TextBuffer::Wide(points) => points.push(point),
        }
    }
}

struct Scanner<'a> {
    text: &'a str,
    bytes: &'a [u8],
    pos: usize,
}

#[inline]
fn is_ws(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r')
}

impl<'a> Scanner<'a> {
    #[inline]
    fn skip_ws(&mut self) {
        while self.pos < self.bytes.len() && is_ws(self.bytes[self.pos]) {
            self.pos += 1;
        }
    }

    #[inline]
    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.pos).copied()
    }

    #[inline]
    fn starts_with(&self, literal: &[u8]) -> bool {
        self.bytes[self.pos..].starts_with(literal)
    }

    /// Scan a string whose opening quote is at `self.pos - 1`.
    fn string(&mut self) -> Result<JsonText<'a>, Reason> {
        let start = self.pos;
        // Fast path: no escape before the closing quote.
        let mut index = start;
        while index < self.bytes.len() {
            let byte = self.bytes[index];
            if byte == b'"' {
                self.pos = index + 1;
                return Ok(JsonText::Borrowed(&self.text[start..index]));
            }
            if byte == b'\\' {
                break;
            }
            if byte < 0x20 {
                return Err(MALFORMED_JSON);
            }
            index += 1;
        }
        if index >= self.bytes.len() {
            return Err(MALFORMED_JSON);
        }
        let mut buffer = TextBuffer::Utf8(String::with_capacity(index - start + 16));
        buffer.push_str(&self.text[start..index]);
        self.pos = index;
        loop {
            let Some(byte) = self.peek() else {
                return Err(MALFORMED_JSON);
            };
            match byte {
                b'"' => {
                    self.pos += 1;
                    break;
                }
                b'\\' => {
                    self.pos += 1;
                    let Some(escape) = self.peek() else {
                        return Err(MALFORMED_JSON);
                    };
                    self.pos += 1;
                    let decoded = match escape {
                        b'"' => u32::from(b'"'),
                        b'\\' => u32::from(b'\\'),
                        b'/' => u32::from(b'/'),
                        b'b' => 0x08,
                        b'f' => 0x0C,
                        b'n' => 0x0A,
                        b'r' => 0x0D,
                        b't' => 0x09,
                        b'u' => {
                            // CPython requires a byte after the four digits (`end >= len` fails).
                            if self.pos + 4 >= self.bytes.len() {
                                return Err(MALFORMED_JSON);
                            }
                            let mut unit = self.hex4(self.pos)?;
                            self.pos += 4;
                            if (0xD800..=0xDBFF).contains(&unit)
                                && self.pos + 6 < self.bytes.len()
                                && self.bytes[self.pos] == b'\\'
                                && self.bytes[self.pos + 1] == b'u'
                            {
                                let low = self.hex4(self.pos + 2)?;
                                if (0xDC00..=0xDFFF).contains(&low) {
                                    unit = 0x1_0000 + ((unit - 0xD800) << 10) + (low - 0xDC00);
                                    self.pos += 6;
                                }
                            }
                            unit
                        }
                        _ => return Err(MALFORMED_JSON),
                    };
                    buffer.push_point(decoded);
                }
                byte if byte < 0x20 => return Err(MALFORMED_JSON),
                _ => {
                    // Copy the plain run up to the next quote, backslash, or control byte.
                    let run_start = self.pos;
                    let mut run_end = run_start;
                    while run_end < self.bytes.len() {
                        let current = self.bytes[run_end];
                        if current == b'"' || current == b'\\' || current < 0x20 {
                            break;
                        }
                        run_end += 1;
                    }
                    buffer.push_str(&self.text[run_start..run_end]);
                    self.pos = run_end;
                }
            }
        }
        Ok(match buffer {
            TextBuffer::Utf8(text) => JsonText::Owned(text),
            TextBuffer::Wide(points) => JsonText::Wide(points),
        })
    }

    fn hex4(&self, at: usize) -> Result<u32, Reason> {
        let mut unit = 0_u32;
        for offset in 0..4 {
            let digit = self.bytes[at + offset];
            let value = match digit {
                b'0'..=b'9' => digit - b'0',
                b'a'..=b'f' => digit - b'a' + 10,
                b'A'..=b'F' => digit - b'A' + 10,
                _ => return Err(MALFORMED_JSON),
            };
            unit = (unit << 4) | u32::from(value);
        }
        Ok(unit)
    }

    /// Match a number at `self.pos` exactly as CPython's `_match_number_unicode` does.
    /// Returns `(literal, is_float)` or `None` when no number starts here.
    fn number(&mut self) -> Option<(&'a str, bool)> {
        let start = self.pos;
        let bytes = self.bytes;
        let mut index = start;
        if bytes.get(index) == Some(&b'-') {
            index += 1;
            if index >= bytes.len() {
                return None;
            }
        }
        match bytes.get(index) {
            Some(b'1'..=b'9') => {
                index += 1;
                while index < bytes.len() && bytes[index].is_ascii_digit() {
                    index += 1;
                }
            }
            Some(b'0') => index += 1,
            _ => return None,
        }
        let mut is_float = false;
        if index + 1 < bytes.len() && bytes[index] == b'.' && bytes[index + 1].is_ascii_digit() {
            is_float = true;
            index += 2;
            while index < bytes.len() && bytes[index].is_ascii_digit() {
                index += 1;
            }
        }
        if index < bytes.len() && (bytes[index] == b'e' || bytes[index] == b'E') {
            let exponent_start = index;
            index += 1;
            if index < bytes.len() && (bytes[index] == b'-' || bytes[index] == b'+') {
                index += 1;
            }
            let digits_start = index;
            while index < bytes.len() && bytes[index].is_ascii_digit() {
                index += 1;
            }
            if index > digits_start {
                is_float = true;
            } else {
                index = exponent_start;
            }
        }
        self.pos = index;
        Some((&self.text[start..index], is_float))
    }
}

/// Scan `text` (already prechecked) into the sink's value.
pub fn scan<S: JsonSink>(text: &str, sink: &mut S) -> Result<S::Value, S::Error> {
    let mut scanner = Scanner {
        text,
        bytes: text.as_bytes(),
        pos: 0,
    };
    let mut stack: Vec<Frame<S>> = Vec::new();
    scanner.skip_ws();
    'value: loop {
        // A value is expected at scanner.pos.
        let Some(byte) = scanner.peek() else {
            return Err(sink.fail(MALFORMED_JSON));
        };
        let mut value: S::Value = match byte {
            b'"' => {
                scanner.pos += 1;
                let text = scanner.string().map_err(|reason| sink.fail(reason))?;
                sink.string(text)?
            }
            b'[' => {
                if stack.len() >= MAX_PARSE_NESTING {
                    return Err(sink.fail(NESTING_TOO_DEEP));
                }
                scanner.pos += 1;
                scanner.skip_ws();
                let array = sink.begin_array()?;
                if scanner.peek() == Some(b']') {
                    scanner.pos += 1;
                    sink.end_array(array)?
                } else {
                    stack.push(Frame::Array(array));
                    continue 'value;
                }
            }
            b'{' => {
                if stack.len() >= MAX_PARSE_NESTING {
                    return Err(sink.fail(NESTING_TOO_DEEP));
                }
                scanner.pos += 1;
                scanner.skip_ws();
                let object = sink.begin_object()?;
                if scanner.peek() == Some(b'}') {
                    scanner.pos += 1;
                    sink.end_object(object)?
                } else {
                    let key = scan_key(&mut scanner, sink)?;
                    stack.push(Frame::Object(object, Some(key)));
                    continue 'value;
                }
            }
            b'n' if scanner.starts_with(b"null") => {
                scanner.pos += 4;
                sink.null()?
            }
            b't' if scanner.starts_with(b"true") => {
                scanner.pos += 4;
                sink.boolean(true)?
            }
            b'f' if scanner.starts_with(b"false") => {
                scanner.pos += 5;
                sink.boolean(false)?
            }
            b'N' if scanner.starts_with(b"NaN") => {
                scanner.pos += 3;
                sink.float("NaN")?
            }
            b'I' if scanner.starts_with(b"Infinity") => {
                scanner.pos += 8;
                sink.float("Infinity")?
            }
            b'-' if scanner.starts_with(b"-Infinity") => {
                scanner.pos += 9;
                sink.float("-Infinity")?
            }
            _ => match scanner.number() {
                Some((literal, true)) => sink.float(literal)?,
                Some((literal, false)) => sink.integer(literal)?,
                None => return Err(sink.fail(MALFORMED_JSON)),
            },
        };
        // Attach the completed value to its parent, closing containers as they finish.
        loop {
            match stack.last_mut() {
                None => {
                    scanner.skip_ws();
                    if scanner.pos != scanner.bytes.len() {
                        return Err(sink.fail(MALFORMED_JSON));
                    }
                    return Ok(value);
                }
                Some(Frame::Array(array)) => {
                    sink.push(array, value)?;
                    scanner.skip_ws();
                    match scanner.peek() {
                        Some(b',') => {
                            scanner.pos += 1;
                            scanner.skip_ws();
                            continue 'value;
                        }
                        Some(b']') => {
                            scanner.pos += 1;
                            let Some(Frame::Array(array)) = stack.pop() else {
                                unreachable!()
                            };
                            value = sink.end_array(array)?;
                        }
                        _ => return Err(sink.fail(MALFORMED_JSON)),
                    }
                }
                Some(Frame::Object(object, key)) => {
                    let key = key.take().expect("object frame holds its pending key");
                    sink.insert(object, key, value)?;
                    scanner.skip_ws();
                    match scanner.peek() {
                        Some(b',') => {
                            scanner.pos += 1;
                            scanner.skip_ws();
                            let next = scan_key(&mut scanner, sink)?;
                            if let Some(Frame::Object(_, slot)) = stack.last_mut() {
                                *slot = Some(next);
                            }
                            continue 'value;
                        }
                        Some(b'}') => {
                            scanner.pos += 1;
                            let Some(Frame::Object(object, _)) = stack.pop() else {
                                unreachable!()
                            };
                            value = sink.end_object(object)?;
                        }
                        _ => return Err(sink.fail(MALFORMED_JSON)),
                    }
                }
            }
        }
    }
}

/// Scan `"key"` `:` and leave the scanner at the member value.
fn scan_key<S: JsonSink>(scanner: &mut Scanner<'_>, sink: &mut S) -> Result<S::Key, S::Error> {
    if scanner.peek() != Some(b'"') {
        return Err(sink.fail(MALFORMED_JSON));
    }
    scanner.pos += 1;
    let text = scanner.string().map_err(|reason| sink.fail(reason))?;
    let key = sink.key(text)?;
    scanner.skip_ws();
    if scanner.peek() != Some(b':') {
        return Err(sink.fail(MALFORMED_JSON));
    }
    scanner.pos += 1;
    scanner.skip_ws();
    Ok(key)
}

/// A sink building [`Value`] trees for native callers.
pub struct ValueSink;

impl JsonSink for ValueSink {
    type Value = Value;
    type Key = String;
    type Array = Vec<Value>;
    type Object = Vec<(String, Value)>;
    type Error = Reason;

    fn fail(&mut self, reason: Reason) -> Reason {
        reason
    }
    fn null(&mut self) -> Result<Value, Reason> {
        Ok(Value::Null)
    }
    fn boolean(&mut self, value: bool) -> Result<Value, Reason> {
        Ok(Value::Bool(value))
    }
    fn integer(&mut self, literal: &str) -> Result<Value, Reason> {
        if literal == "-0" {
            return Err(FLOAT_FORBIDDEN);
        }
        let parsed: i64 = literal.parse().map_err(|_| INTEGER_OUT_OF_SAFE_RANGE)?;
        if !(-MAX_SAFE_INTEGER..=MAX_SAFE_INTEGER).contains(&parsed) {
            return Err(INTEGER_OUT_OF_SAFE_RANGE);
        }
        Ok(Value::Int(parsed))
    }
    fn float(&mut self, _literal: &str) -> Result<Value, Reason> {
        Err(FLOAT_FORBIDDEN)
    }
    fn string(&mut self, text: JsonText<'_>) -> Result<Value, Reason> {
        Ok(Value::Str(owned(text)?))
    }
    fn key(&mut self, text: JsonText<'_>) -> Result<String, Reason> {
        owned(text)
    }
    fn begin_array(&mut self) -> Result<Vec<Value>, Reason> {
        Ok(Vec::new())
    }
    fn push(&mut self, array: &mut Vec<Value>, value: Value) -> Result<(), Reason> {
        array.push(value);
        Ok(())
    }
    fn end_array(&mut self, array: Vec<Value>) -> Result<Value, Reason> {
        Ok(Value::Array(array))
    }
    fn begin_object(&mut self) -> Result<Vec<(String, Value)>, Reason> {
        Ok(Vec::new())
    }
    fn insert(
        &mut self,
        object: &mut Vec<(String, Value)>,
        key: String,
        value: Value,
    ) -> Result<(), Reason> {
        object.push((key, value));
        Ok(())
    }
    fn end_object(&mut self, object: Vec<(String, Value)>) -> Result<Value, Reason> {
        let mut keys: Vec<&str> = object.iter().map(|(key, _)| key.as_str()).collect();
        keys.sort_unstable();
        if keys.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(DUPLICATE_OBJECT_KEY);
        }
        Ok(Value::Object(object))
    }
}

fn owned(text: JsonText<'_>) -> Result<String, Reason> {
    match text {
        JsonText::Borrowed(text) => Ok(text.to_owned()),
        JsonText::Owned(text) => Ok(text),
        // A native Value cannot hold a lone surrogate; the profile refuses it anyway.
        JsonText::Wide(_) => Err(super::canonical::LONE_SURROGATE),
    }
}

/// Parse and validate strict wire JSON into a [`Value`].
pub fn parse(raw: &[u8]) -> Result<Value, Reason> {
    let text = precheck(raw)?;
    let value = scan(text, &mut ValueSink)?;
    // Validation (depth bound, NUL) uses the encoder's exact checks and order.
    super::canonical::encode(&value)?;
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_like_reference() {
        for (source, reason) in [
            (&b"1.0"[..], FLOAT_FORBIDDEN),
            (b"-0", FLOAT_FORBIDDEN),
            (b"-0e", FLOAT_FORBIDDEN),
            (b"NaN", FLOAT_FORBIDDEN),
            (b"[1,]", MALFORMED_JSON),
            (b"{\"a\":1,\"a\":2}", DUPLICATE_OBJECT_KEY),
            (b"9007199254740992", INTEGER_OUT_OF_SAFE_RANGE),
            (b"\"\\ud800\"", super::super::canonical::LONE_SURROGATE),
            (b"\xef\xbb\xbf1", BYTE_ORDER_MARK_FORBIDDEN),
            (b"\xff", INVALID_UTF8),
            (b"1\x00", NUL_BYTE_FORBIDDEN),
            (b"01", MALFORMED_JSON),
            (b"", MALFORMED_JSON),
        ] {
            assert_eq!(parse(source), Err(reason), "{source:?}");
        }
    }

    #[test]
    fn parses_nested_values() {
        let value =
            parse(b" {\"b\": [1, true, null, \"x\\u00e9\\ud83d\\ude00\"], \"a\": {}} ").unwrap();
        assert_eq!(
            String::from_utf8(super::super::canonical::encode(&value).unwrap()).unwrap(),
            "{\"a\":{},\"b\":[1,true,null,\"x\u{e9}\u{1f600}\"]}"
        );
    }

    #[test]
    fn hostile_nesting_is_bounded() {
        let depth = MAX_PARSE_NESTING + 10;
        let mut source = vec![b'['; depth];
        source.extend(std::iter::repeat_n(b']', depth));
        assert_eq!(parse(&source), Err(NESTING_TOO_DEEP));
    }
}
