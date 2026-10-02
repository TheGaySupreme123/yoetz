//! Pure twins of the hot paths in `yoetz.application.semantic_case`.
//!
//! The Python module stays the authority. These functions work on one [`Node`] tree whose strings
//! borrow from their source (the caller's Python strings or the parsed bytes), so the envelope
//! bounding ladder, the review-packet projection and prose clipping never re-walk Python objects.
//! Sizes come from [`encoded_len`], which is exact for a validated tree and independent of member
//! order, and the ladder caches them per catalog row and per packet member; bytes are produced
//! once with the canonical encoding rules.
//!
//! A [`Node::Tuple`] encodes like an array but is not a `list` to the reference's
//! `type(x) is list` checks, so the ladder treats it exactly as Python does.
//!
//! Every function returns [`Step::Defer`] instead of guessing when the reference would take a path
//! this twin does not reproduce (a `TypeError` from hashing an unhashable member, a
//! `UnicodeEncodeError` from an ASCII sort key, an encoder refusal): the caller then runs the
//! Python reference, which raises exactly what it always raised.

use std::borrow::Cow;
use std::collections::{BTreeSet, HashSet};

use crate::protocol::canonical::{self, MAX_JSON_DEPTH, MAX_SAFE_INTEGER, Reason};
use crate::protocol::json::{self, JsonSink, JsonText};

/// `_PACKET_SCHEMA`.
pub const PACKET_SCHEMA: &str = "yoetz.review-packet-case/2";
/// `REVIEW_PACKET_ITEM_ID`.
pub const REVIEW_PACKET_ITEM_ID: &str = "review-packet";
/// `SEMANTIC_PRIOR_FINDINGS_OVER_LIMIT_GAP`.
pub const PRIOR_FINDINGS_OVER_LIMIT_GAP: &str = "semantic_prior_findings_over_limit";
/// `_PACKET_ID_LIST_KEYS`, in the reference's order.
pub const PACKET_ID_LIST_KEYS: [&str; 7] = [
    "task_statement_item_ids",
    "goal_item_ids",
    "obligation_item_ids",
    "claim_item_ids",
    "decision_item_ids",
    "prior_finding_item_ids",
    "timeline_item_ids",
];
/// `_SECTION_LABELS`, as `(section, label)` pairs.
pub const SECTION_LABELS: [(&str, &str); 2] = [
    (
        "task_statement",
        "task statement: what the user asked for; the source field says who supplied the text",
    ),
    ("goal", "agent plan (the agent's own summary)"),
];
/// `_ELISION_MARKER` split around its two fields.
pub const ELISION_MARKER_PARTS: [&str; 3] = [
    "\n[yoetz: ",
    " of ",
    " bytes elided here; head and tail kept]\n",
];
/// `_MIN_HEAD_TAIL_SIDE_BYTES`.
pub const MIN_HEAD_TAIL_SIDE_BYTES: i64 = 64;
/// `_MIN_CLIPPABLE_PROSE_BYTES`.
pub const MIN_CLIPPABLE_PROSE_BYTES: usize = 256;

const ACCOUNTING_KEY: &str = "selection_accounting";
const CATALOG_KEY: &str = "item_catalog";
const PACKET_KEY: &str = "review_packet";

/// The outcome of a twin: its result, or "run the Python reference instead".
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Step<T> {
    Done(T),
    Defer,
}

macro_rules! done {
    ($expr:expr) => {
        match $expr {
            Step::Done(value) => value,
            Step::Defer => return Step::Defer,
        }
    };
}

// ---------------------------------------------------------------------------------------------
// The tree
// ---------------------------------------------------------------------------------------------

/// A JSON-profile value whose strings may borrow from their source.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Node<'a> {
    Null,
    Bool(bool),
    Int(i64),
    Str(Cow<'a, str>),
    /// An exact `list` (or a parsed JSON array).
    Array(Vec<Node<'a>>),
    /// An exact `tuple`: encoded as an array, but never a `list` to the reference.
    Tuple(Vec<Node<'a>>),
    /// Members in insertion order; encoding sorts them.
    Object(Vec<(Cow<'a, str>, Node<'a>)>),
}

/// Byte classes that cost more than one encoded byte: `"`, `\` and C0 controls.
static EXTRA: [u8; 256] = {
    let mut table = [0u8; 256];
    let mut index = 0;
    while index < 0x20 {
        table[index] = 5;
        index += 1;
    }
    table[0x08] = 1;
    table[0x09] = 1;
    table[0x0A] = 1;
    table[0x0C] = 1;
    table[0x0D] = 1;
    table[b'"' as usize] = 1;
    table[b'\\' as usize] = 1;
    table
};

/// Canonical length of a string literal (quotes included).
#[inline]
pub fn encoded_str_len(text: &str) -> usize {
    let extra: usize = text
        .as_bytes()
        .iter()
        .map(|byte| usize::from(EXTRA[*byte as usize]))
        .sum();
    text.len() + 2 + extra
}

#[inline]
fn int_len(value: i64) -> usize {
    let mut buffer = itoa::Buffer::new();
    buffer.format(value).len()
}

/// Canonical encoded length of a validated tree. Member order does not change it.
pub fn encoded_len(node: &Node<'_>) -> usize {
    match node {
        Node::Null | Node::Bool(true) => 4,
        Node::Bool(false) => 5,
        Node::Int(number) => int_len(*number),
        Node::Str(text) => encoded_str_len(text),
        Node::Array(items) | Node::Tuple(items) => {
            2 + items.iter().map(encoded_len).sum::<usize>() + items.len().saturating_sub(1)
        }
        Node::Object(members) => {
            2 + members
                .iter()
                .map(|(key, item)| encoded_str_len(key) + 1 + encoded_len(item))
                .sum::<usize>()
                + members.len().saturating_sub(1)
        }
    }
}

/// [`encoded_len`] that also applies the encoder's refusals (depth, NUL, integer range).
pub fn checked_len(node: &Node<'_>, depth: usize) -> Option<usize> {
    Some(match node {
        Node::Null | Node::Bool(true) => 4,
        Node::Bool(false) => 5,
        Node::Int(number) => {
            canonical::check_safe_integer(*number).ok()?;
            int_len(*number)
        }
        Node::Str(text) => {
            canonical::validate_str(text).ok()?;
            encoded_str_len(text)
        }
        Node::Array(items) | Node::Tuple(items) => {
            if depth >= MAX_JSON_DEPTH {
                return None;
            }
            let mut total = 2 + items.len().saturating_sub(1);
            for item in items {
                total += checked_len(item, depth + 1)?;
            }
            total
        }
        Node::Object(members) => {
            if depth >= MAX_JSON_DEPTH {
                return None;
            }
            let mut total = 2 + members.len().saturating_sub(1);
            for (key, item) in members {
                canonical::validate_str(key).ok()?;
                total += encoded_str_len(key) + 1 + checked_len(item, depth + 1)?;
            }
            total
        }
    })
}

fn encode_into(out: &mut Vec<u8>, node: &Node<'_>, depth: usize) -> Result<(), Reason> {
    match node {
        Node::Null => out.extend_from_slice(b"null"),
        Node::Bool(true) => out.extend_from_slice(b"true"),
        Node::Bool(false) => out.extend_from_slice(b"false"),
        Node::Int(number) => {
            canonical::check_safe_integer(*number)?;
            canonical::push_int(out, *number);
        }
        Node::Str(text) => canonical::encode_str_into(out, text)?,
        Node::Array(items) | Node::Tuple(items) => {
            if depth >= MAX_JSON_DEPTH {
                return Err(canonical::NESTING_TOO_DEEP);
            }
            out.push(b'[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(b',');
                }
                encode_into(out, item, depth + 1)?;
            }
            out.push(b']');
        }
        Node::Object(members) => {
            if depth >= MAX_JSON_DEPTH {
                return Err(canonical::NESTING_TOO_DEEP);
            }
            for (key, _) in members {
                canonical::validate_str(key)?;
            }
            let mut order: Vec<&(Cow<'_, str>, Node<'_>)> = members.iter().collect();
            order.sort_by(|left, right| canonical::utf16_cmp(&left.0, &right.0));
            out.push(b'{');
            for (index, (key, item)) in order.into_iter().enumerate() {
                if index > 0 {
                    out.push(b',');
                }
                canonical::encode_str_into(out, key)?;
                out.push(b':');
                encode_into(out, item, depth + 1)?;
            }
            out.push(b'}');
        }
    }
    Ok(())
}

/// The canonical bytes of `node` (the reference's `canonical_encode`).
pub fn encode_node(node: &Node<'_>) -> Result<Vec<u8>, Reason> {
    let mut out = Vec::with_capacity(encoded_len(node));
    encode_into(&mut out, node, 0)?;
    Ok(out)
}

fn encode(node: &Node<'_>) -> Step<Vec<u8>> {
    match encode_node(node) {
        Ok(bytes) => Step::Done(bytes),
        Err(_) => Step::Defer,
    }
}

/// Turn every tuple into an array: what a canonical encode/parse round trip does.
pub fn normalize_tuples(node: &mut Node<'_>) {
    match node {
        Node::Tuple(items) => {
            let mut items = std::mem::take(items);
            items.iter_mut().for_each(normalize_tuples);
            *node = Node::Array(items);
        }
        Node::Array(items) => items.iter_mut().for_each(normalize_tuples),
        Node::Object(members) => members
            .iter_mut()
            .for_each(|(_, item)| normalize_tuples(item)),
        _ => {}
    }
}

/// Builds [`Node`] trees whose unescaped strings borrow from the scanned text.
struct NodeSink<'a> {
    source: &'a str,
}

