//! The hook pass-timing aggregate, twin of `yoetz.cli.hook_timing`'s `json.loads` +
//! `_valid_document` read and `_updated_document` fold.
//!
//! The document is decoded with the `json.loads`-compatible scanner ([`json_compat`]), validated
//! exactly as `_valid_document` does, folded, and re-encoded as
//! `json.dumps(document, separators=(",", ":"), sort_keys=True)`. Decoding is accept-only: a
//! document the compatible scanner cannot decide (duplicate keys, non-finite constants, deep
//! nesting) is reported as [`Fold::Defer`] so the caller runs the Python reference.

use std::borrow::Cow;

use crate::protocol::json_compat::{self, CompatLimits, CompatValue};

pub const FORMAT: &str = "yoetz.hook-pass-timing/1";
pub const BUCKET_UPPER_BOUNDS_MS: [i64; 27] = [
    5, 10, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 600, 700, 800, 900, 1_000, 1_250,
    1_500, 2_000, 2_500, 3_000, 4_000, 5_000, 7_500, 10_000,
];
pub const BUCKETS: usize = BUCKET_UPPER_BOUNDS_MS.len() + 1;
const MAX_MS: i64 = 3_600_000;
const MAX_SAFE: i64 = (1_i64 << 53) - 1;
const HOUR_MS: i64 = 3_600_000;
const MAX_EPOCH_MS: i64 = 253_402_300_799_999;
pub const HOSTS: [&str; 3] = ["codex", "claude", "cursor"];
pub const EVENTS: [&str; 22] = [
    "PermissionDenied",
    "PermissionRequest",
    "PostCompact",
    "PostToolUse",
    "PostToolUseFailure",
    "PreCompact",
    "PreToolUse",
    "SessionEnd",
    "SessionStart",
    "Stop",
    "StopFailure",
    "SubagentStart",
    "SubagentStop",
    "UserPromptSubmit",
    "afterFileEdit",
    "afterMCPExecution",
    "postToolUse",
    "postToolUseFailure",
    "preToolUse",
    "sessionEnd",
    "sessionStart",
    "stop",
];
pub const UNKNOWN_EVENT: &str = "unknown_event";
pub const PATHS: [&str; 5] = [
    "observe",
    "sync_fallback_spool",
    "structural",
    "ordinary",
    "invalid_profile",
];
pub const OUTCOMES: [&str; 4] = ["ingested", "followup_deferred", "not_ingested", "failed"];

/// A validated histogram (`all`, or one hour slot when `hour` is set).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Histogram {
    pub hour: Option<i64>,
    pub count: i64,
    pub sum_ms: i64,
    /// `(max_ms, max_at_ms)`, absent exactly when `count` is zero.
    pub max: Option<(i64, i64)>,
    pub buckets: [i64; BUCKETS],
}

impl Histogram {
    fn empty(hour: Option<i64>) -> Self {
        Histogram {
            hour,
            count: 0,
            sum_ms: 0,
            max: None,
            buckets: [0; BUCKETS],
        }
    }

    fn observe(&mut self, ms: i64, at_ms: i64) {
        self.buckets[bucket(ms)] += 1;
        self.count = bounded(self.count + 1);
        self.sum_ms = bounded(self.sum_ms + ms);
        if self.max.is_none_or(|(maximum, _)| ms > maximum) {
            self.max = Some((ms, at_ms));
        }
    }
}

/// A validated entry; `outcomes` keeps the document's own members (sorted on output).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub host: String,
    pub event: String,
    pub path: String,
    pub first_ms: i64,
    pub last_ms: i64,
    pub outcomes: Vec<(String, i64)>,
    pub all: Histogram,
    pub slots: Vec<Histogram>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Document {
    pub since_ms: i64,
    pub evicted_entry_count: i64,
    pub entries: Vec<Entry>,
}

#[inline]
fn bounded(value: i64) -> i64 {
    value.clamp(0, MAX_SAFE)
}

