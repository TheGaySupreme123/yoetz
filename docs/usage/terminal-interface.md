# The Yoetz terminal interface

Running `yoetz` at a terminal opens a full-screen interface: a compact session header, a
scrollable record of what Yoetz did, and a composer at the bottom where you type commands. When
Yoetz needs an answer, a temporary view takes the composer's place; dismissing it puts your
half-typed line back exactly as it was.

There is no settings dashboard, no sidebar, and no page counter. Every command is something the
`yoetz` command tree already does, named in ordinary words.

See [ADR-017](../adr/ADR-017-full-screen-terminal-interface.md) for the decision and its limits.

## When it opens — and when it does not

The interface opens **only** when all of these hold:

- stdin and stdout are both real terminals;
- `TERM` is set and is not `dumb`;
- no CI marker is set (`CI`, `CONTINUOUS_INTEGRATION`, `BUILD_NUMBER`, `GITHUB_ACTIONS`,
  `GITLAB_CI`, `TEAMCITY_VERSION`);
- `YOETZ_TUI` is not `0`;
- the invocation is a bare `yoetz` or `yoetz menu`.

Everything else behaves exactly as it did before: pipes, redirects, CI, `yoetz --help`, `--json`
output, every named subcommand, `yoetz mcp serve`, and the protocol fixtures. A bare `yoetz` with
a redirected stream still prints help.

Set `YOETZ_TUI=0` to always get the prompt-loop menu instead. If the rendering dependency is
missing from an installation, Yoetz falls back to that menu on its own rather than failing.

## Reading the screen

Six symbols carry all of Yoetz's certainty. They mean exactly what they say:

| Symbol | Meaning |
|---|---|
| `›` | the item your cursor is on |
| `•` | something is happening right now |
| `✓` | **verified** — a postcondition was actually observed |
| `!` | a warning, a limitation, or a state that was never proven |
| `■` | a failure, or a safety boundary that stopped an action |
| `○` | optional, disabled, or not configured |

`✓` is never used for "configured". Saving a provider binding earns `✓ Provider binding saved`
and, in the same breath, `! Live provider connection has not been tested`, because those are two
different facts and only one of them was checked.

## Keys

```text
Up / Down       move through options
Enter           confirm or select
Esc             go back, cancel, or close the current view
1 to 9          pick a numbered option
/               open the command list
?               show shortcuts
D               show technical details where they are offered
Ctrl+C          close the current view, or interrupt running work
Page Up/Down    scroll a long list or the history
Home / End      jump to the first or last option
```

Three rules are guaranteed, not conventions:

- **`Esc` never approves anything.** Dismissing an approval means nothing changed.
- **Printable keys never trigger shortcuts while you are typing.** In a search box or a text
  field, `1` and `d` are characters.
- **Disabled options cannot be chosen** — not by arrow key and not by number.

## First run

The first `yoetz` on a new machine walks a linear path, each finished step collapsing into a short
line above the active one:

1. **Welcome and detection.** What Yoetz found: your agent installation, your project, whether
   system secure storage is available, and whether Yoetz is connected yet.
2. **Which installation** — only if more than one was found. Friendly names first; the executable
   path appears when the row is selected, and in full under `D`.
3. **Project trust.** The canonical Git common root, and what allowing project-local guidance and hooks
   actually permits. If you started Yoetz in a subfolder, it says so and names the root the trust
   applies to.
4. **The exact proposed change**, in words, with a `Safety` block stating what will *not* happen.
   `D` shows the executable path, managed paths, MCP command, preview digest, policy digest, and
   planned file count.
5. **Installation activity**, step by step. A step is only reported as done once its postcondition
   was checked.
6. **Secure storage** — system keyring, or a Yoetz passphrase.
7. **Review mode** — Local only, or Add semantic review.
8. **Semantic setup, when selected** — an explicit choice between OpenAI API / compatible API and
   Codex with ChatGPT subscription, followed by the matching secure API-key or Codex-owned login
   flow and the trusted recommendation-first privacy ceremony.
9. **Finish**, with each readiness layer stated separately.

You are never required to configure a provider: Local only is complete and useful. If you choose
semantic review, setup does not claim completion until provider credentials and privacy approval
finish.

If an MCP entry named `yoetz` already exists and Yoetz does not own it, setup stops there. You can
inspect it, continue locally, or read manual resolution steps. **There is no force-replace option
and there will not be one.**

## Commands

Type `/` to open the filtered command list.

| Command | Does |
|---|---|
| `/status` | show setup, readiness, and current work |
| `/work` | open a task by title to view claims, evidence, and findings |
| `/check` | run a verification check |
| `/receipt` | view or export an honest receipt |
| `/connect` | connect or repair an agent integration |
| `/privacy` | choose what may leave this computer |
| `/provider` | configure optional deeper review |
| `/service` | manage the protected local service |
| `/doctor` | diagnose installation problems |
| `/help` | show what Yoetz can do here |
| `/quit` | leave Yoetz |

### `/status`

Reports each readiness layer separately, because they can and do disagree. `D` opens the full
list:

```text
Harness detected                    Local service reachable
MCP registered                      Vault ready
MCP verified                        Local deterministic checks
Guidance installed                  Provider binding saved
Structural hooks installed          Credential stored
Project consent active              Provider connection tested
Approved-check policy trusted       Deeper-review evaluator composed
                                    Machine privacy ceiling permits review
                                    Exact repository grant active
                                    Deeper review ready
```

"Connected" is never a substitute for any of these. If the privacy policy could not be read,
`/status` says so rather than claiming nothing is leaving your computer.

### `/connect`

Inspect the current connection, connect or repair it, or view the exact technical state. Any
action that would change something shows the same preview and approval the first run does. When a
newer package is available under the durable `update_checks` policy, `/connect` offers upgrade
first or continue with the running version before harness add/repair — it never reinstalls the same
package bits to add a harness.

### `/privacy`

Shows where privacy stands and the one recommended policy — **Private** without current eligible
exact-route provider evidence, **Assisted review** with it — with both what accepting it buys and what it costs.
Then three choices: **Keep current**, **Review recommended change**, and **Other privacy options**.
The last lists the same five names the command line uses: Private, Metadata only, Assisted review,
Expanded review, and Custom. If the current policy already matches the recommendation, it is not
offered as a change.

The posture is for the repository derived by the service from the interface session's actual working
directory. Branches and linked worktrees share the Git common root; independent clones do not. The
screen shows machine ceiling, exact repository grant, and legacy migration state separately. It
never uses task `workspace_ref` as privacy scope.

This screen selects; it never authorizes. Choosing anything but *Keep current* suspends the
interface and hands the controlling terminal to `yoetz privacy setup`, and that trusted ceremony is
where the exact `before -> after` policy diff is rendered, where reauthentication happens, and where
a widening is actually approved. If the terminal cannot be handed over, nothing changes and the
interface prints the command to run.

A first repository grant may preview both a machine-ceiling widening and insertion of the exact
repository row. They commit atomically against one authority digest. Eligible legacy carry-forward
is shown as bounded automatic narrowing; later repositories remain Private.

Tightening also goes through that handoff, and commits only after an ordinary explicit
confirmation.

### `/provider`

Choose a preset — OpenAI, Fireworks AI, Anthropic, Google Gemini, OpenRouter, Vercel AI Gateway,
or a custom OpenAI-compatible HTTPS endpoint — or choose **Codex with ChatGPT subscription**.
Yoetz shows the endpoint/runtime and privacy posture before asking for an API key or opening Codex
login, and states plainly that storing a binding does not switch external review on.

API-provider keys are entered through the secure prompt described under *Secrets* below. For a
subscription, `/provider` asks for the exact Codex executable, dedicated evaluator home, model, and
reasoning effort; validates the supported digest-bound cell; shows destination, plan/terms notice,
privacy boundary, disconnect, and rollback; then suspends the UI for Codex's browser flow. OAuth
credentials never pass through a widget or Yoetz vault.

Afterwards Yoetz reports what it actually knows:

```text
✓ Provider binding saved
✓ API key stored securely
! Live provider connection has not been tested
! External semantic review is not yet proven ready
```

The subscription variant replaces the API-key line with `✓ Codex-managed ChatGPT login is
available`. Its status is a structural account/model read with no task case. Live semantic proof
still requires a privacy-authorized `check` and terminal provenance/receipt.

**This build exposes no bounded live provider probe**, so a connection test reports itself as
unavailable rather than reporting a pass. A provider that fails never affects local deterministic
readiness.

### `/work`, `/check`, `/receipt`

`/work` opens a task by the title the agent used and shows its claims, evidence count, checks,
coverage, findings, limitations, and whether a receipt is available. **Yoetz has no browsable
task index** — the local service exposes no task-listing operation and this interface does not
invent one — so tasks are reached by name.

`/check` offers three modes, mapping to the existing check modes: use deeper review when
available, require deeper review, or local deterministic checks only. An unavailable deeper
review is reported as a limitation, never as a success.

`/receipt` produces Markdown, plain text, or JSON. The readable view leads with the verdict, then
coverage, open findings, limitations, whether deeper review contributed, freshness, and — always
— what was *not* verified.

### `/service` and `/doctor`

`/service` shows state and offers unlock, passphrase setup, lock, and stop. Stopping asks for
confirmation with the cursor on *no*.

`/doctor` runs bounded read-only checks across runtime, package version, discovery, registration,
managed files, hooks, consent, policy digest, service reachability, vault, provider, and privacy,
then suggests safe next steps. When policy permits package update checks and a newer release is
known, the package line is optional with remediation `uv tool upgrade yoetz`; when the check is
allowed but fails, the line is unproven with "could not check for updates." **It never changes
anything.**

## Secrets

Yoetz never accepts a secret through this window.

When a credential or passphrase is needed, the interface explains what is about to happen and asks
for explicit consent. On approval it **suspends itself** and hands the terminal to the existing
confidential ceremony, which opens the controlling terminal directly and turns off echo. What you
type there goes straight into the local vault.

No secret byte can reach the transcript, the interface's state, a log, a config file, an event
payload, MCP context, or a screenshot — because no secret byte ever enters this process's UI at
all.

If your environment cannot suspend, Yoetz says so and names the command to run instead. It does
not offer to take the secret through the window as a fallback.