impl<'a> NodeSink<'a> {
    fn text(&self, text: JsonText<'_>) -> Result<Cow<'a, str>, Reason> {
        match text {
            JsonText::Borrowed(slice) => {
                // The scanner hands out slices of `source`; re-slice it to keep its lifetime.
                let base = self.source.as_ptr() as usize;
                let at = slice.as_ptr() as usize;
                let borrowed = at
                    .checked_sub(base)
                    .and_then(|start| self.source.get(start..start + slice.len()));
                Ok(match borrowed {
                    Some(borrowed) => Cow::Borrowed(borrowed),
                    None => Cow::Owned(slice.to_owned()),
                })
            }
            JsonText::Owned(owned) => Ok(Cow::Owned(owned)),
            JsonText::Wide(_) => Err(canonical::LONE_SURROGATE),
        }
    }
}

impl<'a> JsonSink for NodeSink<'a> {
    type Value = Node<'a>;
    type Key = Cow<'a, str>;
    type Array = Vec<Node<'a>>;
    type Object = Vec<(Cow<'a, str>, Node<'a>)>;
    type Error = Reason;

    fn fail(&mut self, reason: Reason) -> Reason {
        reason
    }
    fn null(&mut self) -> Result<Node<'a>, Reason> {
        Ok(Node::Null)
    }
    fn boolean(&mut self, value: bool) -> Result<Node<'a>, Reason> {
        Ok(Node::Bool(value))
    }
    fn integer(&mut self, literal: &str) -> Result<Node<'a>, Reason> {
        if literal == "-0" {
            return Err(canonical::FLOAT_FORBIDDEN);
        }
        let parsed: i64 = literal
            .parse()
            .map_err(|_| canonical::INTEGER_OUT_OF_SAFE_RANGE)?;
        if !(-MAX_SAFE_INTEGER..=MAX_SAFE_INTEGER).contains(&parsed) {
            return Err(canonical::INTEGER_OUT_OF_SAFE_RANGE);
        }
        Ok(Node::Int(parsed))
    }
    fn float(&mut self, _literal: &str) -> Result<Node<'a>, Reason> {
        Err(canonical::FLOAT_FORBIDDEN)
    }
    fn string(&mut self, text: JsonText<'_>) -> Result<Node<'a>, Reason> {
        Ok(Node::Str(self.text(text)?))
    }
    fn key(&mut self, text: JsonText<'_>) -> Result<Cow<'a, str>, Reason> {
        self.text(text)
    }
    fn begin_array(&mut self) -> Result<Vec<Node<'a>>, Reason> {
        Ok(Vec::new())
    }
    fn push(&mut self, array: &mut Vec<Node<'a>>, value: Node<'a>) -> Result<(), Reason> {
        array.push(value);
        Ok(())
    }
    fn end_array(&mut self, array: Vec<Node<'a>>) -> Result<Node<'a>, Reason> {
        Ok(Node::Array(array))
    }
    fn begin_object(&mut self) -> Result<Self::Object, Reason> {
        Ok(Vec::new())
    }
    fn insert(
        &mut self,
        object: &mut Self::Object,
        key: Cow<'a, str>,
        value: Node<'a>,
    ) -> Result<(), Reason> {
        object.push((key, value));
        Ok(())
    }
    fn end_object(&mut self, object: Self::Object) -> Result<Node<'a>, Reason> {
        if object.len() > 1 {
            let mut keys: Vec<&str> = object.iter().map(|(key, _)| key.as_ref()).collect();
            keys.sort_unstable();
            if keys.windows(2).any(|pair| pair[0] == pair[1]) {
                return Err(canonical::DUPLICATE_OBJECT_KEY);
            }
        }
        Ok(Node::Object(object))
    }
}

/// `strict_json_parse(raw)` into a validated [`Node`] and its encoded length.
pub fn parse_node(raw: &[u8]) -> Result<(Node<'_>, usize), Reason> {
    let text = json::precheck(raw)?;
    let node = json::scan(text, &mut NodeSink { source: text })?;
    // The profile walk: nesting bound and decoded NUL characters.
    match checked_len(&node, 0) {
        Some(length) => Ok((node, length)),
        None => Err(encode_node(&node)
            .err()
            .unwrap_or(canonical::NESTING_TOO_DEEP)),
    }
}

// ---------------------------------------------------------------------------------------------
// Tree helpers
// ---------------------------------------------------------------------------------------------

fn get<'n, 'a>(node: &'n Node<'a>, key: &str) -> Option<&'n Node<'a>> {
    match node {
        Node::Object(members) => members.iter().find(|(name, _)| name == key).map(|(_, v)| v),
        _ => None,
    }
}

fn get_mut<'n, 'a>(node: &'n mut Node<'a>, key: &str) -> Option<&'n mut Node<'a>> {
    match node {
        Node::Object(members) => members
            .iter_mut()
            .find(|(name, _)| name == key)
            .map(|(_, v)| v),
        _ => None,
    }
}

/// `mapping[key] = item` (replace in place, or append like a dict insertion).
fn set<'a>(node: &mut Node<'a>, key: &'static str, item: Node<'a>) {
    if let Node::Object(members) = node {
        if let Some(slot) = members.iter_mut().find(|(name, _)| name == key) {
            slot.1 = item;
        } else {
            members.push((Cow::Borrowed(key), item));
        }
    }
}

/// `mapping.pop(key, None)`.
fn pop<'a>(node: &mut Node<'a>, key: &str) -> Option<Node<'a>> {
    match node {
        Node::Object(members) => {
            let index = members.iter().position(|(name, _)| name == key)?;
            Some(members.remove(index).1)
        }
        _ => None,
    }
}

fn str_of<'n>(node: Option<&'n Node<'_>>) -> Option<&'n str> {
    match node {
        Some(Node::Str(text)) => Some(text),
        _ => None,
    }
}

fn is_str(node: Option<&Node<'_>>, expected: &str) -> bool {
    str_of(node) == Some(expected)
}

fn is_object(node: &Node<'_>) -> bool {
    matches!(node, Node::Object(_))
}

fn object<'a>(members: Vec<(&'static str, Node<'a>)>) -> Node<'a> {
    Node::Object(
        members
            .into_iter()
            .map(|(key, item)| (Cow::Borrowed(key), item))
            .collect(),
    )
}

fn owned<'a>(text: impl Into<String>) -> Node<'a> {
    Node::Str(Cow::Owned(text.into()))
}

fn borrowed<'a>(text: &'a str) -> Node<'a> {
    Node::Str(Cow::Borrowed(text))
}

/// A set-membership key with Python's hash/equality for the values the reference collects.
#[derive(Clone, PartialEq, Eq)]
enum SetKey<'n> {
    None,
    Str(&'n str),
}

