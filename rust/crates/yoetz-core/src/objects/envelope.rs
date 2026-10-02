//! Acceptance core of `yoetz.adapters.objects.envelope` (strict `yoetz-object/1` framing).
//!
//! Every function here answers one question: does the Python reference *accept* this input, and
//! if so, with which decoded values? A `None` never carries a reason: the binding defers every
//! refusal to the Python reference, which raises the exact exception (and exception chain) the
//! contract specifies. Acceptance must therefore be a subset of the reference's acceptance, and
//! accepted values must equal the reference's values exactly.

use crate::protocol::canonical::{self, Value};
use crate::protocol::json;

pub const MAGIC: &[u8; 4] = b"YZO1";
pub const VERSION: u8 = 1;
pub const NONCE_BYTES: usize = 12;
pub const TAG_BYTES: usize = 16;
pub const WRAPPED_DEK_BYTES: usize = 40;
const WRAPPED_DEK_CHARS: usize = 54;

pub const ENCRYPTION_FORMAT: &str = "yoetz-object/1";
pub const PAYLOAD_ALGORITHM: &str = "aes-256-gcm";
pub const WRAP_ALGORITHM: &str = "aes-256-kw-rfc3394";

/// The header's member names (`_HEADER_KEYS`).
pub const HEADER_KEYS: [&str; 11] = [
    "created_at",
    "encryption_format",
    "key_slot",
    "media_type",
    "object_id",
    "object_kind",
    "payload_algorithm",
    "plaintext_size",
    "task_id",
    "wrap_algorithm",
    "wrapped_dek",
];

/// The calendar fields of an accepted `created_at`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CreatedAt {
    pub year: i32,
    pub month: u8,
    pub day: u8,
    pub hour: u8,
    pub minute: u8,
    pub second: u8,
    pub microsecond: u32,
}

fn digits(bytes: &[u8]) -> Option<u32> {
    let mut value = 0u32;
    for &byte in bytes {
        if !byte.is_ascii_digit() {
            return None;
        }
        value = value * 10 + u32::from(byte - b'0');
    }
    Some(value)
}

fn is_leap(year: u32) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

fn days_in_month(year: u32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ if is_leap(year) => 29,
        _ => 28,
    }
}

/// `_created_at_from_wire` acceptance: the ASCII wire pattern
/// `^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$`, then exactly what
/// `datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")` admits for those digits (year 1..=9999,
/// month 1..=12, a real calendar day, hour 0..=23, minute 0..=59, and second 0..=59, since the
/// strptime pattern's leap seconds 60 and 61 are refused by the `datetime` constructor), then
/// whole milliseconds.
pub fn created_at_from_wire(text: &str) -> Option<CreatedAt> {
    let bytes = text.as_bytes();
    if bytes.len() != 27
        || bytes[4] != b'-'
        || bytes[7] != b'-'
        || bytes[10] != b'T'
        || bytes[13] != b':'
        || bytes[16] != b':'
        || bytes[19] != b'.'
        || bytes[26] != b'Z'
    {
        return None;
    }
    let year = digits(&bytes[0..4])?;
    let month = digits(&bytes[5..7])?;
    let day = digits(&bytes[8..10])?;
    let hour = digits(&bytes[11..13])?;
    let minute = digits(&bytes[14..16])?;
    let second = digits(&bytes[17..19])?;
    let microsecond = digits(&bytes[20..26])?;
    if !(1..=9999).contains(&year)
        || !(1..=12).contains(&month)
        || day < 1
        || day > days_in_month(year, month)
        || hour > 23
        || minute > 59
        || second > 59
        || microsecond % 1000 != 0
    {
        return None;
    }
    Some(CreatedAt {
        year: year as i32,
        month: month as u8,
        day: day as u8,
        hour: hour as u8,
        minute: minute as u8,
        second: second as u8,
        microsecond,
    })
}

fn is_key_slot_char(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-')
}

/// `_KEY_SLOT_PATTERN`: `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$` (ASCII).
pub fn is_key_slot(text: &str) -> bool {
    let bytes = text.as_bytes();
    match bytes.split_first() {
        Some((first, rest)) => {
            first.is_ascii_alphanumeric()
                && rest.len() <= 127
                && rest.iter().all(|&b| is_key_slot_char(b))
        }
        None => false,
    }
}

fn is_media_char(byte: u8) -> bool {
    byte.is_ascii_lowercase()
        || byte.is_ascii_digit()
        || matches!(
            byte,
            b'!' | b'#' | b'$' | b'&' | b'^' | b'_' | b'.' | b'+' | b'-'
        )
}

