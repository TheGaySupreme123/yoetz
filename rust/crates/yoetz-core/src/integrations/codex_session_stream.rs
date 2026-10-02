//! Pure twins of `yoetz.adapters.integrations.codex_session_stream` helpers.
//!
//! `_token`, rollout filename matching, the keyed oversized-line partial state and commitment,
//! and the rollout-item decision fold (#910). The fold works over facts the binding extracts
//! from live envelopes, so its control flow mirrors the reference line for line.

use std::collections::{HashMap, HashSet};

use sha2::{Digest, Sha256};

use crate::domain::observation::is_token;

/// `_OVERSIZED_PARTIAL_PREFIX`.
pub const OVERSIZED_PARTIAL_PREFIX: &[u8] = b"\x00yoetz-oversized-line/v1\x00";
/// `_OVERSIZED_PARTIAL_DOMAIN`.
pub const OVERSIZED_PARTIAL_DOMAIN: &[u8] = b"yoetz/observation-stream-oversized-state/v1\x00";
/// `_OVERSIZED_LINE_DOMAIN`.
pub const OVERSIZED_LINE_DOMAIN: &[u8] = b"yoetz/observation-stream-oversized-line/v1\x00";
/// `_MAX_CANONICAL_INTEGER`.
pub const MAX_CANONICAL_INTEGER: i128 = (1 << 53) - 1;
/// `_JSONL_SUFFIXES`.
pub const JSONL_SUFFIXES: [&str; 2] = [".jsonl", ".jsonl.zst"];

/// `_token(value) is not None` for a string: `[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}`.
#[inline]
pub fn token(value: &str) -> bool {
    is_token(value)
}

