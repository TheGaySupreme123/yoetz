//! Captured-content kernels of `yoetz.application.observation_coordinator._capture_content`.
//!
//! * [`encode_manifest`] is `canonical_encode(JsonObject({... "content_b64":
//!   base64.b64encode(content).decode("ascii")}))` plus the content's `sha256:` digest.
//! * [`verify_manifest`] is the read-back check: the bytes must be exactly the canonical
//!   encoding of the nine-key manifest object (`strict_json_parse` plus the canonical round
//!   trip), name the fixed format and media type, carry a string `content_b64` and a boolean
//!   `redacted`, and `base64.b64decode(content_b64, validate=True)` must succeed. It answers
//!   only when every check passes and the base64 text has the plain padded shape; any other
//!   input is left to the reference, which reports the exact outcome.

use super::super::protocol::canonical::{self as canonical, Reason, Value};
use super::super::protocol::canonical_check::is_canonical_json_bytes;
use super::super::protocol::json;

pub const MANIFEST_FORMAT: &str = "yoetz.observation-content/1";
pub const MANIFEST_MEDIA_TYPE: &str = "text/plain";
pub const MANIFEST_KEYS: [&str; 9] = [
    "format",
    "content_kind",
    "correlation_identity",
    "source_commitment",
    "media_type",
    "part_index",
    "part_count",
    "redacted",
    "content_b64",
];

const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/// `base64.b64encode(data).decode("ascii")`.
pub fn b64_encode(data: &[u8]) -> String {
    let mut out = Vec::with_capacity(data.len().div_ceil(3) * 4);
    let mut chunks = data.chunks_exact(3);
    for chunk in &mut chunks {
        let word = (u32::from(chunk[0]) << 16) | (u32::from(chunk[1]) << 8) | u32::from(chunk[2]);
        out.push(ALPHABET[(word >> 18) as usize & 63]);
        out.push(ALPHABET[(word >> 12) as usize & 63]);
        out.push(ALPHABET[(word >> 6) as usize & 63]);
        out.push(ALPHABET[word as usize & 63]);
    }
    match chunks.remainder() {
        [first] => {
            let word = u32::from(*first) << 16;
            out.push(ALPHABET[(word >> 18) as usize & 63]);
            out.push(ALPHABET[(word >> 12) as usize & 63]);
            out.extend_from_slice(b"==");
        }
        [first, second] => {
            let word = (u32::from(*first) << 16) | (u32::from(*second) << 8);
            out.push(ALPHABET[(word >> 18) as usize & 63]);
            out.push(ALPHABET[(word >> 12) as usize & 63]);
            out.push(ALPHABET[(word >> 6) as usize & 63]);
            out.push(b'=');
        }
        _ => {}
    }
    // Every byte pushed is from the ASCII alphabet or '='.
    String::from_utf8(out).unwrap_or_default()
}

fn sextet(byte: u8) -> Option<u32> {
    Some(u32::from(match byte {
        b'A'..=b'Z' => byte - b'A',
        b'a'..=b'z' => byte - b'a' + 26,
        b'0'..=b'9' => byte - b'0' + 52,
        b'+' => 62,
        b'/' => 63,
        _ => return None,
    }))
}

/// `base64.b64decode(text, validate=True)` for the plain padded shape (alphabet characters,
/// then exactly the padding the data length needs), or `None` for anything else. CPython's
/// strict decoder accepts every such text and, like this one, ignores non-zero trailing bits.
pub fn b64_decode_padded(text: &[u8]) -> Option<Vec<u8>> {
    let data_len = text.iter().position(|&byte| byte == b'=').unwrap_or(text.len());
    let (data, padding) = text.split_at(data_len);
    let expected_padding = match data.len() % 4 {
        0 => 0,
        2 => 2,
        3 => 1,
        _ => return None,
    };
    if padding.len() != expected_padding || padding.iter().any(|&byte| byte != b'=') {
        return None;
    }
    let mut out = Vec::with_capacity(data.len() / 4 * 3 + 2);
    let mut chunks = data.chunks_exact(4);
    for chunk in &mut chunks {
        let word = (sextet(chunk[0])? << 18) | (sextet(chunk[1])? << 12) | (sextet(chunk[2])? << 6) | sextet(chunk[3])?;
        out.push((word >> 16) as u8);
        out.push((word >> 8) as u8);
        out.push(word as u8);
    }
    match chunks.remainder() {
        [first, second] => {
            let word = (sextet(*first)? << 18) | (sextet(*second)? << 12);
            out.push((word >> 16) as u8);
        }
        [first, second, third] => {
            let word = (sextet(*first)? << 18) | (sextet(*second)? << 12) | (sextet(*third)? << 6);
            out.push((word >> 16) as u8);
            out.push((word >> 8) as u8);
        }
        _ => {}
    }
    Some(out)
}

