//! Pure parts of `yoetz.domain.observation_selection`: the closed tables, the shell grammar,
//! the outcome reducer, and the classification assembly.
//!
//! `str.casefold()` is ASCII lowercase only for ASCII text; non-ASCII text can fold into ASCII
//! (`"ſ"` folds to `"s"`), so every fold goes through a caller-supplied function that defers to
//! Python's own `str.casefold` for non-ASCII input.

use crate::shlex;

/// `_MAX_RESULT_JSON_BYTES`.
pub const MAX_RESULT_JSON_BYTES: usize = 65_536;
/// `_MAX_COMMAND_CHARS` (code points).
pub const MAX_COMMAND_CHARS: usize = 16_384;

pub const ROUTINE_READ_TOOLS: &[&str] = &["glob", "grep", "list_files", "read", "read_file", "search", "view_file"];
pub const SHELL_TOOLS: &[&str] =
    &["bash", "command", "exec", "exec_command", "local_shell", "run_terminal_cmd", "shell"];
pub const READ_ONLY_COMMANDS: &[&str] = &["head", "ls", "pwd", "rg", "tail", "wc"];
pub const PRE_EVENTS: &[&str] = &["PreToolUse", "preToolUse"];
pub const POST_EVENTS: &[&str] = &["PostToolUse", "postToolUse", "PostToolUseFailure", "postToolUseFailure"];
pub const SUCCESS_STATUSES: &[&str] = &["complete", "completed", "ok", "passed", "success", "succeeded"];
pub const FAILURE_STATUSES: &[(&str, &str)] = &[
    ("aborted", "failure"),
    ("canceled", "cancelled"),
    ("cancelled", "cancelled"),
    ("denied", "denied"),
    ("error", "failure"),
    ("errored", "failure"),
    ("failed", "failure"),
    ("failure", "failure"),
    ("interrupted", "cancelled"),
    ("nonzero", "failure"),
    ("nonzero_exit", "failure"),
    ("permission_denied", "denied"),
    ("timed_out", "failure"),
    ("timeout", "failure"),
];
pub const PARTIAL_STATUSES: &[&str] = &["partial", "partially_completed"];
pub const RG_PRE_OPTIONS: &[&str] = &["--pre", "--pre-glob", "--pre-files"];
pub const GIT_SIDE_EFFECT_PREFIXES: &[&str] = &["--output=", "--ext-diff=", "--textconv="];
pub const GIT_SIDE_EFFECT_OPTIONS: &[&str] = &["--ext-diff", "--output", "-o", "--textconv", "--filters"];
pub const EDIT_TOOL_HINTS: &[&str] = &[
    "apply_patch",
    "create_file",
    "delete",
    "delete_file",
    "edit",
    "insert",
    "mkdir",
    "move",
    "patch",
    "remove",
    "rename",
    "replace",
    "update_file",
    "write",
    "write_file",
];
pub const TEST_TOOL_HINTS: &[&str] = &["cargo_test", "jest", "mocha", "nox", "pytest", "test", "tox", "vitest"];
pub const VERIFICATION_TOOL_HINTS: &[&str] = &["assert", "check", "lint", "review", "typecheck", "verify"];
pub const EDIT_COMMANDS: &[&str] =
    &["apply_patch", "cp", "install", "mkdir", "mv", "perl", "rm", "rmdir", "sed", "tee", "touch"];
pub const TEST_COMMANDS: &[&str] = &[
    "cargo", "go", "jest", "make", "mocha", "mypy", "nox", "npm", "pnpm", "pytest", "pyright", "ruff", "tox", "tsc",
    "uv", "vitest", "yarn",
];
const SHELL_VERIFICATION_COMMANDS: &[&str] = &["check", "lint", "review", "verify", "typecheck"];
const GIT_READ_SUBCOMMANDS: &[&str] = &["diff", "log", "rev-parse", "show", "status"];
/// Substrings that make a shell command ambiguous (after the NUL check).
const SHELL_MARKERS: &[&str] = &["\n", "\r", ";", "&", "|", ">", "<", "`", "$("];

