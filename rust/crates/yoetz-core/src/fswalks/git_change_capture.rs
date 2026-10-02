//! Twin of `_new_file_diff` in `yoetz.adapters.git_change_capture`.

use super::pytext::utf8_replace;

/// `_quote_path(path)`: Git's `core.quotePath=true` form, ASCII only.
pub fn quote_path(path: &[u8]) -> String {
    let plain = |byte: u8| (0x20..0x7F).contains(&byte) && byte != b'"' && byte != b'\\';
    if path.iter().all(|byte| plain(*byte)) {
        // Every byte is printable ASCII.
        return path.iter().map(|byte| *byte as char).collect();
    }
    let mut rendered = String::with_capacity(path.len() + 2);
    rendered.push('"');
    for &byte in path {
        match byte {
            7 => rendered.push_str("\\a"),
            8 => rendered.push_str("\\b"),
            9 => rendered.push_str("\\t"),
            10 => rendered.push_str("\\n"),
            11 => rendered.push_str("\\v"),
            12 => rendered.push_str("\\f"),
            13 => rendered.push_str("\\r"),
            b'"' | b'\\' => {
                rendered.push('\\');
                rendered.push(byte as char);
            }
            _ if !(0x20..0x7F).contains(&byte) => {
                rendered.push('\\');
                rendered.push((b'0' + (byte >> 6)) as char);
                rendered.push((b'0' + ((byte >> 3) & 7)) as char);
                rendered.push((b'0' + (byte & 7)) as char);
            }
            _ => rendered.push(byte as char),
        }
    }
    rendered.push('"');
    rendered
}

/// `_prefixed(prefix, path)`.
pub fn prefixed(prefix: &str, path: &[u8]) -> String {
    let quoted = quote_path(path);
    if let Some(rest) = quoted.strip_prefix('"') {
        format!("\"{prefix}{rest}")
    } else {
        format!("{prefix}{quoted}")
    }
}

/// `_new_file_diff(path, content, executable)` with `_BINARY_PROBE_BYTES = binary_probe`.
pub fn new_file_diff(path: &[u8], content: &[u8], executable: bool, binary_probe: usize) -> (Vec<u8>, String) {
    let mode = if executable { "100755" } else { "100644" };
    let mut head = format!("diff --git {} {}\nnew file mode {mode}\n", prefixed("a/", path), prefixed("b/", path));
    if content.is_empty() {
        return (head.into_bytes(), "0".to_owned());
    }
    let probe = &content[..content.len().min(binary_probe)];
    let b_side = prefixed("b/", path);
    if probe.contains(&0) {
        head.push_str("Binary files /dev/null and ");
        head.push_str(&b_side);
        head.push_str(" differ\n");
        return (head.into_bytes(), "-".to_owned());
    }
    let trailing_newline = content.ends_with(b"\n");
    let body = if trailing_newline { &content[..content.len() - 1] } else { content };
    // content.split(b"\n") minus the empty tail after a trailing newline.
    let count = memchr::memchr_iter(b'\n', body).count() + 1;
    head.push_str("--- /dev/null\n+++ ");
    head.push_str(&b_side);
    head.push('\n');
    if count == 1 {
        head.push_str("@@ -0,0 +1 @@\n");
    } else {
        head.push_str("@@ -0,0 +1,");
        head.push_str(&count.to_string());
        head.push_str(" @@\n");
    }
    let mut rendered = head.into_bytes();
    rendered.reserve(body.len() + count * 2 + 32);
    for line in body.split(|byte| *byte == b'\n') {
        rendered.push(b'+');
        rendered.extend_from_slice(line);
        rendered.push(b'\n');
    }
    if !trailing_newline {
        rendered.extend_from_slice(b"\\ No newline at end of file\n");
    }
    let decoded = match utf8_replace(&rendered) {
        std::borrow::Cow::Borrowed(_) => rendered,
        std::borrow::Cow::Owned(text) => text.into_bytes(),
    };
    (decoded, count.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quoting_matches_git() {
        assert_eq!(quote_path(b"a b.txt"), "a b.txt");
        assert_eq!(quote_path(b"a\"b"), "\"a\\\"b\"");
        assert_eq!(quote_path(b"\xc3\xa9\t"), "\"\\303\\251\\t\"");
        assert_eq!(prefixed("a/", b"x\ny"), "\"a/x\\ny\"");
    }

    #[test]
    fn diff_shapes() {
        let (text, added) = new_file_diff(b"f", b"one\ntwo\n", false, 8000);
        assert_eq!(added, "2");
        assert_eq!(
            text,
            b"diff --git a/f b/f\nnew file mode 100644\n--- /dev/null\n+++ b/f\n@@ -0,0 +1,2 @@\n+one\n+two\n"
        );
        let (text, added) = new_file_diff(b"f", b"x", true, 8000);
        assert_eq!(added, "1");
        assert!(text.ends_with(b"@@ -0,0 +1 @@\n+x\n\\ No newline at end of file\n"));
        let (text, added) = new_file_diff(b"f", b"\n", false, 8000);
        assert_eq!(added, "1");
        assert!(text.ends_with(b"@@ -0,0 +1 @@\n+\n"));
        let (_, added) = new_file_diff(b"f", b"a\0", false, 8000);
        assert_eq!(added, "-");
        let (_, added) = new_file_diff(b"f", b"a\0", false, 1);
        assert_eq!(added, "1");
        let (text, _) = new_file_diff(b"f", b"\xff\n", false, 8000);
        assert!(text.ends_with("+\u{fffd}\n".as_bytes()));
    }
}