/// `hmac.new(key, message, hashlib.sha256).digest()`.
pub fn hmac_sha256(key: &[u8], parts: &[&[u8]]) -> [u8; 32] {
    const BLOCK: usize = 64;
    let mut block = [0u8; BLOCK];
    if key.len() > BLOCK {
        block[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        block[..key.len()].copy_from_slice(key);
    }
    let mut inner = Sha256::new();
    inner.update(block.map(|byte| byte ^ 0x36));
    for part in parts {
        inner.update(part);
    }
    let mut outer = Sha256::new();
    outer.update(block.map(|byte| byte ^ 0x5c));
    outer.update(inner.finalize());
    outer.finalize().into()
}

/// The final path component the way `pathlib.PurePosixPath(name).name` reads it.
pub fn posix_name(path: &str) -> &str {
    path.split('/')
        .rev()
        .find(|part| !part.is_empty() && *part != ".")
        .unwrap_or("")
}

/// `rollout_filename_matches_token` for an ASCII file name and a valid token.
pub fn rollout_filename_matches(name: &str, token_value: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    for suffix in JSONL_SUFFIXES {
        if !lower.ends_with(suffix) {
            continue;
        }
        let stem = &name[..name.len() - suffix.len()];
        return stem == token_value
            || stem.ends_with(&format!("-{token_value}"))
            || stem.contains(&format!("-{token_value}_"));
    }
    false
}

/// `session_commitment \0 str(source_generation) \0 source_identity \0` (all ASCII).
fn oversized_context(
    session_commitment: &str,
    source_generation: i64,
    source_identity: &str,
) -> Vec<u8> {
    let mut context = Vec::with_capacity(session_commitment.len() + source_identity.len() + 24);
    context.extend_from_slice(session_commitment.as_bytes());
    context.push(0);
    context.extend_from_slice(itoa::Buffer::new().format(source_generation).as_bytes());
    context.push(0);
    context.extend_from_slice(source_identity.as_bytes());
    context.push(0);
    context
}

fn is_lower_hex_digest(text: &[u8]) -> bool {
    text.len() == 64
        && text
            .iter()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

/// `_encode_oversized_partial` once the caller has checked `line_start` is a non-negative `int`
/// and every text argument is ASCII. `None` is the reference's `session_stream_partial_invalid`.
pub fn encode_oversized_partial(
    line_start: i64,
    prefix_commitment: &str,
    session_commitment: &str,
    source_generation: i64,
    source_identity: &str,
    key_material: &[u8],
) -> Option<Vec<u8>> {
    let digest = prefix_commitment
        .strip_prefix("hmac-sha256:")
        .unwrap_or(prefix_commitment);
    if line_start < 0 || !is_lower_hex_digest(digest.as_bytes()) {
        return None;
    }
    let mut body = Vec::with_capacity(84);
    body.extend_from_slice(itoa::Buffer::new().format(line_start).as_bytes());
    body.push(b':');
    body.extend_from_slice(digest.as_bytes());
    let mut context = oversized_context(session_commitment, source_generation, source_identity);
    context.extend_from_slice(&body);
    let tag = hmac_sha256(key_material, &[OVERSIZED_PARTIAL_DOMAIN, &context]);
    let mut out = Vec::with_capacity(OVERSIZED_PARTIAL_PREFIX.len() + 65 + body.len());
    out.extend_from_slice(OVERSIZED_PARTIAL_PREFIX);
    out.extend_from_slice(hex::encode(tag).as_bytes());
    out.push(b':');
    out.extend_from_slice(&body);
    Some(out)
}

/// Outcome of `_decode_oversized_partial`.
#[derive(Debug, PartialEq, Eq)]
pub enum OversizedPartial {
    /// Not an oversized-line partial (`None`).
    NotOversized,
    /// `ValueError("session_stream_partial_invalid")`.
    Invalid,
    /// `_OversizedLineState(line_start, prefix_digest)`.
    State(i64, String),
    /// A canonical decimal beyond `i64`: only the reference's arbitrary-size `int` can say.
    Defer,
}

/// `_decode_oversized_partial` with ASCII text arguments.
pub fn decode_oversized_partial(
    value: &[u8],
    session_commitment: &str,
    source_generation: i64,
    source_identity: &str,
    key_material: &[u8],
) -> OversizedPartial {
    let Some(rest) = value.strip_prefix(OVERSIZED_PARTIAL_PREFIX) else {
        return OversizedPartial::NotOversized;
    };
    let parts: Vec<&[u8]> = rest.split(|&byte| byte == b':').collect();
    let [tag, line_start_raw, prefix_digest_raw] = parts.as_slice() else {
        return OversizedPartial::Invalid;
    };
    // `int(line_start_raw.decode("ascii"))` accepts spellings `str(int)` never produces; the
    // reference then refuses any of them by comparing against `str(line_start)`. Only a
    // canonical non-negative decimal survives both, so that is all this accepts.
    let canonical_decimal = !line_start_raw.is_empty()
        && line_start_raw.iter().all(u8::is_ascii_digit)
        && (line_start_raw.len() == 1 || line_start_raw[0] != b'0');
    if !canonical_decimal || !prefix_digest_raw.is_ascii() {
        return OversizedPartial::Invalid;
    }
    let Some(line_start) = std::str::from_utf8(line_start_raw)
        .ok()
        .and_then(|text| text.parse::<i64>().ok())
    else {
        return OversizedPartial::Defer;
    };
    if !is_lower_hex_digest(prefix_digest_raw) {
        return OversizedPartial::Invalid;
    }
    let mut context = oversized_context(session_commitment, source_generation, source_identity);
    context.extend_from_slice(line_start_raw);
    context.push(b':');
    context.extend_from_slice(prefix_digest_raw);
    let expected = hex::encode(hmac_sha256(
        key_material,
        &[OVERSIZED_PARTIAL_DOMAIN, &context],
    ));
    if !constant_time_eq(tag, expected.as_bytes()) {
        return OversizedPartial::Invalid;
    }
    // The digest is 64 lowercase hex characters, so this conversion cannot fail.
    OversizedPartial::State(
        line_start,
        String::from_utf8_lossy(prefix_digest_raw).into_owned(),
    )
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right)
        .fold(0u8, |acc, (l, r)| acc | (l ^ r))
        == 0
}

/// `_oversized_line_commitment` with ASCII text arguments.
pub fn oversized_line_commitment(
    line_start: i64,
    prefix_digest: &str,
    byte_end: i64,
    session_commitment: &str,
    source_generation: i64,
    source_identity: &str,
    key_material: &[u8],
) -> String {
    let mut body = oversized_context(session_commitment, source_generation, source_identity);
    body.extend_from_slice(format!("{line_start}:{byte_end}:{prefix_digest}").as_bytes());
    let digest = hmac_sha256(key_material, &[OVERSIZED_LINE_DOMAIN, &body]);
    format!("hmac-sha256:{}", hex::encode(digest))
}

/// One `_source_file_identity` member: the integer itself or `"hex:<x>"` outside the safe range.
pub enum Bounded {
    Int(i64),
    Hex(String),
}

/// `bounded(value)` inside `_source_file_identity`.
pub fn bounded(value: i128) -> Bounded {
    if (-MAX_CANONICAL_INTEGER..=MAX_CANONICAL_INTEGER).contains(&value) {
        Bounded::Int(value as i64)
    } else if value < 0 {
        Bounded::Hex(format!("-{:x}", value.unsigned_abs()))
    } else {
        Bounded::Hex(format!("{value:x}"))
    }
}

fn push_bounded(out: &mut String, value: &Bounded) {
    match value {
        Bounded::Int(number) => out.push_str(itoa::Buffer::new().format(*number)),
        Bounded::Hex(hex) => {
            out.push_str("\"hex:");
            out.push_str(hex);
            out.push('"');
        }
    }
}

/// `_source_file_identity(facts, key_material)` given the bounded device and inode numbers.
pub fn source_file_identity(device: &Bounded, inode: &Bounded, key_material: &[u8]) -> String {
    let mut payload = String::with_capacity(64);
    payload.push_str("{\"device\":");
    push_bounded(&mut payload, device);
    payload.push_str(",\"inode\":");
    push_bounded(&mut payload, inode);
    payload.push('}');
    let digest = hmac_sha256(key_material, &[payload.as_bytes()]);
    format!("hmac-sha256:{}", hex::encode(digest))
}

/// The source lane of one stored row, as `_rollout_item_decisions` distinguishes them.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RowSource {
    CodexHook,
    CodexSessionStream,
    Other,
}

