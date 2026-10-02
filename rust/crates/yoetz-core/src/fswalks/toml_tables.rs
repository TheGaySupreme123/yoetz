//! Twins of `exact_table_span` and `strip_exact_table` in
//! `yoetz.adapters.integrations.toml_tables` (pure bytes).

use super::pytext::{bytes_splitlines_keepends, rstrip_crlf};

/// `_TOML_TABLE_HEADER_RE.fullmatch(line)` for a line without `\r` or `\n`.
///
/// The pattern is `[ \t]*\[\[?[^\r\n\]]+\]\]?[ \t]*(?:#[^\r\n]*)?`. With no line breaks in the
/// subject, backtracking reduces it to: leading blanks, `[`, a non-empty run without `]`, the
/// first `]`, an optional second `]`, trailing blanks, and then the end or a `#` comment.
pub fn is_table_header(line: &[u8]) -> bool {
    debug_assert!(!line.contains(&b'\r') && !line.contains(&b'\n'));
    let mut index = 0;
    while index < line.len() && matches!(line[index], b' ' | b'\t') {
        index += 1;
    }
    if index >= line.len() || line[index] != b'[' {
        return false;
    }
    let body = &line[index + 1..];
    let Some(close) = body.iter().position(|byte| *byte == b']') else {
        return false;
    };
    if close == 0 {
        return false;
    }
    let mut rest = &body[close + 1..];
    if rest.first() == Some(&b']') {
        rest = &rest[1..];
    }
    let blanks = rest.iter().take_while(|byte| matches!(**byte, b' ' | b'\t')).count();
    let rest = &rest[blanks..];
    rest.is_empty() || rest[0] == b'#'
}

/// `exact_table_span(raw, table)` with `table` already UTF-8 encoded and non-empty.
pub fn exact_table_span(raw: &[u8], expected: &[u8]) -> Option<(usize, usize)> {
    let header = *bytes_splitlines_keepends(expected).first()?;
    let lines = bytes_splitlines_keepends(raw);
    let mut start: Option<usize> = None;
    let mut offset = 0;
    for line in &lines {
        if *line == header {
            if start.is_some() {
                return None;
            }
            start = Some(offset);
        }
        offset += line.len();
    }
    let start = start?;
    let mut end = raw.len();
    let mut offset = 0;
    let mut seen = false;
    for line in &lines {
        if offset == start {
            seen = true;
        } else if seen && is_table_header(rstrip_crlf(line)) {
            end = offset;
            break;
        }
        offset += line.len();
    }
    let candidate = rstrip_crlf(&raw[start..end]);
    if candidate.len() + 1 != expected.len() || !expected.starts_with(candidate) || expected[candidate.len()] != b'\n' {
        return None;
    }
    Some((start, end))
}

/// `strip_exact_table(raw, table)`.
pub fn strip_exact_table(raw: &[u8], table: &[u8]) -> Option<Vec<u8>> {
    let (start, _end) = exact_table_span(raw, table)?;
    let mut before = &raw[..start];
    // Python slicing clamps past the end.
    let after = raw.get(start + table.len()..).unwrap_or_default();
    if before.ends_with(b"\n\n") {
        before = &before[..before.len() - 1];
    }
    let mut merged = Vec::with_capacity(before.len() + after.len() + 1);
    merged.extend_from_slice(before);
    merged.extend_from_slice(after);
    if merged.is_empty() || merged == b"\n" {
        return Some(Vec::new());
    }
    if !merged.ends_with(b"\n") {
        merged.push(b'\n');
    }
    Some(merged)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn header_pattern() {
        for good in [&b"[a]"[..], b"  [[a.b]] # x", b"[[a]", b"[a]]", b"\t[a] \t", b"[[]", b"[a]#", b"[a b]"] {
            assert!(is_table_header(good), "{:?}", String::from_utf8_lossy(good));
        }
        for bad in [&b"[]"[..], b"a", b"[a] x", b"[a]]]", b"x[a]", b"[a", b"", b"[]]"] {
            assert!(!is_table_header(bad), "{:?}", String::from_utf8_lossy(bad));
        }
    }

    #[test]
    fn span_and_strip() {
        let table = b"[mcp_servers.yoetz]\ncommand = \"yoetz\"\n";
        let raw = b"[a]\nx = 1\n\n[mcp_servers.yoetz]\ncommand = \"yoetz\"\n";
        assert_eq!(exact_table_span(raw, table), Some((11, raw.len())));
        assert_eq!(strip_exact_table(raw, table), Some(b"[a]\nx = 1\n".to_vec()));
        let edited = b"[mcp_servers.yoetz]\ncommand = \"yoetz\"\nextra = 1\n";
        assert_eq!(exact_table_span(edited, table), None);
    }
}
