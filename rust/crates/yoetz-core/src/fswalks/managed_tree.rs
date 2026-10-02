//! Hashing and encoding core of the managed-tree integrations: the canonical tree digest of
//! `portable_plugin._tree_digest` and the Markdown link fence of `codex_skill._validated_text`.

use sha2::{Digest, Sha256};

use crate::protocol::canonical::{self, MAX_SAFE_INTEGER, Reason};

/// `_sha(data)`: `"sha256:" + hexdigest`.
pub fn sha_prefixed(data: &[u8]) -> String {
    let mut out = String::with_capacity(71);
    out.push_str("sha256:");
    out.push_str(&hex::encode(Sha256::digest(data)));
    out
}

/// One managed file: `(relative_path, size, sha256)`.
pub struct Member<'a> {
    pub path: &'a str,
    pub data: &'a [u8],
}

/// Order members by `path.encode("ascii")`; the caller has refused non-ASCII paths.
pub fn sort_ascii(members: &mut [Member<'_>]) {
    members.sort_by(|left, right| left.path.as_bytes().cmp(right.path.as_bytes()));
}

/// `_tree_digest(files)` for members already in ASCII order:
/// `canonical_digest({"files": [{"relative_path", "sha256", "size"}, ...]})`.
pub fn tree_digest(members: &[Member<'_>]) -> Result<String, Reason> {
    let mut out = Vec::with_capacity(16 + members.len() * 128);
    out.extend_from_slice(b"{\"files\":[");
    for (index, member) in members.iter().enumerate() {
        if index > 0 {
            out.push(b',');
        }
        out.extend_from_slice(b"{\"relative_path\":");
        canonical::encode_str_into(&mut out, member.path)?;
        out.extend_from_slice(b",\"sha256\":\"");
        out.extend_from_slice(sha_prefixed(member.data).as_bytes());
        out.extend_from_slice(b"\",\"size\":");
        let size = i64::try_from(member.data.len()).unwrap_or(i64::MAX);
        if size > MAX_SAFE_INTEGER {
            return Err(canonical::INTEGER_OUT_OF_SAFE_RANGE);
        }
        canonical::push_int(&mut out, size);
        out.push(b'}');
    }
    out.extend_from_slice(b"]}");
    Ok(canonical::sha256_prefixed(&out))
}

/// `_LINK_RE.findall(text)` for `\[[^\]]+\]\(([^)]+)\)`: the captured link targets.
///
/// The pattern has no alternation and each repeat stops at a single excluded character, so a
/// backtracking engine and this scan agree: at a `[`, the label runs to the first `]` (and must
/// be non-empty), `(` must follow it directly, and the target runs to the first `)` (and must
/// be non-empty). A failed attempt resumes at the next character, a match after its `)`.
pub fn markdown_link_targets(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut targets = Vec::new();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] != b'[' {
            index += 1;
            continue;
        }
        let label_start = index + 1;
        let Some(label_len) = bytes[label_start..].iter().position(|byte| *byte == b']') else {
            index += 1;
            continue;
        };
        let after_label = label_start + label_len + 1;
        if label_len == 0 || bytes.get(after_label) != Some(&b'(') {
            index += 1;
            continue;
        }
        let target_start = after_label + 1;
        let Some(target_len) = bytes[target_start..].iter().position(|byte| *byte == b')') else {
            index += 1;
            continue;
        };
        if target_len == 0 {
            index += 1;
            continue;
        }
        targets.push(&text[target_start..target_start + target_len]);
        index = target_start + target_len + 1;
    }
    targets
}

/// The data-level checks of `codex_skill._validated_text` (everything except the `SKILL.md`
/// front matter): `true` when the reference would not raise.
pub fn validated_text_ok(data: &[u8], limit: usize) -> bool {
    if data.len() > limit || !data.ends_with(b"\n") || data.contains(&b'\r') {
        return false;
    }
    let Ok(text) = std::str::from_utf8(data) else {
        return false;
    };
    if text.starts_with('\u{feff}') {
        return false;
    }
    for link in markdown_link_targets(text) {
        let target = link.split('#').next().unwrap_or_default();
        if target.is_empty() || target.contains("://") || target.starts_with('#') {
            continue;
        }
        // PurePosixPath(target).parts contains ".." exactly when one "/"-separated component is
        // "..": empty and "." components are dropped, never merged into a "..".
        let dotdot = target.split('/').any(|part| part == "..");
        if target.starts_with('/') || target.starts_with('\\') || dotdot || target.contains('\\') {
            return false;
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn link_scan_matches_findall() {
        assert_eq!(markdown_link_targets("[a](b) [c](d#e)"), vec!["b", "d#e"]);
        assert_eq!(markdown_link_targets("[[a](b)"), vec!["b"]);
        assert_eq!(markdown_link_targets("[](b) [a] (c) [a](b"), Vec::<&str>::new());
        assert_eq!(markdown_link_targets("[a]()[x](y)"), vec!["y"]);
        assert_eq!(markdown_link_targets("[a\n](b\n)"), vec!["b\n"]);
        assert_eq!(markdown_link_targets("[a]([b](c))"), vec!["[b](c"]);
    }

    #[test]
    fn text_checks() {
        assert!(validated_text_ok(b"see [x](a/b.md)\n", 100));
        assert!(!validated_text_ok(b"see [x](../b.md)\n", 100));
        assert!(!validated_text_ok(b"see [x](/b.md)\n", 100));
        assert!(validated_text_ok(b"see [x](https://a/../b)\n", 100));
        assert!(!validated_text_ok(b"x", 100));
        assert!(!validated_text_ok(b"x\r\n", 100));
    }
}