/// `ObjectMetadata` media-type acceptance: at most 128 characters and
/// `^[a-z][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$` (ASCII). Neither character
/// class admits `/`, so the first `/` is the only separator the pattern can match.
pub fn is_media_type(text: &str) -> bool {
    let bytes = text.as_bytes();
    if bytes.len() > 128 {
        return false;
    }
    let Some(slash) = bytes.iter().position(|&b| b == b'/') else {
        return false;
    };
    let (kind, rest) = (&bytes[..slash], &bytes[slash + 1..]);
    let kind_ok = match kind.split_first() {
        Some((first, tail)) => first.is_ascii_lowercase() && tail.iter().all(|&b| is_media_char(b)),
        None => false,
    };
    let sub_ok = match rest.split_first() {
        Some((first, tail)) => {
            (first.is_ascii_lowercase() || first.is_ascii_digit())
                && tail.len() <= 126
                && tail.iter().all(|&b| is_media_char(b))
        }
        None => false,
    };
    kind_ok && sub_ok
}

fn base64url_value(byte: u8) -> Option<u32> {
    match byte {
        b'A'..=b'Z' => Some(u32::from(byte - b'A')),
        b'a'..=b'z' => Some(u32::from(byte - b'a') + 26),
        b'0'..=b'9' => Some(u32::from(byte - b'0') + 52),
        b'-' => Some(62),
        b'_' => Some(63),
        _ => None,
    }
}

/// `_decode_wrapped_dek`: 54 unpadded base64url characters decoding to 40 bytes whose
/// re-encoding is the input (so the final character's four spare bits are zero).
pub fn decode_wrapped_dek(text: &str) -> Option<[u8; WRAPPED_DEK_BYTES]> {
    let bytes = text.as_bytes();
    if bytes.len() != WRAPPED_DEK_CHARS {
        return None;
    }
    let mut out = [0u8; WRAPPED_DEK_BYTES];
    let mut written = 0;
    for quad in bytes[..52].chunks_exact(4) {
        let mut word = 0u32;
        for &byte in quad {
            word = (word << 6) | base64url_value(byte)?;
        }
        out[written] = (word >> 16) as u8;
        out[written + 1] = (word >> 8) as u8;
        out[written + 2] = word as u8;
        written += 3;
    }
    let high = base64url_value(bytes[52])?;
    let low = base64url_value(bytes[53])?;
    if low & 0x0F != 0 {
        return None;
    }
    out[written] = ((high << 2) | (low >> 4)) as u8;
    Some(out)
}

/// An accepted header's members.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Header {
    pub created_at: String,
    pub created: CreatedAt,
    pub key_slot: String,
    pub media_type: String,
    pub object_id: String,
    pub object_kind: String,
    pub plaintext_size: u64,
    pub task_id: String,
    pub wrapped_dek: [u8; WRAPPED_DEK_BYTES],
}

fn take_str(value: Value) -> Option<String> {
    match value {
        Value::Str(text) => Some(text),
        _ => None,
    }
}

/// The header checks that need no Python: everything except the object/task identifier
/// grammars (the binding calls the Python validators) and the object-kind enumeration (the
/// binding looks the value up in the live `ObjectKind`).
pub fn header_from_value(value: Value, max_plaintext: u64) -> Option<Header> {
    let Value::Object(members) = value else {
        return None;
    };
    if members.len() != HEADER_KEYS.len() {
        return None;
    }
    let mut slots: [Option<Value>; 11] = Default::default();
    for (key, member) in members {
        let index = HEADER_KEYS.iter().position(|name| *name == key)?;
        slots[index] = Some(member);
    }
    let mut slots = slots.map(|slot| slot.expect("keys are unique and complete"));
    let mut take = |index: usize| std::mem::replace(&mut slots[index], Value::Null);
    let created_at = take_str(take(0))?;
    let created = created_at_from_wire(&created_at)?;
    if take(1).as_str() != Some(ENCRYPTION_FORMAT) {
        return None;
    }
    let key_slot = take_str(take(2))?;
    let media_type = take_str(take(3))?;
    let object_id = take_str(take(4))?;
    let object_kind = take_str(take(5))?;
    if take(6).as_str() != Some(PAYLOAD_ALGORITHM) {
        return None;
    }
    let plaintext_size = u64::try_from(take(7).as_i64()?).ok()?;
    let task_id = take_str(take(8))?;
    if take(9).as_str() != Some(WRAP_ALGORITHM) {
        return None;
    }
    let wrapped_dek = decode_wrapped_dek(take(10).as_str()?)?;
    if !is_key_slot(&key_slot) || !is_media_type(&media_type) || plaintext_size > max_plaintext {
        return None;
    }
    Some(Header {
        created_at,
        created,
        key_slot,
        media_type,
        object_id,
        object_kind,
        plaintext_size,
        task_id,
        wrapped_dek,
    })
}