/// Hashable form of a value: `None` for missing or null, a string, or `Err(hashable)` when
/// membership would not reduce to string comparison (`Err(false)`: unhashable, a TypeError).
fn set_key<'n>(node: Option<&'n Node<'_>>) -> Result<SetKey<'n>, bool> {
    match node {
        None | Some(Node::Null) => Ok(SetKey::None),
        Some(Node::Str(text)) => Ok(SetKey::Str(text)),
        Some(Node::Int(_) | Node::Bool(_)) => Err(true),
        // A tuple hashes only when its members do; leave that to the reference.
        Some(Node::Array(_) | Node::Tuple(_) | Node::Object(_)) => Err(false),
    }
}

// ---------------------------------------------------------------------------------------------
// bounded_case_envelope
// ---------------------------------------------------------------------------------------------

/// The reduction counters `_set_selection_accounting` publishes.
#[derive(Clone, Copy, Default, Debug)]
pub struct Reductions {
    pub assessment_links_stripped: i64,
    pub catalog_dropped: i64,
    pub change_observations_dropped: i64,
    pub deterministic_assessments_dropped: i64,
    pub omissions_dropped: i64,
    pub targeted_excerpts_dropped: i64,
}

#[derive(Clone, Copy)]
enum Section {
    ChangeObservations,
    TargetedExcerpts,
    Omissions,
    DeterministicAssessments,
}

impl Reductions {
    fn slot(&mut self, section: Section) -> &mut i64 {
        match section {
            Section::ChangeObservations => &mut self.change_observations_dropped,
            Section::TargetedExcerpts => &mut self.targeted_excerpts_dropped,
            Section::Omissions => &mut self.omissions_dropped,
            Section::DeterministicAssessments => &mut self.deterministic_assessments_dropped,
        }
    }

    /// The `selection_accounting` object.
    pub fn to_node(self) -> Node<'static> {
        let counts = [
            self.assessment_links_stripped,
            self.catalog_dropped,
            self.change_observations_dropped,
            self.deterministic_assessments_dropped,
            self.omissions_dropped,
            self.targeted_excerpts_dropped,
        ];
        let minimized = counts.iter().any(|count| *count > 0);
        let count = |value: i64| owned(value.to_string());
        object(vec![
            (
                "assessment_links_stripped_count",
                count(self.assessment_links_stripped),
            ),
            ("catalog_dropped_count", count(self.catalog_dropped)),
            (
                "change_observations_dropped_count",
                count(self.change_observations_dropped),
            ),
            (
                "deterministic_assessments_dropped_count",
                count(self.deterministic_assessments_dropped),
            ),
            ("omissions_dropped_count", count(self.omissions_dropped)),
            (
                "reason",
                borrowed(if minimized {
                    "size_minimized"
                } else {
                    "not_minimized"
                }),
            ),
            (
                "targeted_excerpts_dropped_count",
                count(self.targeted_excerpts_dropped),
            ),
        ])
    }
}

/// What bounding produced.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Bounded {
    /// The canonical bytes of the envelope tree as it now stands.
    Fits(Vec<u8>),
    /// `SemanticCaseTooLarge("semantic_case_envelope_too_large")`.
    TooLarge,
}

fn set_accounting(envelope: &mut Node<'_>, reductions: Reductions) {
    set(envelope, ACCOUNTING_KEY, reductions.to_node());
}

/// Cached encoded sizes of the parts of the envelope the ladder rewrites.
///
/// The envelope's length is `fixed` (every other member, all keys and separators) plus the
/// catalog, the packet and the accounting. Catalog rows are measured once and mirrored through
/// every removal; packet members are re-measured only after a change touches them.
struct Sizer {
    fixed: usize,
    catalog: Option<Vec<usize>>,
    packet: Option<Vec<(String, usize)>>,
}

impl Sizer {
    fn new(envelope: &Node<'_>) -> Sizer {
        let catalog = match get(envelope, CATALOG_KEY) {
            Some(Node::Array(rows)) => Some(rows.iter().map(encoded_len).collect()),
            _ => None,
        };
        let packet = match get(envelope, PACKET_KEY) {
            Some(Node::Object(members)) => Some(
                members
                    .iter()
                    .map(|(key, item)| (key.to_string(), encoded_len(item)))
                    .collect(),
            ),
            _ => None,
        };
        let mut sizer = Sizer {
            fixed: 0,
            catalog,
            packet,
        };
        let mut fixed = 2;
        if let Node::Object(members) = envelope {
            fixed += members.len().saturating_sub(1);
            for (key, item) in members {
                fixed += encoded_str_len(key) + 1;
                let variable = match key.as_ref() {
                    CATALOG_KEY => sizer.catalog.is_some(),
                    PACKET_KEY => sizer.packet.is_some(),
                    ACCOUNTING_KEY => true,
                    _ => false,
                };
                if !variable {
                    fixed += encoded_len(item);
                }
            }
        }
        sizer.fixed = fixed;
        sizer
    }

    fn catalog_len(&self) -> usize {
        self.catalog.as_ref().map_or(0, |rows| {
            2 + rows.iter().sum::<usize>() + rows.len().saturating_sub(1)
        })
    }

    fn packet_len(&mut self, envelope: &Node<'_>) -> usize {
        let Some(cache) = self.packet.as_mut() else {
            return 0;
        };
        let Some(Node::Object(members)) = get(envelope, PACKET_KEY) else {
            return 0;
        };
        let mut total = 2 + members.len().saturating_sub(1);
        for (key, item) in members {
            let length = match cache.iter().find(|(name, _)| name == key) {
                Some((_, length)) => *length,
                None => {
                    let length = encoded_len(item);
                    cache.push((key.to_string(), length));
                    length
                }
            };
            total += encoded_str_len(key) + 1 + length;
        }
        total
    }

    /// Forget a packet member's size after it changed.
    fn touch(&mut self, key: &str) {
        if let Some(cache) = self.packet.as_mut() {
            cache.retain(|(name, _)| name != key);
        }
    }

    fn total(&mut self, envelope: &Node<'_>) -> usize {
        self.fixed
            + self.catalog_len()
            + self.packet_len(envelope)
            + get(envelope, ACCOUNTING_KEY).map_or(0, encoded_len)
    }
}

/// `_sync_prior_finding_refs(packet_obj)`; reports whether the list changed.
fn sync_prior_finding_refs(packet: &mut Node<'_>) -> bool {
    let carried: HashSet<String> = match get(packet, "prior_finding_item_ids") {
        Some(Node::Array(ids)) => ids
            .iter()
            .filter_map(|value| match value {
                Node::Str(text) => Some(text.to_string()),
                _ => None,
            })
            .collect(),
        _ => HashSet::new(),
    };
    let Some(Node::Array(refs)) = get_mut(packet, "prior_finding_refs") else {
        return false;
    };
    let before = refs.len();
    refs.retain(|value| match value {
        Node::Str(text) => carried.contains(&format!("prior-finding-{text}")),
        _ => false,
    });
    refs.len() != before
}

/// Keep only `catalog[i]` with `keep[i]`, mirroring the removal in the cached row sizes.
fn retain_rows(envelope: &mut Node<'_>, sizer: &mut Sizer, keep: &[bool]) {
    if let Some(Node::Array(rows)) = get_mut(envelope, CATALOG_KEY) {
        let mut index = 0;
        rows.retain(|_| {
            index += 1;
            keep[index - 1]
        });
    }
    if let Some(lengths) = sizer.catalog.as_mut() {
        let mut index = 0;
        lengths.retain(|_| {
            index += 1;
            keep[index - 1]
        });
    }
}

