//! Twins of the argv classifiers and the Linux `/proc` scan in
//! `yoetz.adapters.integrations.cursor_mcp_runtime`.
//!
//! Python measures string lengths in code points; every length check here counts `char`s.

use std::collections::HashMap;
use std::io::Read;

use super::pytext::{ascii_ignore, ascii_py_int, ascii_splitlines, ascii_strip, universal_newlines, utf8_ignore};

pub const MAX_TOKENS: usize = 12;
pub const MAX_COMM: usize = 32;
pub const MAX_PROJECT_SELECTOR: usize = 8_192;
const MAX_CMDLINE: usize = 4_096;

/// `(serve suffix class, launcher match)`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Strict,
    Policy,
    Foreign,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Strict => "strict",
            Kind::Policy => "policy",
            Kind::Foreign => "foreign",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Launcher {
    Matched,
    Different,
    Unresolved,
}

impl Launcher {
    pub fn as_str(self) -> &'static str {
        match self {
            Launcher::Matched => "matched",
            Launcher::Different => "different",
            Launcher::Unresolved => "unresolved",
        }
    }
}

/// `_cursor_helper_comm(value)` for a `str` without lone surrogates.
pub fn cursor_helper_comm(value: &str) -> bool {
    if value.is_empty() {
        return false;
    }
    let value = value.rsplit('/').next().unwrap_or_default();
    if value.is_empty() || value.chars().count() > MAX_COMM {
        return false;
    }
    if value.bytes().any(|byte| !(32..=126).contains(&byte)) {
        return false;
    }
    if value == "Cursor" || value == "mcp-process" || value == "cursor" {
        return true;
    }
    (value.starts_with("Cursor") || value.starts_with("cursor-helper"))
        && value.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b' ' | b'(' | b')' | b'-'))
}

/// `_yoetz_launcher_token(value)`.
fn yoetz_launcher_token(value: &str) -> bool {
    if value.is_empty() || value.chars().count() > 4_096 {
        return false;
    }
    let last = value.rsplit(['/', '\\']).next().unwrap_or_default();
    last == "yoetz" || last == "yoetz.exe"
}

/// `_valid_project_selector(value)`.
fn valid_project_selector(value: &str) -> bool {
    if value.is_empty() || value.len() > MAX_PROJECT_SELECTOR {
        return false;
    }
    if !value.starts_with('/') || value.starts_with("//") {
        return false;
    }
    if value.chars().any(|character| (character as u32) < 32 || character as u32 == 127) {
        return false;
    }
    // PurePosixPath drops empty and "." components; the selector must not be the root and must
    // not contain a ".." part.
    let mut parts = value[1..].split('/').filter(|part| !part.is_empty() && *part != ".").peekable();
    if parts.peek().is_none() {
        return false;
    }
    parts.all(|part| part != "..")
}

/// `_classify_cursor_project_suffix(suffix)`.
fn classify_cursor_project_suffix<S: AsRef<str>>(suffix: &[S]) -> Option<Kind> {
    const PREFIX: [&str; 5] = ["mcp", "serve", "--host", "cursor", "--project-root"];
    if suffix.len() < PREFIX.len() + 1 || !suffix.iter().zip(PREFIX).all(|(token, part)| token.as_ref() == part) {
        return None;
    }
    if !valid_project_selector(suffix[PREFIX.len()].as_ref()) {
        return None;
    }
    let tail = &suffix[PREFIX.len() + 1..];
    if tail.is_empty() {
        return Some(Kind::Policy);
    }
    if tail.len() == 2 && tail[0].as_ref() == "--semantic" && tail[1].as_ref() == "off" {
        return Some(Kind::Strict);
    }
    None
}

fn same<S: AsRef<str>, T: AsRef<str>>(left: &[S], right: &[T]) -> bool {
    left.len() == right.len() && left.iter().zip(right).all(|(a, b)| a.as_ref() == b.as_ref())
}

fn explicit_path(token: &str) -> bool {
    token.contains('/') || token.contains('\\')
}

