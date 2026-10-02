//! Pure twins of `yoetz.domain.events` hot paths.
//!
//! [`entry_digest`] writes the canonical bytes of the fixed eighteen-member accepted-record
//! digest preimage (`accepted_record_digest_preimage`: the accepted record without its
//! `entry_digest` member) straight from the record's primitive fields and hashes them. The
//! reference builds the preimage as two frozen JSON objects and canonical-encodes it; the bytes
//! here are identical because every member key is ASCII (so UTF-16 key order is byte order) and
//! the member order below is that sorted order, fixed at compile time.
//!
//! [`ascii_sorted_unique`] is the success predicate of `_validate_ascii_sorted_unique`: callers
//! hand a failing set back to the Python reference, which raises the exact refusal.

use crate::protocol::canonical::{
    self as canonical, MAX_SAFE_INTEGER, Reason, encode_str_into, push_int, sha256_prefixed,
};

/// The record fields the preimage reads, as the reference's frozen JSON would hold them.
#[derive(Debug, Clone, Copy)]
pub struct EntryPreimage<'a> {
    pub protocol: &'a str,
    pub protocol_version: &'a str,
    pub event_id: &'a str,
    pub task_id: &'a str,
    pub session_id: &'a str,
    pub schema_name: &'a str,
    pub schema_version: &'a str,
    pub actor_id: &'a str,
    pub actor_type: &'a str,
    pub assurance: &'a str,
    pub writer_id: &'a str,
    /// Rendered with `str(int)` by the reference.
    pub writer_sequence: i64,
    pub writer_previous_entry_digest: &'a str,
    /// Rendered with `str(int)` by the reference.
    pub ingestion_sequence: i64,
    pub ledger_previous_entry_digest: &'a str,
    pub accepted_at: &'a str,
    pub operation_id: &'a str,
    pub occurred_at: &'a str,
    pub causal_parents: &'a [&'a str],
    pub publication_channel: &'a str,
    pub coverage: CoveragePreimage<'a>,
    pub payload_object_id: &'a str,
    pub payload_media_type: &'a str,
    /// Encoded as a JSON integer; must sit inside the safe-integer profile.
    pub payload_plaintext_size: i64,
    pub payload_commitment: &'a str,
    pub payload_encryption_format: &'a str,
    pub redaction: &'a str,
    pub artifact_refs: &'a [&'a str],
    pub evidence_refs: &'a [&'a str],
}

/// `coverage_to_json(coverage)` members.
#[derive(Debug, Clone, Copy)]
pub struct CoveragePreimage<'a> {
    pub publication_channels: &'a [&'a str],
    pub authorship_assurance: &'a str,
    pub artifact_observation: &'a str,
    pub evidence_immutability: &'a str,
    pub ledger_freshness: &'a str,
    pub check_types: &'a [&'a str],
    pub known_gaps: &'a [&'a str],
}

fn key(out: &mut Vec<u8>, name: &str, first: bool) {
    if !first {
        out.push(b',');
    }
    out.push(b'"');
    out.extend_from_slice(name.as_bytes());
    out.extend_from_slice(b"\":");
}

fn string_array(out: &mut Vec<u8>, items: &[&str]) -> Result<(), Reason> {
    out.push(b'[');
    for (index, item) in items.iter().enumerate() {
        if index > 0 {
            out.push(b',');
        }
        encode_str_into(out, item)?;
    }
    out.push(b']');
    Ok(())
}

fn integer_string(out: &mut Vec<u8>, value: i64) {
    // `str(int)` output is a plain decimal literal: nothing in it needs escaping.
    out.push(b'"');
    push_int(out, value);
    out.push(b'"');
}