/// `_drop_prior_finding_rows(envelope)`.
fn drop_prior_finding_rows(
    envelope: &mut Node<'_>,
    sizer: &mut Sizer,
    maximum: usize,
) -> Step<i64> {
    // `rows` in the reference: the dict rows of the catalog, assigned back on the first removal.
    let order: Vec<String> = {
        let Some(Node::Array(rows)) = get(envelope, CATALOG_KEY) else {
            return Step::Done(0);
        };
        let mut order: Vec<String> = Vec::new();
        for row in rows.iter().filter(|row| is_object(row)) {
            if is_str(get(row, "section"), "prior_finding") {
                if let Some(source) = str_of(get(row, "source_ref")) {
                    if !order.iter().any(|seen| seen == source) {
                        order.push(source.to_owned());
                    }
                }
            }
        }
        order
    };
    let mut dropped = 0;
    for source in &order {
        if sizer.total(envelope) <= maximum {
            break;
        }
        let (keep, removed_count, removed_owned) = {
            let Some(Node::Array(rows)) = get(envelope, CATALOG_KEY) else {
                return Step::Defer;
            };
            let mut removed: Vec<SetKey<'_>> = Vec::new();
            for row in rows.iter().filter(|row| is_object(row)) {
                if is_str(get(row, "section"), "prior_finding")
                    && is_str(get(row, "source_ref"), source)
                {
                    match set_key(get(row, "item_id")) {
                        Ok(key) => {
                            if !removed.contains(&key) {
                                removed.push(key);
                            }
                        }
                        // Only string or missing ids are reproduced; anything else defers.
                        Err(_) => return Step::Defer,
                    }
                }
            }
            let mut keep = Vec::with_capacity(rows.len());
            for row in rows {
                keep.push(
                    is_object(row)
                        && match set_key(get(row, "item_id")) {
                            Ok(key) => !removed.contains(&key),
                            Err(true) => true,
                            Err(false) => return Step::Defer,
                        },
                );
            }
            let owned: Vec<Option<String>> = removed
                .iter()
                .map(|key| match key {
                    SetKey::None => None,
                    SetKey::Str(text) => Some((*text).to_owned()),
                })
                .collect();
            (keep, removed.len(), owned)
        };
        let is_removed = |value: &Node<'_>| -> Result<bool, ()> {
            match set_key(Some(value)) {
                Ok(SetKey::None) => Ok(removed_owned.contains(&None)),
                Ok(SetKey::Str(text)) => Ok(removed_owned
                    .iter()
                    .any(|item| item.as_deref() == Some(text))),
                Err(true) => Ok(false),
                Err(false) => Err(()),
            }
        };
        // Check the packet's id list before mutating anything, so a deferral leaves no trace.
        if let Some(Node::Array(current)) =
            get(envelope, PACKET_KEY).and_then(|packet| get(packet, "prior_finding_item_ids"))
        {
            if current.iter().any(|value| is_removed(value).is_err()) {
                return Step::Defer;
            }
        }
        if let Some(Node::Array(gaps)) = get(envelope, PACKET_KEY)
            .and_then(|packet| get(packet, "coverage"))
            .filter(|coverage| is_object(coverage))
            .and_then(|coverage| get(coverage, "known_gaps"))
        {
            let present = gaps
                .iter()
                .any(|gap| is_str(Some(gap), PRIOR_FINDINGS_OVER_LIMIT_GAP));
            if !present && gaps.iter().any(|gap| !matches!(gap, Node::Str(_))) {
                // `sorted(..., key=str.encode)` raises TypeError on a non-string.
                return Step::Defer;
            }
        }
        retain_rows(envelope, sizer, &keep);
        dropped += removed_count as i64;
        let Some(packet @ Node::Object(_)) = get_mut(envelope, PACKET_KEY) else {
            continue;
        };
        if let Some(Node::Array(current)) = get_mut(packet, "prior_finding_item_ids") {
            current.retain(|value| !matches!(is_removed(value), Ok(true)));
        }
        sync_prior_finding_refs(packet);
        if let Some(coverage @ Node::Object(_)) = get_mut(packet, "coverage") {
            if let Some(Node::Array(gaps)) = get_mut(coverage, "known_gaps") {
                if !gaps
                    .iter()
                    .any(|gap| is_str(Some(gap), PRIOR_FINDINGS_OVER_LIMIT_GAP))
                {
                    gaps.push(borrowed(PRIOR_FINDINGS_OVER_LIMIT_GAP));
                    gaps.sort_by(|left, right| match (left, right) {
                        (Node::Str(l), Node::Str(r)) => l.as_bytes().cmp(r.as_bytes()),
                        _ => std::cmp::Ordering::Equal,
                    });
                }
            }
        }
        for key in ["prior_finding_item_ids", "prior_finding_refs", "coverage"] {
            sizer.touch(key);
        }
    }
    Step::Done(dropped)
}

/// `_strip_assessment_links(envelope)`.
fn strip_assessment_links(envelope: &mut Node<'_>, sizer: &mut Sizer) -> i64 {
    let Some(packet @ Node::Object(_)) = get_mut(envelope, PACKET_KEY) else {
        return 0;
    };
    let Some(Node::Array(rows)) = get_mut(packet, "deterministic_assessments") else {
        return 0;
    };
    let mut removed = 0;
    let mut changed = false;
    for row in rows.iter_mut().filter(|row| is_object(row)) {
        for key in ["summary_item_id", "detail_item_id"] {
            if let Some(value) = pop(row, key) {
                changed = true;
                if value != Node::Null {
                    removed += 1;
                }
            }
        }
    }
    if changed {
        sizer.touch("deterministic_assessments");
    }
    removed
}

/// `_fit_packet_section`: the first fitting suffix reduction, found on computed sizes.
fn fit_packet_section(
    envelope: &mut Node<'_>,
    sizer: &mut Sizer,
    reductions: &mut Reductions,
    key: &'static str,
    section: Section,
    maximum: usize,
    want_bytes: bool,
) -> Step<Option<Vec<u8>>> {
    let mut original = {
        let Some(packet @ Node::Object(_)) = get_mut(envelope, PACKET_KEY) else {
            return Step::Done(None);
        };
        match get_mut(packet, key) {
            Some(Node::Array(rows)) if !rows.is_empty() => std::mem::take(rows),
            _ => return Step::Done(None),
        }
    };
    sizer.touch(key);
    let total = original.len();
    let prior = *reductions.slot(section);
    let mut prefix = Vec::with_capacity(total + 1);
    prefix.push(0usize);
    for row in &original {
        prefix.push(prefix[prefix.len() - 1] + encoded_len(row));
    }
    let rows_len = |kept: usize| prefix[kept] + kept.saturating_sub(1);
    // The state with the whole section dropped. Every other count differs from it only in the
    // kept rows and in the digits of this section's counter.
    *reductions.slot(section) = prior + total as i64;
    set_accounting(envelope, *reductions);
    let base = sizer.total(envelope) - encoded_len(&reductions.to_node());
    let snapshot = *reductions;
    let size = |count: usize| {
        let mut probe = snapshot;
        *probe.slot(section) = prior + count as i64;
        base + encoded_len(&probe.to_node()) + rows_len(total - count)
    };
    if size(total) > maximum {
        return Step::Done(None);
    }
    let (mut low, mut high, mut best) = (1usize, total, total);
    while low < high {
        let middle = (low + high) / 2;
        if size(middle) <= maximum {
            high = middle;
            best = middle;
        } else {
            low = middle + 1;
        }
    }
    *reductions.slot(section) = prior + best as i64;
    set_accounting(envelope, *reductions);
    original.truncate(total - best);
    if let Some(packet) = get_mut(envelope, PACKET_KEY) {
        set(packet, key, Node::Array(original));
    }
    Step::Done(Some(if want_bytes {
        done!(encode(envelope))
    } else {
        Vec::new()
    }))
}

/// `_drop_catalog_row(envelope)`.
fn drop_catalog_row(envelope: &mut Node<'_>, sizer: &mut Sizer) -> bool {
    let keep: Vec<bool> = match get(envelope, CATALOG_KEY) {
        Some(Node::Array(rows)) if rows.iter().any(is_object) => {
            rows.iter().map(is_object).collect()
        }
        _ => return false,
    };
    retain_rows(envelope, sizer, &keep);
    let Some(Node::Array(rows)) = get_mut(envelope, CATALOG_KEY) else {
        return false;
    };
    let dropped = rows.pop().unwrap_or(Node::Null);
    if let Some(lengths) = sizer.catalog.as_mut() {
        lengths.pop();
    }
    let Some(dropped_id) = str_of(get(&dropped, "item_id")) else {
        return true;
    };
    let Some(packet @ Node::Object(_)) = get_mut(envelope, PACKET_KEY) else {
        return true;
    };
    for key in PACKET_ID_LIST_KEYS {
        if let Some(Node::Array(values)) = get_mut(packet, key) {
            let before = values.len();
            values.retain(|value| !is_str(Some(value), dropped_id));
            if values.len() != before {
                sizer.touch(key);
            }
        }
    }
    if sync_prior_finding_refs(packet) {
        sizer.touch("prior_finding_refs");
    }
    if let Some(Node::Array(excerpts)) = get_mut(packet, "targeted_excerpts") {
        let before = excerpts.len();
        excerpts.retain(|row| !(is_object(row) && is_str(get(row, "excerpt_item_id"), dropped_id)));
        if excerpts.len() != before {
            sizer.touch("targeted_excerpts");
        }
    }
    for key in ["summary_item_id", "detail_item_id"] {
        if let Some(Node::Array(assessments)) = get_mut(packet, "deterministic_assessments") {
            let mut changed = false;
            for row in assessments.iter_mut() {
                if is_object(row) && is_str(get(row, key), dropped_id) {
                    pop(row, key);
                    changed = true;
                }
            }
            if changed {
                sizer.touch("deterministic_assessments");
            }
        }
    }
    true
}

