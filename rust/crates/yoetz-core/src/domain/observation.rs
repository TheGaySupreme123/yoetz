//! Pure parts of `yoetz.domain.observation`: observed-command normalization and the structural
//! payload key tables.

use crate::shlex;

/// `_MAX_OBSERVED_COMMAND_CHARS` (code points).
pub const MAX_OBSERVED_COMMAND_CHARS: usize = 16_384;
/// `_MAX_OBSERVED_ARGV`.
pub const MAX_OBSERVED_ARGV: usize = 1_024;
/// `_MAX_GAP_CODES` (the default `maximum` of `_sorted_unique_gap_codes`).
pub const MAX_GAP_CODES: usize = 64;
/// `_MAX_STRUCTURAL_BYTES`.
pub const MAX_STRUCTURAL_BYTES: usize = 65_536;

/// `_SHELL_WRAPPERS`.
pub const SHELL_WRAPPERS: &[&str] = &["sh", "bash", "zsh", "dash", "ksh"];
/// The `_SHELL_COMMAND_FLAG_RE` pattern `is_shell_command_flag` implements.
pub const SHELL_COMMAND_FLAG_PATTERN: &str = r"^-[a-z]*c[a-z]*$";
/// The `_GAP_RE` pattern `is_gap_code` implements.
pub const GAP_PATTERN: &str = r"^[a-z][a-z0-9_]{0,127}$";
/// The `_TOKEN_RE` pattern `is_token` implements.
pub const TOKEN_PATTERN: &str = r"^[a-zA-Z0-9][a-zA-Z0-9._:/+-]{0,127}$";

/// `_SHELL_COMMAND_FLAG_RE.fullmatch(flag)`: `^-[a-z]*c[a-z]*$` (ASCII).
fn is_shell_command_flag(flag: &str) -> bool {
    match flag.strip_prefix('-') {
        Some(rest) => rest.bytes().all(|byte| byte.is_ascii_lowercase()) && rest.contains('c'),
        None => false,
    }
}

/// `_shell_wrapped_command(argv)`: `X` for `/bin/bash -lc X`, `sh -c X`, and the like.
pub fn shell_wrapped_command<S: AsRef<str>>(argv: &[S]) -> Option<&str> {
    if argv.len() != 3 {
        return None;
    }
    let program = argv[0].as_ref();
    let shell = match program.rfind(['/', '\\']) {
        Some(index) => &program[index + 1..],
        None => program,
    };
    if !SHELL_WRAPPERS.contains(&shell) || !is_shell_command_flag(argv[1].as_ref()) {
        return None;
    }
    Some(argv[2].as_ref())
}

/// `_collapse_unquoted_blanks(text)`.
pub fn collapse_unquoted_blanks(text: &str) -> String {
    let mut output = String::with_capacity(text.len());
    let mut quote: Option<char> = None;
    let mut escaped = false;
    let mut pending_blank = false;
    for char in text.chars() {
        if quote.is_none() && !escaped && (char == ' ' || char == '\t') {
            pending_blank = true;
            continue;
        }
        if pending_blank && !output.is_empty() {
            output.push(' ');
        }
        pending_blank = false;
        output.push(char);
        if escaped {
            escaped = false;
        } else if char == '\\' && quote != Some('\'') {
            escaped = true;
        } else if quote.is_none() && (char == '\'' || char == '"') {
            quote = Some(char);
        } else if Some(char) == quote {
            quote = None;
        }
    }
    output
}

fn non_empty(text: String) -> Option<String> {
    if text.is_empty() { None } else { Some(text) }
}

/// The part of `normalize_observed_command` after the command text is resolved.
fn normalize_text(mut text: String) -> Option<String> {
    if text.contains('\0') || text.chars().count() > MAX_OBSERVED_COMMAND_CHARS {
        return None;
    }
    for _ in 0..3 {
        text = collapse_unquoted_blanks(&text);
        let Ok(words) = shlex::split(&text) else {
            return non_empty(text);
        };
        let inner = match shell_wrapped_command(&words) {
            Some(inner) if shlex::join(&words) == text => inner.to_owned(),
            _ => return non_empty(text),
        };
        text = inner;
    }
    None
}

/// `normalize_observed_command(value)` for a `str` value.
pub fn normalize_observed_command_str(value: &str) -> Option<String> {
    normalize_text(value.to_owned())
}

