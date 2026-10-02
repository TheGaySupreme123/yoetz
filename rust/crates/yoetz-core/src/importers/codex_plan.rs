//! Batch partition of `CodexImportPlans.prepare` (`yoetz.adapters.importers.codex_plan`).
//!
//! The reference walks `range(0, draft_count, batch_size)`; each batch holds
//! `candidates[start:end]` and selects, in input order:
//!
//! * every line outcome whose `candidate_indexes` intersect the batch's candidate indexes;
//! * every gap overlapping some batch candidate span, where overlap is
//!   `not (gap.byte_end <= left or gap.byte_start >= right)`.
//!
//! The twin indexes candidates once (index value to batches, and per-batch spans sorted by
//! start with a running maximum end), so each outcome costs its own index count and each gap
//! one binary search per batch instead of a scan of every candidate span.

use std::collections::HashMap;

/// One candidate: `(candidate_index, byte_start, byte_end)`.
pub type Candidate = (i64, i64, i64);

/// The positions (into the reference's outcome and gap sequences) one batch selects.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct BatchSelection {
    pub outcomes: Vec<usize>,
    pub gaps: Vec<usize>,
}

struct BatchSpans {
    /// Span starts, ascending.
    starts: Vec<i64>,
    /// `max(end)` over the spans at or before each position of `starts`.
    reach: Vec<i64>,
}

impl BatchSpans {
    fn new(members: &[Candidate]) -> Self {
        let mut spans: Vec<(i64, i64)> = members.iter().map(|&(_, start, end)| (start, end)).collect();
        spans.sort_unstable();
        let mut reach = Vec::with_capacity(spans.len());
        let mut furthest = i64::MIN;
        for &(_, end) in &spans {
            furthest = furthest.max(end);
            reach.push(furthest);
        }
        BatchSpans { starts: spans.into_iter().map(|(start, _)| start).collect(), reach }
    }

    /// Some span satisfies `start < gap_end and end > gap_start`.
    fn overlaps(&self, gap_start: i64, gap_end: i64) -> bool {
        let below = self.starts.partition_point(|&start| start < gap_end);
        below > 0 && self.reach[below - 1] > gap_start
    }
}

/// Partition `candidates` into batches and select each batch's outcomes and gaps.
///
/// Returns `None` when `batch_size` is not positive (the reference's `range` would raise or
/// yield nothing in a way the caller must observe itself).
pub fn partition_batches<I: AsRef<[i64]>>(
    candidates: &[Candidate],
    draft_count: usize,
    batch_size: i64,
    outcome_indexes: &[I],
    gaps: &[(i64, i64)],
) -> Option<Vec<BatchSelection>> {
    if batch_size <= 0 {
        return None;
    }
    let size = usize::try_from(batch_size).ok()?;
    let batch_count = draft_count.div_ceil(size);
    let mut members: Vec<&[Candidate]> = Vec::with_capacity(batch_count);
    for batch in 0..batch_count {
        let start = batch * size;
        let end = start.saturating_add(size).min(draft_count);
        let low = start.min(candidates.len());
        let high = end.min(candidates.len());
        members.push(&candidates[low..high]);
    }

    // candidate_index value -> the batches holding it, ascending and without repeats.
    let mut holders: HashMap<i64, Vec<usize>> = HashMap::with_capacity(candidates.len().min(draft_count));
    for (batch, slice) in members.iter().enumerate() {
        for &(index, _, _) in slice.iter() {
            let entry = holders.entry(index).or_default();
            if entry.last() != Some(&batch) {
                entry.push(batch);
            }
        }
    }

    let mut selections: Vec<BatchSelection> = (0..batch_count).map(|_| BatchSelection::default()).collect();
    // Last outcome (1-based) appended per batch, so one outcome joins a batch once.
    let mut marker = vec![0_usize; batch_count];
    for (position, indexes) in outcome_indexes.iter().enumerate() {
        for index in indexes.as_ref() {
            if let Some(batches) = holders.get(index) {
                for &batch in batches {
                    if marker[batch] != position + 1 {
                        marker[batch] = position + 1;
                        selections[batch].outcomes.push(position);
                    }
                }
            }
        }
    }

    let spans: Vec<BatchSpans> = members.iter().map(|slice| BatchSpans::new(slice)).collect();
    for (position, &(gap_start, gap_end)) in gaps.iter().enumerate() {
        for (batch, batch_spans) in spans.iter().enumerate() {
            if batch_spans.overlaps(gap_start, gap_end) {
                selections[batch].gaps.push(position);
            }
        }
    }
    Some(selections)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The reference's quadratic filter, for comparison.
    fn reference(
        candidates: &[Candidate],
        draft_count: usize,
        size: usize,
        outcomes: &[Vec<i64>],
        gaps: &[(i64, i64)],
    ) -> Vec<BatchSelection> {
        let mut result = Vec::new();
        let mut start = 0;
        while start < draft_count {
            let end = (start + size).min(draft_count);
            let slice = &candidates[start.min(candidates.len())..end.min(candidates.len())];
            let outcome_hits = outcomes
                .iter()
                .enumerate()
                .filter(|(_, indexes)| indexes.iter().any(|index| slice.iter().any(|c| c.0 == *index)))
                .map(|(position, _)| position)
                .collect();
            let gap_hits = gaps
                .iter()
                .enumerate()
                .filter(|(_, gap)| slice.iter().any(|c| !(gap.1 <= c.1 || gap.0 >= c.2)))
                .map(|(position, _)| position)
                .collect();
            result.push(BatchSelection { outcomes: outcome_hits, gaps: gap_hits });
            start += size;
        }
        result
    }

    #[test]
    fn matches_the_quadratic_reference() {
        let mut seed = 0x2545_f491_4f6c_dd1d_u64;
        let mut next = |bound: u64| {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            (seed % bound) as i64
        };
        for _ in 0..200 {
            let count = next(40) as usize;
            let candidates: Vec<Candidate> = (0..count)
                .map(|position| {
                    let start = next(500);
                    (if next(5) == 0 { next(10) } else { position as i64 }, start, start + 1 + next(30))
                })
                .collect();
            let drafts = if next(4) == 0 { count + next(10) as usize } else { count };
            let size = 1 + next(7) as usize;
            let outcomes: Vec<Vec<i64>> =
                (0..next(30)).map(|_| (0..next(4)).map(|_| next(45)).collect()).collect();
            let gaps: Vec<(i64, i64)> = (0..next(30))
                .map(|_| {
                    let start = next(520);
                    (start, start + 1 + next(40))
                })
                .collect();
            assert_eq!(
                partition_batches(&candidates, drafts, size as i64, &outcomes, &gaps).unwrap(),
                reference(&candidates, drafts, size, &outcomes, &gaps)
            );
        }
    }

    #[test]
    fn refuses_a_non_positive_batch_size() {
        let no_outcomes: [Vec<i64>; 0] = [];
        assert!(partition_batches(&[], 0, 0, &no_outcomes, &[]).is_none());
        assert_eq!(partition_batches(&[], 0, 100, &no_outcomes, &[]), Some(Vec::new()));
    }
}