/// `bounded_case_envelope` from the freshly built envelope tree.
///
/// Sets the zero accounting the reference sets first and validates the tree as its first
/// `canonical_encode` would (an invalid tree defers). On [`Bounded::Fits`] the tree is left exactly
/// in the state the returned bytes encode; with `want_bytes` false the bytes are not produced and
/// `Fits` carries an empty vector.
pub fn bound_envelope(envelope: &mut Node<'_>, maximum: usize, want_bytes: bool) -> Step<Bounded> {
    let finish = |envelope: &Node<'_>| -> Step<Bounded> {
        Step::Done(Bounded::Fits(if want_bytes {
            done!(encode(envelope))
        } else {
            Vec::new()
        }))
    };
    let mut reductions = Reductions::default();
    set_accounting(envelope, reductions);
    let Some(length) = checked_len(envelope, 0) else {
        return Step::Defer;
    };
    if length <= maximum {
        return finish(envelope);
    }
    let mut sizer = Sizer::new(envelope);

    reductions.catalog_dropped += done!(drop_prior_finding_rows(envelope, &mut sizer, maximum));
    set_accounting(envelope, reductions);
    if sizer.total(envelope) <= maximum {
        return finish(envelope);
    }

    reductions.assessment_links_stripped += strip_assessment_links(envelope, &mut sizer);
    set_accounting(envelope, reductions);
    if sizer.total(envelope) <= maximum {
        return finish(envelope);
    }
    for (key, section) in [
        ("change_observations", Section::ChangeObservations),
        ("targeted_excerpts", Section::TargetedExcerpts),
        ("omissions", Section::Omissions),
        (
            "deterministic_assessments",
            Section::DeterministicAssessments,
        ),
    ] {
        let fitted = done!(fit_packet_section(
            envelope,
            &mut sizer,
            &mut reductions,
            key,
            section,
            maximum,
            want_bytes
        ));
        if let Some(bytes) = fitted {
            return Step::Done(Bounded::Fits(bytes));
        }
    }
    while drop_catalog_row(envelope, &mut sizer) {
        reductions.catalog_dropped += 1;
        set_accounting(envelope, reductions);
        if sizer.total(envelope) <= maximum {
            return finish(envelope);
        }
    }
    Step::Done(Bounded::TooLarge)
}

// ---------------------------------------------------------------------------------------------
// assemble_filtered_review_packet
// ---------------------------------------------------------------------------------------------

/// The exact-`str` members of an exact `list` (the reference's `type(x) is list` filters).
fn str_members<'n>(node: Option<&'n Node<'_>>) -> Vec<&'n str> {
    match node {
        Some(Node::Array(items)) => items.iter().filter_map(|item| str_of(Some(item))).collect(),
        _ => Vec::new(),
    }
}

fn str_array<'a>(values: impl IntoIterator<Item = &'a str>) -> Node<'a> {
    Node::Array(values.into_iter().map(borrowed).collect())
}

fn section_label(section: &str) -> Option<&'static str> {
    SECTION_LABELS
        .iter()
        .find(|(name, _)| *name == section)
        .map(|(_, label)| *label)
}