#[inline]
fn member(table: &[&str], value: &str) -> bool {
    table.contains(&value)
}

/// `_RoutineFacts`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RoutineFacts {
    pub candidate: bool,
    pub reason: &'static str,
}

const fn facts(candidate: bool, reason: &'static str) -> RoutineFacts {
    RoutineFacts { candidate, reason }
}

/// `_is_edit_tool_token(lowered)`.
pub fn is_edit_tool_token(lowered: &str) -> bool {
    member(EDIT_TOOL_HINTS, lowered) || ["edit", "write", "patch"].iter().any(|hint| lowered.contains(hint))
}

/// What `_routine_facts` decides from the folded tool name alone.
pub enum ToolDecision {
    Facts(RoutineFacts),
    /// A shell tool: the decision needs `_routine_shell_facts`.
    Shell,
}

/// `_routine_facts` after `tool.casefold()`.
pub fn tool_decision(lowered: &str) -> ToolDecision {
    if member(ROUTINE_READ_TOOLS, lowered) {
        return ToolDecision::Facts(facts(true, "routine_tool"));
    }
    if member(SHELL_TOOLS, lowered) {
        return ToolDecision::Shell;
    }
    if is_edit_tool_token(lowered) {
        return ToolDecision::Facts(facts(false, "edit"));
    }
    if member(TEST_TOOL_HINTS, lowered) || ["test", "pytest", "check"].iter().any(|hint| lowered.contains(hint)) {
        let reason = if lowered.contains("test") || lowered.contains("pytest") { "test" } else { "verification" };
        return ToolDecision::Facts(facts(false, reason));
    }
    if member(VERIFICATION_TOOL_HINTS, lowered)
        || ["lint", "verify", "review", "typecheck"].iter().any(|hint| lowered.contains(hint))
    {
        return ToolDecision::Facts(facts(false, "verification"));
    }
    ToolDecision::Facts(facts(false, "unknown_operation"))
}

/// `is_edit_tool_name` after `tool.casefold()`.
pub fn is_edit_tool_lowered(lowered: &str) -> bool {
    if member(ROUTINE_READ_TOOLS, lowered) || member(SHELL_TOOLS, lowered) {
        return false;
    }
    is_edit_tool_token(lowered)
}

/// `_routine_shell_reason(command, argv)` with `lowered = command.casefold()`.
fn routine_shell_reason(lowered: &str, argv: &[String]) -> &'static str {
    if member(EDIT_COMMANDS, lowered) {
        return "edit";
    }
    if lowered == "git" && argv.len() >= 2 && argv[1] == "diff" && argv[2..].iter().any(|item| item == "--check") {
        return "verification";
    }
    if member(TEST_COMMANDS, lowered) {
        return "test";
    }
    if member(SHELL_VERIFICATION_COMMANDS, lowered) {
        return "verification";
    }
    "unknown_operation"
}

