//! Timestamp parsing of `yoetz.domain.values` (`parse_rfc3339_millis`, `Timestamp`).
//!
//! The reference accepts exactly the texts `protocol::timestamp::is_wire_timestamp` accepts:
//! the closed `YYYY-MM-DDTHH:MM:SS.mmmZ` pattern, then what `datetime.strptime` and the
//! `datetime` constructor admit (years 0001-9999, months 01-12, a day that exists in its
//! month, hours 00-23, minutes 00-59, and seconds 00-59: `%S` matches 60 and 61 but the
//! constructor refuses them). The format round trip it then performs always holds for such a
//! text, so the components below are the parsed `datetime`'s fields.

use super::super::protocol::timestamp::is_wire_timestamp;

/// `(year, month, day, hour, minute, second, microsecond)` of a valid wire timestamp.
pub type TimestampParts = (u16, u8, u8, u8, u8, u8, u32);

fn number(bytes: &[u8]) -> u32 {
    bytes
        .iter()
        .fold(0, |total, &byte| total * 10 + u32::from(byte - b'0'))
}

/// The parsed fields of `text`, or `None` when the reference refuses it.
pub fn parse_wire_timestamp(text: &str) -> Option<TimestampParts> {
    if !is_wire_timestamp(text) {
        return None;
    }
    let bytes = text.as_bytes();
    Some((
        number(&bytes[0..4]) as u16,
        number(&bytes[5..7]) as u8,
        number(&bytes[8..10]) as u8,
        number(&bytes[11..13]) as u8,
        number(&bytes[14..16]) as u8,
        number(&bytes[17..19]) as u8,
        number(&bytes[20..23]) * 1000,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parts() {
        assert_eq!(
            parse_wire_timestamp("2024-02-29T23:59:59.999Z"),
            Some((2024, 2, 29, 23, 59, 59, 999_000))
        );
        assert_eq!(
            parse_wire_timestamp("0001-01-01T00:00:00.001Z"),
            Some((1, 1, 1, 0, 0, 0, 1000))
        );
        for refused in [
            "0000-01-01T00:00:00.000Z",
            "2023-02-29T00:00:00.000Z",
            "2023-01-01T00:00:60.000Z",
            "2023-01-01T00:00:61.000Z",
            "2023-00-01T00:00:00.000Z",
            "2023-01-32T00:00:00.000Z",
            "2023-01-01T00:00:00.000z",
            "\u{661}023-01-01T00:00:00.000Z",
        ] {
            assert_eq!(parse_wire_timestamp(refused), None, "{refused}");
        }
    }
}
