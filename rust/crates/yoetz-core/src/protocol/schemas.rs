//! Pure pieces of `yoetz.protocol.schemas` validity checking.
//!
//! `jsonschema` applies `pattern` with Python's `re.search` and the catalog's only checked format,
//! `date-time`, with `_is_rfc3339_date_time`. This module translates the subset of Python regular
//! expressions whose search semantics the `regex` crate reproduces exactly, and decides the
//! `date-time` format for every input whose answer does not depend on the Python version.
//! Everything outside those subsets returns `None` so the caller defers to Python.

use regex::{Regex, RegexBuilder};

/// Largest counted repetition accepted from a Python pattern.
const MAX_REPEAT: u32 = 1000;

/// Translate a Python `re` pattern into an equivalent `regex` crate pattern for `search`.
///
/// The accepted subset: literals (every one emitted as `\x{..}`), the escapes `\n \r \t \f \v \a`,
/// `\xHH`, `\uHHHH`, `\UHHHHHHHH` and escaped non-alphanumerics, `.`, character classes with
/// ranges and negation, `(...)` and `(?:...)` groups, `|`, `^`, a `$` that ends the pattern, and
/// the quantifiers `* + ? {n} {n,} {n,m} {,m}` with an optional lazy `?`. Python's `$` also
/// matches before a final newline; as the last token it becomes `\n?\z`, which accepts exactly
/// the same strings. Anything else (shorthand classes, word boundaries, lookaround, backreferences,
/// inline flags, possessive quantifiers, a literal `{`, surrogates) returns `None`.
pub fn translate_python_pattern(pattern: &str) -> Option<String> {
    let chars: Vec<char> = pattern.chars().collect();
    let mut out = String::with_capacity(pattern.len() * 4);
    let mut depth: usize = 0;
    // Whether the previous token can take a quantifier.
    let mut quantifiable = false;
    let mut index = 0;
    while index < chars.len() {
        let c = chars[index];
        index += 1;
        match c {
            '\\' => {
                let (literal, next) = escape(&chars, index, false)?;
                index = next;
                push_literal(&mut out, literal);
                quantifiable = true;
            }
            '[' => {
                index = class(&chars, index, &mut out)?;
                quantifiable = true;
            }
            '(' => {
                if chars.get(index) == Some(&'?') {
                    if chars.get(index + 1) != Some(&':') {
                        return None;
                    }
                    index += 2;
                }
                depth += 1;
                out.push_str("(?:");
                quantifiable = false;
            }
            ')' => {
                depth = depth.checked_sub(1)?;
                out.push(')');
                quantifiable = true;
            }
            '|' => {
                out.push('|');
                quantifiable = false;
            }
            '^' => {
                out.push('^');
                quantifiable = false;
            }
            '$' => {
                if index != chars.len() {
                    return None;
                }
                out.push_str(r"(?:\n?\z)");
                quantifiable = false;
            }
            '.' => {
                out.push('.');
                quantifiable = true;
            }
            '*' | '+' | '?' => {
                if !quantifiable {
                    return None;
                }
                out.push(c);
                index = lazy_suffix(&chars, index, &mut out)?;
                quantifiable = false;
            }
            '{' => {
                if !quantifiable {
                    return None;
                }
                let (low, high, next) = counted(&chars, index)?;
                index = next;
                match high {
                    Some(high) => out.push_str(&format!("{{{low},{high}}}")),
                    None => out.push_str(&format!("{{{low},}}")),
                }
                index = lazy_suffix(&chars, index, &mut out)?;
                quantifiable = false;
            }
            _ => {
                push_literal(&mut out, c);
                quantifiable = true;
            }
        }
    }
    if depth != 0 {
        return None;
    }
    Some(out)
}

/// Compile a Python pattern for `search` when the translation is exact.
pub fn compile_python_pattern(pattern: &str) -> Option<Regex> {
    let translated = translate_python_pattern(pattern)?;
    RegexBuilder::new(&translated).unicode(true).size_limit(1 << 26).build().ok()
}

fn push_literal(out: &mut String, literal: char) {
    out.push_str(&format!("\\x{{{:X}}}", literal as u32));
}

/// A lazy `?` after a quantifier is accepted; a possessive `+` or a stacked quantifier is not.
fn lazy_suffix(chars: &[char], index: usize, out: &mut String) -> Option<usize> {
    let mut index = index;
    if chars.get(index) == Some(&'?') {
        out.push('?');
        index += 1;
    }
    match chars.get(index) {
        Some('*' | '+' | '?' | '{') => None,
        _ => Some(index),
    }
}