/// `_routine_shell_facts` from the selected non-empty command string onward.
///
/// `fold` is `str.casefold`; it may fail only if the Python call it wraps fails.
pub fn shell_command_facts<E>(raw: &str, fold: &mut impl FnMut(&str) -> Result<String, E>) -> Result<RoutineFacts, E> {
    const AMBIGUOUS: RoutineFacts = facts(false, "ambiguous_shell");
    if raw.is_empty() || raw.chars().count() > MAX_COMMAND_CHARS {
        return Ok(AMBIGUOUS);
    }
    if raw.contains('\0') || SHELL_MARKERS.iter().any(|marker| raw.contains(marker)) {
        return Ok(AMBIGUOUS);
    }
    let Ok(argv) = shlex::split(raw) else {
        return Ok(AMBIGUOUS);
    };
    if argv.is_empty() {
        return Ok(AMBIGUOUS);
    }
    let command = &argv[0];
    if command.contains('/') || command.contains('\\') {
        return Ok(AMBIGUOUS);
    }
    let lowered = fold(command)?;
    if member(READ_ONLY_COMMANDS, &lowered) {
        if lowered == "rg" {
            for argument in &argv[1..] {
                let head = match argument.find('=') {
                    Some(index) => &argument[..index],
                    None => argument.as_str(),
                };
                let option = fold(head)?;
                if member(RG_PRE_OPTIONS, &option) || option.starts_with("--pre") {
                    return Ok(facts(false, "unsafe_shell"));
                }
            }
        }
        return Ok(facts(true, "routine_shell"));
    }
    if lowered != "git" || argv.len() < 2 {
        return Ok(facts(false, routine_shell_reason(&lowered, &argv)));
    }
    for argument in &argv[1..] {
        let option = fold(argument)?;
        if member(GIT_SIDE_EFFECT_OPTIONS, &option)
            || GIT_SIDE_EFFECT_PREFIXES.iter().any(|prefix| option.starts_with(prefix))
            || option.contains("textconv")
            || option.contains("ext-diff")
        {
            return Ok(facts(false, "unsafe_shell"));
        }
    }
    let subcommand = argv[1].as_str();
    if !member(GIT_READ_SUBCOMMANDS, subcommand) {
        return Ok(facts(false, routine_shell_reason(&lowered, &argv)));
    }
    if subcommand == "diff" && argv[2..].iter().any(|item| item == "--check") {
        return Ok(facts(false, "verification"));
    }
    Ok(facts(true, "routine_shell"))
}

/// How a folded status token reads in `_classification_outcome`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StatusClass {
    Failure,
    Denied,
    Cancelled,
    Success,
    Partial,
    Unknown,
}

/// Classify one folded `result_status`/`status`/`outcome`/... token.
pub fn status_class(lowered: &str) -> StatusClass {
    if let Some((_, state)) = FAILURE_STATUSES.iter().find(|(name, _)| *name == lowered) {
        return match *state {
            "denied" => StatusClass::Denied,
            "cancelled" => StatusClass::Cancelled,
            _ => StatusClass::Failure,
        };
    }
    if member(SUCCESS_STATUSES, lowered) {
        return StatusClass::Success;
    }
    if member(PARTIAL_STATUSES, lowered) {
        return StatusClass::Partial;
    }
    StatusClass::Unknown
}

/// The accumulated flags of `_classification_outcome`.
#[derive(Clone, Copy, Debug, Default)]
pub struct Outcome {
    pub denied: bool,
    pub cancelled: bool,
    pub failure: bool,
    pub partial: bool,
    pub success: bool,
    pub unknown: bool,
    pub invalid_exit: bool,
    pub any_valid_exit: bool,
    pub any_nonzero_exit: bool,
}

impl Outcome {
    pub fn status(&mut self, class: StatusClass) {
        match class {
            StatusClass::Denied => self.denied = true,
            StatusClass::Cancelled => self.cancelled = true,
            StatusClass::Failure => self.failure = true,
            StatusClass::Success => self.success = true,
            StatusClass::Partial => self.partial = true,
            StatusClass::Unknown => self.unknown = true,
        }
    }

    pub fn exit(&mut self, code: i64) {
        self.any_valid_exit = true;
        if code != 0 {
            self.any_nonzero_exit = true;
        }
    }

    /// The final reduction, given whether the native post-hook success fallback applies.
    pub fn state(mut self, native_post_success: bool) -> Option<&'static str> {
        if native_post_success
            && !(self.denied
                || self.cancelled
                || self.failure
                || self.partial
                || self.success
                || self.unknown
                || self.invalid_exit
                || self.any_valid_exit)
        {
            self.success = true;
        }
        if self.denied {
            return Some("denied");
        }
        if self.cancelled {
            return Some("cancelled");
        }
        if self.failure || self.any_nonzero_exit {
            return Some("failure");
        }
        if self.partial {
            return Some("partial");
        }
        if self.invalid_exit || self.unknown {
            return Some("unknown");
        }
        if self.any_valid_exit {
            return Some("success");
        }
        if self.success {
            return Some("success");
        }
        None
    }
}

