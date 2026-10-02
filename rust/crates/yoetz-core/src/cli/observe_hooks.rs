//! Lexical edit-capture helpers, the twins of `yoetz.cli.observe_hooks._absolute_path_key`,
//! `workspace_relative_edit_path`, `_sanitize_patch_paths`, `_sanitize_patch_result`,
//! `_shell_heredocs`, and `_codex_header_exit_codes`.
//!
//! Each regular expression of the reference is matched by hand. The patterns only branch at
//! ASCII bytes, so byte offsets here are code-point offsets there; `\s`/`\S` and `str.strip()`
//! use Python's `str.isspace()` set. Casefolding a non-ASCII locator is the one step not
//! reproduced: it yields [`Defer`], and the caller runs the Python reference for the whole call.

use std::borrow::Cow;

use crate::application::missing_for_assessment::is_python_space;

/// The Python reference must decide (a non-ASCII locator would be casefolded).
#[derive(Debug, PartialEq, Eq)]
pub struct Defer;

pub const OUTSIDE_WORKSPACE_PATH: &str = "<outside-workspace>";
pub const MAX_SHELL_HEREDOCS: usize = 8;
pub const CODEX_HEADER_MAX_LINES: usize = 12;

#[inline]
fn drive_letter(text: &str) -> Option<u8> {
    let bytes = text.as_bytes();
    (bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':').then(|| bytes[0])
}

/// `_absolute_path_key(value)`: a comparable absolute key and whether it compares
/// case-insensitively, or `None` for a relative path.
pub fn absolute_path_key(value: &str) -> Option<(Cow<'_, str>, bool)> {
    let text: Cow<'_, str> = if value.contains('\\') {
        Cow::Owned(value.replace('\\', "/"))
    } else {
        Cow::Borrowed(value)
    };
    let bytes = text.as_bytes();
    // ``^([A-Za-z]):/(.*)$`` (DOTALL)
    if let Some(letter) = drive_letter(&text) {
        if bytes.get(2) == Some(&b'/') {
            let mut key = String::with_capacity(text.len() + 4);
            key.push_str("/mnt/");
            key.push(char::from(letter.to_ascii_lowercase()));
            key.push('/');
            key.push_str(&text[3..]);
            return Some((Cow::Owned(key), true));
        }
    }
    if let Some(unc) = text.strip_prefix("//") {
        let mut key = String::with_capacity(text.len());
        key.push_str("//");
        for (index, part) in unc.split('/').filter(|part| !part.is_empty()).enumerate() {
            if index > 0 {
                key.push('/');
            }
            key.push_str(part);
        }
        return Some((Cow::Owned(key), true));
    }
    // ``^/mnt/([A-Za-z])(/.*)?$`` (DOTALL): ``$`` also matches before one final newline.
    if bytes.len() >= 6 && text.starts_with("/mnt/") && bytes[5].is_ascii_alphabetic() {
        let rest = &text[6..];
        if rest.is_empty() || rest.starts_with('/') || rest == "\n" {
            let tail = if rest == "\n" { "" } else { rest };
            let mut key = String::with_capacity(6 + tail.len());
            key.push_str("/mnt/");
            key.push(char::from(bytes[5].to_ascii_lowercase()));
            key.push_str(tail);
            return Some((Cow::Owned(key), true));
        }
    }
    if text.starts_with('/') || text.starts_with('~') || drive_letter(&text).is_some() {
        return Some((text, false));
    }
    None
}

fn ascii_casefold(text: &str) -> Result<Cow<'_, str>, Defer> {
    if !text.is_ascii() {
        return Err(Defer);
    }
    if text.bytes().any(|byte| byte.is_ascii_uppercase()) {
        Ok(Cow::Owned(text.to_ascii_lowercase()))
    } else {
        Ok(Cow::Borrowed(text))
    }
}

