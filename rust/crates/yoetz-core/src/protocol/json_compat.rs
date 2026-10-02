//! `json.loads`-compatible acceptance: the stdlib decoder's grammar with its value semantics.
//!
//! The strict wire scanner in [`super::json`] already reproduces CPython's `json` C scanner
//! token for token. This mode reuses that scanner with a sink that keeps what `json.loads`
//! keeps instead of what the wire profile keeps: integers of any length (the caller builds
//! them, so the interpreter's own `int_max_str_digits` rule decides), floats parsed exactly as
//! `float()` parses them (correctly rounded, overflow to infinity), and decoded strings.
//!
//! It is **accept-only**. Every caller pairs it with a Python reference whose refusals carry
//! caller-specific reasons and orders (an `object_pairs_hook` raising on a duplicate key, a
//! `parse_constant` hook, a post-parse tree walk). The mode admits a document only when the
//! reference is certain to admit the same value; anything else, including every input the
//! reference would refuse, yields `None` so the caller re-runs its reference for the exact
//! refusal. Rejections therefore carry no reason.

use std::borrow::Cow;
use std::collections::HashSet;

use super::canonical::Reason;
use super::json::{self, JsonSink, JsonText};

/// Hard ceiling on caller-supplied depth limits; deeper limits are refused (the caller then
/// runs its reference), so native recursion over an accepted tree stays bounded.
pub const MAX_COMPAT_DEPTH: usize = 512;

/// A value `json.loads` would build. `B` is the caller's big-integer representation.
#[derive(Debug, PartialEq)]
pub enum CompatValue<'a, B> {
    Null,
    Bool(bool),
    /// An integer literal of at most 18 digits.
    Int(i64),
    /// A longer integer literal, built by the caller's constructor.
    Big(B),
    Float(f64),
    Str(Cow<'a, str>),
    Array(Vec<CompatValue<'a, B>>),
    /// Members in document order; keys are unique.
    Object(Vec<(Cow<'a, str>, CompatValue<'a, B>)>),
}

/// What the caller's post-parse checks admit.
#[derive(Clone, Copy, Debug)]
pub struct CompatLimits {
    /// Deepest admitted depth of any value, the root being depth 0.
    pub max_value_depth: usize,
    /// Deepest admitted depth of a container (array or object), the root being depth 0.
    pub max_container_depth: usize,
    /// Admit a float literal that overflows to infinity (`float("1e400")`). The
    /// `NaN`/`Infinity`/`-Infinity` constants are never admitted.
    pub allow_overflow: bool,
}

/// Marker for "the reference decides".
#[derive(Debug, PartialEq, Eq)]
pub struct Deferred;

/// The byte gates every caller applies before decoding: non-empty, no NUL, no UTF-8 BOM, and
/// strict UTF-8.
pub fn precheck_line(raw: &[u8]) -> Option<&str> {
    if raw.is_empty() || raw.starts_with(b"\xef\xbb\xbf") || memchr::memchr(0, raw).is_some() {
        return None;
    }
    std::str::from_utf8(raw).ok()
}

/// `float(literal)` for a JSON number lexeme. Rust's float parser is correctly rounded and
/// overflows to infinity like CPython's `PyOS_string_to_double` path for `float()`.
pub fn python_float(literal: &str) -> Option<f64> {
    literal.parse::<f64>().ok()
}

struct CompatSink<'a, F> {
    text: &'a str,
    depth: usize,
    limits: CompatLimits,
    big: F,
}

impl<'a, F> CompatSink<'a, F> {
    #[inline]
    fn scalar_depth_ok(&self) -> bool {
        self.depth <= self.limits.max_value_depth
    }