/// `_classification_phase(event_name)`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Pre,
    Post,
    Other,
}

pub fn phase(event_name: &str) -> Phase {
    if member(PRE_EVENTS, event_name) {
        Phase::Pre
    } else if member(POST_EVENTS, event_name) {
        Phase::Post
    } else {
        Phase::Other
    }
}

/// `ObservationContentRole` value.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ContentRole {
    None,
    ToolInput,
    ToolOutput,
    Both,
}

/// The fields `classify_observation` hands to `ObservationClassification`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Classification {
    pub protected: bool,
    pub routine_candidate: bool,
    pub proven_routine_success: bool,
    pub content_role: ContentRole,
    pub reason_tokens: Vec<&'static str>,
}

/// The assembly at the end of `classify_observation`.
pub fn assemble(phase: Phase, routine: RoutineFacts, outcome: Option<&'static str>, untrusted: bool) -> Classification {
    let proven = phase == Phase::Post && routine.candidate && outcome == Some("success");
    let protected = phase != Phase::Post || !proven;
    let mut reasons = Vec::with_capacity(5);
    reasons.push(routine.reason);
    if routine.candidate {
        reasons.push("routine_candidate");
    }
    if untrusted {
        reasons.push("untrusted_action");
    }
    if phase == Phase::Pre {
        reasons.push("incomplete");
    } else if let Some(state) = outcome {
        reasons.push(state);
    } else if phase == Phase::Post {
        reasons.push("unknown");
    }
    if proven {
        reasons.push("routine_success");
    }
    let content_role = if routine.candidate && (phase == Phase::Pre || proven) {
        ContentRole::None
    } else if protected {
        ContentRole::Both
    } else if phase == Phase::Pre {
        ContentRole::ToolInput
    } else if phase == Phase::Post {
        ContentRole::ToolOutput
    } else {
        ContentRole::Both
    };
    Classification {
        protected,
        routine_candidate: routine.candidate,
        proven_routine_success: proven,
        content_role,
        reason_tokens: reasons,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ascii_fold(text: &str) -> Result<String, ()> {
        Ok(text.to_ascii_lowercase())
    }

    fn shell(raw: &str) -> RoutineFacts {
        shell_command_facts(raw, &mut ascii_fold).unwrap()
    }

    #[test]
    fn shell_grammar() {
        assert_eq!(shell("rg -n foo"), facts(true, "routine_shell"));
        assert_eq!(shell("rg --pre=x foo"), facts(false, "unsafe_shell"));
        assert_eq!(shell("git status"), facts(true, "routine_shell"));
        assert_eq!(shell("git diff --check"), facts(false, "verification"));
        assert_eq!(shell("git -c x diff --output=y"), facts(false, "unsafe_shell"));
        assert_eq!(shell("git commit"), facts(false, "unknown_operation"));
        assert_eq!(shell("ls | wc"), facts(false, "ambiguous_shell"));
        assert_eq!(shell("'ls"), facts(false, "ambiguous_shell"));
        assert_eq!(shell("./ls"), facts(false, "ambiguous_shell"));
        assert_eq!(shell("pytest -q"), facts(false, "test"));
        assert_eq!(shell("rm x"), facts(false, "edit"));
    }

    #[test]
    fn outcome_reduction() {
        let mut outcome = Outcome::default();
        assert_eq!(outcome.state(true), Some("success"));
        assert_eq!(outcome.state(false), None);
        outcome.exit(0);
        assert_eq!(outcome.state(false), Some("success"));
        outcome.exit(2);
        assert_eq!(outcome.state(false), Some("failure"));
        assert_eq!(status_class("timeout"), StatusClass::Failure);
        assert_eq!(status_class("canceled"), StatusClass::Cancelled);
    }
}
