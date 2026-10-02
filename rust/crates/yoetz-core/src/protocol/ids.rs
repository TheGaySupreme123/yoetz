//! Identifier validation, the Rust twin of `yoetz.protocol.ids` (`validate_id`,
//! `validate_actor_id`).
//!
//! The checks run in the reference's order so the first refusal is the reference's: length in
//! code points, ASCII printability, prefix, the lowercase UUID shape, the version nibble, then the
//! variant nibble.

use super::canonical::Reason;

/// Every non-actor ID is a four-byte prefix plus a 36-character UUID.
pub const ID_TOTAL_LENGTH: usize = 40;
/// Actor identifiers are at most this many code points.
pub const MAX_ACTOR_ID_LENGTH: usize = 128;

pub const ID_WRONG_TYPE: Reason = "id_wrong_type";
pub const ID_WRONG_LENGTH: Reason = "id_wrong_length";
pub const ID_NOT_ASCII: Reason = "id_not_ascii";
pub const ID_WRONG_PREFIX: Reason = "id_wrong_prefix";
pub const ID_MALFORMED_UUID: Reason = "id_malformed_uuid";
pub const ID_UUID_NOT_VERSION_4: Reason = "id_uuid_not_version_4";
pub const ID_UUID_WRONG_VARIANT: Reason = "id_uuid_wrong_variant";
pub const ACTOR_ID_MALFORMED: Reason = "actor_id_malformed";

/// What the caller knows about a candidate string before the byte checks.
pub enum Candidate<'a> {
    /// The string's UTF-8 text (it holds no lone surrogate).
    Text(&'a str),
    /// The string holds a lone surrogate, so it is not ASCII.
    NotUtf8,
}

#[inline]
fn is_lower_hex(byte: u8) -> bool {
    byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
}

/// `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` over exactly 36 bytes.
pub fn is_uuid_shape(text: &[u8]) -> bool {
    text.len() == 36
        && text.iter().enumerate().all(|(index, &byte)| match index {
            8 | 13 | 18 | 23 => byte == b'-',
            _ => is_lower_hex(byte),
        })
}

/// `validate_id` after the kind and type checks: `char_count` is the string's length in code
/// points (`str.__len__`), `prefix` the kind's four-byte prefix.
pub fn validate_id_text(candidate: Candidate<'_>, char_count: usize, prefix: &str) -> Result<(), Reason> {
    if char_count != ID_TOTAL_LENGTH {
        return Err(ID_WRONG_LENGTH);
    }
    let Candidate::Text(text) = candidate else {
        return Err(ID_NOT_ASCII);
    };
    let bytes = text.as_bytes();
    if !text.is_ascii() || bytes.iter().any(|&byte| !(0x21..=0x7E).contains(&byte)) {
        return Err(ID_NOT_ASCII);
    }
    if &bytes[..4] != prefix.as_bytes() {
        return Err(ID_WRONG_PREFIX);
    }
    let uuid = &bytes[4..];
    if !is_uuid_shape(uuid) {
        return Err(ID_MALFORMED_UUID);
    }
    if uuid[14] != b'4' {
        return Err(ID_UUID_NOT_VERSION_4);
    }
    if !matches!(uuid[19], b'8' | b'9' | b'a' | b'b') {
        return Err(ID_UUID_WRONG_VARIANT);
    }
    Ok(())
}

/// `validate_actor_id` after the type check (`^[A-Za-z0-9._:-]{1,128}$`).
pub fn validate_actor_id_text(candidate: Candidate<'_>, char_count: usize) -> Result<(), Reason> {
    if char_count > MAX_ACTOR_ID_LENGTH {
        return Err(ID_WRONG_LENGTH);
    }
    let Candidate::Text(text) = candidate else {
        return Err(ACTOR_ID_MALFORMED);
    };
    let admitted = |byte: &u8| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-');
    if text.is_empty() || !text.as_bytes().iter().all(admitted) {
        return Err(ACTOR_ID_MALFORMED);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const VALID: &str = "req_00000000-0000-4000-8000-000000000001";

    fn check(text: &str) -> Result<(), Reason> {
        validate_id_text(Candidate::Text(text), text.chars().count(), "req_")
    }

    #[test]
    fn accepts_a_canonical_id() {
        assert_eq!(check(VALID), Ok(()));
    }

    #[test]
    fn refusals_follow_the_reference_order() {
        assert_eq!(check("req_"), Err(ID_WRONG_LENGTH));
        assert_eq!(check("req_00000000-0000-4000-8000-00000000000\u{e9}"), Err(ID_NOT_ASCII));
        assert_eq!(check("req_00000000-0000-4000-8000-00000000000 "), Err(ID_NOT_ASCII));
        assert_eq!(check("tsk_00000000-0000-4000-8000-000000000001"), Err(ID_WRONG_PREFIX));
        assert_eq!(check("req_00000000-0000-4000-8000-00000000000A"), Err(ID_MALFORMED_UUID));
        assert_eq!(check("req_00000000-0000-1000-8000-000000000001"), Err(ID_UUID_NOT_VERSION_4));
        assert_eq!(check("req_00000000-0000-4000-c000-000000000001"), Err(ID_UUID_WRONG_VARIANT));
        assert_eq!(validate_id_text(Candidate::NotUtf8, 40, "req_"), Err(ID_NOT_ASCII));
    }

    #[test]
    fn actor_ids() {
        let ok = |text: &str| validate_actor_id_text(Candidate::Text(text), text.chars().count());
        assert_eq!(ok("agent.one:two-3_x"), Ok(()));
        assert_eq!(ok(""), Err(ACTOR_ID_MALFORMED));
        assert_eq!(ok("bad id"), Err(ACTOR_ID_MALFORMED));
        assert_eq!(ok("trailing\n"), Err(ACTOR_ID_MALFORMED));
        assert_eq!(ok(&"a".repeat(129)), Err(ID_WRONG_LENGTH));
        assert_eq!(ok(&"a".repeat(128)), Ok(()));
    }
}