/// The facts `_rollout_item_decisions` reads from one envelope of the session.
#[derive(Clone, Debug)]
pub struct RolloutRow<'a> {
    pub source: RowSource,
    pub event_kind: &'a str,
    pub source_identity: &'a str,
    /// `_token(structural.get("tool_call_id"))`.
    pub call_id: Option<&'a str>,
    /// `_token(structural.get("command_commitment"))`.
    pub commitment: Option<&'a str>,
    /// `exit_status if type(exit_status) is int else None`.
    pub exit_fact: Option<i64>,
    /// `_hook_post_stated(structural)`.
    pub stated: bool,
    /// `_hooked_tool_item(envelope)`.
    pub hooked_item: bool,
}

/// `_ITEM_*` decisions.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Decision {
    Carrier,
    Copy,
    Pending,
    Unpaired,
}

#[derive(Clone, Copy)]
struct PendingItem<'a> {
    row: usize,
    source_identity: &'a str,
    call_id: Option<&'a str>,
    commitment: Option<&'a str>,
    exit_status: Option<i64>,
}

struct Fold<'a> {
    unstated_calls: HashMap<&'a str, Option<&'a str>>,
    owed: HashMap<&'a str, i64>,
    stated_exits: HashMap<&'a str, Vec<Option<i64>>>,
    stated_calls: HashSet<&'a str>,
    seen_exits: HashMap<&'a str, HashSet<Option<i64>>>,
    open_calls: HashMap<&'a str, HashSet<&'a str>>,
    pending: Vec<PendingItem<'a>>,
    /// Insertion-ordered `decisions` dict: the row whose identity keys it, and its decision.
    decisions: Vec<(usize, Decision)>,
    decided: HashMap<&'a str, usize>,
}

impl<'a> Fold<'a> {
    fn record(&mut self, row: usize, identity: &'a str, decision: Decision) {
        match self.decided.get(identity) {
            Some(&index) => self.decisions[index].1 = decision,
            None => {
                self.decided.insert(identity, self.decisions.len());
                self.decisions.push((row, decision));
            }
        }
    }

    fn decide(&mut self, position: usize, decision: Decision) {
        let item = self.pending.remove(position);
        self.record(item.row, item.source_identity, decision);
    }

    fn proven_copy(&self, item: &PendingItem<'a>) -> bool {
        let Some(commitment) = item.commitment else {
            return true;
        };
        if let Some(call_id) = item.call_id {
            if self.stated_calls.contains(call_id) {
                return true;
            }
        }
        self.seen_exits
            .get(commitment)
            .is_some_and(|exits| exits.contains(&item.exit_status))
    }