/// `normalize_observed_command(value)` for a list or tuple of `str` (already type-checked).
pub fn normalize_observed_command_argv<S: AsRef<str>>(argv: &[S]) -> Option<String> {
    if argv.is_empty() || argv.len() > MAX_OBSERVED_ARGV {
        return None;
    }
    // `_shell_wrapped_command(argv) or shlex.join(argv)`: an empty wrapped command falls through.
    let text = match shell_wrapped_command(argv) {
        Some(inner) if !inner.is_empty() => inner.to_owned(),
        _ => shlex::join(argv),
    };
    normalize_text(text)
}

/// `_STRUCTURAL_KEYS`.
pub const STRUCTURAL_KEYS: &[&str] = &[
    "tool_name",
    "action",
    "exit_status",
    "correlation_id",
    "changed_paths_digest",
    "result_status",
    "permission_decision",
    "subagent_id",
    "claim_kind",
    "event_ordinal",
    "duration_ms",
    "attempt",
    "success",
    "denied",
    "truncated",
    "source_lag_ms",
    "hook_name",
    "stream_kind",
    "command_digest",
    "command_commitment",
    "argv_digest",
    "cwd_commitment",
    "file_count",
    "bytes_touched",
    "tool_call_id",
    "parent_tool_call_id",
    "lineage_child_task_id",
    "lineage_child_session_id",
    "lineage_child_writer_id",
    "lineage_parent_task_id",
    "permission_kind",
    "decision_reason_code",
    "mapping_hint",
    "capability_profile_id",
    "codex_version",
    "cursor_version",
    "model_id",
    "model_effort",
    "pairing_mode",
    "correlation_kind",
    "generation_id",
    "summary_count",
    "input_count",
    "member_digest",
    "fence",
    "provenance",
    "summary_schema",
    "selection_policy_version",
    "content_scope",
    "coverage_gaps",
    "subject_state_digest",
    "members",
    "selection_task_id",
    "selection_session_id",
    "selection_writer_id",
    "selection_authority_generation",
    "protection_reference",
];

/// `_STRUCTURAL_TOKEN_KEYS`.
pub const STRUCTURAL_TOKEN_KEYS: &[&str] = &[
    "cursor_version",
    "model_id",
    "model_effort",
    "pairing_mode",
    "correlation_kind",
    "generation_id",
    "provenance",
    "summary_schema",
    "selection_policy_version",
    "content_scope",
    "subject_state_digest",
    "selection_task_id",
    "selection_session_id",
    "selection_writer_id",
    "selection_authority_generation",
    "protection_reference",
];

/// `_PROSE_KEYS`.
pub const PROSE_KEYS: &[&str] = &[
    "transcript",
    "reasoning",
    "content_full",
    "content",
    "message",
    "output",
    "stderr",
    "stdout",
    "prompt",
    "hidden_reasoning",
    "thinking",
    "raw_text",
    "body",
    "text",
    "command",
    "argv",
    "cwd",
    "path",
    "paths",
    "working_directory",
];

/// `_TOKEN_RE.fullmatch(value)`: `^[a-zA-Z0-9][a-zA-Z0-9._:/+-]{0,127}$` (ASCII).
pub fn is_token(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.is_empty() || bytes.len() > 128 || !bytes[0].is_ascii_alphanumeric() {
        return false;
    }
    bytes[1..]
        .iter()
        .all(|&byte| byte.is_ascii_alphanumeric() || b"._:/+-".contains(&byte))
}