/// `_launcher_match(tokens, serve_index, expected_launcher)`.
fn launcher_match<S: AsRef<str>, T: AsRef<str>>(tokens: &[S], serve_index: usize, expected: &[T]) -> Launcher {
    let width = expected.len();
    if serve_index >= width && same(&tokens[serve_index - width..serve_index], expected) {
        return Launcher::Matched;
    }
    if width > 1 && serve_index >= width {
        let head = tokens[serve_index - width].as_ref();
        let tail = &tokens[serve_index - width + 1..serve_index];
        if same(tail, &expected[1..]) && explicit_path(head) {
            return Launcher::Different;
        }
    }
    // serve_index > 0 always: `mcp` must follow a launcher token.
    if explicit_path(tokens[serve_index - 1].as_ref()) {
        return Launcher::Different;
    }
    Launcher::Unresolved
}

/// `classify_serve_argv(tokens, expected_launcher)` for all-`str` tokens.
pub fn classify_serve_argv<S: AsRef<str>, T: AsRef<str>>(
    tokens: &[S],
    expected_launcher: Option<&[T]>,
) -> (Option<Kind>, Option<Launcher>) {
    let mut serve_index: Option<usize> = None;
    for index in 1..tokens.len() {
        if tokens[index].as_ref() == "mcp"
            && yoetz_launcher_token(tokens[index - 1].as_ref())
            && index + 1 < tokens.len()
            && tokens[index + 1].as_ref() == "serve"
        {
            if tokens.len() - index > MAX_TOKENS {
                return (Some(Kind::Foreign), None);
            }
            serve_index = Some(index);
        }
    }
    let Some(serve_index) = serve_index else {
        return (None, None);
    };
    let suffix = &tokens[serve_index..];
    let launcher = match expected_launcher {
        Some(expected) if !expected.is_empty() => Some(launcher_match(tokens, serve_index, expected)),
        _ => None,
    };
    if let Some(kind) = classify_cursor_project_suffix(suffix) {
        return (Some(kind), launcher);
    }
    let policy: [&[&str]; 2] = [&["mcp", "serve"], &["mcp", "serve", "--host", "cursor"]];
    let strict: [&[&str]; 2] =
        [&["mcp", "serve", "--semantic", "off"], &["mcp", "serve", "--host", "cursor", "--semantic", "off"]];
    if policy.iter().any(|candidate| same(suffix, candidate)) {
        return (Some(Kind::Policy), launcher);
    }
    if strict.iter().any(|candidate| same(suffix, candidate)) {
        return (Some(Kind::Strict), launcher);
    }
    (Some(Kind::Foreign), launcher)
}

/// One classified `/proc` process: `(parent is a Cursor helper, route, launcher)`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Snapshot {
    pub cursor_helper: bool,
    pub kind: Kind,
    pub launcher: Option<Launcher>,
}

/// `str.isdigit()` over a `/proc` entry name; only ASCII digits can name a process.
fn pid_name(name: &[u8]) -> Option<i64> {
    if name.is_empty() || !name.iter().all(u8::is_ascii_digit) {
        return None;
    }
    std::str::from_utf8(name).ok()?.parse().ok()
}

fn read_text_ascii(path: &std::path::Path) -> std::io::Result<String> {
    let raw = std::fs::read(path)?;
    Ok(universal_newlines(&ascii_ignore(&raw)).into_owned())
}

fn read_capped(path: &std::path::Path, cap: usize) -> std::io::Result<Vec<u8>> {
    let mut file = std::fs::File::open(path)?;
    let mut raw = Vec::with_capacity(512);
    file.by_ref().take(cap as u64 + 1).read_to_end(&mut raw)?;
    Ok(raw)
}