/// The canonical bytes of the accepted-record digest preimage.
pub fn encode_entry_preimage(record: &EntryPreimage<'_>) -> Result<Vec<u8>, Reason> {
    canonical::check_safe_integer(record.payload_plaintext_size)?;
    let mut out = Vec::with_capacity(1536);
    out.push(b'{');
    key(&mut out, "artifact_refs", true);
    string_array(&mut out, record.artifact_refs)?;
    key(&mut out, "author", false);
    out.push(b'{');
    key(&mut out, "actor_id", true);
    encode_str_into(&mut out, record.actor_id)?;
    key(&mut out, "actor_type", false);
    encode_str_into(&mut out, record.actor_type)?;
    key(&mut out, "assurance", false);
    encode_str_into(&mut out, record.assurance)?;
    out.push(b'}');
    key(&mut out, "causal_parents", false);
    string_array(&mut out, record.causal_parents)?;
    key(&mut out, "coverage", false);
    let coverage = &record.coverage;
    out.push(b'{');
    key(&mut out, "artifact_observation", true);
    encode_str_into(&mut out, coverage.artifact_observation)?;
    key(&mut out, "authorship_assurance", false);
    encode_str_into(&mut out, coverage.authorship_assurance)?;
    key(&mut out, "check_types", false);
    string_array(&mut out, coverage.check_types)?;
    key(&mut out, "evidence_immutability", false);
    encode_str_into(&mut out, coverage.evidence_immutability)?;
    key(&mut out, "known_gaps", false);
    string_array(&mut out, coverage.known_gaps)?;
    key(&mut out, "ledger_freshness", false);
    encode_str_into(&mut out, coverage.ledger_freshness)?;
    key(&mut out, "publication_channels", false);
    string_array(&mut out, coverage.publication_channels)?;
    out.push(b'}');
    key(&mut out, "event_id", false);
    encode_str_into(&mut out, record.event_id)?;
    key(&mut out, "evidence_refs", false);
    string_array(&mut out, record.evidence_refs)?;
    key(&mut out, "ledger", false);
    out.push(b'{');
    key(&mut out, "accepted_at", true);
    encode_str_into(&mut out, record.accepted_at)?;
    key(&mut out, "ingestion_sequence", false);
    integer_string(&mut out, record.ingestion_sequence);
    key(&mut out, "previous_entry_digest", false);
    encode_str_into(&mut out, record.ledger_previous_entry_digest)?;
    out.push(b'}');
    key(&mut out, "occurred_at", false);
    encode_str_into(&mut out, record.occurred_at)?;
    key(&mut out, "operation_id", false);
    encode_str_into(&mut out, record.operation_id)?;
    key(&mut out, "payload_ref", false);
    out.push(b'{');
    key(&mut out, "commitment", true);
    encode_str_into(&mut out, record.payload_commitment)?;
    key(&mut out, "encryption_format", false);
    encode_str_into(&mut out, record.payload_encryption_format)?;
    key(&mut out, "media_type", false);
    encode_str_into(&mut out, record.payload_media_type)?;
    key(&mut out, "object_id", false);
    encode_str_into(&mut out, record.payload_object_id)?;
    key(&mut out, "plaintext_size", false);
    push_int(&mut out, record.payload_plaintext_size);
    out.push(b'}');
    key(&mut out, "protocol", false);
    encode_str_into(&mut out, record.protocol)?;
    key(&mut out, "protocol_version", false);
    encode_str_into(&mut out, record.protocol_version)?;
    key(&mut out, "publication_channel", false);
    encode_str_into(&mut out, record.publication_channel)?;
    key(&mut out, "redaction", false);
    encode_str_into(&mut out, record.redaction)?;
    key(&mut out, "schema", false);
    out.push(b'{');
    key(&mut out, "name", true);
    encode_str_into(&mut out, record.schema_name)?;
    key(&mut out, "version", false);
    encode_str_into(&mut out, record.schema_version)?;
    out.push(b'}');
    key(&mut out, "session_id", false);
    encode_str_into(&mut out, record.session_id)?;
    key(&mut out, "task_id", false);
    encode_str_into(&mut out, record.task_id)?;
    key(&mut out, "writer", false);
    out.push(b'{');
    key(&mut out, "previous_entry_digest", true);
    encode_str_into(&mut out, record.writer_previous_entry_digest)?;
    key(&mut out, "sequence", false);
    integer_string(&mut out, record.writer_sequence);
    key(&mut out, "writer_id", false);
    encode_str_into(&mut out, record.writer_id)?;
    out.push(b'}');
    out.push(b'}');
    Ok(out)
}