    /// Recover the input-lifetime slice for a borrowed string (always a subslice of `text`).
    fn text_of(&self, text: JsonText<'_>) -> Result<Cow<'a, str>, Deferred> {
        match text {
            JsonText::Borrowed(slice) => {
                let base = self.text.as_ptr() as usize;
                let at = slice.as_ptr() as usize;
                if at >= base && at + slice.len() <= base + self.text.len() {
                    let offset = at - base;
                    match self.text.get(offset..offset + slice.len()) {
                        Some(original) => Ok(Cow::Borrowed(original)),
                        None => Ok(Cow::Owned(slice.to_owned())),
                    }
                } else {
                    Ok(Cow::Owned(slice.to_owned()))
                }
            }
            JsonText::Owned(owned) => Ok(Cow::Owned(owned)),
            // A lone surrogate: every caller's post-parse walk refuses unencodable text.
            JsonText::Wide(_) => Err(Deferred),
        }
    }

    fn open(&mut self) -> Result<(), Deferred> {
        if self.depth > self.limits.max_value_depth || self.depth > self.limits.max_container_depth {
            return Err(Deferred);
        }
        self.depth += 1;
        Ok(())
    }
}

fn has_duplicate<T>(members: &[(Cow<'_, str>, T)]) -> bool {
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
    let mut seen: HashSet<&str> = HashSet::with_capacity(members.len());
    members.iter().any(|(key, _)| !seen.insert(key.as_ref()))
}

impl<'a, B, F: FnMut(&str) -> Option<B>> JsonSink for CompatSink<'a, F> {
    type Value = CompatValue<'a, B>;
    type Key = Cow<'a, str>;
    type Array = Vec<CompatValue<'a, B>>;
    type Object = Vec<(Cow<'a, str>, CompatValue<'a, B>)>;
    type Error = Deferred;

    fn fail(&mut self, _reason: Reason) -> Deferred {
        Deferred
    }
    fn null(&mut self) -> Result<Self::Value, Deferred> {
        if !self.scalar_depth_ok() {
            return Err(Deferred);
        }
        Ok(CompatValue::Null)
    }
    fn boolean(&mut self, value: bool) -> Result<Self::Value, Deferred> {
        if !self.scalar_depth_ok() {
            return Err(Deferred);
        }
        Ok(CompatValue::Bool(value))
    }
    fn integer(&mut self, literal: &str) -> Result<Self::Value, Deferred> {
        if !self.scalar_depth_ok() {
            return Err(Deferred);
        }
        let digits = literal.strip_prefix('-').unwrap_or(literal);
        if digits.len() <= 18 {
            return literal.parse::<i64>().map(CompatValue::Int).map_err(|_| Deferred);
        }
        (self.big)(literal).map(CompatValue::Big).ok_or(Deferred)
    }
    fn float(&mut self, literal: &str) -> Result<Self::Value, Deferred> {
        if !self.scalar_depth_ok() {
            return Err(Deferred);
        }
        // The scanner reports the NaN/Infinity constants through this hook too; no caller's
        // `parse_constant` admits them.
        if matches!(literal, "NaN" | "Infinity" | "-Infinity") {
            return Err(Deferred);
        }
        let value = python_float(literal).ok_or(Deferred)?;
        if !value.is_finite() && !self.limits.allow_overflow {
            return Err(Deferred);
        }
        Ok(CompatValue::Float(value))
    }
    fn string(&mut self, text: JsonText<'_>) -> Result<Self::Value, Deferred> {
        if !self.scalar_depth_ok() {
            return Err(Deferred);
        }
        Ok(CompatValue::Str(self.text_of(text)?))
    }
    fn key(&mut self, text: JsonText<'_>) -> Result<Self::Key, Deferred> {
        self.text_of(text)
    }
    fn begin_array(&mut self) -> Result<Self::Array, Deferred> {
        self.open()?;
        Ok(Vec::new())
    }
    fn push(&mut self, array: &mut Self::Array, value: Self::Value) -> Result<(), Deferred> {
        array.push(value);
        Ok(())
    }
    fn end_array(&mut self, array: Self::Array) -> Result<Self::Value, Deferred> {
        self.depth -= 1;
        Ok(CompatValue::Array(array))
    }
    fn begin_object(&mut self) -> Result<Self::Object, Deferred> {
        self.open()?;
        Ok(Vec::new())
    }
    fn insert(&mut self, object: &mut Self::Object, key: Self::Key, value: Self::Value) -> Result<(), Deferred> {
        object.push((key, value));
        Ok(())
    }
    fn end_object(&mut self, object: Self::Object) -> Result<Self::Value, Deferred> {
        self.depth -= 1;
        // Every caller's object_pairs_hook refuses a repeated key.
        if has_duplicate(&object) {
            return Err(Deferred);
        }
        Ok(CompatValue::Object(object))
    }
}

/// Decode `text` as `json.loads` would, or `None` when the caller's reference must decide.
///
/// `big` builds an integer literal longer than 18 digits and returns `None` when the
/// interpreter would refuse it (for example past `sys.get_int_max_str_digits()`).
pub fn accept<'a, B, F: FnMut(&str) -> Option<B>>(
    text: &'a str,
    limits: CompatLimits,
    big: F,
) -> Option<CompatValue<'a, B>> {
    if limits.max_value_depth > MAX_COMPAT_DEPTH || limits.max_container_depth > MAX_COMPAT_DEPTH {
        return None;
    }
    let mut sink = CompatSink { text, depth: 0, limits, big };
    json::scan(text, &mut sink).ok()
}

/// [`accept`] for a top-level object read from raw line bytes.
pub fn accept_object_line<B, F: FnMut(&str) -> Option<B>>(
    raw: &[u8],
    limits: CompatLimits,
    big: F,
) -> Option<Vec<(Cow<'_, str>, CompatValue<'_, B>)>> {
    let text = precheck_line(raw)?;
    match accept(text, limits, big)? {
        CompatValue::Object(members) => Some(members),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const CODEX: CompatLimits =
        CompatLimits { max_value_depth: 64, max_container_depth: 64, allow_overflow: false };
    const MCP: CompatLimits =
        CompatLimits { max_value_depth: usize::MAX >> 1, max_container_depth: 63, allow_overflow: true };

    fn big(literal: &str) -> Option<String> {
        (literal.trim_start_matches('-').len() <= 4300).then(|| literal.to_owned())
    }

    fn accepts(source: &str, limits: CompatLimits) -> Option<CompatValue<'_, String>> {
        let limits = CompatLimits {
            max_value_depth: limits.max_value_depth.min(MAX_COMPAT_DEPTH),
            ..limits
        };
        accept(source, limits, big)
    }

    #[test]
    fn keeps_stdlib_values() {
        let value = accepts(r#"{"a": [1, -0, 1.5e3, 1e400, "xé", null, true], "b": 12345678901234567890}"#, MCP);
        let Some(CompatValue::Object(members)) = value else { panic!("object expected") };
        assert_eq!(members[0].0, "a");
        let CompatValue::Array(items) = &members[0].1 else { panic!("array expected") };
        assert_eq!(items[0], CompatValue::Int(1));
        assert_eq!(items[1], CompatValue::Int(0));
        assert_eq!(items[2], CompatValue::Float(1500.0));
        assert_eq!(items[3], CompatValue::Float(f64::INFINITY));
        assert_eq!(items[4], CompatValue::Str(Cow::Owned("x\u{e9}".to_owned())));
        assert_eq!(members[1].1, CompatValue::Big("12345678901234567890".to_owned()));
    }

    #[test]
    fn floats_round_like_python() {
        for (literal, expected) in [
            ("0.1", 0.1_f64),
            ("2.2250738585072011e-308", 2.225_073_858_507_201e-308),
            ("1e-400", 0.0),
            ("-0.0", -0.0),
            ("9007199254740993.0", 9_007_199_254_740_992.0),
        ] {
            assert_eq!(python_float(literal).map(f64::to_bits), Some(expected.to_bits()), "{literal}");
        }
    }

    #[test]
    fn defers_everything_a_reference_refuses() {
        for source in [
            "NaN",
            "[Infinity]",
            "-Infinity",
            r#"{"a":1,"a":2}"#,
            r#""\ud800""#,
            "[1,]",
            "01",
            "",
            " ",
            "\"\x01\"",
        ] {
            assert!(accepts(source, MCP).is_none(), "{source:?}");
        }
        assert!(accepts("1e400", CODEX).is_none());
        assert!(accepts(&format!("1{}", "0".repeat(4300)), CODEX).is_none());
    }

    #[test]
    fn duplicate_detection_covers_wide_objects() {
        let mut source = String::from("{");
        for index in 0..20 {
            source.push_str(&format!("\"k{index}\":{index},"));
        }
        source.push_str("\"k3\":0}");
        assert!(accepts(&source, CODEX).is_none());
        source.truncate(source.len() - 8);
        source.push('}');
        assert!(accepts(&source, CODEX).is_some());
    }

    #[test]
    fn depth_limits_match_each_reference() {
        // codex: values at depth <= 64; an empty container may sit at depth 64.
        let nested = |levels: usize, inner: &str| format!("{}{}{}", "[".repeat(levels), inner, "]".repeat(levels));
        assert!(accepts(&nested(65, ""), CODEX).is_some());
        assert!(accepts(&nested(66, ""), CODEX).is_none());
        assert!(accepts(&nested(64, "1"), CODEX).is_some());
        assert!(accepts(&nested(65, "1"), CODEX).is_none());
        // mcp: containers at depth <= 63 (root counted as depth 1 by the reference: <= 64).
        assert!(accepts(&nested(64, ""), MCP).is_some());
        assert!(accepts(&nested(65, ""), MCP).is_none());
        assert!(accepts(&nested(64, "1"), MCP).is_some());
        assert!(accepts(&nested(100_000, ""), MCP).is_none());
    }

    #[test]
    fn object_lines_require_an_object() {
        let limits = CODEX;
        assert!(accept_object_line(b"{\"a\":1}", limits, big).is_some());
        for raw in [&b"[1]"[..], b"", b"\xef\xbb\xbf{}", b"{\"a\":\"\x00\"}", b"{\"a\":\"\xff\"}"] {
            assert!(accept_object_line(raw, limits, big).is_none(), "{raw:?}");
        }
    }
}