/// `workspace_relative_edit_path(value, workspace_locator)` for a `str` value.
pub fn workspace_relative_edit_path(
    value: &str,
    workspace_locator: Option<&str>,
) -> Result<Option<String>, Defer> {
    if value.is_empty() || value.contains('\n') || value.contains('\0') {
        return Ok(None);
    }
    let relative: Cow<'_, str> = match absolute_path_key(value) {
        None => {
            if value.contains('\\') {
                Cow::Owned(value.replace('\\', "/"))
            } else {
                Cow::Borrowed(value)
            }
        }
        Some((path_key, path_folds)) => {
            let Some(locator) = workspace_locator else {
                return Ok(None);
            };
            if path_key.starts_with('~') || drive_letter(&path_key).is_some() {
                return Ok(None);
            }
            let Some((root_key, root_folds)) = absolute_path_key(locator) else {
                return Ok(None);
            };
            let root_key = root_key.trim_end_matches('/');
            if root_key.is_empty() {
                return Ok(None);
            }
            let folds = path_folds || root_folds;
            let inside = if folds {
                let compare_path = ascii_casefold(&path_key)?;
                let compare_root = ascii_casefold(root_key)?;
                starts_with_dir(&compare_path, &compare_root)
            } else {
                starts_with_dir(&path_key, root_key)
            };
            if !inside {
                return Ok(None);
            }
            Cow::Owned(path_key[root_key.len() + 1..].to_owned())
        }
    };
    let mut joined = String::with_capacity(relative.len());
    let mut any = false;
    for part in relative.split('/') {
        if part.is_empty() || part == "." {
            continue;
        }
        if part == ".." {
            return Ok(None);
        }
        if any {
            joined.push('/');
        }
        joined.push_str(part);
        any = true;
    }
    Ok(any.then_some(joined))
}

#[inline]
fn starts_with_dir(path: &str, root: &str) -> bool {
    path.len() > root.len() && path.starts_with(root) && path.as_bytes()[root.len()] == b'/'
}

fn rewrite_token(
    token: &str,
    workspace_locator: Option<&str>,
    out: &mut String,
) -> Result<(), Defer> {
    if token == "/dev/null" {
        out.push_str(token);
        return Ok(());
    }
    if (token.starts_with("a/") || token.starts_with("b/")) && absolute_path_key(token).is_none() {
        match workspace_relative_edit_path(&token[2..], workspace_locator)? {
            Some(relative) => {
                out.push_str(&token[..2]);
                out.push_str(&relative);
            }
            None => out.push_str(OUTSIDE_WORKSPACE_PATH),
        }
        return Ok(());
    }
    match workspace_relative_edit_path(token, workspace_locator)? {
        Some(relative) => out.push_str(&relative),
        None => out.push_str(OUTSIDE_WORKSPACE_PATH),
    }
    Ok(())
}

const PATCH_PATH_PREFIXES: [&str; 4] = [
    "*** Add File: ",
    "*** Update File: ",
    "*** Delete File: ",
    "*** Move to: ",
];

/// `(\.*?)(\r?)$` over `rest`: the body and whether a final carriage return closed it.
#[inline]
fn split_final_cr(rest: &str) -> (&str, &str) {
    match rest.strip_suffix('\r') {
        Some(body) => (body, "\r"),
        None => (rest, ""),
    }
}

/// `^diff --git (\S+) (\S+)(\r?)$` → the two tokens and the carriage return.
fn git_diff_header(line: &str) -> Option<(&str, &str, &str)> {
    let rest = line.strip_prefix("diff --git ")?;
    let first_end = rest.find(is_python_space).unwrap_or(rest.len());
    if first_end == 0 || !rest[first_end..].starts_with(' ') {
        return None;
    }
    let after = &rest[first_end + 1..];
    let second_end = after.find(is_python_space).unwrap_or(after.len());
    if second_end == 0 {
        return None;
    }
    let tail = &after[second_end..];
    if tail.is_empty() || tail == "\r" {
        Some((&rest[..first_end], &after[..second_end], tail))
    } else {
        None
    }
}

