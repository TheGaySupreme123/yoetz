//! Envelope rules of `yoetz.kernel.policies.observation_advice`, over pre-flattened envelopes.
//!
//! The Python pack walks every session envelope about ten times per hook, reading each payload
//! field through `Mapping.get`. This module runs the envelope-walking rules once over plain
//! structs the binding flattened, and reports which envelopes each rule cites, in the exact order
//! the reference builds them. The binding then builds the same candidates the reference builds.
//! Rules that read no envelopes (observation gaps, provider readiness, semantic attention) and
//! every digest stay in Python.
//!
//! [`scan`] returns `None` for an input the reference would refuse (an observed run whose command
//! commitment is malformed raises in `ObservedRun`); the binding then runs the reference.

use std::collections::{HashMap, HashSet};

/// The source/session/generation-fenced correlation key of one tool stream.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct CorrelationKey {
    pub source: String,
    pub session: String,
    pub generation: i64,
    pub raw: String,
}

/// One envelope, reduced to the structural fields the rules read.
#[derive(Clone, Debug, Default)]
pub struct Envelope {
    pub event_kind: String,
    /// `_tool_correlation_key`, or `None` when the chosen raw key is not a `str`.
    pub key: Option<CorrelationKey>,
    /// `tool_name` when it is an exact `str`.
    pub tool: Option<String>,
    /// `exit_status` when it is an exact `int`.
    pub exit_status: Option<i64>,
    /// `success` when it is a `bool`.
    pub success: Option<bool>,
    pub claim_kind: Option<String>,
    pub result_status: Option<String>,
    /// `denied is True`.
    pub denied: bool,
    /// `command_commitment` when it is an exact `str` starting with `hmac-sha256:`.
    pub command_commitment: Option<String>,
    pub changed_paths_digest: Option<String>,
    pub mapping_hint: Option<String>,
    pub subagent_id: Option<String>,
    /// `action` when it is an exact `str` (the binding defers any other non-null value).
    pub action: Option<String>,
    /// `attempt is not None`.
    pub attempt_present: bool,
    /// Python `str.lower()` of `claim_kind or ""`, `mapping_hint or ""` and `tool_name or ""`.
    pub claim_lower: String,
    pub hint_lower: String,
    pub tool_lower: String,
    pub event_position: i64,
}

/// One approved-check fact: `status == "passed" and is_current`, and its cursor position.
#[derive(Clone, Copy, Debug)]
pub struct CheckFact {
    pub passed_current: bool,
    pub cursor_event_position: i64,
}

/// The module constants the rules read, as the Python module defines them.
#[derive(Clone, Debug, Default)]
pub struct Vocabulary {
    pub edit_tools: HashSet<String>,
    pub verification_tools: HashSet<String>,
    pub command_tools: HashSet<String>,
    pub routine_read_actions: HashSet<String>,
    pub static_check_hints: Vec<String>,
    pub live_claim_hints: Vec<String>,
    pub post_tool_event_kinds: HashSet<String>,
    pub originating_tool_actions: HashSet<String>,
}

/// Envelope indices each rule cites, in the reference's order.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Scan {
    /// `_failed_commands`: each live unresolved failure, in `unresolved` insertion order.
    pub failed: Vec<usize>,
    /// `_edits_after_check`: the phases of each stale edit group, in `grouped` insertion order.
    pub edits_after_check: Vec<Vec<usize>>,
    /// `_completion_without_verification`: the completion claims, when the rule fires.
    pub completion_without_verification: Option<Vec<usize>>,
    /// `_static_for_live`: live claims then static support, when the rule fires.
    pub static_for_live: Option<Vec<usize>>,
    /// `_subagent_unaddressed`: the finding envelope of each unaddressed subagent, in order.
    pub subagent_unaddressed: Vec<usize>,
    /// `_outside_plan` inputs: every envelope carrying a `changed_paths_digest`, in order.
    pub changed_paths: Vec<usize>,
    /// `_semantic_without_attempt`: the semantic claims, when the rule fires.
    pub semantic_without_attempt: Option<Vec<usize>>,
}

const PRE_TOOL_EVENT_KINDS: [&str; 2] = ["PreToolUse", "preToolUse"];
const COMMITMENT_PREFIX: &str = "hmac-sha256:";