/// `_GAP_RE.fullmatch(value)`: `^[a-z][a-z0-9_]{0,127}$` (ASCII).
pub fn is_gap_code(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.is_empty() || bytes.len() > 128 || !bytes[0].is_ascii_lowercase() {
        return false;
    }
    bytes[1..]
        .iter()
        .all(|&byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
}

/// Python `str.isalpha()` for one character, ASCII only (`None` when non-ASCII).
fn ascii_isalpha(c: char) -> Option<bool> {
    if c.is_ascii() {
        Some(c.is_ascii_alphabetic())
    } else {
        None
    }
}

/// `_looks_like_path(value)`. `None` when the answer needs Python's Unicode `str.isalpha()`.
pub fn looks_like_path(value: &str) -> Option<bool> {
    if value.contains(['\0', '\r', '\n']) {
        return Some(true);
    }
    for prefix in ["/", "\\", "./", "../", "~/", "~\\"] {
        if value.starts_with(prefix) {
            return Some(true);
        }
    }
    let mut chars = value.chars();
    let (Some(first), Some(second), Some(third)) = (chars.next(), chars.next(), chars.next())
    else {
        return Some(false);
    };
    if second != ':' {
        return Some(false);
    }
    match ascii_isalpha(first) {
        Some(false) => Some(false),
        Some(true) => Some(third == '/' || third == '\\'),
        None => {
            if third == '/' || third == '\\' {
                None
            } else {
                Some(false)
            }
        }
    }
}

/// `key.endswith(("_path", "_paths", "_cwd", "_directory"))`.
pub fn has_path_suffix(key: &str) -> bool {
    key.ends_with("_path")
        || key.ends_with("_paths")
        || key.ends_with("_cwd")
        || key.ends_with("_directory")
}

/// `OBSERVATION_WORKSPACE_DOMAIN`.
pub const WORKSPACE_DOMAIN: &[u8] = b"yoetz/observation-workspace/v1\x00";
/// `OBSERVATION_STREAM_LINE_DOMAIN`.
pub const STREAM_LINE_DOMAIN: &[u8] = b"yoetz/observation-stream-line/v1\x00";
/// `OBSERVATION_HOOK_COMMITMENT_DOMAIN`.
pub const HOOK_COMMITMENT_DOMAIN: &[u8] = b"yoetz/observation-hook-commitment/v1\x00";
/// `OBSERVATION_COMMAND_COMMITMENT_DOMAIN`.
pub const COMMAND_COMMITMENT_DOMAIN: &[u8] = b"yoetz/observation-command-commitment/v1\x00";

/// `16 <= len(key_material) <= 64`, the commitment key bound every commitment shares.
pub fn is_commitment_key(key: &[u8]) -> bool {
    (16..=64).contains(&key.len())
}

/// `"hmac-sha256:" + hmac.new(key, domain + message, hashlib.sha256).hexdigest()`.
pub fn hmac_commitment(key: &[u8], domain: &[u8], message: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    const BLOCK: usize = 64;
    let mut block = [0u8; BLOCK];
    if key.len() > BLOCK {
        block[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        block[..key.len()].copy_from_slice(key);
    }
    let mut inner = Sha256::new();
    inner.update(block.map(|byte| byte ^ 0x36));
    inner.update(domain);
    inner.update(message);
    let mut outer = Sha256::new();
    outer.update(block.map(|byte| byte ^ 0x5c));
    outer.update(inner.finalize());
    let mut out = String::with_capacity(12 + 64);
    out.push_str("hmac-sha256:");
    out.push_str(&hex::encode(outer.finalize()));
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hmac_matches_rfc_4231() {
        // RFC 4231 test case 2 ("Jefe"), split across domain and message.
        let commitment = hmac_commitment(b"Jefe", b"what do ya want ", b"for nothing?");
        assert_eq!(
            commitment,
            "hmac-sha256:5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"
        );
        let long_key = [0xaa_u8; 131];
        assert_eq!(
            hmac_commitment(
                &long_key,
                b"Test Using Larger Than Block-Size Key - Hash Key First",
                b""
            ),
            "hmac-sha256:60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54"
        );
    }

    #[test]
    fn collapses_unquoted_blanks() {
        assert_eq!(collapse_unquoted_blanks("  a \t b  "), "a b");
        assert_eq!(collapse_unquoted_blanks("a 'b  c'  d"), "a 'b  c' d");
        assert_eq!(collapse_unquoted_blanks("a\\  b"), "a\\  b");
    }

    #[test]
    fn strips_exact_shell_wrappers() {
        assert_eq!(
            normalize_observed_command_str("bash -lc 'ls  -la'").as_deref(),
            Some("ls -la")
        );
        assert_eq!(
            normalize_observed_command_str("bash -lc \"ls\"").as_deref(),
            Some("bash -lc \"ls\"")
        );
        assert_eq!(
            normalize_observed_command_argv(&["/bin/bash", "-lc", "git status"]).as_deref(),
            Some("git status")
        );
        assert_eq!(normalize_observed_command_argv(&["sh", "-c", ""]), None);
        assert_eq!(normalize_observed_command_str("   "), None);
        assert_eq!(normalize_observed_command_str("a\0"), None);
    }

    #[test]
    fn matches_token_patterns() {
        assert!(is_token("hook:1"));
        assert!(!is_token("-x"));
        assert!(is_gap_code("a_gap"));
        assert!(!is_gap_code("A"));
        assert_eq!(looks_like_path("c:/x"), Some(true));
        assert_eq!(looks_like_path("1:/x"), Some(false));
        assert_eq!(looks_like_path("é:/x"), None);
    }
}