/// `^(--- |\+\+\+ )(.*?)(\t[^\r\n]*)?(\r?)$` → prefix, path group, and carriage return.
fn unified_file_header(line: &str) -> Option<(&str, &str, &str)> {
    let (prefix, rest) = if let Some(rest) = line.strip_prefix("--- ") {
        ("--- ", rest)
    } else {
        ("+++ ", line.strip_prefix("+++ ")?)
    };
    let (body, cr) = split_final_cr(rest);
    // The lazy path group ends at the first tab after which no carriage return remains.
    let floor = body.rfind('\r').map_or(0, |at| at + 1);
    let path = match body[floor..].find('\t') {
        Some(at) => &body[..floor + at],
        None => body,
    };
    Some((prefix, path, cr))
}

/// `_sanitize_patch_paths(text, workspace_locator)`.
pub fn sanitize_patch_paths(text: &str, workspace_locator: Option<&str>) -> Result<String, Defer> {
    let lines: Vec<&str> = text.split('\n').collect();
    let mut out = String::with_capacity(text.len() + 64);
    for (index, line) in lines.iter().enumerate() {
        if index > 0 {
            out.push('\n');
        }
        if let Some(prefix) = PATCH_PATH_PREFIXES
            .iter()
            .find(|prefix| line.starts_with(*prefix))
        {
            let (path, cr) = split_final_cr(&line[prefix.len()..]);
            out.push_str(prefix);
            rewrite_token(path, workspace_locator, &mut out)?;
            out.push_str(cr);
            continue;
        }
        if let Some((first, second, cr)) = git_diff_header(line) {
            out.push_str("diff --git ");
            rewrite_token(first, workspace_locator, &mut out)?;
            out.push(' ');
            rewrite_token(second, workspace_locator, &mut out)?;
            out.push_str(cr);
            continue;
        }
        if let Some((prefix, path, cr)) = unified_file_header(line) {
            // ``--- x`` is a header only when paired with ``+++ y``; rewriting keeps each
            // line's prefix, so the neighbors are read from the original lines.
            let paired = if prefix == "--- " {
                lines
                    .get(index + 1)
                    .is_some_and(|next| next.starts_with("+++ "))
            } else {
                index > 0 && lines[index - 1].starts_with("--- ")
            };
            if paired {
                out.push_str(prefix);
                rewrite_token(path, workspace_locator, &mut out)?;
                out.push_str(cr);
                continue;
            }
        }
        out.push_str(line);
    }
    Ok(out)
}

/// `_sanitize_patch_result(text, workspace_locator)`.
pub fn sanitize_patch_result(text: &str, workspace_locator: Option<&str>) -> Result<String, Defer> {
    let mut out = String::with_capacity(text.len() + 16);
    for (index, line) in text.split('\n').enumerate() {
        if index > 0 {
            out.push('\n');
        }
        // ``^([AMDR] )(.+?)(\r?)$``
        let bytes = line.as_bytes();
        let matched =
            bytes.len() >= 3 && matches!(bytes[0], b'A' | b'M' | b'D' | b'R') && bytes[1] == b' ';
        if matched {
            let rest = &line[2..];
            let (path, cr) = if rest.len() >= 2 {
                split_final_cr(rest)
            } else {
                (rest, "")
            };
            if absolute_path_key(path).is_some() {
                out.push_str(&line[..2]);
                match workspace_relative_edit_path(path, workspace_locator)? {
                    Some(relative) => out.push_str(&relative),
                    None => out.push_str(OUTSIDE_WORKSPACE_PATH),
                }
                out.push_str(cr);
                continue;
            }
        }
        out.push_str(line);
    }
    Ok(out)
}

#[inline]
fn is_ident_start(byte: u8) -> bool {
    byte.is_ascii_alphabetic() || byte == b'_'
}

#[inline]
fn is_ident(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}

