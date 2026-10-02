//! Twin of the sensitive-content scanner in `yoetz.observability.privacy`.
//!
//! `scan_for_sensitive_content` windows its input into overlapping chunks, looks for canaries,
//! then private-key markers, then eight credential regexes, appends each distinct
//! `(kind, start, end)` finding until a cap, and finally sorts by `(start, end, kind)`. This
//! module reproduces that discovery order, the cap, the chunk windowing, and the overlap dedup
//! exactly.
//!
//! The regexes are not run through a regex engine. Each one is a hand-written matcher whose
//! result is derived from Python `re` backtracking semantics (leftmost start, greedy quantifiers
//! tried longest first, alternatives tried in order, non-overlapping `finditer` resumption at the
//! previous match end). Every bounded repeat in these patterns is over a single byte class whose
//! complement contains the next required byte, so greedy backtracking can only succeed at the
//! maximal run; the per-pattern notes below record why each matcher is equivalent. The
//! differential fuzz harness (`privacy_pattern_spans` against `Pattern.finditer`) checks it.
//!
//! Bytes patterns in Python are ASCII-only: `\s` is `[ \t\n\r\x0b\x0c]` and `(?i)` folds only
//! ASCII letters, so every class here is a plain byte predicate.

use std::collections::HashSet;

use memchr::{memchr2_iter, memmem};

/// Finding kinds, declared in the reference's string order
/// (`canary` < `credential_pattern` < `private_key_marker`) so the derived `Ord` sorts like it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum FindingKind {
    Canary,
    CredentialPattern,
    PrivateKeyMarker,
}

impl FindingKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            FindingKind::Canary => "canary",
            FindingKind::CredentialPattern => "credential_pattern",
            FindingKind::PrivateKeyMarker => "private_key_marker",
        }
    }
}

/// One structural finding: a kind and a byte span, never the matched bytes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Finding {
    pub kind: FindingKind,
    pub start: usize,
    pub end: usize,
}

/// The module constants the reference reads at call time.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ScanLimits {
    /// `_MAX_SCAN_FINDINGS`; zero (or a negative reference value) records nothing.
    pub max_findings: usize,
    /// `_SCAN_CHUNK_BYTES`.
    pub chunk_bytes: usize,
    /// `_SCAN_OVERLAP_BYTES`.
    pub overlap_bytes: usize,
}

impl ScanLimits {
    pub const DEFAULT: ScanLimits = ScanLimits {
        max_findings: 128,
        chunk_bytes: 65_536,
        overlap_bytes: 4_096,
    };

    /// Limits from the reference's integer constants, or `None` when the windowing would not
    /// terminate in the reference (`overlap >= chunk`) or a value is out of range.
    pub fn new(max_findings: i64, chunk_bytes: i64, overlap_bytes: i64) -> Option<Self> {
        if overlap_bytes < 0 || chunk_bytes <= overlap_bytes {
            return None;
        }
        Some(ScanLimits {
            max_findings: usize::try_from(max_findings.max(0)).ok()?,
            chunk_bytes: usize::try_from(chunk_bytes).ok()?,
            overlap_bytes: usize::try_from(overlap_bytes).ok()?,
        })
    }
}

impl Default for ScanLimits {
    fn default() -> Self {
        ScanLimits::DEFAULT
    }
}

/// The PEM private-key markers, in the reference's order.
pub const PRIVATE_KEY_MARKERS: [&[u8]; 4] = [
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
];

/// `_scan_chunks`: `(start, end)` windows over `len` bytes.
pub fn chunk_windows(len: usize, limits: ScanLimits) -> ChunkWindows {
    ChunkWindows {
        len,
        limits,
        next: Some(0),
    }
}

pub struct ChunkWindows {
    len: usize,
    limits: ScanLimits,
    next: Option<usize>,
}

impl Iterator for ChunkWindows {
    type Item = (usize, usize);

    fn next(&mut self) -> Option<(usize, usize)> {
        let start = self.next?;
        if self.len <= self.limits.chunk_bytes {
            self.next = None;
            return Some((0, self.len));
        }
        let end = self.len.min(start.saturating_add(self.limits.chunk_bytes));
        // `ScanLimits` guarantees overlap < chunk, so the next window always advances.
        self.next = if end == self.len {
            None
        } else {
            Some(end - self.limits.overlap_bytes)
        };
        Some((start, end))
    }
}

/// `_append_finding` over a findings list and its seen set.
struct Collector {
    findings: Vec<Finding>,
    seen: HashSet<Finding>,
    max: usize,
}

impl Collector {
    fn new(max: usize) -> Self {
        Collector {
            findings: Vec::new(),
            seen: HashSet::new(),
            max,
        }
    }