/// `{n}`, `{n,}`, `{n,m}` or `{,m}` starting after the `{`. Anything else is a literal `{` in
/// Python, which this subset refuses.
fn counted(chars: &[char], index: usize) -> Option<(u32, Option<u32>, usize)> {
    let mut index = index;
    let low = digits(chars, &mut index);
    if chars.get(index) == Some(&'}') {
        let low = low?;
        return Some((low, Some(low), index + 1));
    }
    if chars.get(index) != Some(&',') {
        return None;
    }
    index += 1;
    let high = digits(chars, &mut index);
    if chars.get(index) != Some(&'}') {
        return None;
    }
    if low.is_none() && high.is_none() {
        return None;
    }
    let low = low.unwrap_or(0);
    if let Some(high) = high {
        if high < low {
            return None;
        }
    }
    Some((low, high, index + 1))
}

fn digits(chars: &[char], index: &mut usize) -> Option<u32> {
    let start = *index;
    let mut value: u32 = 0;
    while let Some(digit) = chars.get(*index).and_then(|c| c.to_digit(10)) {
        if !chars[*index].is_ascii_digit() {
            return None;
        }
        value = value.checked_mul(10)?.checked_add(digit)?;
        *index += 1;
    }
    if *index == start || value > MAX_REPEAT {
        return None;
    }
    Some(value)
}

/// One escape after `\` (at `index`), inside or outside a class.
fn escape(chars: &[char], index: usize, in_class: bool) -> Option<(char, usize)> {
    let e = *chars.get(index)?;
    let index = index + 1;
    if !e.is_ascii_alphanumeric() {
        return Some((e, index));
    }
    let literal = match e {
        'n' => '\n',
        'r' => '\r',
        't' => '\t',
        'f' => '\x0c',
        'v' => '\x0b',
        'a' => '\x07',
        'b' if in_class => '\x08',
        'x' => return hex(chars, index, 2),
        'u' => return hex(chars, index, 4),
        'U' => return hex(chars, index, 8),
        _ => return None,
    };
    Some((literal, index))
}

fn hex(chars: &[char], index: usize, width: usize) -> Option<(char, usize)> {
    let mut value: u32 = 0;
    for offset in 0..width {
        let c = *chars.get(index + offset)?;
        if !c.is_ascii_hexdigit() {
            return None;
        }
        value = value * 16 + c.to_digit(16)?;
    }
    // A surrogate is a valid Python pattern character but not a Rust `char`.
    Some((char::from_u32(value)?, index + width))
}

/// A character class starting after `[`, following `sre_parse` exactly for the accepted
/// subset: a leading `]` (after an optional `^`) is literal, `-` before `]` is literal, and a
/// range needs literal endpoints in order.
fn class(chars: &[char], index: usize, out: &mut String) -> Option<usize> {
    let mut index = index;
    out.push('[');
    if chars.get(index) == Some(&'^') {
        out.push('^');
        index += 1;
    }
    let mut members = 0;
    loop {
        let this = *chars.get(index)?;
        index += 1;
        if this == ']' && members > 0 {
            break;
        }
        let low = if this == '\\' {
            let (literal, next) = escape(chars, index, true)?;
            index = next;
            literal
        } else {
            this
        };
        members += 1;
        if chars.get(index) == Some(&'-') {
            let that = *chars.get(index + 1)?;
            if that == ']' {
                push_literal(out, low);
                push_literal(out, '-');
                index += 2;
                break;
            }
            index += 2;
            let high = if that == '\\' {
                let (literal, next) = escape(chars, index, true)?;
                index = next;
                literal
            } else {
                that
            };
            if high < low {
                return None;
            }
            push_literal(out, low);
            out.push('-');
            push_literal(out, high);
        } else {
            push_literal(out, low);
        }
    }
    out.push(']');
    Some(index)
}