/// `entry_digest(accepted_record_digest_preimage(record))` for a record whose preimage passes
/// the accepted-envelope gate (`protocol == "yoetz.event"`).
///
/// `Err` means the reference would refuse (or the protocol gate fails); callers then run the
/// reference, which raises its exact error.
pub fn entry_digest(record: &EntryPreimage<'_>) -> Result<String, Reason> {
    if record.protocol != "yoetz.event" {
        return Err(canonical::NOT_AN_ACCEPTED_ENVELOPE);
    }
    let bytes = encode_entry_preimage(record)?;
    Ok(sha256_prefixed(&bytes))
}

/// Whether `_validate_ascii_sorted_unique` accepts these members (ASCII, strictly increasing).
pub fn ascii_sorted_unique<'a, I>(members: I) -> bool
where
    I: IntoIterator<Item = &'a str>,
{
    let mut previous: Option<&[u8]> = None;
    for member in members {
        let bytes = member.as_bytes();
        if !bytes.is_ascii() {
            return false;
        }
        if previous.is_some_and(|prior| bytes <= prior) {
            return false;
        }
        previous = Some(bytes);
    }
    true
}

/// Integers the preimage renders must stay inside the range the reference can produce.
pub const MAX_PREIMAGE_INTEGER: i64 = MAX_SAFE_INTEGER;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::canonical::{Value, encode};

    fn strings(items: &[&str]) -> Value {
        Value::Array(
            items
                .iter()
                .map(|item| Value::Str((*item).to_owned()))
                .collect(),
        )
    }

    fn object(members: &[(&str, Value)]) -> Value {
        Value::Object(
            members
                .iter()
                .map(|(name, value)| ((*name).to_owned(), value.clone()))
                .collect(),
        )
    }

    fn text(value: &str) -> Value {
        Value::Str(value.to_owned())
    }

    fn sample() -> EntryPreimage<'static> {
        EntryPreimage {
            protocol: "yoetz.event",
            protocol_version: "0.1",
            event_id: "evt_1b4e28ba-2fa1-41d2-883f-0016d3cca427",
            task_id: "tsk_1b4e28ba-2fa1-41d2-883f-0016d3cca427",
            session_id: "ses_1b4e28ba-2fa1-41d2-883f-0016d3cca427",
            schema_name: "session_opened",
            schema_version: "0.1.0",
            actor_id: "agent:\"quoted\"\\x",
            actor_type: "logical_agent",
            assurance: "self_asserted",
            writer_id: "wrt_1b4e28ba-2fa1-41d2-883f-0016d3cca427",
            writer_sequence: 12,
            writer_previous_entry_digest: "genesis",
            ingestion_sequence: 9_007_199_254_740_993,
            ledger_previous_entry_digest: "genesis",
            accepted_at: "2026-01-01T00:00:00.000Z",
            operation_id: "req_1b4e28ba-2fa1-41d2-883f-0016d3cca427",
            occurred_at: "2026-01-01T00:00:00.000Z",
            causal_parents: &["evt_a", "evt_b"],
            publication_channel: "cooperative_mcp",
            coverage: CoveragePreimage {
                publication_channels: &["cooperative_mcp"],
                authorship_assurance: "self_asserted",
                artifact_observation: "published_only",
                evidence_immutability: "metadata_only",
                ledger_freshness: "current",
                check_types: &["none"],
                known_gaps: &[],
            },
            payload_object_id: "obj_1b4e28ba-2fa1-41d2-883f-0016d3cca427",
            payload_media_type: "application/vnd.yoetz.session-opened+json",
            payload_plaintext_size: 123,
            payload_commitment: "hmac-sha256:00",
            payload_encryption_format: "yoetz-object/1",
            redaction: "present",
            artifact_refs: &[],
            evidence_refs: &["evd_x", "res_y"],
        }
    }

    fn reference(record: &EntryPreimage<'_>) -> Value {
        let coverage = &record.coverage;
        object(&[
            ("protocol", text(record.protocol)),
            ("protocol_version", text(record.protocol_version)),
            ("event_id", text(record.event_id)),
            ("task_id", text(record.task_id)),
            ("session_id", text(record.session_id)),
            (
                "schema",
                object(&[
                    ("name", text(record.schema_name)),
                    ("version", text(record.schema_version)),
                ]),
            ),
            (
                "author",
                object(&[
                    ("actor_id", text(record.actor_id)),
                    ("actor_type", text(record.actor_type)),
                    ("assurance", text(record.assurance)),
                ]),
            ),
            (
                "writer",
                object(&[
                    ("writer_id", text(record.writer_id)),
                    ("sequence", Value::Str(record.writer_sequence.to_string())),
                    (
                        "previous_entry_digest",
                        text(record.writer_previous_entry_digest),
                    ),
                ]),
            ),
            (
                "ledger",
                object(&[
                    (
                        "ingestion_sequence",
                        Value::Str(record.ingestion_sequence.to_string()),
                    ),
                    (
                        "previous_entry_digest",
                        text(record.ledger_previous_entry_digest),
                    ),
                    ("accepted_at", text(record.accepted_at)),
                ]),
            ),
            ("operation_id", text(record.operation_id)),
            ("occurred_at", text(record.occurred_at)),
            ("causal_parents", strings(record.causal_parents)),
            ("publication_channel", text(record.publication_channel)),
            (
                "coverage",
                object(&[
                    (
                        "publication_channels",
                        strings(coverage.publication_channels),
                    ),
                    ("authorship_assurance", text(coverage.authorship_assurance)),
                    ("artifact_observation", text(coverage.artifact_observation)),
                    (
                        "evidence_immutability",
                        text(coverage.evidence_immutability),
                    ),
                    ("ledger_freshness", text(coverage.ledger_freshness)),
                    ("check_types", strings(coverage.check_types)),
                    ("known_gaps", strings(coverage.known_gaps)),
                ]),
            ),
            (
                "payload_ref",
                object(&[
                    ("object_id", text(record.payload_object_id)),
                    ("media_type", text(record.payload_media_type)),
                    ("plaintext_size", Value::Int(record.payload_plaintext_size)),
                    ("commitment", text(record.payload_commitment)),
                    ("encryption_format", text(record.payload_encryption_format)),
                ]),
            ),
            ("redaction", text(record.redaction)),
            ("artifact_refs", strings(record.artifact_refs)),
            ("evidence_refs", strings(record.evidence_refs)),
        ])
    }

    #[test]
    fn preimage_bytes_match_the_generic_canonical_encoder() {
        let record = sample();
        let expected = encode(&reference(&record)).unwrap();
        assert_eq!(encode_entry_preimage(&record).unwrap(), expected);
        assert_eq!(
            entry_digest(&record).unwrap(),
            canonical::entry_digest(&reference(&record)).unwrap()
        );
    }

    #[test]
    fn preimage_refuses_what_the_reference_refuses() {
        let mut record = sample();
        record.payload_plaintext_size = MAX_PREIMAGE_INTEGER + 1;
        assert!(encode_entry_preimage(&record).is_err());
        let mut record = sample();
        record.actor_id = "nul\u{0}";
        assert!(encode_entry_preimage(&record).is_err());
        let mut record = sample();
        record.protocol = "other";
        assert!(entry_digest(&record).is_err());
    }

    #[test]
    fn ascii_sorted_unique_matches_the_reference_predicate() {
        assert!(ascii_sorted_unique([]));
        assert!(ascii_sorted_unique(["a", "b", "ba"]));
        assert!(!ascii_sorted_unique(["b", "a"]));
        assert!(!ascii_sorted_unique(["a", "a"]));
        assert!(!ascii_sorted_unique(["\u{e9}"]));
        assert!(!ascii_sorted_unique(["a", "\u{e9}"]));
    }
}