/// `_HEREDOC_START.search(line)`: `<<-?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1`, as
/// `(start, end, delimiter)` byte offsets.
fn heredoc_start(line: &str) -> Option<(usize, usize, &str)> {
    let bytes = line.as_bytes();
    let mut from = 0;
    while let Some(found) = memchr::memmem::find(&bytes[from..], b"<<") {
        let start = from + found;
        let mut at = start + 2;
        if bytes.get(at) == Some(&b'-') {
            at += 1;
        }
        // ``\s*`` is greedy and never worth backtracking: neither a quote nor an
        // identifier can start with whitespace.
        at += line[at..]
            .find(|c: char| !is_python_space(c))
            .unwrap_or(line.len() - at);
        let quote = match bytes.get(at) {
            Some(&byte @ (b'\'' | b'"')) => {
                at += 1;
                Some(byte)
            }
            _ => None,
        };
        if bytes.get(at).is_some_and(|byte| is_ident_start(*byte)) {
            let ident_start = at;
            at += 1;
            while bytes.get(at).is_some_and(|byte| is_ident(*byte)) {
                at += 1;
            }
            let ident_end = at;
            match quote {
                None => return Some((start, ident_end, &line[ident_start..ident_end])),
                Some(quote) if bytes.get(at) == Some(&quote) => {
                    return Some((start, ident_end + 1, &line[ident_start..ident_end]));
                }
                // A quote that does not close the identifier cannot match, and dropping
                // the optional quote leaves a quote where the identifier must start.
                Some(_) => {}
            }
        }
        from = start + 1;
    }
    None
}

/// `_shell_heredocs(command)`: at most `max_heredocs` `(prefix, suffix, body)` triples.
pub fn shell_heredocs(command: &str, max_heredocs: usize) -> Vec<(&str, &str, String)> {
    let lines: Vec<&str> = command.split('\n').collect();
    let mut found = Vec::new();
    let mut index = 0;
    while index < lines.len() && found.len() < max_heredocs {
        let line = lines[index];
        index += 1;
        let Some((start, end, delimiter)) = heredoc_start(line) else {
            continue;
        };
        let body_start = index;
        while index < lines.len() && lines[index].trim_matches(is_python_space) != delimiter {
            index += 1;
        }
        if index >= lines.len() {
            break;
        }
        let mut body = lines[body_start..index].join("\n");
        body.push('\n');
        index += 1;
        found.push((&line[..start], &line[end..], body));
    }
    found
}

fn digits_between(text: &str, min: usize, max: usize) -> Option<(&str, &str)> {
    let count = text.bytes().take_while(u8::is_ascii_digit).count();
    (min..=max).contains(&count).then(|| text.split_at(count))
}

/// `_CODEX_HEADER_EXIT.fullmatch(line)`: `(?:Exit code: |Process exited with code )(-?[0-9]{1,4})`.
fn header_exit(line: &str) -> Option<i64> {
    let rest = line
        .strip_prefix("Exit code: ")
        .or_else(|| line.strip_prefix("Process exited with code "))?;
    let (negative, unsigned) = match rest.strip_prefix('-') {
        Some(unsigned) => (true, unsigned),
        None => (false, rest),
    };
    let (digits, tail) = digits_between(unsigned, 1, 4)?;
    if !tail.is_empty() {
        return None;
    }
    let value: i64 = digits.parse().ok()?;
    Some(if negative { -value } else { value })
}

/// `_CODEX_HEADER_FIELD.fullmatch(line) is not None`.
fn header_field(line: &str) -> bool {
    let digits_only = |rest: &str, max: usize| {
        digits_between(rest, 1, max).is_some_and(|(_, tail)| tail.is_empty())
    };
    if let Some(rest) = line.strip_prefix("Chunk ID: ") {
        return (1..=64).contains(&rest.len())
            && rest
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'));
    }
    if let Some(rest) = line.strip_prefix("Wall time: ") {
        let Some((_, mut tail)) = digits_between(rest, 1, 12) else {
            return false;
        };
        if let Some(fraction) = tail.strip_prefix('.') {
            match digits_between(fraction, 1, 12) {
                Some((_, after)) => tail = after,
                None => return false,
            }
        }
        return tail == " seconds";
    }
    if let Some(rest) = line.strip_prefix("Original token count: ") {
        return digits_only(rest, 12);
    }
    if let Some(rest) = line.strip_prefix("Total output lines: ") {
        return digits_only(rest, 12);
    }
    if let Some(rest) = line.strip_prefix("Process running with session ID ") {
        return digits_only(rest, 20);
    }
    false
}

