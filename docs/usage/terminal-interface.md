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
3. **Project trust.** The repository root, and what allowing project-local guidance and hooks
   actually permits. If you started Yoetz in a subfolder, it says so and names the root the trust
   applies to.
4. **The exact proposed change**, in words, with a `Safety` block stating what will *not* happen.
   `D` shows the executable path, managed paths, MCP command, preview digest, policy digest, and
   planned file count.
5. **Installation activity**, step by step. A step is only reported as done once its postcondition
   was checked.
6. **Secure storage** — system keyring, or a Yoetz passphrase.
7. **Finish**, with each readiness layer stated separately.

You are never asked to configure a provider to finish setup. Local verification is complete
without one.

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
                                    Privacy permits external review
                                    Deeper review ready
```

"Connected" is never a substitute for any of these. If the privacy policy could not be read,
`/status` says so rather than claiming nothing is leaving your computer.

### `/connect`

Inspect the current connection, connect or repair it, or view the exact technical state. Any
action that would change something shows the same preview and approval the first run does.

### `/privacy`

Four choices, mapping to the durable privacy profiles: **Local only** (the default),
**Ask every time**, **Minimal external review**, and **Trusted provider**.

Widening shows an exact disclosure first — which data categories become eligible, the provider,
model, endpoint profile, purpose, and scope, plus what is never sent under any choice. The cursor
starts on *decline*. Approving there hands off to the trusted ceremony
(`yoetz privacy propose` then `yoetz privacy decide`); the interface does not widen policy itself.

Tightening is also an explicit ceremony (`yoetz privacy tighten`).

### `/provider`

Choose a preset — OpenAI, Fireworks AI, Anthropic, Google Gemini, OpenRouter, Vercel AI Gateway,
or a custom OpenAI-compatible HTTPS endpoint — then a model. Yoetz shows the endpoint and privacy
posture before asking for anything secret, and states plainly that storing a key does not switch
external review on.

The API key is entered through the secure prompt described under *Secrets* below.

Afterwards Yoetz reports what it actually knows:

```text
✓ Provider binding saved
✓ API key stored securely
! Live provider connection has not been tested
! External semantic review is not yet proven ready
```

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
then suggests safe next steps. **It never changes anything.**

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
