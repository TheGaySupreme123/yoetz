//! Pure twins of `yoetz.adapters.integrations.observation_local` helpers.
//!
//! The store's orchestration (locks, batches, atomic writes, clocks, limits) stays in Python;
//! these are the pure computations its hot paths repeat: the dedup ring order and fair eviction
//! choice, and the canonical digest of a flat JSON object whose values are already encoded.

use std::collections::HashMap;
use std::collections::HashSet;
use std::hash::{BuildHasherDefault, Hash, Hasher};

use crate::protocol::canonical::{Reason, encode_str_into, sha256_prefixed, utf16_cmp};

/// `LocalObservationStore._ordered_dedup_keys`: positions into `order` of the first occurrence of
/// every key still present (`present[i]`), followed by the remaining members of the live set.
///
/// `order` holds the durable insertion order; `present[i]` says whether `order[i]` is in the live
/// set. `extra` are live-set members, and the ones not already taken from `order` are appended
/// in UTF-8 byte order (`sorted(..., key=str.encode)`). The result names each key by where it
/// came from so the caller can return the reference's own objects.
pub fn ordered_dedup_keys<'a>(
    order: &[&'a str],
    present: &[bool],
    extra: &[&'a str],
) -> Vec<DedupSource> {
    let mut seen: HashSet<&str> = HashSet::with_capacity(order.len());
    let mut ordered = Vec::with_capacity(order.len().max(extra.len()));
    for (index, key) in order.iter().enumerate() {
        if present[index] && seen.insert(key) {
            ordered.push(DedupSource::Order(index));
        }
    }
    let mut rest: Vec<usize> = (0..extra.len())
        .filter(|&index| !seen.contains(extra[index]))
        .collect();
    rest.sort_by(|&left, &right| extra[left].as_bytes().cmp(extra[right].as_bytes()));
    ordered.extend(rest.into_iter().map(DedupSource::Extra));
    ordered
}

/// Where one ordered dedup key came from.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DedupSource {
    Order(usize),
    Extra(usize),
}

/// A string with a precomputed hash (for example CPython's cached `hash(str)`).
///
/// Equality is by text; the hash must come from one function that gives equal texts equal
/// hashes, so lookups never rehash the text.
#[derive(Clone, Copy, Debug)]
pub struct HashedStr<'a> {
    pub hash: u64,
    pub text: &'a str,
}

impl PartialEq for HashedStr<'_> {
    fn eq(&self, other: &Self) -> bool {
        self.text == other.text
    }
}

impl Eq for HashedStr<'_> {}

impl Hash for HashedStr<'_> {
    fn hash<H: Hasher>(&self, state: &mut H) {
        state.write_u64(self.hash);
    }
}

/// A hasher that passes a precomputed `u64` through.
#[derive(Default)]
pub struct Precomputed(u64);

impl Hasher for Precomputed {
    fn finish(&self) -> u64 {
        self.0
    }

    fn write(&mut self, bytes: &[u8]) {
        for &byte in bytes {
            self.0 = self.0.rotate_left(8) ^ u64::from(byte);
        }
    }

    fn write_u64(&mut self, value: u64) {
        self.0 = value;
    }
}

/// `LocalObservationStore._select_dedup_eviction_key` over already ordered keys.
///
/// `lanes[i]` is the lane the reference counts `keys[i]` under: its recorded lane, or
/// `"_unknown:<key>"` when it has none (which can coincide with a real lane of that spelling).
/// Returns the position of the oldest key whose lane holds more than one key, else the first
/// key, else `None` for an empty ring.
pub fn dedup_eviction_position(lanes: &[HashedStr<'_>]) -> Option<usize> {
    if lanes.is_empty() {
        return None;
    }
    let mut counts: HashMap<HashedStr<'_>, usize, BuildHasherDefault<Precomputed>> =
        HashMap::with_capacity_and_hasher(lanes.len(), BuildHasherDefault::default());
    for lane in lanes {
        *counts.entry(*lane).or_insert(0) += 1;
    }
    for (index, lane) in lanes.iter().enumerate() {
        if counts[lane] > 1 {
            return Some(index);
        }
    }
    Some(0)
}

/// Canonical digest of a flat JSON object given each member's key and canonical value text.
///
/// Keys are sorted by UTF-16 code units, as canonical encoding requires. The caller has already
/// validated every key and value in the reference's (insertion) order.
pub fn flat_object_digest(members: &mut [(&str, Vec<u8>)]) -> Result<String, Reason> {
    members.sort_by(|left, right| utf16_cmp(left.0, right.0));
    let capacity = members
        .iter()
        .map(|(key, value)| key.len() + value.len() + 4)
        .sum::<usize>()
        + 2;
    let mut out = Vec::with_capacity(capacity);
    out.push(b'{');
    for (position, (key, value)) in members.iter().enumerate() {
        if position > 0 {
            out.push(b',');
        }
        encode_str_into(&mut out, key)?;
        out.push(b':');
        out.extend_from_slice(value);
    }
    out.push(b'}');
    Ok(sha256_prefixed(&out))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ordered_keys_keep_first_occurrence_then_sorted_rest() {
        let order = ["b", "a", "b", "gone", "c"];
        let present = [true, true, true, false, true];
        let extra = ["z", "a", "y", "b", "c"];
        let got = ordered_dedup_keys(&order, &present, &extra);
        assert_eq!(
            got,
            vec![
                DedupSource::Order(0),
                DedupSource::Order(1),
                DedupSource::Order(4),
                DedupSource::Extra(2),
                DedupSource::Extra(0),
            ]
        );
    }

    fn lanes<'a>(texts: &[&'a str]) -> Vec<HashedStr<'a>> {
        texts
            .iter()
            .map(|text| HashedStr {
                hash: text.len() as u64,
                text,
            })
            .collect()
    }

    #[test]
    fn eviction_prefers_oldest_key_of_an_overrepresented_lane() {
        assert_eq!(dedup_eviction_position(&[]), None);
        assert_eq!(dedup_eviction_position(&lanes(&["a", "b"])), Some(0));
        assert_eq!(dedup_eviction_position(&lanes(&["a", "b", "b"])), Some(1));
        // Equal hashes, different texts: still distinct lanes.
        assert_eq!(
            dedup_eviction_position(&lanes(&["a", "c", "b", "b"])),
            Some(2)
        );
        assert_eq!(
            dedup_eviction_position(&lanes(&["_unknown:k1", "_unknown:k1"])),
            Some(0)
        );
    }

    #[test]
    fn flat_digest_sorts_members() {
        let mut members = vec![("b", b"1".to_vec()), ("a", b"\"x\"".to_vec())];
        assert_eq!(
            flat_object_digest(&mut members).unwrap(),
            sha256_prefixed(br#"{"a":"x","b":1}"#)
        );
    }
}