/// `_codex_header_exit_codes(text)` reading at most `max_lines` lines (`max_lines >= 1`).
pub fn codex_header_exit_codes(text: &str, max_lines: usize) -> Vec<i64> {
    let mut codes = Vec::new();
    for raw_line in text
        .splitn(max_lines.saturating_add(1), '\n')
        .take(max_lines)
    {
        let line = raw_line.strip_suffix('\r').unwrap_or(raw_line);
        if line == "Output:" {
            return codes;
        }
        if let Some(code) = header_exit(line) {
            codes.push(code);
        } else if !header_field(line) {
            return Vec::new();
        }
    }
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn relative_paths_match_reference_matrix() {
        let ws = Some("/work/repo");
        for (value, expected) in [
            ("/work/repo/src/a.py", Some("src/a.py")),
            ("/work/repo", None),
            ("/work/repository/a", None),
            ("src/./a.py", Some("src/a.py")),
            ("src/../a.py", None),
            ("~/a", None),
            ("C:/x", None),
            ("", None),
            ("a\nb", None),
        ] {
            assert_eq!(
                workspace_relative_edit_path(value, ws),
                Ok(expected.map(str::to_owned)),
                "{value:?}"
            );
        }
        assert_eq!(
            workspace_relative_edit_path("C:\\Repo\\x.py", Some("/mnt/c/repo/")),
            Ok(Some("x.py".to_owned()))
        );
        assert_eq!(
            workspace_relative_edit_path("/mnt/C/Ä/x", Some("/mnt/c/ä")),
            Err(Defer)
        );
        assert_eq!(
            absolute_path_key("/mnt/c\n"),
            Some((Cow::Borrowed("/mnt/c"), true))
        );
        assert_eq!(
            absolute_path_key("//srv//share/"),
            Some((Cow::Borrowed("//srv/share"), true))
        );
    }

    #[test]
    fn patch_headers_are_rewritten() {
        let patch = "*** Update File: /work/repo/a.py\r\n--- a/x\t2024\n+++ /tmp/y\n--- removed\n diff\ndiff --git a/p b/q\r";
        assert_eq!(
            sanitize_patch_paths(patch, Some("/work/repo")).unwrap(),
            "*** Update File: a.py\r\n--- a/x\n+++ <outside-workspace>\n--- removed\n diff\ndiff --git a/p b/q\r"
        );
        assert_eq!(
            sanitize_patch_result("M /work/repo/a\r\nA rel\nD /etc/x", Some("/work/repo")).unwrap(),
            "M a\r\nA rel\nD <outside-workspace>"
        );
    }

    fn shell_heredocs_default(command: &str) -> Vec<(&str, &str, String)> {
        shell_heredocs(command, MAX_SHELL_HEREDOCS)
    }

    #[test]
    fn heredocs_match_reference() {
        let found = shell_heredocs_default(
            "cat > f <<'EOF' && x\nline\n  EOF  \napply_patch <<<PATCH\nbody\nPATCH\ncat <<\"A\nnope",
        );
        assert_eq!(
            found,
            vec![
                ("cat > f ", " && x", "line\n".to_owned()),
                ("apply_patch <", "", "body\n".to_owned())
            ]
        );
    }

    fn codex_header_exit_codes_default(text: &str) -> Vec<i64> {
        codex_header_exit_codes(text, CODEX_HEADER_MAX_LINES)
    }

    #[test]
    fn header_exit_codes() {
        assert_eq!(
            codex_header_exit_codes_default(
                "Chunk ID: ab_1\nWall time: 0.5 seconds\nProcess exited with code -2\r\nOutput:\nx"
            ),
            vec![-2]
        );
        assert_eq!(
            codex_header_exit_codes_default("Exit code: 0\nWall time: 1 seconds\nOutput:"),
            vec![0]
        );
        assert!(codex_header_exit_codes_default("Exit code: 12345\nOutput:").is_empty());
        assert!(codex_header_exit_codes_default("Exit code: 0").is_empty());
    }
}
