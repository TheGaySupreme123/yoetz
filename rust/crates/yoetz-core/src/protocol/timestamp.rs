//! RFC 3339 millisecond UTC timestamps (`YYYY-MM-DDTHH:MM:SS.mmmZ`), the Rust twin of
//! `yoetz.protocol.models._timestamp_wire`: the closed pattern, then the calendar check
//! `datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")` performs (year 1 or later, a day that
//! exists in its month).

fn digits(bytes: &[u8]) -> Option<u32> {
    bytes.iter().try_fold(0_u32, |total, &byte| {
        byte.is_ascii_digit()
            .then(|| total * 10 + u32::from(byte - b'0'))
    })
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

/// Whether `text` is a valid wire timestamp.
pub fn is_wire_timestamp(text: &str) -> bool {
    let bytes = text.as_bytes();
    if bytes.len() != 24
        || bytes[4] != b'-'
        || bytes[7] != b'-'
        || bytes[10] != b'T'
        || bytes[13] != b':'
        || bytes[16] != b':'
        || bytes[19] != b'.'
        || bytes[23] != b'Z'
    {
        return false;
    }
    let (Some(year), Some(month), Some(day), Some(hour), Some(minute), Some(second), Some(_millis)) = (
        digits(&bytes[0..4]),
        digits(&bytes[5..7]),
        digits(&bytes[8..10]),
        digits(&bytes[11..13]),
        digits(&bytes[14..16]),
        digits(&bytes[17..19]),
        digits(&bytes[20..23]),
    ) else {
        return false;
    };
    // The pattern: months 01-12, days 01-31, hours 00-23, minutes and seconds 00-59.
    if !(1..=12).contains(&month)
        || !(1..=31).contains(&day)
        || hour > 23
        || minute > 59
        || second > 59
    {
        return false;
    }
    // strptime: year 0 is out of range, and the day must exist in its month.
    year >= 1 && day <= days_in_month(year, month)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timestamps() {
        assert!(is_wire_timestamp("2024-02-29T23:59:59.999Z"));
        assert!(is_wire_timestamp("0001-01-01T00:00:00.000Z"));
        assert!(!is_wire_timestamp("2023-02-29T00:00:00.000Z"));
        assert!(!is_wire_timestamp("2023-04-31T00:00:00.000Z"));
        assert!(!is_wire_timestamp("0000-01-01T00:00:00.000Z"));
        assert!(!is_wire_timestamp("2023-13-01T00:00:00.000Z"));
        assert!(!is_wire_timestamp("2023-01-00T00:00:00.000Z"));
        assert!(!is_wire_timestamp("2023-01-01T24:00:00.000Z"));
        assert!(!is_wire_timestamp("2023-01-01T00:60:00.000Z"));
        assert!(!is_wire_timestamp("2023-01-01T00:00:60.000Z"));
        assert!(!is_wire_timestamp("2023-01-01T00:00:00.000Z\n"));
        assert!(!is_wire_timestamp("2023-01-01T00:00:00.00Z"));
        assert!(!is_wire_timestamp("2023-01-01 00:00:00.000Z"));
        assert!(is_wire_timestamp("1900-02-28T00:00:00.000Z"));
        assert!(!is_wire_timestamp("1900-02-29T00:00:00.000Z"));
        assert!(is_wire_timestamp("2000-02-29T00:00:00.000Z"));
    }
}