fn is_pre_tool(envelope: &Envelope) -> bool {
    PRE_TOOL_EVENT_KINDS.contains(&envelope.event_kind.as_str())
}

/// `kernel.observed_failures._COMMITMENT_PATTERN`: `\Ahmac-sha256:[0-9a-f]{64}\Z`.
pub fn is_observed_command_identity(value: &str) -> bool {
    value.len() == COMMITMENT_PREFIX.len() + 64
        && value.starts_with(COMMITMENT_PREFIX)
        && value.as_bytes()[COMMITMENT_PREFIX.len()..]
            .iter()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Outcome {
    Failure,
    Success,
    Unknown,
}

struct Run<'a> {
    envelope: usize,
    outcome: Outcome,
    identity: Option<&'a str>,
    edit: bool,
}

/// `kernel.observed_failures.classify_observed_runs`, reporting only which failed runs are live.
fn live_failures(runs: &[Run<'_>]) -> HashSet<usize> {
    // Runs are appended in envelope order and positions are unique, so the backward pass is the
    // reverse of the append order.
    let mut edited_after = false;
    let mut passed_after: HashSet<&str> = HashSet::new();
    let mut ran_after: HashSet<&str> = HashSet::new();
    let mut live = HashSet::new();
    for run in runs.iter().rev() {
        if run.outcome == Outcome::Failure {
            let superseded = run
                .identity
                .is_some_and(|identity| passed_after.contains(identity));
            let rerun = run
                .identity
                .is_some_and(|identity| ran_after.contains(identity));
            if !superseded && !rerun && !edited_after {
                live.insert(run.envelope);
            }
        }
        if run.edit && run.outcome == Outcome::Success {
            edited_after = true;
        }
        if let Some(identity) = run.identity {
            ran_after.insert(identity);
            if run.outcome == Outcome::Success {
                passed_after.insert(identity);
            }
        }
    }
    live
}

/// An insertion-ordered map with Python `dict` semantics: assigning an existing key keeps its
/// position, and a popped key re-inserted goes to the end.
struct OrderedMap<K, V> {
    slots: Vec<Option<(K, V)>>,
    index: HashMap<K, usize>,
}

impl<K: Clone + Eq + std::hash::Hash, V> OrderedMap<K, V> {
    fn new() -> Self {
        OrderedMap {
            slots: Vec::new(),
            index: HashMap::new(),
        }
    }

    fn insert(&mut self, key: K, value: V) {
        match self.index.get(&key) {
            Some(&slot) => {
                if let Some(entry) = self.slots[slot].as_mut() {
                    entry.1 = value;
                }
            }
            None => {
                self.index.insert(key.clone(), self.slots.len());
                self.slots.push(Some((key, value)));
            }
        }
    }

    fn pop(&mut self, key: &K) {
        if let Some(slot) = self.index.remove(key) {
            self.slots[slot] = None;
        }
    }

    fn get_mut_or_insert_with(&mut self, key: &K, make: impl FnOnce() -> V) -> &mut V {
        let slot = match self.index.get(key) {
            Some(&slot) => slot,
            None => {
                self.index.insert(key.clone(), self.slots.len());
                self.slots.push(Some((key.clone(), make())));
                self.slots.len() - 1
            }
        };
        &mut self.slots[slot].as_mut().expect("indexed slot is live").1
    }

    fn values(&self) -> impl Iterator<Item = &V> {
        self.slots.iter().flatten().map(|(_, value)| value)
    }
}

struct Rules<'a> {
    envelopes: &'a [Envelope],
    vocabulary: &'a Vocabulary,
    /// Per envelope: `_resolved_tool` (tool, key present).
    resolved: Vec<Option<&'a str>>,
}