/// `_bucket(ms)`.
pub fn bucket(ms: i64) -> usize {
    BUCKET_UPPER_BOUNDS_MS
        .iter()
        .position(|bound| ms <= *bound)
        .unwrap_or(BUCKETS - 1)
}

pub type Value<'a> = CompatValue<'a, ()>;
type Members<'a> = [(Cow<'a, str>, Value<'a>)];

fn member<'v, 'a>(members: &'v Members<'a>, name: &str) -> &'v Value<'a> {
    // Callers check the exact key set first.
    &members
        .iter()
        .find(|(key, _)| key == name)
        .expect("validated key set")
        .1
}

fn has_keys(members: &Members<'_>, keys: &[&str]) -> bool {
    // ``frozenset(mapping) == keys``; the decoder already refused duplicate keys.
    members.len() == keys.len() && members.iter().all(|(key, _)| keys.contains(&key.as_ref()))
}

fn count_of(value: &Value<'_>) -> Option<i64> {
    match value {
        CompatValue::Int(number) if (0..=MAX_SAFE).contains(number) => Some(*number),
        _ => None,
    }
}

fn moment_of(value: &Value<'_>) -> Option<i64> {
    match value {
        CompatValue::Int(number) if (0..=MAX_EPOCH_MS).contains(number) => Some(*number),
        _ => None,
    }
}

const HISTOGRAM_KEYS: [&str; 5] = ["count", "sum_ms", "max_ms", "max_at_ms", "buckets"];
const SLOT_KEYS: [&str; 6] = ["count", "sum_ms", "max_ms", "max_at_ms", "buckets", "hour"];
const ENTRY_KEYS: [&str; 8] = [
    "host", "event", "path", "first_ms", "last_ms", "outcomes", "all", "slots",
];
const DOCUMENT_KEYS: [&str; 4] = ["format", "since_ms", "evicted_entry_count", "entries"];

fn valid_histogram(raw: &Value<'_>, slot: bool) -> Option<Histogram> {
    let CompatValue::Object(members) = raw else {
        return None;
    };
    if !has_keys(members, if slot { &SLOT_KEYS } else { &HISTOGRAM_KEYS }) {
        return None;
    }
    let count = count_of(member(members, "count"))?;
    let sum_ms = count_of(member(members, "sum_ms"))?;
    let CompatValue::Array(items) = member(members, "buckets") else {
        return None;
    };
    if items.len() != BUCKETS {
        return None;
    }
    let mut buckets = [0_i64; BUCKETS];
    for (slot_value, item) in buckets.iter_mut().zip(items) {
        *slot_value = count_of(item)?;
    }
    if buckets.iter().sum::<i64>() != count {
        return None;
    }
    let maximum = member(members, "max_ms");
    let moment = member(members, "max_at_ms");
    let max = if count == 0 {
        if !matches!(maximum, CompatValue::Null) || !matches!(moment, CompatValue::Null) {
            return None;
        }
        None
    } else {
        let CompatValue::Int(maximum) = maximum else {
            return None;
        };
        if !(0..=MAX_MS).contains(maximum) {
            return None;
        }
        let moment = moment_of(moment)?;
        let highest = buckets.iter().rposition(|item| *item != 0)?;
        if bucket(*maximum) != highest {
            return None;
        }
        Some((*maximum, moment))
    };
    let hour = if slot {
        let hour = count_of(member(members, "hour"))?;
        if hour > MAX_EPOCH_MS / HOUR_MS {
            return None;
        }
        Some(hour)
    } else {
        None
    };
    Some(Histogram {
        hour,
        count,
        sum_ms,
        max,
        buckets,
    })
}

fn text_of<'v>(value: &'v Value<'_>) -> Option<&'v str> {
    match value {
        CompatValue::Str(text) => Some(text),
        _ => None,
    }
}