/// `_linux_snapshots(expected_launcher)` over `proc_root`; `None` when the scan is unavailable.
pub fn linux_snapshots<T: AsRef<str>>(
    proc_root: &std::path::Path,
    expected_launcher: Option<&[T]>,
    max_processes: usize,
) -> Option<Vec<Snapshot>> {
    if !proc_root.is_dir() {
        return None;
    }
    let mut pid_dirs: Vec<(std::path::PathBuf, i64)> = Vec::new();
    for entry in std::fs::read_dir(proc_root).ok()? {
        let entry = entry.ok()?;
        let name = entry.file_name();
        if let Some(pid) = pid_name(std::os::unix::ffi::OsStrExt::as_bytes(name.as_os_str())) {
            pid_dirs.push((entry.path(), pid));
        }
    }
    // Absent keys behave like an unknown parent, which is also how an out-of-range PPid acts.
    let mut comm_by_pid: HashMap<i64, String> = HashMap::new();
    let mut ppid_by_pid: HashMap<i64, Option<i64>> = HashMap::new();
    for (path, pid) in &pid_dirs {
        let Ok(comm) = read_text_ascii(&path.join("comm")) else {
            continue;
        };
        comm_by_pid.insert(*pid, ascii_strip(&comm).to_owned());
        let Ok(status) = read_text_ascii(&path.join("status")) else {
            continue;
        };
        for line in ascii_splitlines(&status) {
            if let Some(rest) = line.strip_prefix("PPid:") {
                if let Some(value) = ascii_py_int(rest) {
                    ppid_by_pid.insert(*pid, value);
                }
                break;
            }
        }
    }
    let lookup_comm = |pid: Option<i64>| -> &str { pid.and_then(|key| comm_by_pid.get(&key)).map_or("", String::as_str) };
    let mut classified = Vec::new();
    for (path, pid) in &pid_dirs {
        if classified.len() > max_processes {
            break;
        }
        let Ok(raw) = read_capped(&path.join("cmdline"), MAX_CMDLINE) else {
            continue;
        };
        if raw.is_empty() || raw.len() > MAX_CMDLINE {
            continue;
        }
        let tokens: Vec<std::borrow::Cow<'_, str>> =
            raw.split(|byte| *byte == 0).filter(|part| !part.is_empty()).map(utf8_ignore).collect();
        let (Some(kind), launcher) = classify_serve_argv(&tokens, expected_launcher) else {
            continue;
        };
        // `ppid_by_pid.get(pid)`: a parent outside i64 is a key no process has.
        let parent: Option<Option<i64>> = ppid_by_pid.get(pid).copied();
        let parent_key = parent.flatten();
        let parent_comm = if parent.is_some() { lookup_comm(parent_key) } else { "" };
        let grand: Option<i64> = match parent {
            Some(_) => match parent_key.and_then(|key| ppid_by_pid.get(&key)) {
                Some(value) => *value,
                None => Some(0),
            },
            None => Some(0),
        };
        let grand_comm = if grand != Some(0) { lookup_comm(grand) } else { "" };
        let helper = cursor_helper_comm(parent_comm) || cursor_helper_comm(grand_comm);
        classified.push(Snapshot { cursor_helper: helper, kind, launcher });
    }
    Some(classified)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classify_matches_reference_examples() {
        let console = ["/opt/yoetz/bin/yoetz"];
        let serve = ["mcp", "serve"];
        let argv: Vec<&str> = console.iter().chain(serve.iter()).copied().collect();
        assert_eq!(classify_serve_argv(&argv, Some(&console[..])), (Some(Kind::Policy), Some(Launcher::Matched)));
        assert_eq!(
            classify_serve_argv(&["/opt/older/bin/yoetz", "mcp", "serve"], Some(&console[..])),
            (Some(Kind::Policy), Some(Launcher::Different))
        );
        assert_eq!(classify_serve_argv(&["yoetz", "mcp", "serve"], Some(&console[..])), (Some(Kind::Policy), Some(Launcher::Unresolved)));
        assert_eq!(classify_serve_argv(&["unrelated"], Some(&console[..])), (None, None));
        assert_eq!(classify_serve_argv(&["yoetz", "mcp", "serve", "--host", "cursor", "--project-root", "/w", "--semantic", "off"], None::<&[&str]>), (Some(Kind::Strict), None));
        assert_eq!(classify_serve_argv(&["yoetz", "mcp", "serve", "--host", "cursor", "--project-root", "/./", ], None::<&[&str]>), (Some(Kind::Foreign), None));
        let long: Vec<&str> = ["yoetz", "mcp", "serve"].into_iter().chain(std::iter::repeat_n("x", 10)).collect();
        assert_eq!(classify_serve_argv(&long, None::<&[&str]>), (Some(Kind::Foreign), None));
    }

    #[test]
    fn helper_comm() {
        assert!(cursor_helper_comm("/Applications/Cursor.app/Contents/MacOS/Cursor Helper (Plugin)"));
        assert!(cursor_helper_comm("cursor"));
        assert!(!cursor_helper_comm("Cursor\u{e9}"));
        assert!(!cursor_helper_comm("Cursor_x"));
        assert!(!cursor_helper_comm(""));
        assert!(!cursor_helper_comm("a/"));
    }
}