/// `assemble_filtered_review_packet(envelope, content_by_id=..., included_item_ids=...)`.
///
/// `content` answers `content_by_id.get(item_id)`; `included` is the approved id set.
pub fn assemble_filtered_review_packet<'c>(
    envelope: &Node<'_>,
    content: impl Fn(&str) -> Option<&'c [u8]>,
    included: &HashSet<&str>,
) -> Step<Vec<u8>> {
    let frontier: BTreeSet<&str> = str_members(get(envelope, "frontier_refs"))
        .into_iter()
        .collect();
    let local: BTreeSet<&str> = str_members(get(envelope, "local_check_refs"))
        .into_iter()
        .collect();
    let allowed: BTreeSet<&str> = frontier.union(&local).copied().collect();

    let catalog: Vec<&Node<'_>> = match get(envelope, CATALOG_KEY) {
        Some(Node::Array(rows)) => rows.iter().filter(|row| is_object(row)).collect(),
        _ => Vec::new(),
    };

    let mut content_rows: Vec<Node<'_>> = Vec::new();
    let mut carried: HashSet<&str> = HashSet::new();
    let mut omitted_extra: Vec<Node<'_>> = Vec::new();
    for meta in catalog {
        let Some(item_id) = str_of(get(meta, "item_id")) else {
            continue;
        };
        if item_id == REVIEW_PACKET_ITEM_ID {
            continue;
        }
        let category = str_of(get(meta, "category"));
        let source_kind = str_of(get(meta, "source_kind"));
        let source_ref = str_of(get(meta, "source_ref"));
        let section = str_of(get(meta, "section"));
        let linked = str_members(get(meta, "linked_subject_refs"));
        let approved = included.contains(item_id);
        if approved {
            if let Some(plaintext) = content(item_id) {
                let Ok(text) = std::str::from_utf8(plaintext) else {
                    continue;
                };
                if memchr::memchr(0, plaintext).is_some() {
                    return Step::Defer;
                }
                let occurred_order = match get(meta, "occurred_order") {
                    Some(Node::Int(order)) => *order,
                    _ => 0,
                };
                let superseded = str_members(get(meta, "superseded_by"));
                let mut row = vec![
                    ("category", borrowed(category.unwrap_or(""))),
                    ("content", borrowed(text)),
                    ("content_bytes", Node::Int(plaintext.len() as i64)),
                    (
                        "content_digest",
                        owned(canonical::sha256_prefixed(plaintext)),
                    ),
                    ("item_id", borrowed(item_id)),
                    ("linked_subject_refs", str_array(linked.iter().copied())),
                    ("occurred_order", Node::Int(occurred_order)),
                    ("section", borrowed(section.unwrap_or("timeline"))),
                    ("source_kind", borrowed(source_kind.unwrap_or("task"))),
                    ("source_ref", borrowed(source_ref.unwrap_or(item_id))),
                ];
                if let Some(latest_for) = str_of(get(meta, "latest_for")) {
                    row.push(("latest_for", borrowed(latest_for)));
                }
                if !superseded.is_empty() {
                    row.push(("superseded_by", str_array(superseded)));
                }
                // Only the fixed section label the builder catalogued, never caller text.
                if let Some(label) = section.and_then(section_label) {
                    if is_str(get(meta, "label"), label) {
                        row.push(("label", borrowed(label)));
                    }
                }
                content_rows.push(object(row));
                carried.insert(item_id);
                continue;
            }
        }
        if approved {
            continue;
        }
        let subject = match source_ref {
            Some(source) if allowed.contains(source) => Some(source),
            _ => linked
                .iter()
                .copied()
                .find(|reference| allowed.contains(reference)),
        };
        let Some(subject) = subject else {
            continue;
        };
        omitted_extra.push(object(vec![
            ("category", borrowed(category.unwrap_or(""))),
            ("reason", borrowed("withheld_by_policy")),
            ("source_kind", borrowed(source_kind.unwrap_or("task"))),
            ("subject_ref", borrowed(subject)),
        ]));
    }

    let source_packet = match get(envelope, PACKET_KEY) {
        Some(Node::Object(members)) => members.as_slice(),
        _ => &[],
    };
    // A shallow copy that leaves the rewritten members out instead of cloning them first.
    let rewritten = |key: &str| {
        PACKET_ID_LIST_KEYS.contains(&key)
            || matches!(
                key,
                "targeted_excerpts" | "deterministic_assessments" | "omissions"
            )
    };
    let mut packet = Node::Object(
        source_packet
            .iter()
            .map(|(key, item)| {
                let copied = if rewritten(key) {
                    Node::Null
                } else {
                    item.clone()
                };
                (key.clone(), copied)
            })
            .collect(),
    );
    let source = |key: &str| {
        source_packet
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, v)| v)
    };
    for key in PACKET_ID_LIST_KEYS {
        let filtered: Vec<Node<'_>> = match source(key) {
            Some(Node::Array(values)) => values
                .iter()
                .filter(|value| matches!(value, Node::Str(text) if carried.contains(text.as_ref())))
                .cloned()
                .collect(),
            _ => Vec::new(),
        };
        set(&mut packet, key, Node::Array(filtered));
    }
    sync_prior_finding_refs(&mut packet);

    let mut excerpts = Vec::new();
    if let Some(Node::Array(rows)) = source("targeted_excerpts") {
        for row in rows.iter().filter(|row| is_object(row)) {
            match get(row, "excerpt_item_id") {
                Some(Node::Str(text)) if carried.contains(text.as_ref()) => {
                    excerpts.push(row.clone())
                }
                // Membership of an unhashable value raises TypeError in the reference.
                Some(Node::Array(_) | Node::Tuple(_) | Node::Object(_)) => return Step::Defer,
                _ => {}
            }
        }
    }
    set(&mut packet, "targeted_excerpts", Node::Array(excerpts));

    let mut assessments = Vec::new();
    if let Some(Node::Array(rows)) = source("deterministic_assessments") {
        for raw in rows.iter().filter(|row| is_object(row)) {
            let mut row = raw.clone();
            if let (Some(summary), Some(detail)) = (
                str_of(get(raw, "summary_item_id")),
                str_of(get(raw, "detail_item_id")),
            ) {
                if !carried.contains(summary) || !carried.contains(detail) {
                    pop(&mut row, "summary_item_id");
                    pop(&mut row, "detail_item_id");
                }
            }
            assessments.push(row);
        }
    }
    set(
        &mut packet,
        "deterministic_assessments",
        Node::Array(assessments),
    );

    let base_omissions: Vec<&Node<'_>> = match source("omissions") {
        Some(Node::Array(rows)) => rows.iter().filter(|row| is_object(row)).collect(),
        _ => Vec::new(),
    };
    let mut seen: HashSet<(&str, &str, &str)> = HashSet::new();
    let mut omissions: Vec<((&str, &str, &str), &Node<'_>)> = Vec::new();
    for row in base_omissions.into_iter().chain(omitted_extra.iter()) {
        let (Some(subject), Some(category), Some(reason)) = (
            str_of(get(row, "subject_ref")),
            str_of(get(row, "category")),
            str_of(get(row, "reason")),
        ) else {
            continue;
        };
        let key = (subject, category, reason);
        if seen.contains(&key) || !allowed.contains(subject) {
            continue;
        }
        seen.insert(key);
        omissions.push((key, row));
    }
    if omissions.iter().any(|((subject, category, reason), _)| {
        !subject.is_ascii() || !category.is_ascii() || !reason.is_ascii()
    }) {
        // `str.encode("ascii")` raises UnicodeEncodeError in the reference's sort key.
        return Step::Defer;
    }
    omissions.sort_by(|(left, _), (right, _)| {
        (left.0.as_bytes(), left.1.as_bytes(), left.2.as_bytes()).cmp(&(
            right.0.as_bytes(),
            right.1.as_bytes(),
            right.2.as_bytes(),
        ))
    });
    let omissions: Vec<Node<'_>> = omissions.into_iter().map(|(_, row)| row.clone()).collect();
    set(&mut packet, "omissions", Node::Array(omissions));
    if get(&packet, "change_observations").is_none() {
        set(&mut packet, "change_observations", Node::Array(Vec::new()));
    }
    if get(&packet, "coverage").is_none() {
        set(&mut packet, "coverage", Node::Object(Vec::new()));
    }

    // Approved content whose catalog row is absent cannot travel; it is counted instead.
    let uncatalogued = included
        .iter()
        .filter(|item_id| {
            **item_id != REVIEW_PACKET_ITEM_ID
                && content(item_id).is_some()
                && !carried.contains(**item_id)
        })
        .count();
    let mut accounting = match get(envelope, ACCOUNTING_KEY) {
        Some(accounting @ Node::Object(_)) => accounting.clone(),
        _ => Reductions::default().to_node(),
    };
    set(
        &mut accounting,
        "uncatalogued_approved_count",
        owned(uncatalogued.to_string()),
    );

    let or = |key: &str, default: &'static str| {
        get(envelope, key)
            .cloned()
            .unwrap_or_else(|| borrowed(default))
    };
    let document = object(vec![
        ("case_digest", or("case_digest", "")),
        ("case_id", or("case_id", "")),
        ("citable_refs", str_array(allowed.iter().copied())),
        (
            "omitted_reference_count",
            or("omitted_reference_count", "0"),
        ),
        (ACCOUNTING_KEY, accounting),
        ("dependency_digest", or("dependency_digest", "")),
        ("frontier_refs", str_array(frontier.iter().copied())),
        ("items", Node::Array(content_rows)),
        ("local_check_refs", str_array(local.iter().copied())),
        ("policy_id", or("policy_id", "")),
        ("policy_version", or("policy_version", "")),
        (
            "question_set",
            match get(envelope, "question_set") {
                Some(Node::Array(questions)) => Node::Array(questions.clone()),
                _ => Node::Array(Vec::new()),
            },
        ),
        ("review_context_profile", or("review_context_profile", "")),
        (PACKET_KEY, packet),
        ("review_selection_digest", or("review_selection_digest", "")),
        ("schema", borrowed(PACKET_SCHEMA)),
        (
            "subject_frontier",
            match get(envelope, "subject_frontier") {
                Some(frontier @ Node::Object(_)) => frontier.clone(),
                _ => Node::Object(Vec::new()),
            },
        ),
    ]);
    encode(&document)
}

/// `_catalog_item_ids(envelope)`.
pub fn catalog_item_ids<'n>(envelope: &'n Node<'_>) -> Vec<&'n str> {
    match get(envelope, CATALOG_KEY) {
        Some(Node::Array(rows)) => rows
            .iter()
            .filter(|row| is_object(row))
            .filter_map(|row| str_of(get(row, "item_id")))
            .collect(),
        _ => Vec::new(),
    }
}

// ---------------------------------------------------------------------------------------------
// Prose clipping
// ---------------------------------------------------------------------------------------------

fn floor_boundary(text: &str, index: usize) -> usize {
    let mut index = index.min(text.len());
    while !text.is_char_boundary(index) {
        index -= 1;
    }
    index
}

fn ceil_boundary(text: &str, index: usize) -> usize {
    let mut index = index.min(text.len());
    while !text.is_char_boundary(index) {
        index += 1;
    }
    index
}

/// `raw[:limit].decode("utf-8", errors="ignore")` for valid UTF-8 and `limit >= 0`.
pub fn utf8_prefix(text: &str, limit: usize) -> &str {
    &text[..floor_boundary(text, limit)]
}

fn elision_marker(elided: i64, total: i64) -> String {
    let [open, middle, close] = ELISION_MARKER_PARTS;
    format!("{open}{elided}{middle}{total}{close}")
}

/// `_head_tail(raw, limit)` for valid UTF-8 `raw`.
pub fn head_tail(text: &str, limit: i64) -> Step<String> {
    if limit < 0 {
        return Step::Defer;
    }
    let total = text.len() as i64;
    if total <= limit {
        return Step::Done(text.to_owned());
    }
    let mut available = limit - elision_marker(total, total).len() as i64;
    if available < 2 * MIN_HEAD_TAIL_SIDE_BYTES {
        return Step::Done(utf8_prefix(text, limit as usize).to_owned());
    }
    let mut best = String::new();
    for _attempt in 0..4 {
        let head_bytes = available.div_euclid(2);
        let tail_bytes = available - head_bytes;
        if head_bytes < 0 || tail_bytes < 0 || tail_bytes > total {
            // Python slicing would wrap a negative bound; leave that to the reference.
            return Step::Defer;
        }
        let head = utf8_prefix(text, head_bytes as usize);
        let tail = &text[ceil_boundary(text, (total - tail_bytes) as usize)..];
        let kept = (head.len() + tail.len()) as i64;
        let candidate = format!("{head}{}{tail}", elision_marker(total - kept, total));
        let size = candidate.len() as i64;
        if size <= limit && size > best.len() as i64 {
            best = candidate;
        }
        if size == limit {
            break;
        }
        // The marker's digit count depends on what was elided; settle on the exact fill.
        available += limit - size;
    }
    if best.is_empty() {
        return Step::Done(utf8_prefix(text, limit as usize).to_owned());
    }
    Step::Done(best)
}

