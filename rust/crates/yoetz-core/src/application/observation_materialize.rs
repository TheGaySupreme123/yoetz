//! Deterministic identities of `yoetz.application.observation_materialize` (and the sibling
//! application modules that derive UUIDv4-shaped ids the same way).
//!
//! * [`uuid4_text`] is `str(uuid.UUID(bytes=raw))` after the version-4 and RFC 4122 variant bits
//!   are forced (`raw[6] = (raw[6] & 0x0F) | 0x40`, `raw[8] = (raw[8] & 0x3F) | 0x80`).
//! * [`stable_uuid4`] is that applied to the first 16 bytes of `sha256(material)`
//!   (`stable_observation_id`, `stable_advice_finding_id`).
//! * [`uuid4_from_hex_digest`] is that applied to `bytes.fromhex(digest.removeprefix("sha256:")
//!   [:32])` (`lineage_coordinator._stable_id`, `ObservationCoordinator._stable_operation_id`).
//!   It answers only for 32 plain hex digits; anything `bytes.fromhex` might treat differently
//!   (whitespace, a short tail) is left to the reference.
//! * [`logical_identity_digest`] is `_logical_identity_digest`.

use sha2::{Digest, Sha256};

const HEX: &[u8; 16] = b"0123456789abcdef";

/// `str(uuid.UUID(bytes=raw))` with the version and variant bits forced.
pub fn uuid4_text(mut raw: [u8; 16]) -> String {
    raw[6] = (raw[6] & 0x0F) | 0x40;
    raw[8] = (raw[8] & 0x3F) | 0x80;
    let mut out = String::with_capacity(36);
    for (index, byte) in raw.iter().enumerate() {
        if matches!(index, 4 | 6 | 8 | 10) {
            out.push('-');
        }
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0F) as usize] as char);
    }
    out
}

/// The UUIDv4 text of the first 16 bytes of `sha256(parts...)`.
pub fn stable_uuid4<'a, I: IntoIterator<Item = &'a [u8]>>(parts: I) -> String {
    let mut hasher = Sha256::new();
    for part in parts {
        hasher.update(part);
    }
    let digest = hasher.finalize();
    let mut raw = [0_u8; 16];
    raw.copy_from_slice(&digest[..16]);
    uuid4_text(raw)
}

fn hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

/// `uuid.UUID(bytes=bytes.fromhex(digest.removeprefix("sha256:")[:32]))` with the version and
/// variant forced, or `None` when the first 32 characters are not plain hex digits.
pub fn uuid4_from_hex_digest(digest: &str) -> Option<String> {
    let material = digest.strip_prefix("sha256:").unwrap_or(digest).as_bytes();
    if material.len() < 32 {
        return None;
    }
    let mut raw = [0_u8; 16];
    for (index, slot) in raw.iter_mut().enumerate() {
        let high = hex_value(material[2 * index])?;
        let low = hex_value(material[2 * index + 1])?;
        *slot = (high << 4) | low;
    }
    Some(uuid4_text(raw))
}

/// `stable_observation_id` material: `domain + f"{kind}\0{task}\0{source}\0{mapping}\0{role}"`.
pub fn stable_observation_uuid(domain: &[u8], fields: [&str; 5]) -> String {
    let mut parts: Vec<&[u8]> = Vec::with_capacity(10);
    parts.push(domain);
    for (index, field) in fields.iter().enumerate() {
        if index > 0 {
            parts.push(b"\0");
        }
        parts.push(field.as_bytes());
    }
    stable_uuid4(parts)
}

/// `"sha256:" + sha256(domain + b"\0" + b"\0".join(components)).hexdigest()`.
pub fn logical_identity_digest(domain: &str, components: &[&str]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(domain.as_bytes());
    hasher.update(b"\0");
    for (index, component) in components.iter().enumerate() {
        if index > 0 {
            hasher.update(b"\0");
        }
        hasher.update(component.as_bytes());
    }
    let mut out = String::with_capacity(71);
    out.push_str("sha256:");
    out.push_str(&hex::encode(hasher.finalize()));
    out
}

/// `prefix + sha256(material).hexdigest()[:48]` over several material parts.
pub fn prefixed_hex48<'a, I: IntoIterator<Item = &'a [u8]>>(prefix: &str, parts: I) -> String {
    let mut hasher = Sha256::new();
    for part in parts {
        hasher.update(part);
    }
    let digest = hex::encode(hasher.finalize());
    let mut out = String::with_capacity(prefix.len() + 48);
    out.push_str(prefix);
    out.push_str(&digest[..48]);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uuid_text_forces_version_and_variant() {
        assert_eq!(
            uuid4_text([0xFF; 16]),
            "ffffffff-ffff-4fff-bfff-ffffffffffff"
        );
        assert_eq!(uuid4_text([0; 16]), "00000000-0000-4000-8000-000000000000");
    }

    #[test]
    fn hex_digest_prefix() {
        let digest = format!("sha256:{}", "AB".repeat(32));
        assert_eq!(
            uuid4_from_hex_digest(&digest).unwrap(),
            "abababab-abab-4bab-abab-abababababab"
        );
        assert_eq!(
            uuid4_from_hex_digest(&"ab".repeat(16)).unwrap(),
            "abababab-abab-4bab-abab-abababababab"
        );
        assert!(uuid4_from_hex_digest("sha256:abcd").is_none());
        assert!(uuid4_from_hex_digest(&format!("ab {}", "a".repeat(40))).is_none());
        assert!(uuid4_from_hex_digest(&format!("{}g", "a".repeat(31))).is_none());
    }

    #[test]
    fn logical_digest_joins_components() {
        let joined = logical_identity_digest("d", &["a", "b"]);
        let mut hasher = Sha256::new();
        hasher.update(b"d\0a\0b");
        assert_eq!(joined, format!("sha256:{}", hex::encode(hasher.finalize())));
        let empty = logical_identity_digest("d", &[]);
        let mut hasher = Sha256::new();
        hasher.update(b"d\0");
        assert_eq!(empty, format!("sha256:{}", hex::encode(hasher.finalize())));
    }
}
