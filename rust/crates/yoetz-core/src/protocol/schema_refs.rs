//! String-level pieces of `yoetz.protocol.schemas` catalog loading: the UTF-16 key order of
//! `_freeze_json`, and the reference shapes `_validate_references` can prove admissible and
//! resolvable without Python's `urllib.parse` and `referencing` (0.37, draft 2020-12).
//!
//! Every function here answers conservatively: a reference outside the narrow shape it
//! recognizes is "undecided", and the caller then runs the Python reference unchanged.

use std::cmp::Ordering;

/// `sorted(keys, key=lambda item: item.encode("utf-16-be"))` order for two keys without lone
/// surrogates: big-endian UTF-16 bytes compare exactly like UTF-16 code unit sequences.
pub fn utf16_cmp(left: &str, right: &str) -> Ordering {
    if left.is_ascii() && right.is_ascii() {
        return left.as_bytes().cmp(right.as_bytes());
    }
    left.encode_utf16().cmp(right.encode_utf16())
}

/// The document a proven-admissible reference names.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefDocument<'a> {
    /// `#...`: the referring document itself.
    Local,
    /// `https://host/path[#...]`: the catalog document whose `$id` is this base.
    External(&'a str),
}

/// The fragment of a proven-admissible reference.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefFragment<'a> {
    /// No fragment (or an empty one): the document root.
    Root,
    /// A JSON pointer starting with `/` (no percent-escapes, so `unquote` is the identity).
    Pointer(&'a str),
}

/// Characters for which `urlsplit`, `urldefrag`, `urljoin` and `unquote` are the identity on the
/// shapes below: no whitespace or control characters they strip, no `%`, `?`, `;`, `@`, `[`,
/// `]`, or `\`.
fn plain_reference_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric()
        || matches!(byte, b'#' | b'$' | b'-' | b'.' | b'/' | b':' | b'_' | b'~')
}

/// Split `reference` into its document and fragment when `_validate_references` would find it
/// admissible on syntax alone (the external base must still be a known catalog `$id`), and
/// `referencing` would resolve it to that document's root or a pointer within it. `None` means
/// undecided: an anchor fragment, a second `#`, a non-`https` or relative reference, or any
/// character outside the plain set.
pub fn split_reference(reference: &str) -> Option<(RefDocument<'_>, RefFragment<'_>)> {
    if !reference.bytes().all(plain_reference_byte) {
        return None;
    }
    let (document, fragment) = match reference.strip_prefix('#') {
        Some(fragment) => (RefDocument::Local, fragment),
        None => {
            let (base, fragment) = reference.split_once('#').unwrap_or((reference, ""));
            let rest = base.strip_prefix("https://")?;
            let netloc = rest.split('/').next().unwrap_or("");
            // `urljoin` keeps an absolute same-scheme reference as is (no dot-segment removal in
            // that branch); refusing `.` path segments keeps the proof independent of that.
            if netloc.is_empty() || base.contains("/.") {
                return None;
            }
            (RefDocument::External(base), fragment)
        }
    };
    if fragment.contains('#') {
        return None;
    }
    if fragment.is_empty() {
        return Some((document, RefFragment::Root));
    }
    if fragment.starts_with('/') {
        return Some((document, RefFragment::Pointer(fragment)));
    }
    None
}

/// `int(segment)` for a pointer step into an array, restricted to plain ASCII digits.
pub fn list_index(segment: &str) -> Option<usize> {
    if segment.is_empty()
        || segment.len() > 18
        || !segment.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    segment.parse().ok()
}

/// `segment.replace("~1", "/").replace("~0", "~")`, in `referencing`'s order.
pub fn unescape_segment(segment: &str) -> String {
    segment.replace("~1", "/").replace("~0", "~")
}

/// One step of a resolved pointer, as `referencing` records it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Segment {
    Index(usize),
    Key(String),
}

const IN_VALUE: &[&str] = &[
    "additionalProperties",
    "contains",
    "contentSchema",
    "else",
    "if",
    "items",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
];
const IN_CHILD: &[&str] = &[
    "allOf",
    "anyOf",
    "oneOf",
    "prefixItems",
    "$defs",
    "definitions",
    "dependentSchemas",
    "patternProperties",
    "properties",
];

/// Draft 2020-12 `maybe_in_subresource`: whether the walked `segments` end at a subresource, so
/// `referencing` asks the reached value for its `$id`.
pub fn is_subresource_position(segments: &[Segment]) -> bool {
    let mut iter = segments.iter();
    while let Some(segment) = iter.next() {
        let key = match segment {
            Segment::Key(key) => Some(key.as_str()),
            Segment::Index(_) => None,
        };
        let in_value = key.is_some_and(|key| IN_VALUE.contains(&key));
        if in_value {
            continue;
        }
        let in_child = key.is_some_and(|key| IN_CHILD.contains(&key));
        if !in_child || iter.next().is_none() {
            return false;
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utf16_order_differs_from_utf8_above_the_bmp() {
        // U+FF61 sorts before U+1F600 in UTF-8 but after it in UTF-16 (surrogate 0xD83D).
        assert_eq!(utf16_cmp("\u{ff61}", "\u{1f600}"), Ordering::Greater);
        assert_eq!(utf16_cmp("a", "b"), Ordering::Less);
        assert_eq!(utf16_cmp("ab", "a"), Ordering::Greater);
    }

    #[test]
    fn splits_plain_references_only() {
        assert_eq!(
            split_reference("#"),
            Some((RefDocument::Local, RefFragment::Root))
        );
        assert_eq!(
            split_reference("#/$defs/a"),
            Some((RefDocument::Local, RefFragment::Pointer("/$defs/a")))
        );
        assert_eq!(
            split_reference("https://h/x.json#/a"),
            Some((
                RefDocument::External("https://h/x.json"),
                RefFragment::Pointer("/a")
            ))
        );
        assert_eq!(
            split_reference("https://h/x.json"),
            Some((RefDocument::External("https://h/x.json"), RefFragment::Root))
        );
        assert_eq!(split_reference("#anchor"), None);
        assert_eq!(split_reference("#/a#b"), None);
        assert_eq!(split_reference("http://h/x.json"), None);
        assert_eq!(split_reference("https:///x.json"), None);
        assert_eq!(split_reference("https://h/./x.json"), None);
        assert_eq!(split_reference("https://h/x.json?q"), None);
        assert_eq!(split_reference("#/a%20b"), None);
        assert_eq!(split_reference("x.json"), None);
    }

    #[test]
    fn pointer_segments() {
        assert_eq!(list_index("01"), Some(1));
        assert_eq!(list_index("-1"), None);
        assert_eq!(list_index("1_0"), None);
        assert_eq!(list_index(""), None);
        assert_eq!(unescape_segment("a~1b~0c"), "a/b~c");
        assert_eq!(unescape_segment("~01"), "~1");
    }

    #[test]
    fn subresource_positions() {
        let key = |text: &str| Segment::Key(text.to_owned());
        assert!(is_subresource_position(&[key("$defs"), key("x")]));
        assert!(!is_subresource_position(&[key("$defs")]));
        assert!(is_subresource_position(&[key("items")]));
        assert!(is_subresource_position(&[
            key("oneOf"),
            Segment::Index(0),
            key("not")
        ]));
        assert!(!is_subresource_position(&[
            key("oneOf"),
            Segment::Index(0),
            key("enum")
        ]));
        assert!(!is_subresource_position(&[key("enum"), Segment::Index(0)]));
    }
}