/// `_longest_prose_leaf`: the first longest string leaf, keys visited in UTF-8 order.
fn longest_prose_leaf(node: &Node<'_>, path: &mut Vec<usize>, best: &mut (Vec<usize>, usize)) {
    match node {
        Node::Str(text) => {
            if text.len() > best.1 {
                *best = (path.clone(), text.len());
            }
        }
        Node::Object(members) => {
            let mut order: Vec<usize> = (0..members.len()).collect();
            order.sort_by(|left, right| {
                members[*left]
                    .0
                    .as_bytes()
                    .cmp(members[*right].0.as_bytes())
            });
            for index in order {
                path.push(index);
                longest_prose_leaf(&members[index].1, path, best);
                path.pop();
            }
        }
        Node::Array(items) | Node::Tuple(items) => {
            for (index, item) in items.iter().enumerate() {
                path.push(index);
                longest_prose_leaf(item, path, best);
                path.pop();
            }
        }
        _ => {}
    }
}

fn leaf_mut<'n, 'a>(node: &'n mut Node<'a>, path: &[usize]) -> Option<&'n mut Node<'a>> {
    let mut current = node;
    for &index in path {
        current = match current {
            Node::Object(members) => &mut members.get_mut(index)?.1,
            Node::Array(items) | Node::Tuple(items) => items.get_mut(index)?,
            _ => return None,
        };
    }
    Some(current)
}

/// What `_clip_json_prose` produced.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Clipped {
    /// The canonical bytes of the clipped value.
    Fits(Vec<u8>),
    /// The reference's `None`: the payload cannot fit with its prose at the minimum clip.
    CannotFit,
}

/// `_clip_json_prose(value, limit)` on a validated tree.
pub fn clip_json_prose(mut node: Node<'_>, limit: usize) -> Step<Clipped> {
    for _attempt in 0..64 {
        let encoded = encoded_len(&node);
        if encoded <= limit {
            return Step::Done(Clipped::Fits(done!(encode(&node))));
        }
        let mut best = (Vec::new(), 0usize);
        longest_prose_leaf(&node, &mut Vec::new(), &mut best);
        let (path, size) = best;
        if size <= MIN_CLIPPABLE_PROSE_BYTES {
            return Step::Done(Clipped::CannotFit);
        }
        let Some(Node::Str(raw)) = leaf_mut(&mut node, &path) else {
            return Step::Defer;
        };
        let target = MIN_CLIPPABLE_PROSE_BYTES.max(size - (encoded - limit).min(size));
        let mut clipped = done!(head_tail(raw, target as i64));
        if clipped.len() >= size {
            clipped = utf8_prefix(raw, target).to_owned();
        }
        *raw = Cow::Owned(clipped);
    }
    Step::Done(Clipped::CannotFit)
}

// ---------------------------------------------------------------------------------------------
// Task statement and lineage helpers
// ---------------------------------------------------------------------------------------------

/// `_encoded_prefix(text, budget)`: the byte length of the longest fitting prefix.
pub fn encoded_prefix_len(text: &str, budget: i64) -> usize {
    let mut used: i64 = 0;
    for (index, character) in text.char_indices() {
        let cost = match character {
            '"' | '\\' | '\u{8}' | '\t' | '\n' | '\u{c}' | '\r' => 2,
            control if (control as u32) < 0x20 => 6,
            other => other.len_utf8() as i64,
        };
        if used + cost > budget {
            return index;
        }
        used += cost;
    }
    text.len()
}

/// One `yoetz.lineage-semantic-input/2` part, encoded from already-canonical rows.
pub struct LineagePart<'a> {
    pub children: &'a [&'a [u8]],
    pub gaps: &'a [&'a [u8]],
    pub manifest_digest: Option<&'a str>,
    pub part_index: i64,
    pub part_count: i64,
    pub child_count: i64,
    pub gap_count: i64,
    pub schema: &'a str,
}

fn push_rows(out: &mut Vec<u8>, rows: &[&[u8]]) {
    out.push(b'[');
    for (index, row) in rows.iter().enumerate() {
        if index > 0 {
            out.push(b',');
        }
        out.extend_from_slice(row);
    }
    out.push(b']');
}

/// `_lineage_part_bytes(...)`; the members are written in canonical (sorted) key order.
pub fn lineage_part_bytes(part: &LineagePart<'_>) -> Step<Vec<u8>> {
    let mut out = Vec::with_capacity(256);
    out.extend_from_slice(b"{\"child_count\":");
    canonical::push_int(&mut out, part.child_count);
    out.extend_from_slice(b",\"children\":");
    push_rows(&mut out, part.children);
    out.extend_from_slice(b",\"gap_count\":");
    canonical::push_int(&mut out, part.gap_count);
    out.extend_from_slice(b",\"gaps\":");
    push_rows(&mut out, part.gaps);
    out.extend_from_slice(b",\"manifest_digest\":");
    match part.manifest_digest {
        None => out.extend_from_slice(b"null"),
        Some(digest) => {
            if canonical::encode_str_into(&mut out, digest).is_err() {
                return Step::Defer;
            }
        }
    }
    out.extend_from_slice(b",\"part_count\":");
    canonical::push_int(&mut out, part.part_count);
    out.extend_from_slice(b",\"part_index\":");
    canonical::push_int(&mut out, part.part_index);
    out.extend_from_slice(b",\"schema\":");
    if canonical::encode_str_into(&mut out, part.schema).is_err() {
        return Step::Defer;
    }
    out.push(b'}');
    Step::Done(out)
}

/// What `_lineage_partition` produced.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Partition {
    Parts(Vec<Vec<u8>>),
    /// `LineageSemanticCapacityExceeded("lineage_semantic_input_too_large")`.
    TooLarge,
}