fn valid_entry(raw: &Value<'_>) -> Option<Entry> {
    let CompatValue::Object(members) = raw else {
        return None;
    };
    if !has_keys(members, &ENTRY_KEYS) {
        return None;
    }
    let host = text_of(member(members, "host"))?;
    let event = text_of(member(members, "event"))?;
    let path = text_of(member(members, "path"))?;
    if !HOSTS.contains(&host)
        || (!EVENTS.contains(&event) && event != UNKNOWN_EVENT)
        || !PATHS.contains(&path)
    {
        return None;
    }
    let first_ms = moment_of(member(members, "first_ms"))?;
    let last_ms = moment_of(member(members, "last_ms"))?;
    let all = valid_histogram(member(members, "all"), false)?;
    let CompatValue::Object(outcome_members) = member(members, "outcomes") else {
        return None;
    };
    let mut outcomes = Vec::with_capacity(outcome_members.len());
    let mut outcome_total: i64 = 0;
    for (name, item) in outcome_members.iter() {
        if !OUTCOMES.contains(&name.as_ref()) {
            return None;
        }
        let count = count_of(item)?;
        outcome_total += count;
        outcomes.push((name.to_string(), count));
    }
    if outcome_total != all.count {
        return None;
    }
    let CompatValue::Array(slot_items) = member(members, "slots") else {
        return None;
    };
    if slot_items.len() > 2 {
        return None;
    }
    let mut slots = Vec::with_capacity(slot_items.len());
    for item in slot_items {
        slots.push(valid_histogram(item, true)?);
    }
    if slots.len() == 2 && slots[0].hour == slots[1].hour {
        return None;
    }
    Some(Entry {
        host: host.to_owned(),
        event: event.to_owned(),
        path: path.to_owned(),
        first_ms,
        last_ms,
        outcomes,
        all,
        slots,
    })
}

/// `_valid_document(json.loads(raw))` for a decoded value.
fn valid_document(raw: &Value<'_>, max_entries: usize) -> Option<Document> {
    let CompatValue::Object(members) = raw else {
        return None;
    };
    if !has_keys(members, &DOCUMENT_KEYS) || text_of(member(members, "format"))? != FORMAT {
        return None;
    }
    let since_ms = moment_of(member(members, "since_ms"))?;
    let evicted_entry_count = count_of(member(members, "evicted_entry_count"))?;
    let CompatValue::Array(items) = member(members, "entries") else {
        return None;
    };
    if items.len() > max_entries {
        return None;
    }
    let mut entries: Vec<Entry> = Vec::with_capacity(items.len() + 1);
    for item in items {
        let entry = valid_entry(item)?;
        if entries.iter().any(|prior| {
            prior.host == entry.host && prior.event == entry.event && prior.path == entry.path
        }) {
            return None;
        }
        entries.push(entry);
    }
    Some(Document {
        since_ms,
        evicted_entry_count,
        entries,
    })
}

/// What decoding a stored document decided.
#[derive(Debug, PartialEq, Eq)]
pub enum Read {
    Valid(Document),
    /// `_read_descriptor` returns `None`: the aggregate restarts.
    Invalid,
    /// The Python reference must decode it.
    Defer,
}

/// Decode stored bytes as `json.loads(bytes)` would, or `None` when the reference must.
pub fn decode(raw: &[u8]) -> Option<Value<'_>> {
    // ``json.loads(bytes)`` detects UTF-16/32 from NUL bytes and BOMs and decodes with
    // ``surrogatepass``; only plain strict UTF-8 is decided here.
    let text = json_compat::precheck_line(raw)?;
    let limits = CompatLimits {
        max_value_depth: 64,
        max_container_depth: 64,
        allow_overflow: true,
    };
    // A long integer never validates (every count is a safe integer), so its value is unused.
    json_compat::accept(text, limits, |_| Some(()))
}

/// `_valid_document(value)` for a decoded value.
pub fn validate(value: &Value<'_>, max_entries: usize) -> Option<Document> {
    valid_document(value, max_entries)
}

/// `json.loads(raw)` + `_valid_document` for the bytes `_read_descriptor` read.
pub fn read_document(raw: &[u8], max_entries: usize) -> Read {
    let Some(value) = decode(raw) else {
        return Read::Defer;
    };
    match valid_document(&value, max_entries) {
        Some(document) => Read::Valid(document),
        None => Read::Invalid,
    }
}

