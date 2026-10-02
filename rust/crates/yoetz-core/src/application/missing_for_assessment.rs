//! Pure parts of `yoetz.application.missing_for_assessment`: `_diff_scope` and the lexical path
//! normalization it relies on.

use crate::shlex;

/// `_WHOLE_TREE`.
pub const WHOLE_TREE: &str = ".";

/// Python `str.isspace()` for one character (`Py_UNICODE_ISSPACE`), which is also the set
/// `str.split()` and `str.strip()` use. It is Unicode `White_Space` plus the ASCII information
/// separators U+001C..U+001F.
pub fn is_python_space(c: char) -> bool {
    matches!(
        c,
        '\t' | '\n'
            | '\u{0b}'
            | '\u{0c}'
            | '\r'
            | '\u{1c}'..='\u{1f}'
            | ' '
            | '\u{85}'
            | '\u{a0}'
            | '\u{1680}'
            | '\u{2000}'..='\u{200a}'
            | '\u{2028}'
            | '\u{2029}'
            | '\u{202f}'
            | '\u{205f}'
            | '\u{3000}'
    )
}

/// `posixpath.normpath(path)` (CPython's `_path_normpath`).
pub fn posix_normpath(path: &str) -> String {
    if path.is_empty() {
        return ".".to_owned();
    }
    let leading = path.bytes().take_while(|&byte| byte == b'/').count();
    let prefix = match leading {
        0 => "",
        2 => "//",
        _ => "/",
    };
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/') {
        if part.is_empty() || part == "." {
            continue;
        }
        if part == ".." {
            if parts.last().is_some_and(|last| *last != "..") {
                parts.pop();
                continue;
            }
            if !prefix.is_empty() {
                continue;
            }
        }
        parts.push(part);
    }
    let mut out = String::with_capacity(path.len());
    out.push_str(prefix);
    out.push_str(&parts.join("/"));
    if out.is_empty() {
        out.push('.');
    }
    out
}

/// `_inside(path, roots)`: roots in the caller's (Python frozenset) iteration order.
fn inside<S: AsRef<str>>(path: &str, roots: &[S]) -> Option<String> {
    for root in roots {
        let root = root.as_ref();
        if path == root {
            return Some(WHOLE_TREE.to_owned());
        }
        if let Some(rest) = path.strip_prefix(root) {
            if let Some(relative) = rest.strip_prefix('/') {
                return Some(relative.to_owned());
            }
        }
    }
    None
}

/// `_normal_path(text, roots, known_path=...)` as `(path, certain)`.
pub fn normal_path<S: AsRef<str>>(text: &str, roots: &[S], known_path: bool) -> Option<(String, bool)> {
    let text = text.trim_matches(is_python_space);
    if text.is_empty() || text.chars().any(is_python_space) || text.contains("://") {
        return None;
    }
    if text.starts_with('/') {
        return inside(&posix_normpath(text), roots).map(|relative| (relative, true));
    }
    let parts: Vec<&str> = text.split('/').filter(|part| !part.is_empty() && *part != ".").collect();
    let Some(name) = parts.last() else {
        return Some((WHOLE_TREE.to_owned(), true));
    };
    let mut name_chars = name.chars();
    name_chars.next();
    let certain = known_path
        || text.contains('/')
        || parts.len() > 1
        || (!name.starts_with('.') && name_chars.as_str().contains('.'));
    Some((parts.join("/"), certain))
}

/// `_SUMMARY_DIFF_OPTIONS`.
pub const SUMMARY_DIFF_OPTIONS: &[&str] = &[
    "--name-only",
    "--name-status",
    "--numstat",
    "--shortstat",
    "--summary",
    "--quiet",
    "--raw",
    "--no-patch",
    "-s",
    "--check",
];

/// The `_REVISION` pattern `is_revision` implements.
pub const REVISION_PATTERN: &str = r"(?:HEAD|FETCH_HEAD|ORIG_HEAD|MERGE_HEAD|@)(?:[~^][0-9]*)*|[0-9a-f]{7,40}|.*\.\..*";