/// `_is_rfc3339_date_time` for a string, or `None` when only Python can answer.
///
/// The string must fully match `[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z`
/// and name a real proleptic-Gregorian instant with year 1..=9999, hour 0..=23 and no leap
/// second, which is exactly what `datetime.fromisoformat` accepts for that shape. Hour 24, which
/// newer Pythons accept as the next midnight, is left to Python.
pub fn is_rfc3339_date_time(value: &str) -> Option<bool> {
    let bytes = value.as_bytes();
    const SHAPE: &[u8; 24] = b"dddd-dd-ddTdd:dd:dd.dddZ";
    if bytes.len() != SHAPE.len() {
        return Some(false);
    }
    for (byte, shape) in bytes.iter().zip(SHAPE.iter()) {
        let fits = if *shape == b'd' { byte.is_ascii_digit() } else { byte == shape };
        if !fits {
            return Some(false);
        }
    }
    let number = |start: usize, width: usize| -> u32 {
        bytes[start..start + width].iter().fold(0, |acc, digit| acc * 10 + u32::from(digit - b'0'))
    };
    let (year, month, day) = (number(0, 4), number(5, 2), number(8, 2));
    let (hour, minute, second) = (number(11, 2), number(14, 2), number(17, 2));
    if hour == 24 {
        return None;
    }
    if !(1..=9999).contains(&year) || !(1..=12).contains(&month) {
        return Some(false);
    }
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let days = match month {
        2 if leap => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    };
    Some(day >= 1 && day <= days && hour <= 23 && minute <= 59 && second <= 59)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn search(pattern: &str, text: &str) -> bool {
        compile_python_pattern(pattern).expect("translatable").is_match(text)
    }

    #[test]
    fn dollar_matches_before_a_final_newline_only() {
        assert!(search("^a$", "a"));
        assert!(search("^a$", "a\n"));
        assert!(!search("^a$", "a\n\n"));
        assert!(!search("^a$", "ab"));
        assert!(search("==$", "xx==\n"));
    }

    #[test]
    fn classes_follow_sre_parse() {
        assert!(search("^[a-c-e]+$", "a-e"));
        assert!(!search("^[a-c-e]+$", "d"));
        assert!(search("^[]a]$", "]"));
        assert!(search("^[^]a]$", "b"));
        assert!(search("^[a-z0-9!#$&^_.+-]*$", "a!#$&^_.+-"));
        assert!(search(r"^[^/\\\u0000]+$", "abc"));
        assert!(!search(r"^[^/\\\u0000]+$", "a\\b"));
        assert!(!search(r"^[^/\\\u0000]+$", "a\0b"));
        assert!(search(r"^[^\u0000-\u001f\u007f-\u009f]+$", "é"));
        assert!(!search(r"^[^\u0000-\u001f\u007f-\u009f]+$", "a\u{85}"));
        assert!(search("^[ -~]+$", "hello world"));
    }

    #[test]
    fn dot_never_matches_newline() {
        assert!(!search("^.$", "\n"));
        assert!(search("^.$", "é"));
    }

    #[test]
    fn quantifiers() {
        assert!(search("^[0-9]{4}$", "2024"));
        assert!(!search("^[0-9]{4}$", "202"));
        assert!(search("^a{,2}$", "aa"));
        assert!(!search("^a{,2}$", "aaa"));
        assert!(search("^(?:ab)+?c$", "ababc"));
    }

    #[test]
    fn refuses_outside_the_subset() {
        for pattern in [
            r"\d", r"\w", r"\s", r"\b", r"(?=a)", r"(?!a)", r"(?<=a)", r"(?P<n>a)", r"(a)\1",
            "(?i)a", "a*+", "a**", "a{", "a{x}", "a$b", r"\ud800", "(a", "a)", "*a", r"[\d]",
        ] {
            assert!(translate_python_pattern(pattern).is_none(), "{pattern}");
        }
    }

    #[test]
    fn date_time() {
        assert_eq!(is_rfc3339_date_time("2024-02-29T23:59:59.999Z"), Some(true));
        assert_eq!(is_rfc3339_date_time("2023-02-29T00:00:00.000Z"), Some(false));
        assert_eq!(is_rfc3339_date_time("0000-01-01T00:00:00.000Z"), Some(false));
        assert_eq!(is_rfc3339_date_time("2026-01-01T23:59:60.000Z"), Some(false));
        assert_eq!(is_rfc3339_date_time("2026-01-01T24:00:00.000Z"), None);
        assert_eq!(is_rfc3339_date_time("2026-01-01T23:59:59.000Z\n"), Some(false));
        assert_eq!(is_rfc3339_date_time("2026-01-01t23:59:59.000Z"), Some(false));
    }
}