    #[inline]
    fn full(&self) -> bool {
        self.findings.len() >= self.max
    }

    #[inline]
    fn push(&mut self, kind: FindingKind, start: usize, end: usize) {
        let finding = Finding { kind, start, end };
        if !self.full() && self.seen.insert(finding) {
            self.findings.push(finding);
        }
    }

    fn sorted(mut self) -> Vec<Finding> {
        self.findings
            .sort_by_key(|finding| (finding.start, finding.end, finding.kind));
        self.findings
    }
}

/// Literal search in every chunk, resuming at `found + step(len)` like `bytes.find(sub, offset)`.
fn scan_literal(
    data: &[u8],
    needle: &[u8],
    kind: FindingKind,
    limits: ScanLimits,
    collector: &mut Collector,
) {
    let finder = memmem::Finder::new(needle);
    // `found + max(1, len(canary))` / `found + len(marker)`: identical for non-empty needles.
    let step = needle.len().max(1);
    for (chunk_start, chunk_end) in chunk_windows(data.len(), limits) {
        let chunk = &data[chunk_start..chunk_end];
        let mut offset = 0usize;
        while !collector.full() {
            if offset > chunk.len() {
                break;
            }
            let Some(index) = finder.find(&chunk[offset..]) else {
                break;
            };
            let found = offset + index;
            let absolute = chunk_start + found;
            collector.push(kind, absolute, absolute + needle.len());
            offset = found + step;
        }
        if collector.full() {
            return;
        }
    }
}

fn scan_markers(data: &[u8], limits: ScanLimits, collector: &mut Collector) {
    for marker in PRIVATE_KEY_MARKERS {
        if collector.full() {
            return;
        }
        scan_literal(
            data,
            marker,
            FindingKind::PrivateKeyMarker,
            limits,
            collector,
        );
    }
}

fn scan_patterns(data: &[u8], limits: ScanLimits, collector: &mut Collector) {
    for pattern in PATTERNS {
        for (chunk_start, chunk_end) in chunk_windows(data.len(), limits) {
            if collector.full() {
                return;
            }
            let chunk = &data[chunk_start..chunk_end];
            pattern.find_iter(chunk, &mut |start, end| {
                collector.push(
                    FindingKind::CredentialPattern,
                    chunk_start + start,
                    chunk_start + end,
                );
                !collector.full()
            });
        }
    }
}

/// `scan_for_sensitive_content(data, canaries=canaries)` after its input validation.
pub fn scan(data: &[u8], canaries: &[&[u8]], limits: ScanLimits) -> Vec<Finding> {
    let mut collector = Collector::new(limits.max_findings);
    if collector.full() {
        return Vec::new();
    }
    for canary in canaries {
        if collector.full() {
            break;
        }
        scan_literal(data, canary, FindingKind::Canary, limits, &mut collector);
    }
    scan_markers(data, limits, &mut collector);
    scan_patterns(data, limits, &mut collector);
    collector.sorted()
}

/// `_replace_sensitive_spans` over sorted findings.
pub fn replace_spans(data: &[u8], findings: &[Finding]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len());
    let mut cursor = 0usize;
    for finding in findings {
        if finding.start < cursor {
            continue;
        }
        out.extend_from_slice(&data[cursor..finding.start]);
        out.extend_from_slice(b"[REDACTED]");
        cursor = finding.end;
    }
    out.extend_from_slice(&data[cursor.min(data.len())..]);
    out
}

/// `redact_sensitive_content`: `None` when nothing was found (the caller keeps its input).
pub fn redact(data: &[u8], limits: ScanLimits) -> Option<Vec<u8>> {
    let findings = scan(data, &[], limits);
    if findings.is_empty() {
        return None;
    }
    Some(replace_spans(data, &findings))
}

/// Outcome of repeated redaction passes.
#[derive(Debug, PartialEq, Eq)]
pub enum RedactPasses {
    /// The first pass found nothing; the input stands unchanged.
    Clean,
    /// Some pass redacted and a later pass found nothing.
    Redacted(Vec<u8>),
    /// Every allowed pass still found something.
    Incomplete,
}

/// `check_change._redacted`'s loop: redact until a pass finds nothing, at most `passes` times.
pub fn redact_passes(data: &[u8], passes: u64, limits: ScanLimits) -> RedactPasses {
    let mut current: Option<Vec<u8>> = None;
    for _ in 0..passes {
        let text = current.as_deref().unwrap_or(data);
        match redact(text, limits) {
            None => {
                return match current {
                    None => RedactPasses::Clean,
                    Some(text) => RedactPasses::Redacted(text),
                };
            }
            Some(replaced) => current = Some(replaced),
        }
    }
    RedactPasses::Incomplete
}

