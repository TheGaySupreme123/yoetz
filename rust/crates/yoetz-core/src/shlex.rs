//! Exact port of CPython's `shlex.split` (posix mode), `shlex.quote`, and `shlex.join`.
//!
//! `shlex.split(s)` builds `shlex.shlex(s, posix=True)` with `whitespace_split = True` and no
//! commenters, then drains it. This module reproduces that lexer state machine character for
//! character (Python code points; callers hand in a `&str`, so a Python string with a lone
//! surrogate must stay on the Python path). It is deliberately not the `shlex` crate, whose
//! escape rules differ from CPython's.
//!
//! The tables are CPython's: whitespace is exactly `' \t\r\n'` (no Unicode spaces), quotes are
//! `'` and `"`, the escape is `\`, only `"` honors escapes inside quotes, and
//! `punctuation_chars` is off. `wordchars` is kept for fidelity even though, with
//! `whitespace_split` on, a word character and any other non-special character take the same
//! transition.

/// Why a split failed; `message()` is CPython's exact `ValueError` text.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShlexError {
    NoClosingQuotation,
    NoEscapedCharacter,
}

impl ShlexError {
    pub fn message(self) -> &'static str {
        match self {
            ShlexError::NoClosingQuotation => "No closing quotation",
            ShlexError::NoEscapedCharacter => "No escaped character",
        }
    }
}

/// `shlex.shlex.whitespace`.
pub const WHITESPACE: &str = " \t\r\n";
/// `shlex.shlex.wordchars` in posix mode.
pub const WORDCHARS: &str = concat!(
    "abcdfeghijklmnopqrstuvwxyz",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_",
    "ßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ",
    "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞ",
);

#[inline]
fn is_whitespace(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\r' | '\n')
}

#[inline]
fn is_wordchar(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || (!c.is_ascii() && WORDCHARS.contains(c))
}

#[inline]
fn is_quote(c: char) -> bool {
    c == '\'' || c == '"'
}

/// Lexer state (`shlex.state`).
#[derive(Clone, Copy, PartialEq, Eq)]
enum State {
    /// `' '`
    Whitespace,
    /// `'a'`
    Word,
    /// A quote character.
    Quote(char),
    /// The escape character `\`.
    Escape,
    /// `None`: past end of input.
    End,
}

/// `shlex.split(s, comments=False, posix=True)`.
pub fn split(s: &str) -> Result<Vec<String>, ShlexError> {
    let mut tokens = Vec::new();
    let mut chars = s.chars();
    let mut state = State::Whitespace;
    loop {
        // One `read_token` call.
        let mut token = String::new();
        let mut quoted = false;
        // `escapedstate`: the state an escape returns to ('a' or a quote character).
        let mut escaped_from = State::Word;
        loop {
            let next = chars.next();
            match state {
                State::End => {
                    token.clear();
                    break;
                }
                State::Whitespace => match next {
                    None => {
                        state = State::End;
                        break;
                    }
                    Some(c) if is_whitespace(c) => {
                        if !token.is_empty() || quoted {
                            break;
                        }
                    }
                    Some('\\') => {
                        escaped_from = State::Word;
                        state = State::Escape;
                    }
                    Some(c) if is_wordchar(c) => {
                        token.clear();
                        token.push(c);
                        state = State::Word;
                    }
                    Some(c) if is_quote(c) => state = State::Quote(c),
                    Some(c) => {
                        // whitespace_split
                        token.clear();
                        token.push(c);
                        state = State::Word;
                    }
                },
                State::Quote(quote) => {
                    quoted = true;
                    match next {
                        None => return Err(ShlexError::NoClosingQuotation),
                        Some(c) if c == quote => state = State::Word,
                        Some('\\') if quote == '"' => {
                            escaped_from = state;
                            state = State::Escape;
                        }
                        Some(c) => token.push(c),
                    }
                }
                State::Escape => match next {
                    None => return Err(ShlexError::NoEscapedCharacter),
                    Some(c) => {
                        // In posix shells, only the quote itself or the escape character may be
                        // escaped within quotes.
                        if let State::Quote(quote) = escaped_from {
                            if c != '\\' && c != quote {
                                token.push('\\');
                            }
                        }
                        token.push(c);
                        state = escaped_from;
                    }
                },
                State::Word => match next {
                    None => {
                        state = State::End;
                        break;
                    }
                    Some(c) if is_whitespace(c) => {
                        state = State::Whitespace;
                        if !token.is_empty() || quoted {
                            break;
                        }
                    }
                    Some(c) if is_quote(c) => state = State::Quote(c),
                    Some('\\') => {
                        escaped_from = State::Word;
                        state = State::Escape;
                    }
                    // wordchars, quotes, or anything under whitespace_split
                    Some(c) => token.push(c),
                },
            }
        }
        if !quoted && token.is_empty() {
            // posix eof (`None`) ends iteration.
            return Ok(tokens);
        }
        tokens.push(token);
    }
}