impl<'a> Rules<'a> {
    fn new(envelopes: &'a [Envelope], vocabulary: &'a Vocabulary) -> Self {
        // `_tool_resolution`: a same-key originating call wins (last one), else the first tool.
        let mut originating: HashMap<&CorrelationKey, &str> = HashMap::new();
        let mut fallback: HashMap<&CorrelationKey, &str> = HashMap::new();
        for envelope in envelopes {
            let (Some(key), Some(tool)) = (&envelope.key, &envelope.tool) else {
                continue;
            };
            if envelope
                .action
                .as_deref()
                .is_some_and(|action| vocabulary.originating_tool_actions.contains(action))
            {
                originating.insert(key, tool);
            } else {
                fallback.entry(key).or_insert(tool);
            }
        }
        let resolved = envelopes
            .iter()
            .map(|envelope| {
                let key = envelope.key.as_ref()?;
                if let Some(origin) = originating.get(key) {
                    return Some(*origin);
                }
                // `_tool(envelope) or fallback.get(key)`: an empty tool name is falsy.
                match envelope.tool.as_deref() {
                    Some(tool) if !tool.is_empty() => Some(tool),
                    _ => fallback.get(key).copied(),
                }
            })
            .collect();
        Rules {
            envelopes,
            vocabulary,
            resolved,
        }
    }

    fn is_routine_read(&self, envelope: &Envelope) -> bool {
        envelope
            .action
            .as_deref()
            .is_some_and(|action| self.vocabulary.routine_read_actions.contains(action))
    }

    fn observed_check_success(&self, envelope: &Envelope, tool: Option<&str>) -> bool {
        let Some(tool) = tool else {
            return false;
        };
        if !self.vocabulary.verification_tools.contains(tool) {
            return false;
        }
        if self.is_routine_read(envelope) || is_pre_tool(envelope) {
            return false;
        }
        envelope.exit_status == Some(0) || envelope.success == Some(true)
    }

    fn is_edit_envelope(&self, envelope: &Envelope, tool: Option<&str>) -> bool {
        if tool.is_some_and(|tool| self.vocabulary.edit_tools.contains(tool)) {
            return true;
        }
        matches!(
            envelope.action.as_deref(),
            Some("write" | "edit" | "delete")
        ) || envelope.changed_paths_digest.is_some()
    }

    fn is_post_tool(&self, envelope: &Envelope) -> bool {
        self.vocabulary
            .post_tool_event_kinds
            .contains(&envelope.event_kind)
    }

    fn failed_commands(&self) -> Option<Vec<usize>> {
        let mut unresolved: OrderedMap<&CorrelationKey, usize> = OrderedMap::new();
        let mut runs: Vec<Run<'a>> = Vec::new();
        for (position, envelope) in self.envelopes.iter().enumerate() {
            let Some(key) = &envelope.key else {
                continue;
            };
            if is_pre_tool(envelope) {
                continue;
            }
            let tool = self.resolved[position];
            let failed = envelope.exit_status.is_some_and(|status| status != 0)
                || envelope.success == Some(false);
            let passed =
                !failed && (envelope.exit_status == Some(0) || envelope.success == Some(true));
            if tool.is_some_and(|tool| self.vocabulary.command_tools.contains(tool)) {
                let identity = envelope.command_commitment.as_deref();
                let mut run = |outcome| -> Option<()> {
                    // `ObservedRun` refuses a malformed identity; the reference raises there.
                    if identity.is_some_and(|value| !is_observed_command_identity(value)) {
                        return None;
                    }
                    runs.push(Run {
                        envelope: position,
                        outcome,
                        identity,
                        edit: false,
                    });
                    Some(())
                };
                if failed {
                    unresolved.insert(key, position);
                    run(Outcome::Failure)?;
                } else if passed {
                    unresolved.pop(&key);
                    run(Outcome::Success)?;
                } else if identity.is_some() && self.is_post_tool(envelope) {
                    run(Outcome::Unknown)?;
                }
            } else if self.is_post_tool(envelope)
                && self.is_edit_envelope(envelope, tool)
                && passed
                && !envelope.denied
            {
                runs.push(Run {
                    envelope: position,
                    outcome: Outcome::Success,
                    identity: None,
                    edit: true,
                });
            }
        }
        let live = live_failures(&runs);
        Some(
            unresolved
                .values()
                .copied()
                .filter(|position| live.contains(position))
                .collect(),
        )
    }