/// The finding kinds `scan(data, &[], limits)` would report, without building the findings:
/// `(private_key_marker present, credential_pattern present)`.
///
/// Markers are collected exactly (they are discovered first and can exhaust the cap). A
/// credential finding is recorded iff the markers left room under the cap and any pattern
/// matches anywhere: the first such match is never a duplicate of a marker finding.
pub fn sensitive_kinds(data: &[u8], limits: ScanLimits) -> (bool, bool) {
    let mut collector = Collector::new(limits.max_findings);
    if collector.full() {
        return (false, false);
    }
    scan_markers(data, limits, &mut collector);
    let private_key = !collector.findings.is_empty();
    let credential = !collector.full() && any_pattern_match(data, limits);
    (private_key, credential)
}

fn any_pattern_match(data: &[u8], limits: ScanLimits) -> bool {
    for pattern in PATTERNS {
        for (chunk_start, chunk_end) in chunk_windows(data.len(), limits) {
            let mut found = false;
            pattern.find_iter(&data[chunk_start..chunk_end], &mut |_, _| {
                found = true;
                false
            });
            if found {
                return true;
            }
        }
    }
    false
}

// ------------------------------------------------------------------------------------------------
// Byte classes.
// ------------------------------------------------------------------------------------------------

#[inline]
fn is_alpha(byte: u8) -> bool {
    byte.is_ascii_alphabetic()
}

#[inline]
fn is_alnum(byte: u8) -> bool {
    byte.is_ascii_alphanumeric()
}

/// `[A-Za-z0-9_]`
#[inline]
fn is_word(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}

/// `[A-Za-z0-9_-]`
#[inline]
fn is_ident(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-'
}

/// `[A-Za-z0-9-]`
#[inline]
fn is_alnum_dash(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'-'
}

/// `[A-Z0-9]`
#[inline]
fn is_upper_digit(byte: u8) -> bool {
    byte.is_ascii_uppercase() || byte.is_ascii_digit()
}

/// Python bytes `\s`: `[ \t\n\r\x0b\x0c]`.
#[inline]
fn is_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
}

#[inline]
fn is_quote(byte: u8) -> bool {
    byte == b'\'' || byte == b'"'
}

#[inline]
fn is_separator(byte: u8) -> bool {
    byte == b'_' || byte == b'-'
}

/// `[^\s,'";}{]`
#[inline]
fn is_value(byte: u8) -> bool {
    !is_space(byte) && !matches!(byte, b',' | b'\'' | b'"' | b';' | b'}' | b'{')
}

/// `[A-Za-z_/=+-]`
#[inline]
fn is_token_value_mark(byte: u8) -> bool {
    byte.is_ascii_alphabetic() || matches!(byte, b'_' | b'/' | b'=' | b'+' | b'-')
}

/// `[A-Za-z0-9+.-]`
#[inline]
fn is_scheme(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'.' | b'-')
}

/// `[^\s/:@]`
#[inline]
fn is_userinfo(byte: u8) -> bool {
    !is_space(byte) && !matches!(byte, b'/' | b':' | b'@')
}

/// `[^\s/@]`
#[inline]
fn is_password(byte: u8) -> bool {
    !is_space(byte) && !matches!(byte, b'/' | b'@')
}

/// Length of the run of `class` bytes starting at `from`, counting at most `cap`.
#[inline]
fn run(hay: &[u8], from: usize, cap: usize, class: fn(u8) -> bool) -> usize {
    if from >= hay.len() {
        return 0;
    }
    let limit = hay.len().min(from.saturating_add(cap));
    hay[from..limit]
        .iter()
        .position(|&byte| !class(byte))
        .unwrap_or(limit - from)
}

#[inline]
fn byte_at(hay: &[u8], index: usize) -> Option<u8> {
    hay.get(index).copied()
}

// ------------------------------------------------------------------------------------------------
// The credential patterns, in `(*_CREDENTIAL_PATTERNS, _URI_PASSWORD, _SECRET_ASSIGNMENT,
// _TOKEN_ASSIGNMENT)` order.
// ------------------------------------------------------------------------------------------------

/// One credential regex as a `finditer` over a chunk.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Pattern {
    /// `(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,256}(?![A-Za-z0-9_-])`
    OpenAiKey,
    /// `(?<![A-Za-z0-9])(?:ghp_|gho_|ghu_|ghs_)[A-Za-z0-9]{20,256}`
    GitHubToken,
    /// `(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,256}`
    GitHubPat,
    /// `(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{20,256}`
    SlackToken,
    /// `(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])`
    AwsAccessKey,
    /// `[A-Za-z][A-Za-z0-9+.-]{0,31}://[^\s/:@]{1,128}:[^\s/@]{1,256}@`
    UriPassword,
    /// `_SECRET_ASSIGNMENT`
    SecretAssignment,
    /// `_TOKEN_ASSIGNMENT`
    TokenAssignment,
}