/// Characters `shlex.quote` leaves unquoted.
#[inline]
fn is_safe(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || b"%+,-./:=@_".contains(&byte)
}

/// Whether `shlex.quote(s)` returns `s` unchanged (non-empty, only safe ASCII).
pub fn is_quote_safe(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(is_safe)
}

/// `shlex.quote(s)` appended to `out`.
pub fn quote_into(out: &mut String, s: &str) {
    if s.is_empty() {
        out.push_str("''");
        return;
    }
    if s.bytes().all(is_safe) {
        out.push_str(s);
        return;
    }
    out.push('\'');
    let mut rest = s;
    while let Some(index) = rest.find('\'') {
        out.push_str(&rest[..index]);
        out.push_str("'\"'\"'");
        rest = &rest[index + 1..];
    }
    out.push_str(rest);
    out.push('\'');
}

/// `shlex.quote(s)`.
pub fn quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    quote_into(&mut out, s);
    out
}

/// `shlex.join(words)`.
pub fn join<S: AsRef<str>>(words: &[S]) -> String {
    let mut out = String::new();
    for (index, word) in words.iter().enumerate() {
        if index > 0 {
            out.push(' ');
        }
        quote_into(&mut out, word.as_ref());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ok(s: &str) -> Vec<String> {
        split(s).expect("split")
    }

    #[test]
    fn splits_like_cpython() {
        assert_eq!(ok("a b  c"), ["a", "b", "c"]);
        assert_eq!(ok(""), Vec::<String>::new());
        assert_eq!(ok("   "), Vec::<String>::new());
        assert_eq!(ok("a ''"), ["a", ""]);
        assert_eq!(ok("''"), [""]);
        assert_eq!(ok("'a b'c"), ["a bc"]);
        assert_eq!(ok(r#""a\"b""#), [r#"a"b"#]);
        assert_eq!(ok(r#""a\nb""#), [r"a\nb"]);
        assert_eq!(ok(r"'a\b'"), [r"a\b"]);
        assert_eq!(ok(r"a\ b"), ["a b"]);
        assert_eq!(ok("a#b #c"), ["a#b", "#c"]);
        assert_eq!(ok("a;b|c"), ["a;b|c"]);
        assert_eq!(ok("x\u{a0}y"), ["x\u{a0}y"]);
        assert_eq!(ok("x\x0by"), ["x\x0by"]);
        assert_eq!(ok("a\r\nb"), ["a", "b"]);
        assert_eq!(ok("\\\n"), ["\n"]);
        assert_eq!(ok(r#""\\""#), ["\\"]);
    }

    #[test]
    fn reports_cpython_errors() {
        assert_eq!(split("'a"), Err(ShlexError::NoClosingQuotation));
        assert_eq!(split("a\\"), Err(ShlexError::NoEscapedCharacter));
        assert_eq!(split("\"a\\"), Err(ShlexError::NoEscapedCharacter));
        assert_eq!(ShlexError::NoClosingQuotation.message(), "No closing quotation");
    }

    #[test]
    fn quotes_like_cpython() {
        assert_eq!(quote(""), "''");
        assert_eq!(quote("abc-./=@%+,:_"), "abc-./=@%+,:_");
        assert_eq!(quote("a b"), "'a b'");
        assert_eq!(quote("it's"), "'it'\"'\"'s'");
        assert_eq!(quote("é"), "'é'");
        assert_eq!(join(&["a", "b c", ""]), "a 'b c' ''");
    }
}