/// The scalar fields of one captured-content chunk.
pub struct ManifestFields<'a> {
    pub content_kind: &'a str,
    pub correlation_identity: &'a str,
    pub source_commitment: &'a str,
    pub media_type: &'a str,
    pub part_index: i64,
    pub part_count: i64,
    pub redacted: bool,
}

/// The canonical manifest bytes and `sha256:` content digest for one chunk.
pub fn encode_manifest(fields: &ManifestFields<'_>, content: &[u8]) -> Result<(Vec<u8>, String), Reason> {
    let value = Value::Object(vec![
        ("format".to_owned(), Value::Str(MANIFEST_FORMAT.to_owned())),
        ("content_kind".to_owned(), Value::Str(fields.content_kind.to_owned())),
        ("correlation_identity".to_owned(), Value::Str(fields.correlation_identity.to_owned())),
        ("source_commitment".to_owned(), Value::Str(fields.source_commitment.to_owned())),
        ("media_type".to_owned(), Value::Str(fields.media_type.to_owned())),
        ("part_index".to_owned(), Value::Int(fields.part_index)),
        ("part_count".to_owned(), Value::Int(fields.part_count)),
        ("redacted".to_owned(), Value::Bool(fields.redacted)),
        ("content_b64".to_owned(), Value::Str(b64_encode(content))),
    ]);
    let encoded = canonical::encode(&value)?;
    Ok((encoded, canonical::sha256_prefixed(content)))
}

/// What a verified manifest yields (the reference reads these from the parsed object).
pub struct VerifiedManifest {
    pub content_kind: Value,
    pub part_index: Value,
    pub part_count: Value,
    pub redacted: bool,
    pub correlation_identity: Value,
    pub source_commitment: Value,
    pub content_digest: String,
    pub content_bytes: usize,
}

fn take(members: &mut [(String, Value)], key: &str) -> Option<Value> {
    members.iter_mut().find(|(name, _)| name == key).map(|(_, value)| std::mem::replace(value, Value::Null))
}

/// Verify one stored manifest object, or `None` to leave the input to the reference.
pub fn verify_manifest(material: &[u8]) -> Option<VerifiedManifest> {
    if !is_canonical_json_bytes(material) {
        return None;
    }
    let Ok(Value::Object(mut members)) = json::parse(material) else {
        return None;
    };
    // Canonical objects carry no duplicate keys, so nine members naming nine keys is set equality.
    if members.len() != MANIFEST_KEYS.len() || !members.iter().all(|(key, _)| MANIFEST_KEYS.contains(&key.as_str())) {
        return None;
    }
    let format = take(&mut members, "format")?;
    let media_type = take(&mut members, "media_type")?;
    if format.as_str() != Some(MANIFEST_FORMAT) || media_type.as_str() != Some(MANIFEST_MEDIA_TYPE) {
        return None;
    }
    let Value::Str(encoded) = take(&mut members, "content_b64")? else {
        return None;
    };
    let Value::Bool(redacted) = take(&mut members, "redacted")? else {
        return None;
    };
    let content = b64_decode_padded(encoded.as_bytes())?;
    Some(VerifiedManifest {
        content_kind: take(&mut members, "content_kind")?,
        part_index: take(&mut members, "part_index")?,
        part_count: take(&mut members, "part_count")?,
        redacted,
        correlation_identity: take(&mut members, "correlation_identity")?,
        source_commitment: take(&mut members, "source_commitment")?,
        content_digest: canonical::sha256_prefixed(&content),
        content_bytes: content.len(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_round_trip() {
        for length in 0..40 {
            let data: Vec<u8> = (0..length).map(|index| (index * 37 + 11) as u8).collect();
            let text = b64_encode(&data);
            assert_eq!(b64_decode_padded(text.as_bytes()).unwrap(), data);
        }
        assert_eq!(b64_encode(b"A"), "QQ==");
        assert_eq!(b64_decode_padded(b"QR==").unwrap(), b"A");
        assert_eq!(b64_decode_padded(b"").unwrap(), b"");
        for bad in [&b"QQ="[..], b"QQ", b"QQ===", b"Q===", b"====", b"QUI=x", b"QQ==QQ==", b"=QQ=", b"QU=D", b"QQ= =", b"Q\nQ="] {
            assert!(b64_decode_padded(bad).is_none(), "{bad:?}");
        }
    }

    #[test]
    fn manifest_round_trip() {
        let fields = ManifestFields {
            content_kind: "tool_output",
            correlation_identity: "hook:1:call",
            source_commitment: "hmac-sha256:aa",
            media_type: "text/plain",
            part_index: 0,
            part_count: 1,
            redacted: false,
        };
        let (encoded, digest) = encode_manifest(&fields, b"hello").unwrap();
        let verified = verify_manifest(&encoded).unwrap();
        assert_eq!(verified.content_digest, digest);
        assert_eq!(verified.content_bytes, 5);
        assert_eq!(verified.content_kind, Value::Str("tool_output".to_owned()));
        assert!(verify_manifest(&encoded[1..]).is_none());
    }
}