    fn edits_after_check(&self, checks: &[CheckFact]) -> Vec<Vec<usize>> {
        let mut last_success: Option<i64> = None;
        for (position, envelope) in self.envelopes.iter().enumerate() {
            if self.observed_check_success(envelope, self.resolved[position]) {
                last_success = Some(envelope.event_position);
            }
        }
        for check in checks {
            if check.passed_current {
                last_success = Some(last_success.unwrap_or(0).max(check.cursor_event_position));
            }
        }
        let Some(last_success) = last_success else {
            return Vec::new();
        };
        let mut grouped: OrderedMap<&CorrelationKey, Vec<usize>> = OrderedMap::new();
        for (position, envelope) in self.envelopes.iter().enumerate() {
            let Some(key) = &envelope.key else {
                continue;
            };
            if !self.is_edit_envelope(envelope, self.resolved[position]) {
                continue;
            }
            grouped
                .get_mut_or_insert_with(&key, Vec::new)
                .push(position);
        }
        grouped
            .values()
            .filter(|phases| {
                phases
                    .iter()
                    .any(|phase| self.envelopes[*phase].event_position > last_success)
            })
            .cloned()
            .collect()
    }

    fn completion_without_verification(&self, checks: &[CheckFact]) -> Option<Vec<usize>> {
        let refs: Vec<usize> = (0..self.envelopes.len())
            .filter(|position| {
                matches!(
                    self.envelopes[*position].claim_kind.as_deref(),
                    Some("completion" | "done" | "finished")
                )
            })
            .collect();
        if refs.is_empty() {
            return None;
        }
        let has_pass = checks.iter().any(|check| check.passed_current);
        let has_observed_pass = self
            .envelopes
            .iter()
            .enumerate()
            .any(|(position, envelope)| {
                self.observed_check_success(envelope, self.resolved[position])
            });
        if has_pass || has_observed_pass {
            return None;
        }
        Some(refs)
    }

    fn static_for_live(&self) -> Option<Vec<usize>> {
        let mut live_claims = Vec::new();
        let mut static_support = Vec::new();
        for (position, envelope) in self.envelopes.iter().enumerate() {
            let blob = format!(
                "{}:{}:{}",
                envelope.claim_lower, envelope.hint_lower, envelope.tool_lower
            );
            if self
                .vocabulary
                .live_claim_hints
                .iter()
                .any(|token| blob.contains(token.as_str()))
            {
                live_claims.push(position);
            }
            if self
                .vocabulary
                .static_check_hints
                .iter()
                .any(|token| blob.contains(token.as_str()))
                && !self.is_routine_read(envelope)
                && (envelope.exit_status == Some(0) || envelope.success == Some(true))
            {
                static_support.push(position);
            }
        }
        if live_claims.is_empty() || static_support.is_empty() {
            return None;
        }
        let live_verified = self.envelopes.iter().any(|envelope| {
            envelope.hint_lower.contains("live")
                && self.observed_check_success(envelope, envelope.tool.as_deref())
        });
        if live_verified {
            return None;
        }
        live_claims.extend(static_support);
        Some(live_claims)
    }

    fn subagent_unaddressed(&self) -> Vec<usize> {
        let mut findings: OrderedMap<&str, usize> = OrderedMap::new();
        let mut addressed: HashSet<&str> = HashSet::new();
        for (position, envelope) in self.envelopes.iter().enumerate() {
            let sub = envelope.subagent_id.as_deref();
            if let Some(sub) = sub {
                if envelope.event_kind == "SubagentStop"
                    && (matches!(
                        envelope.result_status.as_deref(),
                        Some("finding" | "failed" | "issue")
                    ) || envelope.success == Some(false))
                {
                    findings.insert(sub, position);
                }
                if matches!(
                    envelope.event_kind.as_str(),
                    "PostToolUse" | "UserPromptSubmit"
                ) && matches!(
                    envelope.result_status.as_deref(),
                    Some("resolved" | "addressed" | "fixed")
                ) {
                    addressed.insert(sub);
                }
                if matches!(
                    envelope.claim_kind.as_deref(),
                    Some("resolved" | "addressed")
                ) {
                    addressed.insert(sub);
                }
            }
        }
        findings
            .slots
            .iter()
            .flatten()
            .filter(|(sub, _)| !addressed.contains(sub))
            .map(|(_, position)| *position)
            .collect()
    }