pub const PATTERNS: [Pattern; 8] = [
    Pattern::OpenAiKey,
    Pattern::GitHubToken,
    Pattern::GitHubPat,
    Pattern::SlackToken,
    Pattern::AwsAccessKey,
    Pattern::UriPassword,
    Pattern::SecretAssignment,
    Pattern::TokenAssignment,
];

/// The exact Python regex sources and `re` flags these matchers implement, in `PATTERNS` order.
/// The Python module binds the native scanner only when its compiled patterns still carry these
/// sources and flags, so an edited regex falls back to the reference instead of drifting.
pub const PATTERN_SOURCES: [(&[u8], u32); 8] = [
    (br#"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,256}(?![A-Za-z0-9_-])"#, 0),
    (br#"(?<![A-Za-z0-9])(?:ghp_|gho_|ghu_|ghs_)[A-Za-z0-9]{20,256}"#, 0),
    (br#"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,256}"#, 0),
    (br#"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{20,256}"#, 0),
    (br#"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])"#, 0),
    (br#"[A-Za-z][A-Za-z0-9+.-]{0,31}://[^\s/:@]{1,128}:[^\s/@]{1,256}@"#, 0),
    (
        br#"(?i)(?:^|[^A-Za-z0-9_])['\"]?(?:[A-Za-z][A-Za-z0-9]{0,63}[_-])*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|private[_-]?key|secret)(?:[_-][A-Za-z][A-Za-z0-9]{0,63})*['\"]?\s*[:=]\s*['\"]?[^\s,'\";}{]{1,512}"#,
        2,
    ),
    (
        br#"(?i)(?:^|[^A-Za-z0-9_])['\"]?(?:[A-Za-z][A-Za-z0-9]{0,63}[_-])*token['\"]?\s*[:=]\s*['\"]?(?=[^\s,'\";}{]{0,511}[A-Za-z_/=+-])[^\s,'\";}{]{8,512}"#,
        2,
    ),
];

impl Pattern {
    /// Report every non-overlapping match `(start, end)` in order; `emit` returns `false` to stop.
    pub fn find_iter(self, hay: &[u8], emit: &mut dyn FnMut(usize, usize) -> bool) {
        match self {
            Pattern::OpenAiKey => literal_iter(hay, b"sk-", openai_key_at, emit),
            Pattern::GitHubToken => literal_iter(hay, b"gh", github_token_at, emit),
            Pattern::GitHubPat => literal_iter(hay, b"github_pat_", github_pat_at, emit),
            Pattern::SlackToken => literal_iter(hay, b"xox", slack_token_at, emit),
            Pattern::AwsAccessKey => literal_iter(hay, b"AKIA", aws_access_key_at, emit),
            Pattern::UriPassword => uri_password_iter(hay, emit),
            Pattern::SecretAssignment => assignment_iter(hay, Assignment::Secret, emit),
            Pattern::TokenAssignment => assignment_iter(hay, Assignment::Token, emit),
        }
    }
}

/// `finditer` for a pattern whose every match starts with `literal`: candidate starts are the
/// literal's occurrences at or after the resume point, tried in order.
fn literal_iter(
    hay: &[u8],
    literal: &[u8],
    matcher: fn(&[u8], usize) -> Option<usize>,
    emit: &mut dyn FnMut(usize, usize) -> bool,
) {
    let finder = memmem::Finder::new(literal);
    let mut position = 0usize;
    while position < hay.len() {
        let Some(index) = finder.find(&hay[position..]) else {
            return;
        };
        let start = position + index;
        match matcher(hay, start) {
            Some(end) => {
                if !emit(start, end) {
                    return;
                }
                position = end;
            }
            None => position = start + 1,
        }
    }
}

/// `(?<![A-Za-z0-9])`: true at the haystack start.
#[inline]
fn not_after_alnum(hay: &[u8], start: usize) -> bool {
    start == 0 || !is_alnum(hay[start - 1])
}

/// OpenAI key at `start` (where `sk-` sits). With `r` the maximal `[A-Za-z0-9_-]` run after
/// `sk-`, the negative lookahead (same class) only holds at the run's end, so the match is
/// `sk-` + the whole run when `20 <= r <= 256` (no `proj-`) or, after an optional `proj-`
/// (itself in the class), when `20 <= r - 5 <= 256`. Both end at the run's end.
fn openai_key_at(hay: &[u8], start: usize) -> Option<usize> {
    if !not_after_alnum(hay, start) {
        return None;
    }
    let body = start + 3;
    let length = run(hay, body, 262, is_ident);
    let project = hay[body..].starts_with(b"proj-");
    if (20..=256).contains(&length) || (project && (25..=261).contains(&length)) {
        Some(body + length)
    } else {
        None
    }
}

/// Greedy `{20,256}` over `class` after a fixed prefix of `prefix_len` bytes: without a
/// trailing assertion the match takes `min(run, 256)` and needs `run >= 20`.
#[inline]
fn bounded_run_after(
    hay: &[u8],
    start: usize,
    prefix_len: usize,
    class: fn(u8) -> bool,
) -> Option<usize> {
    let body = start + prefix_len;
    let length = run(hay, body, 256, class);
    (length >= 20).then_some(body + length)
}

fn github_token_at(hay: &[u8], start: usize) -> Option<usize> {
    if !matches!(byte_at(hay, start + 2), Some(b'p' | b'o' | b'u' | b's'))
        || byte_at(hay, start + 3) != Some(b'_')
        || !not_after_alnum(hay, start)
    {
        return None;
    }
    bounded_run_after(hay, start, 4, is_alnum)
}

fn github_pat_at(hay: &[u8], start: usize) -> Option<usize> {
    if !not_after_alnum(hay, start) {
        return None;
    }
    bounded_run_after(hay, start, 11, is_word)
}

fn slack_token_at(hay: &[u8], start: usize) -> Option<usize> {
    if !matches!(
        byte_at(hay, start + 3),
        Some(b'b' | b'a' | b'p' | b'r' | b's')
    ) || byte_at(hay, start + 4) != Some(b'-')
        || !not_after_alnum(hay, start)
    {
        return None;
    }
    bounded_run_after(hay, start, 5, is_alnum_dash)
}

/// `AKIA` + exactly 16 `[A-Z0-9]` not followed by another: the run after `AKIA` must be 16.
fn aws_access_key_at(hay: &[u8], start: usize) -> Option<usize> {
    if start > 0 && is_upper_digit(hay[start - 1]) {
        return None;
    }
    (run(hay, start + 4, 17, is_upper_digit) == 16).then_some(start + 20)
}

/// `_URI_PASSWORD` `finditer`.
///
/// Every class repeat here is followed by a byte outside its class (`:` after the scheme and
/// the userinfo, `@` after the password), so each repeat succeeds only at its maximal run and
/// only when that run fits the bound. A match is therefore fixed by the position `t` of its
/// `://`: userinfo `1..=128` bytes then `:`, password `1..=256` bytes then `@`. Its start is
/// the leftmost alphabetic `s >= resume` with `t - s - 1 <= 31` and `hay[s+1..t]` all scheme
/// bytes. Starts belonging to a later `://` lie after this one's (`:` and `/` are not scheme
/// bytes), so walking `://` occurrences in order yields matches in `finditer` order.
fn uri_password_iter(hay: &[u8], emit: &mut dyn FnMut(usize, usize) -> bool) {
    let finder = memmem::Finder::new(b"://");
    let mut resume = 0usize;
    let mut from = 1usize;
    while from < hay.len() {
        let Some(index) = finder.find(&hay[from..]) else {
            return;
        };
        let separator = from + index;
        from = separator + 1;
        let Some(end) = uri_authority_end(hay, separator + 3) else {
            continue;
        };
        let lowest = resume.max(separator.saturating_sub(32));
        let mut scheme_start = separator;
        while scheme_start > lowest && is_scheme(hay[scheme_start - 1]) {
            scheme_start -= 1;
        }
        let Some(start) = (scheme_start..separator).find(|&index| is_alpha(hay[index])) else {
            continue;
        };
        if !emit(start, end) {
            return;
        }
        resume = end;
        from = end + 1;
    }
}

fn uri_authority_end(hay: &[u8], userinfo: usize) -> Option<usize> {
    let user = run(hay, userinfo, 129, is_userinfo);
    if !(1..=128).contains(&user) || byte_at(hay, userinfo + user) != Some(b':') {
        return None;
    }
    let password = userinfo + user + 1;
    let length = run(hay, password, 257, is_password);
    if !(1..=256).contains(&length) || byte_at(hay, password + length) != Some(b'@') {
        return None;
    }
    Some(password + length + 1)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Assignment {
    /// `_SECRET_ASSIGNMENT`
    Secret,
    /// `_TOKEN_ASSIGNMENT`
    Token,
}

/// `_SECRET_ASSIGNMENT` / `_TOKEN_ASSIGNMENT` `finditer`.
///
/// Shape (`(?i)`, ASCII folding):
/// `(?:^|[^A-Za-z0-9_])['"]?(?:[A-Za-z][A-Za-z0-9]{0,63}[_-])*KEYWORD` then, for secrets only,
/// `(?:[_-][A-Za-z][A-Za-z0-9]{0,63})*`, then `['"]?\s*[:=]\s*['"]?` and the value
/// (`[^\s,'";}{]{1,512}` for secrets; `(?=[^\s,'";}{]{0,511}[A-Za-z_/=+-])[^\s,'";}{]{8,512}` for
/// tokens).
///
/// Everything between the optional opening quote and the closing tail is `[A-Za-z0-9_-]`, and
/// the tail's first byte (quote, space, `:` or `=`) is not, so the tail starts exactly where the
/// identifier run that begins at `p1` ends (`z`). The tail is deterministic: each optional quote
/// or `\s*` that backtracks leaves a byte the next item cannot accept, and the value class
/// excludes quotes and spaces. The match end is therefore a function of `z` alone, and a start
/// matches iff some backtracking path reaches `z` with a keyword; path priority cannot change
/// the reported span.
///
/// Candidate starts are found from the tails: a tail begins at some `z` whose forward walk
/// (optional quote, maximal `\s*`) lands on a `:` or `=`, so walking each `:`/`=` back over its
/// spaces and an optional quote yields every possible `z`, in increasing order. A start `s`
/// that matches has `p1 <= s + 2` inside the identifier run ending at `z`, so it lies in
/// `[run_start(z) - 2, z)`. Those ranges are visited in increasing `s`, each start is evaluated
/// once by the full matcher, and the scan resumes at each match end, so the reported matches
/// are exactly the reference's.
fn assignment_iter(hay: &[u8], kind: Assignment, emit: &mut dyn FnMut(usize, usize) -> bool) {
    let mut cache = TailCache::default();
    let mut resume = 0usize;
    let mut untested = 0usize;
    for assign in memchr2_iter(b':', b'=', hay) {
        if assign < resume {
            continue;
        }
        let mut before_spaces = assign;
        while before_spaces > 0 && is_space(hay[before_spaces - 1]) {
            before_spaces -= 1;
        }
        let tail_start = if before_spaces >= 2
            && is_quote(hay[before_spaces - 1])
            && is_ident(hay[before_spaces - 2])
        {
            before_spaces - 1
        } else if before_spaces >= 1 && is_ident(hay[before_spaces - 1]) {
            before_spaces
        } else {
            continue;
        };
        let mut run_start = tail_start;
        while run_start > 0 && is_ident(hay[run_start - 1]) {
            run_start -= 1;
        }
        if !run_may_hold_keyword(&hay[run_start..tail_start], kind)
            || assignment_tail(hay, tail_start, kind).is_none()
        {
            continue;
        }
        let mut start = run_start.saturating_sub(2).max(resume).max(untested);
        while start < tail_start {
            if (start == 0 || !is_word(hay[start]))
                && let Some(end) = assignment_at(hay, start, kind, &mut cache)
            {
                if !emit(start, end) {
                    return;
                }
                resume = end;
                break;
            }
            start += 1;
        }
        untested = untested.max(start);
    }
}

/// A necessary condition on the identifier run that ends at a tail: every keyword lies inside
/// it. A token keyword must end the run; every secret keyword contains `key`, `token`, `passw`,
/// or `secret`.
fn run_may_hold_keyword(identifier: &[u8], kind: Assignment) -> bool {
    match kind {
        Assignment::Token => {
            identifier.len() >= 5
                && identifier[identifier.len() - 5..].eq_ignore_ascii_case(b"token")
        }
        Assignment::Secret => (0..identifier.len()).any(|index| {
            let rest = &identifier[index..];
            let starts = |literal: &[u8]| {
                rest.len() >= literal.len() && rest[..literal.len()].eq_ignore_ascii_case(literal)
            };
            match identifier[index].to_ascii_lowercase() {
                b'k' => starts(b"key"),
                b't' => starts(b"token"),
                b'p' => starts(b"passw"),
                b's' => starts(b"secret"),
                _ => false,
            }
        }),
    }
}

/// Memo of the last identifier run and its tail: every `p1` inside one run shares `z`.
#[derive(Default)]
struct TailCache {
    run_from: usize,
    run_end: usize,
    tail: Option<usize>,
    valid: bool,
}

fn assignment_at(
    hay: &[u8],
    start: usize,
    kind: Assignment,
    cache: &mut TailCache,
) -> Option<usize> {
    let n = hay.len();
    // `(?:^|[^A-Za-z0-9_])`, alternatives in order.
    let mut openings = [None, None];
    if start == 0 {
        openings[0] = Some(0);
        if n > 0 && !is_word(hay[0]) {
            openings[1] = Some(1);
        }
    } else if !is_word(hay[start]) {
        openings[0] = Some(start + 1);
    }
    for opening in openings.into_iter().flatten() {
        // `['"]?`, greedy.
        let quoted = opening < n && is_quote(hay[opening]);
        let identifiers = [quoted.then_some(opening + 1), Some(opening)];
        for identifier in identifiers.into_iter().flatten() {
            if let Some(end) = identifier_then_tail(hay, identifier, kind, cache) {
                return Some(end);
            }
        }
    }
    None
}

fn identifier_then_tail(
    hay: &[u8],
    from: usize,
    kind: Assignment,
    cache: &mut TailCache,
) -> Option<usize> {
    let n = hay.len();
    // Both the component repeat and every keyword begin with a letter.
    if from >= n || !is_alpha(hay[from]) {
        return None;
    }
    let (run_end, tail) = if cache.valid && cache.run_from <= from && from < cache.run_end {
        (cache.run_end, cache.tail)
    } else {
        let run_end = from + run(hay, from, usize::MAX, is_ident);
        let tail = assignment_tail(hay, run_end, kind);
        *cache = TailCache {
            run_from: from,
            run_end,
            tail,
            valid: true,
        };
        (run_end, tail)
    };
    let end = tail?;
    // `(?:[A-Za-z][A-Za-z0-9]{0,63}[_-])*`: each component is a letter, an alphanumeric run
    // that must end within 63 bytes (a longer run leaves an alphanumeric where `[_-]` is
    // required), and a separator, so the component boundaries are fixed. The keyword may follow
    // any number of them; try each boundary.
    let mut boundary = from;
    loop {
        if keyword_reaches(hay, boundary, run_end, kind) {
            return Some(end);
        }
        if boundary >= n || !is_alpha(hay[boundary]) {
            return None;
        }
        let length = run(hay, boundary + 1, 64, is_alnum);
        let separator = boundary + 1 + length;
        if length > 63 || separator >= n || !is_separator(hay[separator]) {
            return None;
        }
        boundary = separator + 1;
    }
}

/// Whether a keyword at `at` (plus, for secrets, the suffix components) ends exactly at `run_end`.
fn keyword_reaches(hay: &[u8], at: usize, run_end: usize, kind: Assignment) -> bool {
    match kind {
        Assignment::Token => at + 5 == run_end && ascii_ci_at(hay, at, b"token"),
        Assignment::Secret => {
            // (prefix, `[_-]?`, suffix) alternatives in pattern order.
            const KEYWORDS: [(&[u8], bool, &[u8]); 7] = [
                (b"api", true, b"key"),
                (b"access", true, b"token"),
                (b"auth", true, b"token"),
                (b"password", false, b""),
                (b"passwd", false, b""),
                (b"private", true, b"key"),
                (b"secret", false, b""),
            ];
            for (prefix, optional_separator, suffix) in KEYWORDS {
                if !ascii_ci_at(hay, at, prefix) {
                    continue;
                }
                let after = at + prefix.len();
                if optional_separator {
                    if byte_at(hay, after).is_some_and(is_separator)
                        && ascii_ci_at(hay, after + 1, suffix)
                        && suffix_components_reach(hay, after + 1 + suffix.len(), run_end)
                    {
                        return true;
                    }
                    if ascii_ci_at(hay, after, suffix)
                        && suffix_components_reach(hay, after + suffix.len(), run_end)
                    {
                        return true;
                    }
                } else if suffix_components_reach(hay, after, run_end) {
                    return true;
                }
            }
            false
        }
    }
}

/// `(?:[_-][A-Za-z][A-Za-z0-9]{0,63})*` from `at` must stop exactly at `run_end`.
///
/// The tail needs a non-identifier byte, so the repeat must consume every remaining identifier
/// byte: stopping early (or cutting an alphanumeric run short) leaves `[_-]` or an
/// alphanumeric where the tail must start, and a run longer than 63 cannot be consumed.
fn suffix_components_reach(hay: &[u8], mut at: usize, run_end: usize) -> bool {
    loop {
        if at == run_end {
            return true;
        }
        if at > run_end || !is_separator(hay[at]) || !byte_at(hay, at + 1).is_some_and(is_alpha) {
            return false;
        }
        let length = run(hay, at + 2, 64, is_alnum);
        if length > 63 {
            return false;
        }
        at += 2 + length;
    }
}

/// `['"]?\s*[:=]\s*['"]?` + value, from the end of the identifier run.
fn assignment_tail(hay: &[u8], from: usize, kind: Assignment) -> Option<usize> {
    let n = hay.len();
    let mut at = from;
    if at < n && is_quote(hay[at]) {
        at += 1;
    }
    at += run(hay, at, usize::MAX, is_space);
    if at >= n || !(hay[at] == b':' || hay[at] == b'=') {
        return None;
    }
    at += 1;
    at += run(hay, at, usize::MAX, is_space);
    if at < n && is_quote(hay[at]) {
        at += 1;
    }
    let length = run(hay, at, 512, is_value);
    match kind {
        Assignment::Secret => (length >= 1).then_some(at + length),
        Assignment::Token => (length >= 8
            && hay[at..at + length]
                .iter()
                .any(|&byte| is_token_value_mark(byte)))
        .then_some(at + length),
    }
}

#[inline]
fn ascii_ci_at(hay: &[u8], at: usize, literal: &[u8]) -> bool {
    hay.get(at..at + literal.len())
        .is_some_and(|window| window.eq_ignore_ascii_case(literal))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spans(pattern: Pattern, hay: &[u8]) -> Vec<(usize, usize)> {
        let mut out = Vec::new();
        pattern.find_iter(hay, &mut |start, end| {
            out.push((start, end));
            true
        });
        out
    }

    #[test]
    fn positive_examples_match() {
        let cases: [&[u8]; 8] = [
            b"OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456",
            b"https://user:password@example.invalid/resource",
            b"github_pat_abcdefghijklmnopqrstuvwxyz123456",
            b"AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            b"AZURE_CLIENT_SECRET=abc123secretvalue0001",
            b"GITHUB_TOKEN=notakeybutlongenoughvalue",
            b"NPM_TOKEN=npm_notarealtokenvalue12",
            b"-----BEGIN OPENSSH PRIVATE KEY-----",
        ];
        for case in cases {
            assert!(!scan(case, &[], ScanLimits::DEFAULT).is_empty());
        }
    }

    #[test]
    fn negative_examples_do_not_match() {
        let cases: [&[u8]; 10] = [
            b"https://example.invalid/resource",
            b"api_key=",
            b"random structural identifier sk-short",
            b"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            b"\xff\xfe\x80 ordinary invalid utf8 bytes",
            b"AWS_ACCESS_KEY_ID=not-an-akia-identifier",
            b"TOKEN_COUNT=12",
            b"MAX_TOKEN=4096",
            b"SECRETARY=Alice",
            b"tokenize=falsehood",
        ];
        for case in cases {
            assert_eq!(scan(case, &[], ScanLimits::DEFAULT), Vec::new());
        }
    }

    #[test]
    fn spans_follow_the_reference() {
        assert_eq!(
            spans(
                Pattern::SecretAssignment,
                b"export AWS_SECRET_ACCESS_KEY=wJal/K7 x"
            ),
            vec![(6, 36)]
        );
        assert_eq!(
            spans(
                Pattern::TokenAssignment,
                b"  const token = parser.getToken0();"
            ),
            vec![(7, 34)]
        );
        assert_eq!(
            spans(Pattern::UriPassword, b"xhttps://u:p@h"),
            vec![(0, 13)]
        );
        assert_eq!(
            spans(Pattern::AwsAccessKey, b"AKIAABCDEFGHIJKLMNOP"),
            vec![(0, 20)]
        );
        assert_eq!(
            spans(Pattern::AwsAccessKey, b"AKIAABCDEFGHIJKLMNOPQ"),
            vec![]
        );
    }

    #[test]
    fn chunk_windows_overlap() {
        let limits = ScanLimits {
            max_findings: 128,
            chunk_bytes: 10,
            overlap_bytes: 4,
        };
        let windows: Vec<_> = chunk_windows(25, limits).collect();
        assert_eq!(windows, vec![(0, 10), (6, 16), (12, 22), (18, 25)]);
        assert_eq!(chunk_windows(0, limits).collect::<Vec<_>>(), vec![(0, 0)]);
    }

    #[test]
    fn redaction_passes_converge() {
        let text = b"a token = abcdefgh1 b";
        match redact_passes(text, 64, ScanLimits::DEFAULT) {
            RedactPasses::Redacted(out) => assert_eq!(out, b"a[REDACTED] b"),
            other => panic!("unexpected {other:?}"),
        }
        assert_eq!(
            redact_passes(b"clean", 64, ScanLimits::DEFAULT),
            RedactPasses::Clean
        );
        assert_eq!(
            redact_passes(text, 0, ScanLimits::DEFAULT),
            RedactPasses::Incomplete
        );
    }
}