/// One sample to fold.
pub struct Sample<'s> {
    pub host: &'s str,
    pub event: &'s str,
    pub path: &'s str,
    pub outcome: &'s str,
    pub ms: i64,
    pub now_ms: i64,
}

/// `_updated_document(document, ...)`'s fold. `max_entries` must be positive.
pub fn fold(
    document: Option<Document>,
    sample: &Sample<'_>,
    max_entries: usize,
) -> (Document, bool) {
    let now_ms = sample.now_ms;
    let hour = now_ms.div_euclid(HOUR_MS);
    let restarted = document.is_none();
    let mut document = document.unwrap_or(Document {
        since_ms: now_ms,
        evicted_entry_count: 0,
        entries: Vec::new(),
    });
    let position = document.entries.iter().position(|item| {
        item.host == sample.host && item.event == sample.event && item.path == sample.path
    });
    let index = match position {
        Some(index) => index,
        None => {
            if document.entries.len() >= max_entries {
                // ``min`` keeps the first of equal keys; ``list.remove`` removes that one.
                let mut stalest = 0;
                for (index, item) in document.entries.iter().enumerate() {
                    if item.last_ms < document.entries[stalest].last_ms {
                        stalest = index;
                    }
                }
                document.entries.remove(stalest);
                document.evicted_entry_count = bounded(document.evicted_entry_count + 1);
            }
            document.entries.push(Entry {
                host: sample.host.to_owned(),
                event: sample.event.to_owned(),
                path: sample.path.to_owned(),
                first_ms: now_ms,
                last_ms: now_ms,
                outcomes: Vec::new(),
                all: Histogram::empty(None),
                slots: Vec::new(),
            });
            document.entries.len() - 1
        }
    };
    let entry = &mut document.entries[index];
    entry.first_ms = entry.first_ms.min(now_ms);
    entry.last_ms = entry.last_ms.max(now_ms);
    match entry
        .outcomes
        .iter_mut()
        .find(|(name, _)| name == sample.outcome)
    {
        Some((_, count)) => *count = bounded(*count + 1),
        None => entry.outcomes.push((sample.outcome.to_owned(), 1)),
    }
    entry.all.observe(sample.ms, now_ms);
    let mut slots: Vec<Histogram> = std::mem::take(&mut entry.slots)
        .into_iter()
        .filter(|slot| {
            slot.hour
                .is_some_and(|slot_hour| hour - 1 <= slot_hour && slot_hour <= hour)
        })
        .collect();
    match slots.iter_mut().find(|slot| slot.hour == Some(hour)) {
        Some(current) => current.observe(sample.ms, now_ms),
        None => {
            let mut current = Histogram::empty(Some(hour));
            current.observe(sample.ms, now_ms);
            slots.push(current);
        }
    }
    slots.sort_by_key(|slot| slot.hour);
    entry.slots = slots;
    (document, restarted)
}