    fn semantic_without_attempt(&self) -> Option<Vec<usize>> {
        let mut claims = Vec::new();
        let mut attempted = false;
        for (position, envelope) in self.envelopes.iter().enumerate() {
            let claim = envelope.claim_lower.as_str();
            let hint = envelope.hint_lower.as_str();
            if claim.contains("semantic")
                || claim.contains("live-dispatch")
                || claim.contains("live_dispatch")
            {
                claims.push(position);
            }
            if (hint.contains("semantic") || envelope.attempt_present)
                && (hint.contains("semantic")
                    || matches!(claim, "semantic_attempt" | "dispatch_attempt"))
            {
                attempted = true;
            }
            if matches!(
                envelope.action.as_deref(),
                Some("semantic_dispatch" | "live_dispatch")
            ) {
                attempted = true;
            }
        }
        (!claims.is_empty() && !attempted).then_some(claims)
    }
}

/// Run every envelope-walking rule once. `None` means the reference would refuse the input.
pub fn scan(envelopes: &[Envelope], checks: &[CheckFact], vocabulary: &Vocabulary) -> Option<Scan> {
    let rules = Rules::new(envelopes, vocabulary);
    Some(Scan {
        failed: rules.failed_commands()?,
        edits_after_check: rules.edits_after_check(checks),
        completion_without_verification: rules.completion_without_verification(checks),
        static_for_live: rules.static_for_live(),
        subagent_unaddressed: rules.subagent_unaddressed(),
        changed_paths: (0..envelopes.len())
            .filter(|position| envelopes[*position].changed_paths_digest.is_some())
            .collect(),
        semantic_without_attempt: rules.semantic_without_attempt(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vocabulary() -> Vocabulary {
        let set = |items: &[&str]| {
            items
                .iter()
                .map(|item| item.to_string())
                .collect::<HashSet<_>>()
        };
        Vocabulary {
            edit_tools: set(&["apply_patch", "Edit"]),
            verification_tools: set(&["pytest"]),
            command_tools: set(&["pytest", "shell", "Bash"]),
            routine_read_actions: set(&["routine_read"]),
            static_check_hints: vec!["pytest".into(), "static".into()],
            live_claim_hints: vec!["live".into(), "wire".into()],
            post_tool_event_kinds: set(&["PostToolUse", "postToolUse"]),
            originating_tool_actions: set(&["function_call"]),
        }
    }

    fn key(raw: &str) -> Option<CorrelationKey> {
        Some(CorrelationKey {
            source: "codex_hook".into(),
            session: "s".into(),
            generation: 1,
            raw: raw.into(),
        })
    }

    fn post(raw: &str, tool: &str, exit_status: i64, position: i64) -> Envelope {
        Envelope {
            event_kind: "PostToolUse".into(),
            key: key(raw),
            tool: Some(tool.into()),
            exit_status: Some(exit_status),
            tool_lower: tool.to_lowercase(),
            event_position: position,
            ..Envelope::default()
        }
    }

    #[test]
    fn failure_cleared_by_same_key_success() {
        let envelopes = [post("c1", "shell", 1, 1), post("c1", "shell", 0, 2)];
        let scan = scan(&envelopes, &[], &vocabulary()).expect("scan");
        assert!(scan.failed.is_empty());
    }

    #[test]
    fn failure_stays_live_and_reinsertion_moves_to_end() {
        let envelopes = [
            post("a", "shell", 1, 1),
            post("b", "shell", 1, 2),
            post("a", "shell", 0, 3),
            post("a", "shell", 2, 4),
        ];
        let scan = scan(&envelopes, &[], &vocabulary()).expect("scan");
        assert_eq!(scan.failed, vec![1, 3]);
    }

    #[test]
    fn malformed_identity_defers_to_reference() {
        let mut failing = post("a", "shell", 1, 1);
        failing.command_commitment = Some("hmac-sha256:xyz".into());
        assert!(scan(&[failing], &[], &vocabulary()).is_none());
    }

    #[test]
    fn identity_pattern_is_exact() {
        let good = format!("hmac-sha256:{}", "a".repeat(64));
        assert!(is_observed_command_identity(&good));
        assert!(!is_observed_command_identity(&format!("{good}\n")));
        assert!(!is_observed_command_identity(&good.to_uppercase()));
    }

    #[test]
    fn edit_after_passing_check_is_stale() {
        let mut edit = post("e", "apply_patch", 0, 3);
        edit.success = Some(true);
        let envelopes = [post("t", "pytest", 0, 2), edit];
        let scan = scan(&envelopes, &[], &vocabulary()).expect("scan");
        assert_eq!(scan.edits_after_check, vec![vec![1]]);
    }
}