/// `_lineage_partition(children, gaps, manifest_digest)` over pre-encoded rows.
pub fn lineage_partition(
    children: &[&[u8]],
    gaps: &[&[u8]],
    manifest_digest: Option<&str>,
    item_limit: i64,
    max_parts: i64,
    schema: &str,
) -> Step<Partition> {
    let child_count = children.len();
    let gap_count = gaps.len();
    let prefix = |rows: &[&[u8]]| {
        let mut sums = Vec::with_capacity(rows.len() + 1);
        sums.push(0usize);
        for row in rows {
            sums.push(sums[sums.len() - 1] + row.len());
        }
        sums
    };
    let child_sums = prefix(children);
    let gap_sums = prefix(gaps);
    let header = |part_index: i64, part_count: i64| -> Step<usize> {
        let empty = done!(lineage_part_bytes(&LineagePart {
            children: &[],
            gaps: &[],
            manifest_digest,
            part_index,
            part_count,
            child_count: child_count as i64,
            gap_count: gap_count as i64,
            schema,
        }));
        Step::Done(empty.len())
    };
    // Exact encoded size of a part holding children[cs..ce] and gaps[gs..ge].
    let size = |base: usize, cs: usize, ce: usize, gs: usize, ge: usize| -> i64 {
        (base + child_sums[ce] - child_sums[cs] + (ce - cs).saturating_sub(1) + gap_sums[ge]
            - gap_sums[gs]
            + (ge - gs).saturating_sub(1)) as i64
    };
    let (mut child_at, mut gap_at) = (0usize, 0usize);
    let mut slices: Vec<(usize, usize, usize, usize)> = Vec::new();
    while child_at < child_count || gap_at < gap_count {
        if slices.len() as i64 >= max_parts {
            return Step::Done(Partition::TooLarge);
        }
        let part_index = slices.len() as i64;
        // Fit is measured against the widest part header (`part_count` at the cap).
        let base = done!(header(part_index, max_parts));
        let mut child_take = 0;
        if child_at < child_count {
            let (mut low, mut high) = (1usize, child_count - child_at);
            if size(base, child_at, child_at + 1, 0, 0) > item_limit {
                return Step::Done(Partition::TooLarge);
            }
            while low < high {
                let middle = (low + high).div_ceil(2);
                if size(base, child_at, child_at + middle, 0, 0) <= item_limit {
                    low = middle;
                } else {
                    high = middle - 1;
                }
            }
            child_take = low;
        }
        let mut gap_take = 0;
        if gap_at < gap_count {
            let child_end = child_at + child_take;
            if size(base, child_at, child_end, gap_at, gap_at + 1) <= item_limit {
                let (mut low, mut high) = (1usize, gap_count - gap_at);
                while low < high {
                    let middle = (low + high).div_ceil(2);
                    if size(base, child_at, child_end, gap_at, gap_at + middle) <= item_limit {
                        low = middle;
                    } else {
                        high = middle - 1;
                    }
                }
                gap_take = low;
            } else if child_take == 0 {
                return Step::Done(Partition::TooLarge);
            }
        }
        if child_take == 0 && gap_take == 0 {
            return Step::Done(Partition::TooLarge);
        }
        slices.push((child_at, child_at + child_take, gap_at, gap_at + gap_take));
        child_at += child_take;
        gap_at += gap_take;
    }
    let part_count = slices.len() as i64;
    let mut parts = Vec::with_capacity(slices.len());
    for (index, (cs, ce, gs, ge)) in slices.into_iter().enumerate() {
        let encoded = done!(lineage_part_bytes(&LineagePart {
            children: &children[cs..ce],
            gaps: &gaps[gs..ge],
            manifest_digest,
            part_index: index as i64,
            part_count,
            child_count: child_count as i64,
            gap_count: gap_count as i64,
            schema,
        }));
        if encoded.len() as i64 > item_limit {
            return Step::Done(Partition::TooLarge);
        }
        parts.push(encoded);
    }
    Step::Done(Partition::Parts(parts))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tree(source: &str) -> Node<'_> {
        parse_node(source.as_bytes()).unwrap().0
    }

    #[test]
    fn encoded_len_matches_encoder() {
        let value = tree(
            "{\"b\":[1,-20,true,false,null,\"q\\\"\\\\\\n\\u0001\\u001f\u{e9}\u{1f600}\"],\
             \"a\":{},\"c\":[]}",
        );
        let encoded = encode_node(&value).unwrap();
        assert_eq!(encoded_len(&value), encoded.len());
        assert_eq!(checked_len(&value, 0), Some(encoded.len()));
        let reference = crate::protocol::json::parse(&encoded).unwrap();
        assert_eq!(
            crate::protocol::canonical::encode(&reference).unwrap(),
            encoded
        );
    }

    #[test]
    fn parse_borrows_and_refuses_like_reference() {
        let (node, length) = parse_node(b"{\"a\":\"plain\",\"b\":\"esc\\n\"}").unwrap();
        assert_eq!(length, encode_node(&node).unwrap().len());
        assert!(matches!(
            get(&node, "a"),
            Some(Node::Str(Cow::Borrowed("plain")))
        ));
        assert!(matches!(get(&node, "b"), Some(Node::Str(Cow::Owned(_)))));
        assert_eq!(
            parse_node(b"{\"a\":1,\"a\":2}").map(|_| ()),
            Err(canonical::DUPLICATE_OBJECT_KEY)
        );
        assert_eq!(
            parse_node(b"\"\\u0000\"").map(|_| ()),
            Err(canonical::NUL_BYTE_FORBIDDEN)
        );
        assert_eq!(
            parse_node(b"1.5").map(|_| ()),
            Err(canonical::FLOAT_FORBIDDEN)
        );
    }

    #[test]
    fn accounting_keys_are_canonical() {
        let reductions = Reductions {
            omissions_dropped: 12,
            ..Reductions::default()
        };
        let encoded = String::from_utf8(encode_node(&reductions.to_node()).unwrap()).unwrap();
        assert!(encoded.contains("\"omissions_dropped_count\":\"12\""));
        assert!(encoded.contains("\"reason\":\"size_minimized\""));
    }

    #[test]
    fn lineage_part_matches_encoder() {
        let child = encode_node(&tree("{\"x\":\"a\"}")).unwrap();
        let rows: Vec<&[u8]> = vec![&child, &child];
        let Step::Done(bytes) = lineage_part_bytes(&LineagePart {
            children: &rows,
            gaps: &rows[..1],
            manifest_digest: Some("sha256:x"),
            part_index: 3,
            part_count: 64,
            child_count: 2,
            gap_count: 1,
            schema: "yoetz.lineage-semantic-input/2",
        }) else {
            panic!("deferred");
        };
        let reference = tree(
            "{\"schema\":\"yoetz.lineage-semantic-input/2\",\"part_index\":3,\"part_count\":64,\
             \"manifest_digest\":\"sha256:x\",\"gaps\":[{\"x\":\"a\"}],\"gap_count\":1,\
             \"children\":[{\"x\":\"a\"},{\"x\":\"a\"}],\"child_count\":2}",
        );
        assert_eq!(bytes, encode_node(&reference).unwrap());
    }

    #[test]
    fn head_tail_keeps_both_ends() {
        let text = "a".repeat(1000) + &"z".repeat(1000);
        let Step::Done(clipped) = head_tail(&text, 400) else {
            panic!("deferred")
        };
        assert!(clipped.len() <= 400);
        assert!(clipped.starts_with('a') && clipped.ends_with('z'));
        assert!(clipped.contains("bytes elided here; head and tail kept"));
        assert_eq!(head_tail("abc", 10), Step::Done("abc".to_owned()));
        assert_eq!(head_tail(&text, 100), Step::Done("a".repeat(100)));
        assert_eq!(head_tail(&text, -1), Step::Defer);
    }

    #[test]
    fn encoded_prefix_counts_escapes() {
        assert_eq!(encoded_prefix_len("ab\"c", 3), 2);
        assert_eq!(encoded_prefix_len("ab\"c", 4), 3);
        assert_eq!(encoded_prefix_len("\u{1}x", 5), 0);
        assert_eq!(encoded_prefix_len("\u{e9}\u{e9}", 3), 2);
        assert_eq!(encoded_prefix_len("abc", 10), 3);
    }

    #[test]
    fn bounding_drops_catalog_rows_until_it_fits() {
        let envelope = tree(
            "{\"item_catalog\":[{\"item_id\":\"a\"},{\"item_id\":\"b\"}],\
             \"review_packet\":{\"timeline_item_ids\":[\"a\",\"b\"]}}",
        );
        let full = match bound_envelope(&mut envelope.clone(), 10_000, true) {
            Step::Done(Bounded::Fits(bytes)) => bytes.len(),
            other => panic!("{other:?}"),
        };
        match bound_envelope(&mut envelope.clone(), full - 1, true) {
            Step::Done(Bounded::Fits(bytes)) => {
                let text = String::from_utf8(bytes).unwrap();
                assert!(text.contains("\"catalog_dropped_count\":\"1\""), "{text}");
                assert!(text.contains("\"timeline_item_ids\":[\"a\"]"), "{text}");
            }
            other => panic!("{other:?}"),
        }
        assert_eq!(
            bound_envelope(&mut envelope.clone(), 10, true),
            Step::Done(Bounded::TooLarge)
        );
    }

    #[test]
    fn tuples_are_not_lists_to_the_ladder() {
        let mut envelope = Node::Object(vec![(
            Cow::Borrowed("item_catalog"),
            Node::Tuple(vec![Node::Object(vec![(
                Cow::Borrowed("item_id"),
                borrowed("a"),
            )])]),
        )]);
        // A tuple catalog is never trimmed: only the accounting is added.
        assert_eq!(
            bound_envelope(&mut envelope, 20, true),
            Step::Done(Bounded::TooLarge)
        );
        normalize_tuples(&mut envelope);
        assert!(matches!(
            get(&envelope, "item_catalog"),
            Some(Node::Array(_))
        ));
    }
}