    fn settle(&mut self, commitment: Option<&'a str>, closing: bool) {
        let Some(commitment) = commitment else {
            return;
        };
        if self.owed.get(commitment).copied().unwrap_or(0) <= 0 {
            return;
        }
        if self
            .open_calls
            .get(commitment)
            .is_some_and(|calls| !calls.is_empty())
            && !closing
        {
            return;
        }
        let mut waiting = 0;
        let mut index = 0;
        while index < self.pending.len() {
            if self.pending[index].commitment == Some(commitment) {
                self.decide(index, Decision::Carrier);
                waiting += 1;
            } else {
                index += 1;
            }
        }
        let owed = self.owed.entry(commitment).or_insert(0);
        *owed = (*owed - waiting).max(0);
    }

    fn release_unstated(&mut self, call_id: &'a str) {
        if let Some(Some(earlier)) = self.unstated_calls.remove(call_id) {
            if let Some(owed) = self.owed.get_mut(earlier) {
                if *owed > 0 {
                    *owed -= 1;
                }
            }
        }
    }
}

/// `_rollout_item_decisions(envelopes, session_commitment, evicted_open_calls)` over the rows of
/// the session (rows of other sessions are already dropped). Returns the `decisions` dict as an
/// insertion-ordered list of (index of the row whose `source_identity` keys it, decision).
pub fn rollout_item_decisions<'a>(
    rows: &[RolloutRow<'a>],
    evicted_open_calls: &[(&'a str, &'a str)],
) -> Vec<(usize, Decision)> {
    let mut fold = Fold {
        unstated_calls: HashMap::new(),
        owed: HashMap::new(),
        stated_exits: HashMap::new(),
        stated_calls: HashSet::new(),
        seen_exits: HashMap::new(),
        open_calls: HashMap::new(),
        pending: Vec::new(),
        decisions: Vec::new(),
        decided: HashMap::new(),
    };
    for &(commitment, call) in evicted_open_calls {
        fold.open_calls.entry(commitment).or_default().insert(call);
    }
    for (row_index, row) in rows.iter().enumerate() {
        let call_id = row.call_id;
        let commitment = row.commitment;
        let exit_fact = row.exit_fact;
        if row.source == RowSource::CodexHook {
            let kind = row.event_kind;
            if kind == "PreToolUse" {
                if let (Some(call), Some(commitment)) = (call_id, commitment) {
                    fold.open_calls.entry(commitment).or_default().insert(call);
                }
                continue;
            }
            if kind == "Stop" || kind == "SessionEnd" {
                fold.open_calls.clear();
                let mut commitments: Vec<Option<&'a str>> = Vec::new();
                for item in &fold.pending {
                    if !commitments.contains(&item.commitment) {
                        commitments.push(item.commitment);
                    }
                }
                for owed_commitment in commitments {
                    fold.settle(owed_commitment, true);
                }
                while !fold.pending.is_empty() {
                    let item = fold.pending[0];
                    let decision = if fold.proven_copy(&item) {
                        Decision::Copy
                    } else {
                        Decision::Unpaired
                    };
                    fold.decide(0, decision);
                }
                continue;
            }
            if kind != "PostToolUse" {
                continue;
            }
            if let (Some(call), Some(commitment)) = (call_id, commitment) {
                if let Some(calls) = fold.open_calls.get_mut(commitment) {
                    calls.remove(call);
                }
            }
            let mut joined = call_id.and_then(|call| {
                fold.pending
                    .iter()
                    .position(|item| item.call_id == Some(call))
            });
            if !row.stated {
                if let Some(position) = joined {
                    fold.decide(position, Decision::Carrier);
                } else {
                    if let Some(call) = call_id {
                        fold.unstated_calls.insert(call, commitment);
                    }
                    if let Some(commitment) = commitment {
                        *fold.owed.entry(commitment).or_insert(0) += 1;
                    }
                }
                fold.settle(commitment, false);
                continue;
            }
            if let Some(call) = call_id {
                fold.release_unstated(call);
                fold.stated_calls.insert(call);
            }
            if let Some(commitment) = commitment {
                fold.seen_exits
                    .entry(commitment)
                    .or_default()
                    .insert(exit_fact);
            }
            if joined.is_none() {
                if let Some(commitment) = commitment {
                    joined = fold.pending.iter().position(|item| {
                        item.commitment == Some(commitment) && item.exit_status == exit_fact
                    });
                }
            }
            if let Some(position) = joined {
                fold.decide(position, Decision::Copy);
            } else if let Some(commitment) = commitment {
                fold.stated_exits
                    .entry(commitment)
                    .or_default()
                    .push(exit_fact);
            }
            fold.settle(commitment, false);
            continue;
        }
        if row.source != RowSource::CodexSessionStream || !row.hooked_item {
            continue;
        }
        let identity = row.source_identity;
        if fold.decided.contains_key(identity)
            || fold
                .pending
                .iter()
                .any(|item| item.source_identity == identity)
        {
            continue;
        }
        if let Some(call) = call_id.filter(|call| fold.unstated_calls.contains_key(call)) {
            fold.record(row_index, identity, Decision::Carrier);
            fold.release_unstated(call);
        } else if call_id.is_some_and(|call| fold.stated_calls.contains(call)) {
            fold.record(row_index, identity, Decision::Copy);
        } else if let Some(position) = commitment
            .and_then(|commitment| fold.stated_exits.get(commitment))
            .and_then(|exits| exits.iter().position(|exit| *exit == exit_fact))
        {
            // `commitment` is `Some` here: the position came from its exit list.
            if let Some(exits) =
                commitment.and_then(|commitment| fold.stated_exits.get_mut(commitment))
            {
                exits.remove(position);
            }
            fold.record(row_index, identity, Decision::Copy);
        } else {
            fold.pending.push(PendingItem {
                row: row_index,
                source_identity: identity,
                call_id,
                commitment,
                exit_status: exit_fact,
            });
            fold.settle(commitment, false);
        }
    }
    let remaining: Vec<(usize, &'a str)> = fold
        .pending
        .iter()
        .map(|item| (item.row, item.source_identity))
        .collect();
    for (row, identity) in remaining {
        fold.record(row, identity, Decision::Pending);
    }
    fold.decisions
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filename_slots() {
        assert!(rollout_filename_matches(
            "rollout-2026-07-23T12-00-00-root.jsonl",
            "root"
        ));
        assert!(rollout_filename_matches(
            "rollout-2026-07-23T12-00-00-root_child.JSONL",
            "root"
        ));
        assert!(!rollout_filename_matches(
            "rollout-2026-07-23T12-00-00-root-child.jsonl",
            "root"
        ));
        assert!(rollout_filename_matches("root.jsonl.zst", "root"));
        assert_eq!(posix_name("a/b/./"), "b");
        assert_eq!(posix_name("/"), "");
    }

    #[test]
    fn oversized_partial_round_trips() {
        let digest = "ab".repeat(32);
        let encoded =
            encode_oversized_partial(7, &format!("hmac-sha256:{digest}"), "s", 2, "i", b"key")
                .unwrap();
        assert_eq!(
            decode_oversized_partial(&encoded, "s", 2, "i", b"key"),
            OversizedPartial::State(7, digest.clone())
        );
        assert_eq!(
            decode_oversized_partial(&encoded, "s", 3, "i", b"key"),
            OversizedPartial::Invalid
        );
        assert_eq!(
            decode_oversized_partial(b"plain", "s", 2, "i", b"key"),
            OversizedPartial::NotOversized
        );
    }

    #[test]
    fn stated_post_then_item_is_copy() {
        let post = RolloutRow {
            source: RowSource::CodexHook,
            event_kind: "PostToolUse",
            source_identity: "h1",
            call_id: Some("c1"),
            commitment: Some("cmd"),
            exit_fact: Some(0),
            stated: true,
            hooked_item: false,
        };
        let item = RolloutRow {
            source: RowSource::CodexSessionStream,
            event_kind: "item_completed",
            source_identity: "s1",
            call_id: Some("exec-1"),
            commitment: Some("cmd"),
            exit_fact: Some(0),
            stated: true,
            hooked_item: true,
        };
        assert_eq!(
            rollout_item_decisions(&[post, item], &[]),
            vec![(1, Decision::Copy)]
        );
    }
}