/// An accepted frame: the header plus byte ranges of `data`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    pub header: Header,
    pub header_end: usize,
    pub nonce_end: usize,
    pub ciphertext_end: usize,
}

/// `decode_object_envelope` acceptance over the frame layout
/// `YZO1|01|u32be(header_len)|header|nonce12|ciphertext(plaintext_size)|tag16`, with a strict,
/// canonical JSON header.
pub fn decode_frame(data: &[u8], max_header: usize, max_plaintext: u64) -> Option<Frame> {
    if data.len() < 4 + 1 + 4 + 1 + NONCE_BYTES + TAG_BYTES {
        return None;
    }
    if &data[..4] != MAGIC || data[4] != VERSION {
        return None;
    }
    let header_length = u32::from_be_bytes([data[5], data[6], data[7], data[8]]) as usize;
    if header_length < 1 || header_length > max_header {
        return None;
    }
    let header_end = 9 + header_length;
    if header_end > data.len() {
        return None;
    }
    let header_bytes = &data[9..header_end];
    let parsed = json::parse(header_bytes).ok()?;
    if canonical::encode(&parsed).ok()? != header_bytes {
        return None;
    }
    let header = header_from_value(parsed, max_plaintext)?;
    let size = usize::try_from(header.plaintext_size).ok()?;
    let expected = header_end
        .checked_add(NONCE_BYTES + TAG_BYTES)?
        .checked_add(size)?;
    if data.len() != expected {
        return None;
    }
    let nonce_end = header_end + NONCE_BYTES;
    Some(Frame {
        header,
        header_end,
        nonce_end,
        ciphertext_end: nonce_end + size,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn created_at_matches_strptime_rules() {
        assert!(created_at_from_wire("2024-02-29T23:59:59.999000Z").is_some());
        assert!(created_at_from_wire("2000-02-29T00:00:00.000000Z").is_some());
        assert!(created_at_from_wire("0001-01-01T00:00:00.000000Z").is_some());
        for refused in [
            "2023-02-29T00:00:00.000000Z",
            "1900-02-29T00:00:00.000000Z",
            "2024-04-31T00:00:00.000000Z",
            "0000-01-01T00:00:00.000000Z",
            "2024-00-01T00:00:00.000000Z",
            "2024-13-01T00:00:00.000000Z",
            "2024-01-00T00:00:00.000000Z",
            "2024-01-01T24:00:00.000000Z",
            "2024-01-01T00:60:00.000000Z",
            "2024-01-01T00:00:60.000000Z",
            "2024-01-01T00:00:61.000000Z",
            "2024-01-01T00:00:00.000001Z",
            "2024-01-01T00:00:00.000000z",
            "2024-01-01T00:00:00.000Z",
            "2024-01-01 00:00:00.000000Z",
            "２024-01-01T00:00:00.000000Z",
        ] {
            assert_eq!(created_at_from_wire(refused), None, "{refused}");
        }
    }

    #[test]
    fn patterns() {
        assert!(is_key_slot("bmk-1"));
        assert!(is_key_slot(&"a".repeat(128)));
        assert!(!is_key_slot(&"a".repeat(129)));
        assert!(!is_key_slot("-a"));
        assert!(!is_key_slot(""));
        assert!(is_media_type("application/octet-stream"));
        assert!(is_media_type("text/x.y+z"));
        assert!(!is_media_type("Text/plain"));
        assert!(!is_media_type("text/"));
        assert!(!is_media_type("/plain"));
        assert!(!is_media_type("text/plain/x"));
        assert!(!is_media_type("text/-x"));
        assert!(is_media_type(&format!("a/{}", "b".repeat(126))));
        assert!(!is_media_type(&format!("a/{}", "b".repeat(127))));
    }

    #[test]
    fn wrapped_dek_round_trip_is_canonical() {
        let text = "vSonaujHRkx-izlmdKxuDpVYyExgCbP6QTzwamei AII-TXIN8kGfqQ".replace(' ', "");
        assert_eq!(text.len(), 54);
        assert!(decode_wrapped_dek(&text).is_some());
        let mut noncanonical = text.clone();
        noncanonical.replace_range(53.., "R");
        assert_eq!(decode_wrapped_dek(&noncanonical), None);
    }
}