/// `json.dumps` string encoding with `ensure_ascii=True`.
fn push_json_string(out: &mut String, text: &str) {
    out.push('"');
    for character in text.chars() {
        match character {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            ' '..='~' => out.push(character),
            _ => {
                let mut units = [0_u16; 2];
                for unit in character.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
}

fn push_int(out: &mut String, value: i64) {
    out.push_str(itoa::Buffer::new().format(value));
}

fn push_histogram(out: &mut String, histogram: &Histogram) {
    // Keys in sorted order: buckets, count, [hour,] max_at_ms, max_ms, sum_ms.
    out.push_str("{\"buckets\":[");
    for (index, item) in histogram.buckets.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        push_int(out, *item);
    }
    out.push_str("],\"count\":");
    push_int(out, histogram.count);
    if let Some(hour) = histogram.hour {
        out.push_str(",\"hour\":");
        push_int(out, hour);
    }
    match histogram.max {
        Some((maximum, at_ms)) => {
            out.push_str(",\"max_at_ms\":");
            push_int(out, at_ms);
            out.push_str(",\"max_ms\":");
            push_int(out, maximum);
        }
        None => out.push_str(",\"max_at_ms\":null,\"max_ms\":null"),
    }
    out.push_str(",\"sum_ms\":");
    push_int(out, histogram.sum_ms);
    out.push('}');
}

/// `json.dumps(document, separators=(",", ":"), sort_keys=True).encode()`.
pub fn encode(document: &Document) -> String {
    let mut out = String::with_capacity(512 + document.entries.len() * 700);
    out.push_str("{\"entries\":[");
    for (index, entry) in document.entries.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        // Sorted keys: all, event, first_ms, host, last_ms, outcomes, path, slots.
        out.push_str("{\"all\":");
        push_histogram(&mut out, &entry.all);
        out.push_str(",\"event\":");
        push_json_string(&mut out, &entry.event);
        out.push_str(",\"first_ms\":");
        push_int(&mut out, entry.first_ms);
        out.push_str(",\"host\":");
        push_json_string(&mut out, &entry.host);
        out.push_str(",\"last_ms\":");
        push_int(&mut out, entry.last_ms);
        out.push_str(",\"outcomes\":{");
        let mut outcomes: Vec<&(String, i64)> = entry.outcomes.iter().collect();
        outcomes.sort_by(|left, right| left.0.chars().cmp(right.0.chars()));
        for (position, (name, count)) in outcomes.into_iter().enumerate() {
            if position > 0 {
                out.push(',');
            }
            push_json_string(&mut out, name);
            out.push(':');
            push_int(&mut out, *count);
        }
        out.push_str("},\"path\":");
        push_json_string(&mut out, &entry.path);
        out.push_str(",\"slots\":[");
        for (position, slot) in entry.slots.iter().enumerate() {
            if position > 0 {
                out.push(',');
            }
            push_histogram(&mut out, slot);
        }
        out.push_str("]}");
    }
    out.push_str("],\"evicted_entry_count\":");
    push_int(&mut out, document.evicted_entry_count);
    out.push_str(",\"format\":");
    push_json_string(&mut out, FORMAT);
    out.push_str(",\"since_ms\":");
    push_int(&mut out, document.since_ms);
    out.push('}');
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(now_ms: i64, ms: i64) -> Sample<'static> {
        Sample {
            host: "codex",
            event: "Stop",
            path: "observe",
            outcome: "ingested",
            ms,
            now_ms,
        }
    }

    #[test]
    fn fold_round_trips_through_validation() {
        let (document, restarted) = fold(None, &sample(7_200_000, 120), 48);
        assert!(restarted);
        let encoded = encode(&document);
        let Read::Valid(read) = read_document(encoded.as_bytes(), 48) else {
            panic!("{encoded}")
        };
        assert_eq!(read, document);
        let (document, restarted) = fold(Some(read), &sample(10_800_000, 20_000), 48);
        assert!(!restarted);
        let entry = &document.entries[0];
        assert_eq!(entry.all.count, 2);
        assert_eq!(entry.all.max, Some((20_000, 10_800_000)));
        assert_eq!(
            entry.slots.iter().map(|slot| slot.hour).collect::<Vec<_>>(),
            [Some(2), Some(3)]
        );
        assert!(matches!(
            read_document(encode(&document).as_bytes(), 48),
            Read::Valid(_)
        ));
        assert_eq!(
            read_document(encode(&document).as_bytes(), 0),
            Read::Invalid
        );
    }

    #[test]
    fn undecidable_bytes_defer() {
        assert_eq!(read_document(b"{\"a\":1,\"a\":2}", 48), Read::Defer);
        assert_eq!(read_document(b"\xef\xbb\xbf{}", 48), Read::Defer);
        assert_eq!(read_document(b"{\"format\":NaN}", 48), Read::Defer);
        assert_eq!(read_document(b"[]", 48), Read::Invalid);
    }

    #[test]
    fn strings_escape_like_json_dumps() {
        let mut out = String::new();
        push_json_string(&mut out, "a\"\\\u{7f}\u{e9}\u{1f600}\u{1}");
        assert_eq!(out, "\"a\\\"\\\\\\u007f\\u00e9\\ud83d\\ude00\\u0001\"");
    }
}