/// `_REVISION.fullmatch(token)` for
/// `(?:HEAD|FETCH_HEAD|ORIG_HEAD|MERGE_HEAD|@)(?:[~^][0-9]*)*|[0-9a-f]{7,40}|.*\.\..*`.
pub fn is_revision(token: &str) -> bool {
    for name in ["HEAD", "FETCH_HEAD", "ORIG_HEAD", "MERGE_HEAD", "@"] {
        if let Some(rest) = token.strip_prefix(name) {
            let bytes = rest.as_bytes();
            if bytes.is_empty()
                || ((bytes[0] == b'~' || bytes[0] == b'^')
                    && bytes.iter().all(|&byte| byte == b'~' || byte == b'^' || byte.is_ascii_digit()))
            {
                return true;
            }
        }
    }
    let bytes = token.as_bytes();
    if (7..=40).contains(&bytes.len()) && bytes.iter().all(|&byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')) {
        return true;
    }
    // `.` matches anything but a line feed.
    !token.contains('\n') && token.contains("..")
}

/// `_diff_scope(command, roots)`: the pathspecs in token order (the caller builds the set), or
/// `None`. An empty vector means the whole tree.
pub fn diff_scope<S: AsRef<str>>(command: &str, roots: &[S]) -> Option<Vec<String>> {
    let tokens = shlex::split(command).ok()?;
    if tokens.len() < 2 || tokens[0] != "git" {
        return None;
    }
    let mut index = 1;
    while index < tokens.len() && tokens[index] != "diff" {
        let token = tokens[index].as_str();
        if token == "--no-pager" {
            index += 1;
        } else if token == "-C" && index + 1 < tokens.len() {
            let directory = normal_path(&tokens[index + 1], roots, true)?;
            if directory.0 != WHOLE_TREE {
                return None;
            }
            index += 2;
        } else {
            return None;
        }
    }
    if index >= tokens.len() {
        return None;
    }
    let mut specs = Vec::new();
    let mut literal = false;
    for token in &tokens[index + 1..] {
        let token = token.as_str();
        if matches!(token, "&&" | "||" | "|" | ";" | ">" | ">>") {
            break;
        }
        if !literal && token == "--" {
            literal = true;
            continue;
        }
        if !literal && token.starts_with('-') {
            if SUMMARY_DIFF_OPTIONS.contains(&token) || token.starts_with("--stat") || token.starts_with("--dirstat") {
                return None;
            }
            continue;
        }
        if !literal && is_revision(token) {
            continue;
        }
        specs.push(normal_path(token, roots, true)?.0);
    }
    Some(specs)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normpath_matches_posixpath() {
        assert_eq!(posix_normpath("/a/./b/../c//"), "/a/c");
        assert_eq!(posix_normpath("//a//b/"), "//a/b");
        assert_eq!(posix_normpath("///a/../.."), "/");
        assert_eq!(posix_normpath("a/../../b"), "../b");
        assert_eq!(posix_normpath(""), ".");
    }

    #[test]
    fn diff_scope_forms() {
        let roots = ["/work/repo"];
        assert_eq!(diff_scope("git diff src/a.py", &roots), Some(vec!["src/a.py".to_owned()]));
        assert_eq!(diff_scope("git diff", &roots), Some(vec![]));
        assert_eq!(diff_scope("git diff --stat", &roots), None);
        assert_eq!(diff_scope("git -C /work/repo diff HEAD~1 -- /work/repo/x", &roots), Some(vec!["x".to_owned()]));
        assert_eq!(diff_scope("git -C /other diff", &roots), None);
        assert_eq!(diff_scope("pytest", &roots), None);
        assert!(is_revision("HEAD^2~3"));
        assert!(is_revision("main..topic"));
        assert!(!is_revision("HEADx"));
    }

    #[test]
    fn python_whitespace() {
        assert_eq!(normal_path("\u{1c} src/a.py\u{3000}", &["/w"], false), Some(("src/a.py".to_owned(), true)));
        assert_eq!(normal_path("a\u{1f}b", &["/w"], false), None);
        assert!(!is_python_space('\u{200b}'));
    }
}
