"""The Yoetz full-screen terminal application.

One continuous surface: a compact session header, a scrollable transcript, and a
bottom pane that is either the composer or a temporary view stacked over it.
There is no settings dashboard, no sidebar, and no page chrome, because the
interaction this product wants is a calm conversation about verification rather
than an administration console.

Flows read as linear ``async`` functions. ``ask()`` opens a temporary view and
awaits its dismissal, so a first-run sequence or a provider setup is written the
way it is experienced — one step after another, each completed step collapsing
into a short line in the transcript above the active one. Every step's actual
work is delegated to :mod:`yoetz.tui.runtime`, which is the only thing here that
talks to the application services; this module decides what to show, never what
is true.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar, Final

from textual import events, on
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.containers import Vertical
from textual.css.query import NoMatches

from yoetz import __version__
from yoetz.tui.commands import SLASH_COMMANDS, command_named
from yoetz.tui.events import HistoryEvent, Transcript
from yoetz.tui.models import (
    PRIVACY_RECIPES,
    CheckMode,
    Detection,
    HarnessOption,
    IntegrationPlan,
    LayerState,
    PrivacyChoice,
    StorageChoice,
)
from yoetz.tui.render import (
    render_detection,
    render_doctor,
    render_finish,
    render_foreign_entry,
    render_integration_preview,
    render_integration_technical_details,
    render_layers,
    render_project_trust,
    render_provider_endpoint,
    render_provider_stored,
    render_receipt,
    render_status,
    render_welcome,
    render_work_detail,
)
from yoetz.tui.runtime import RuntimeError_, YoetzRuntime
from yoetz.tui.styles import YOETZ_CSS
from yoetz.tui.symbols import Level
from yoetz.tui.text import middle_truncate
from yoetz.tui.widgets.composer import CommandSubmitted, Composer, Footer
from yoetz.tui.widgets.history import History, SessionHeader
from yoetz.tui.widgets.views import (
    ApprovalView,
    BaseView,
    DetailsRequested,
    DetailsView,
    Option,
    SelectionView,
    TextEntryView,
    ViewDismissed,
)

__all__ = ["YoetzTui"]

_SHORTCUTS: Final[tuple[str, ...]] = (
    "Up / Down     move through options",
    "Enter         confirm or select",
    "Esc           go back, cancel, or close this view",
    "1 to 9        pick a numbered option",
    "/             open the command list",
    "?             show these shortcuts",
    "D             show technical details where offered",
    "Ctrl+C        interrupt work, or close this view",
    "Page Up/Down  scroll a long list or the history",
    "Home / End    jump to the first or last option",
)

_NEVER_SENT: Final[tuple[str, ...]] = (
    "API keys, passphrases, and anything else secret",
    "Raw repository file contents that no rule selected",
    "Absolute filesystem paths",
)

# Which durable profile each recipe lands on, used only to notice that the current policy
# already matches the recommendation and stop offering it as a change.
_PROFILE_FOR_RECIPE: Final[dict[str, str]] = {
    "private": PrivacyChoice.LOCAL_ONLY.value,
    "metadata_only": PrivacyChoice.CONFIRM_EVERY_REQUEST.value,
}


class YoetzTui(App[int]):
    """The interactive Yoetz surface. A presentation layer over existing services."""

    CSS: ClassVar[str] = YOETZ_CSS
    TITLE = "Yoetz"
    BINDINGS: ClassVar[list[Any]] = []

    def __init__(
        self,
        runtime: YoetzRuntime | None = None,
        *,
        first_run: bool = False,
        cwd: Path | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime or YoetzRuntime(cwd=cwd)
        self.first_run = first_run
        self.transcript = Transcript()
        self._stack: list[BaseView] = []
        self._pending: dict[int, asyncio.Future[str | None]] = {}
        self._flow: Any = None
        self._detection: Detection | None = None
        self._active_task_title: str | None = None
        self.exit_code = 0

    # -- layout ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield SessionHeader()
        yield History(self.transcript)
        with Vertical(id="bottom-pane"):
            yield Composer()
            yield Footer()

    @property
    def header_widget(self) -> SessionHeader:
        return self.query_one(SessionHeader)

    @property
    def history(self) -> History:
        return self.query_one(History)

    @property
    def composer(self) -> Composer:
        return self.query_one(Composer)

    @property
    def footer_widget(self) -> Footer:
        return self.query_one(Footer)

    @property
    def open_view(self) -> BaseView | None:
        return self._stack[-1] if self._stack else None

    async def on_mount(self) -> None:
        self.header_widget.set_state(version=__version__, project=str(self.runtime.project_root()))
        self.composer.focus_input()
        self._flow = self.run_worker(
            self._first_run_flow() if self.first_run else self._resume_flow(),
            name="flow",
            exclusive=True,
        )

    # -- transcript helpers ---------------------------------------------

    def say(
        self,
        level: Level,
        title: str,
        body: Sequence[str] = (),
        *,
        details: Sequence[str] = (),
    ) -> HistoryEvent:
        event = HistoryEvent(level, title, tuple(body), tuple(details))
        self.history.append(event)
        return event

    def settle(
        self,
        level: Level,
        title: str,
        body: Sequence[str] = (),
        *,
        details: Sequence[str] = (),
    ) -> HistoryEvent:
        """Replace the in-flight activity line with its completed form."""

        event = HistoryEvent(level, title, tuple(body), tuple(details))
        self.history.replace_last(event)
        return event

    @property
    def body_width(self) -> int:
        return max((self.size.width or 80) - 4, 32)

    # -- temporary view stack -------------------------------------------

    def push_view(self, view: BaseView) -> None:
        try:
            pane = self.query_one("#bottom-pane", Vertical)
        except NoMatches:
            return
        if self._stack:
            self._stack[-1].display = False
        else:
            self.composer.display = False
        self._stack.append(view)
        pane.mount(view, before=self.footer_widget)
        self.footer_widget.set_state(left="esc to go back")

    async def pop_view(self) -> BaseView | None:
        """Close the top view and restore whatever was underneath it.

        Tolerates a partially torn-down screen: a flow cancelled during shutdown
        unwinds through here, and by then the composer may already be gone.
        """

        if not self._stack:
            return None
        view = self._stack.pop()
        try:
            await view.remove()
            if self._stack:
                self._stack[-1].display = True
                self._stack[-1].focus()
            else:
                self.composer.display = True
                self.composer.focus_input()
                self.footer_widget.set_state(left="? for shortcuts")
        except NoMatches:
            pass
        return view

    async def ask(self, view: BaseView) -> str | None:
        """Open a temporary view and wait for the user to resolve it.

        The composer keeps its half-typed text while a view is open, so
        dismissing one restores exactly what the user had been writing.
        """

        future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._pending[id(view)] = future
        self.push_view(view)
        try:
            return await future
        except asyncio.CancelledError:
            self._pending.pop(id(view), None)
            if self.open_view is view:
                await self.pop_view()
            raise

    @on(ViewDismissed)
    async def _on_view_dismissed(self, message: ViewDismissed) -> None:
        message.stop()
        future = self._pending.pop(id(message.view), None)
        if self.open_view is message.view:
            await self.pop_view()
        if future is not None and not future.done():
            future.set_result(message.result)

    @on(DetailsRequested)
    async def _on_details_requested(self, message: DetailsRequested) -> None:
        message.stop()
        lines = message.view.technical_details
        if not lines:
            event = self.transcript.latest_with_details()
            lines = event.details if event is not None else ("No technical details recorded.",)
        await self.ask(DetailsView(name="details", title="Technical details", lines=tuple(lines)))

    # -- key handling ----------------------------------------------------

    async def on_key(self, event: events.Key) -> None:
        view = self.open_view
        if event.key == "question_mark" and (view is None or view.accepts_printable_shortcuts):
            event.stop()
            event.prevent_default()
            await self.ask(
                DetailsView(name="shortcuts", title="Keyboard shortcuts", lines=_SHORTCUTS)
            )
            return
        if event.key.lower() != "d" or view is not None:
            return
        try:
            composer_text = self.composer.text
        except NoMatches:
            return
        if not composer_text:
            latest = self.transcript.latest_with_details()
            if latest is not None:
                event.stop()
                event.prevent_default()
                await self.ask(
                    DetailsView(name="details", title="Technical details", lines=latest.details)
                )

    async def action_interrupt(self) -> None:
        """Ctrl+C: close a temporary view, else stop active work, else leave."""

        if self._stack:
            view = self.open_view
            if view is not None:
                view.dismiss(None)
            return
        flow = self._flow
        if flow is not None and not flow.is_finished:
            flow.cancel()
            self.say(Level.UNPROVEN, "Stopped. Nothing further was changed.")
            self._flow = None
            return
        self.exit(self.exit_code)

    async def on_event(self, event: events.Event) -> None:
        # Textual delivers Ctrl+C as a key when the app owns the terminal.
        if isinstance(event, events.Key) and event.key == "ctrl+c":
            event.stop()
            event.prevent_default()
            await self.action_interrupt()
            return
        await super().on_event(event)

    # -- composer --------------------------------------------------------

    @on(CommandSubmitted)
    async def _on_command(self, message: CommandSubmitted) -> None:
        message.stop()
        raw = message.value.strip()
        if not raw:
            return
        command = command_named(raw)
        if command is None:
            self.say(
                Level.UNPROVEN,
                f"{raw} is not a Yoetz command",
                ("Type / to see what is available.",),
            )
            return
        self._flow = self.run_worker(self._dispatch(command.name), name="command", exclusive=True)

    async def _dispatch(self, name: str) -> None:
        handlers: dict[str, Callable[[], Awaitable[None]]] = {
            "status": self.command_status,
            "work": self.command_work,
            "check": self.command_check,
            "receipt": self.command_receipt,
            "connect": self.command_connect,
            "privacy": self.command_privacy,
            "provider": self.command_provider,
            "service": self.command_service,
            "doctor": self.command_doctor,
            "help": self.command_help,
            "quit": self.command_quit,
        }
        handler = handlers.get(name)
        if handler is None:
            return
        try:
            await handler()
        except asyncio.CancelledError:
            raise
        except RuntimeError_ as error:
            self._report(error)

    def _report(self, error: RuntimeError_) -> None:
        self.say(
            Level.BLOCKED,
            error.message,
            (f"Reason: {error.reason}",),
            details=error.details or (f"reason={error.reason}",),
        )

    # ------------------------------------------------------------------
    # First run
    # ------------------------------------------------------------------

    async def _first_run_flow(self) -> None:
        detection = await self.runtime.detect()
        self._detection = detection
        self.say(Level.ACTIVE, "", render_welcome(self.body_width))
        self.say(Level.ACTIVE, "", render_detection(detection, self.body_width))
        await self._refresh_header()

        choice = await self.ask(
            SelectionView(
                name="welcome",
                options=[
                    Option(
                        "connect",
                        "Connect Yoetz to Codex",
                        "Recommended. Adds the local integration for this project.",
                        disabled=not detection.harnesses,
                        disabled_reason="no Codex installation found",
                    ),
                    Option(
                        "local",
                        "Set up Yoetz without Codex",
                        "Use local checks and receipts from the terminal.",
                    ),
                    Option("exit", "Exit"),
                ],
                hint="press enter to continue",
            )
        )
        if choice in {None, "exit"}:
            self.say(Level.OPTIONAL, "Setup stopped. Nothing was changed.")
            self.exit(0)
            return
        if choice == "local":
            await self._choose_storage(detection)
            await self._choose_initial_review(connected=False)
            return

        option = await self._choose_harness(detection.harnesses)
        if option is None:
            self.say(Level.OPTIONAL, "Setup stopped. Nothing was changed.")
            return
        if not await self._confirm_project_trust(detection):
            return
        if not await self._connect(option):
            return
        await self._choose_storage(detection)
        await self._choose_initial_review(connected=True)

    async def _choose_initial_review(self, *, connected: bool) -> None:
        choice = await self.ask(
            SelectionView(
                name="review-mode",
                title="How should Yoetz review work?",
                options=[
                    Option(
                        "local",
                        "Local only",
                        "Deterministic checks; nothing leaves this computer.",
                    ),
                    Option(
                        "semantic",
                        "Add semantic review",
                        "Configure a provider, API key, and explicit privacy boundary.",
                    ),
                ],
                hint="enter to choose",
            )
        )
        if choice != "semantic":
            await self._finish_setup(connected=connected)
            return
        await self.command_provider()
        provider = await self.runtime.provider_posture()
        if not provider.endpoint_bound or provider.credential_connected is not True:
            self.say(
                Level.BLOCKED,
                "Semantic setup is not complete",
                (
                    "A provider binding and stored credential are required.",
                    "You can choose Local only or rerun setup.",
                ),
            )
            return
        try:
            # First run gets the same recommendation-first screen as `yoetz --privacy`.
            report = await self.hand_over_terminal(
                lambda: self.runtime.run_privacy_setup(None, offer_recommended=True)
            )
        except SuspendNotSupported:
            self.say(
                Level.UNPROVEN,
                "This terminal cannot open the trusted privacy ceremony",
                ("Run 'yoetz privacy setup' from your shell. Setup was not marked complete.",),
            )
            return
        except RuntimeError_ as error:
            self._report(error)
            return
        if getattr(report, "outcome", "failed") not in {"configured", "unchanged"}:
            self.say(
                Level.BLOCKED,
                "Semantic privacy setup is not complete",
                (f"Reason: {getattr(report, 'reason', 'privacy_setup_failed')}",),
            )
            return
        await self._finish_setup(connected=connected)

    async def _choose_harness(self, options: Sequence[HarnessOption]) -> HarnessOption | None:
        if not options:
            return None
        if len(options) == 1:
            return options[0]
        rows = [
            Option(str(index), option.label, option.description)
            for index, option in enumerate(options)
        ]
        rows.append(Option("other", "Choose another executable"))
        chosen = await self.ask(
            SelectionView(
                name="harness",
                title="More than one Codex installation is available",
                options=rows,
                hint="enter to choose · esc to cancel",
            )
        )
        if chosen is None:
            return None
        if chosen == "other":
            while True:
                entry = TextEntryView(
                    name="harness-path",
                    title="Choose another executable",
                    label="Full path to the Codex executable",
                    placeholder="/usr/local/bin/codex",
                    empty_is_cancel=False,
                )
                if await self.ask(entry) is None:
                    return None
                path = entry.value.strip()
                if path and Path(path).is_file() and os.access(path, os.X_OK):
                    return HarnessOption(
                        executable_path=path,
                        reported_version=None,
                        label="Codex",
                        description="Executable you selected",
                    )
                self.say(
                    Level.BLOCKED,
                    "That path is not an executable file",
                    ("Nothing was changed. Enter another path or press Esc to cancel.",),
                )
        return options[int(chosen)]

    async def _confirm_project_trust(self, detection: Detection) -> bool:
        answer = await self.ask(
            ApprovalView(
                name="trust",
                title="",
                body=render_project_trust(detection, self.body_width),
                approve_label="Yes, continue",
                decline_label="No, quit",
                hint="enter to choose · esc to cancel",
            )
        )
        if answer != "approve":
            self.say(Level.OPTIONAL, "Setup stopped. This project was not changed.")
            return False
        return True

    async def _connect(self, option: HarnessOption) -> bool:
        try:
            plan = await self.runtime.integration_plan(option)
        except RuntimeError_ as error:
            self._report(error)
            return False
        if plan.foreign_entry:
            await self._handle_foreign_entry(option)
            return False
        if not await self._approve_plan(plan):
            self.say(Level.OPTIONAL, "This project was left unchanged.")
            return False
        return await self._apply_plan(option, plan)

    async def _approve_plan(self, plan: IntegrationPlan) -> bool:
        view = ApprovalView(
            name="integration",
            title="",
            body=render_integration_preview(plan, self.body_width),
            approve_label="Yes, connect Yoetz",
            decline_label="No, keep this project unchanged",
        )
        # 'D' on this view must show the exact paths, digests, and command.
        view.technical_details = render_integration_technical_details(plan, self.body_width)
        return await self.ask(view) == "approve"

    async def _apply_plan(self, option: HarnessOption, plan: IntegrationPlan) -> bool:
        self.say(Level.ACTIVE, "Connecting Yoetz (esc to interrupt)")
        try:
            outcome = await self.runtime.apply_integration(option, plan)
        except RuntimeError_ as error:
            self.settle(Level.BLOCKED, error.message, (f"Reason: {error.reason}",))
            return False
        if outcome.foreign_entry:
            await self._handle_foreign_entry(option)
            return False
        lines = render_layers(outcome.layers, self.body_width)
        blocked = [layer for layer in outcome.layers if layer.state is LayerState.BLOCKED]
        if blocked:
            self.settle(
                Level.BLOCKED,
                "Yoetz could not finish connecting",
                lines,
                details=(f"outcome={outcome.outcome}", f"reason={outcome.reason}"),
            )
            return False
        unproven = [layer for layer in outcome.layers if layer.state is LayerState.UNPROVEN]
        self.settle(
            Level.UNPROVEN if unproven else Level.VERIFIED,
            "Yoetz is connected to this project"
            if not unproven
            else "Yoetz is connected, with some steps unproven",
            lines,
            details=(f"outcome={outcome.outcome}", f"state={outcome.existing_entry_detail}"),
        )
        return True

    async def _handle_foreign_entry(self, option: HarnessOption) -> None:
        detail = await self.runtime.foreign_entry_detail(option)
        self.say(
            Level.BLOCKED,
            "Could not register Yoetz with Codex",
            render_foreign_entry("", self.body_width)[2:],
            details=detail,
        )
        while True:
            choice = await self.ask(
                SelectionView(
                    name="foreign",
                    options=[
                        Option("show", "Show the existing connection"),
                        Option("local", "Continue with local Yoetz only"),
                        Option("manual", "Show manual resolution instructions"),
                        Option("exit", "Exit"),
                    ],
                    hint="Nothing was replaced or removed · esc to go back",
                )
            )
            if choice == "show":
                await self.ask(
                    DetailsView(
                        name="foreign-detail",
                        title="The existing connection",
                        lines=detail,
                    )
                )
                continue
            if choice == "manual":
                await self.ask(
                    DetailsView(
                        name="foreign-manual",
                        title="Resolving this by hand",
                        lines=(
                            "Yoetz will never replace an MCP entry it does not own.",
                            "",
                            "1. Inspect the existing entry:",
                            "     codex mcp get yoetz",
                            "2. If it is yours and you no longer need it, remove it:",
                            "     codex mcp remove yoetz",
                            "3. Return here and run /connect again.",
                            "",
                            "If the entry belongs to another tool, leave it alone and",
                            "keep using Yoetz locally — /check and /receipt work without",
                            "the Codex integration.",
                        ),
                    )
                )
                continue
            if choice == "local":
                await self._finish_setup(connected=False)
            return

    async def _choose_storage(self, detection: Detection) -> None:
        choice = await self.ask(
            SelectionView(
                name="storage",
                title="Where should Yoetz keep local secrets?",
                options=[
                    Option(
                        StorageChoice.SYSTEM_KEYRING.value,
                        "Use system secure storage",
                        "Recommended. Unlocks automatically for the operating-system user.",
                        disabled=not detection.secure_storage_available,
                        disabled_reason="not available on this machine",
                    ),
                    Option(
                        StorageChoice.PASSPHRASE.value,
                        "Use a Yoetz passphrase",
                        "Enter a passphrase when Yoetz needs to unlock.",
                    ),
                ],
                hint="enter to choose · esc to skip for now",
            )
        )
        if choice is None:
            return
        posture = await self.runtime.vault_posture()
        if not posture.reachable:
            self.say(
                Level.UNPROVEN,
                "Secure storage was not set up",
                (
                    "The local service is not running yet.",
                    "Start it with 'yoetz service run', then use /service.",
                ),
            )
            return
        if posture.vault_mode not in {None, "uninitialized"}:
            self.say(Level.VERIFIED, "Secure storage is already set up")
            return
        if choice == StorageChoice.PASSPHRASE.value:
            await self._run_confidential(
                "Choose a Yoetz passphrase",
                self.runtime.initialize_passphrase_vault,
                fallback_command="yoetz service unlock --initialize",
            )
        else:
            await self._run_confidential(
                "Set up system secure storage",
                self.runtime.initialize_system_keyring,
                fallback_command=None,
            )

    async def hand_over_terminal(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        """Run ``operation`` with the full-screen interface suspended.

        This is the seam through which every secret is entered. Suspending gives
        the controlling terminal back to the ceremony in ``yoetz.cli.unlock``,
        which opens ``/dev/tty`` itself, checks that it really is the controlling
        terminal, and disables echo — so no key press ever reaches a widget and
        no secret byte can end up in the transcript, a log, or a snapshot.
        """

        with self.suspend():
            return await operation()

    async def _run_confidential(
        self,
        title: str,
        operation: Callable[[], Awaitable[Any]],
        *,
        fallback_command: str | None = "yoetz service unlock",
    ) -> Any:
        """Ask for explicit consent, then hand the terminal to the ceremony."""

        confirmed = await self.ask(
            ApprovalView(
                name="confidential",
                title=title,
                body=(
                    "Yoetz will hand this terminal to its secure entry prompt.",
                    "",
                    "What you type there is hidden, goes straight into the local",
                    "vault, and never appears in this window, the history above,",
                    "any log, or any file in your project.",
                ),
                approve_label="Continue to secure entry",
                decline_label="Not now",
            )
        )
        if confirmed != "approve":
            self.say(Level.OPTIONAL, "Secure entry was cancelled. Nothing was stored.")
            return None
        try:
            return await self.hand_over_terminal(operation)
        except SuspendNotSupported:
            # Rather than take the secret through a widget, say so and name the
            # command that runs the same ceremony directly.
            guidance = (
                ("Run this from your shell instead:", "", f"    {fallback_command}", "")
                if fallback_command is not None
                else (
                    "No equivalent shell command is exposed for this operation.",
                    "Run Yoetz again in a terminal that supports secure handoff.",
                    "",
                )
            )
            self.say(
                Level.UNPROVEN,
                "This terminal cannot hand over for secure entry",
                (
                    "Yoetz will not accept a secret through this window.",
                    *guidance,
                    "Nothing was stored.",
                ),
                details=("app.suspend is unavailable in this environment",),
            )
            return None
        except RuntimeError_ as error:
            self.say(
                Level.BLOCKED,
                error.message,
                (f"Reason: {error.reason}",),
                details=(f"reason={error.reason}",),
            )
            return None

    async def _finish_setup(self, *, connected: bool) -> None:
        from yoetz.cli.setup import write_setup_marker

        write_setup_marker("registered" if connected else "local_only")
        snapshot = await self.runtime.status_snapshot()
        await self._refresh_header()
        self.say(Level.VERIFIED, "", render_finish(snapshot, self.body_width))

    async def _resume_flow(self) -> None:
        """Post-install landing: a header, a tip, and the composer. No dashboard."""

        await self._refresh_header()
        self.say(
            Level.ACTIVE,
            "",
            (
                "Tip: Ask Codex to use Yoetz when a task needs an auditable",
                "record of claims, evidence, checks, and limitations.",
            ),
        )

    async def _refresh_header(self) -> None:
        harnesses = self.runtime.discover_harnesses()
        state = "not connected"
        observed: list[str] = []
        for harness in harnesses:
            try:
                observed.append(await self.runtime.mcp_state(harness))
            except RuntimeError_:
                observed.append("unknown")
        if observed:
            if "yoetz_owned" in observed:
                mcp = "yoetz_owned"
            elif "foreign_present" in observed:
                mcp = "foreign_present"
            elif all(item == "absent" for item in observed):
                mcp = "absent"
            else:
                mcp = "unknown"
            state = {
                "yoetz_owned": "connected",
                "absent": "not connected",
                "foreign_present": "blocked — another tool owns the name",
            }.get(mcp, mcp)
        privacy = await self.runtime.privacy_posture()
        vault = await self.runtime.vault_posture()
        self.header_widget.set_state(
            version=__version__,
            project=middle_truncate(str(self.runtime.project_root()), 48),
            harness=state,
            privacy=privacy.summary,
        )
        self.footer_widget.set_state(right=self._footer_right(privacy.summary, vault.ready))

    def _footer_right(self, privacy_summary: str, service_ready: bool) -> str:
        """The footer states what was observed, never a default optimism.

        "ready" here means the local service reported itself ready. A footer that
        says it regardless would be the smallest possible version of exactly the
        dishonesty this product exists to avoid.
        """

        return f"{privacy_summary} · {'ready' if service_ready else 'service not ready'}"

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def command_help(self) -> None:
        lines = ["What you can do here:", ""]
        width = max(len(item.token) for item in SLASH_COMMANDS)
        lines.extend(f"{item.token.ljust(width)}  {item.summary}" for item in SLASH_COMMANDS)
        self.say(Level.ACTIVE, "", lines)

    async def command_quit(self) -> None:
        self.exit(self.exit_code)

    async def command_status(self) -> None:
        self.say(Level.ACTIVE, "Yoetz status")
        snapshot = await self.runtime.status_snapshot()
        self.settle(
            Level.ACTIVE,
            "Yoetz status",
            render_status(snapshot, self.body_width),
            details=render_layers(snapshot.layers, self.body_width),
        )
        await self._refresh_header()

    async def command_doctor(self) -> None:
        self.say(Level.ACTIVE, "Checking this installation")
        report = await self.runtime.doctor()
        blocked = report.blocked
        self.settle(
            Level.BLOCKED if blocked else Level.ACTIVE,
            "Installation report" if not blocked else "Installation problems found",
            render_doctor(report, self.body_width),
            details=tuple(
                f"{entry.key}={entry.state.value} {entry.detail}".strip()
                for entry in report.entries
            ),
        )

    async def command_connect(self) -> None:
        harnesses = self.runtime.discover_harnesses()
        if not harnesses:
            self.say(
                Level.OPTIONAL,
                "No Codex installation was found",
                (
                    "Yoetz still works locally: /check runs deterministic checks",
                    "and /receipt produces an honest record.",
                ),
            )
            return
        option = await self._choose_harness(harnesses)
        if option is None:
            return
        choice = await self.ask(
            SelectionView(
                name="connect",
                title=f"{option.label}",
                options=[
                    Option("inspect", "Inspect this connection", "Read-only. Changes nothing."),
                    Option("repair", "Connect or repair", "Shows the exact change first."),
                    Option("technical", "Show exact technical state"),
                ],
                hint="enter to choose · esc to go back",
            )
        )
        if choice is None:
            return
        if choice == "inspect":
            state = await self.runtime.mcp_state(option)
            self.say(
                Level.ACTIVE,
                f"{option.label}",
                (f"Registration: {state}", f"Executable: {option.executable_path}"),
            )
            return
        if choice == "technical":
            plan = await self.runtime.integration_plan(option)
            await self.ask(
                DetailsView(
                    name="connect-technical",
                    title="Exact technical state",
                    lines=render_integration_technical_details(plan, self.body_width),
                )
            )
            return
        await self._connect(option)
        await self._refresh_header()

    async def command_privacy(self) -> None:
        """Show where privacy stands, then the one recommended move, then everything else.

        This screen selects; it never authorizes. The old flow took its own approval here and
        then handed over to a second, differently worded approval in the trusted terminal —
        two consents for one decision, of which only the second one actually gated anything.
        The trusted terminal ceremony is now the sole authorization for a widening, and it is
        the only place the exact ``before → after`` policy diff is rendered.
        """

        current = await self.runtime.privacy_posture()
        recommendation = self.runtime.privacy_recommendation()
        already = current.choice is not None and current.profile == _PROFILE_FOR_RECIPE.get(
            recommendation.recipe
        )
        self.say(
            Level.ACTIVE,
            "Privacy",
            (
                f"Currently: {current.summary}",
                f"Recommended: {recommendation.label}",
                recommendation.reason,
                recommendation.tradeoff,
            ),
        )
        options = [Option("keep", "Keep current", f"Stay on {current.summary}.")]
        if already:
            self.say(
                Level.OPTIONAL,
                "You are already on the recommended privacy policy.",
            )
        else:
            options.append(
                Option(
                    "recommended",
                    f"Review recommended change ({recommendation.label})",
                    "Opens the trusted terminal, which shows the exact change and asks there.",
                )
            )
        options.append(
            Option("other", "Other privacy options", "Choose a different named policy, or Custom.")
        )
        chosen = await self.ask(
            SelectionView(
                name="privacy",
                title="What may leave this computer?",
                options=options,
                hint="enter to choose · esc to keep the current setting",
            )
        )
        if chosen is None or chosen == "keep":
            self.say(Level.OPTIONAL, "Privacy was left unchanged.")
            return
        recipe = recommendation.recipe
        if chosen == "other":
            picked = await self.ask(
                SelectionView(
                    name="privacy-recipe",
                    title="Choose a privacy policy",
                    options=[
                        Option(name, label, description)
                        for name, label, description in PRIVACY_RECIPES
                    ],
                    hint="enter to choose · esc to keep the current setting",
                )
            )
            if picked is None:
                self.say(Level.OPTIONAL, "Privacy was left unchanged.")
                return
            recipe = picked
        await self._hand_privacy_to_trusted_terminal(recipe)

    async def _hand_privacy_to_trusted_terminal(self, recipe: str) -> None:
        """Suspend and let the trusted CLI ceremony own the decision end to end."""

        try:
            report = await self.hand_over_terminal(lambda: self.runtime.run_privacy_setup(recipe))
        except SuspendNotSupported:
            self.say(
                Level.UNPROVEN,
                "This terminal cannot open the trusted privacy ceremony",
                (
                    "Nothing has changed. Run this command from your shell:",
                    "",
                    "    yoetz privacy setup",
                ),
            )
            return
        except RuntimeError_ as error:
            self.say(
                Level.BLOCKED,
                "Privacy setup did not complete",
                (f"Reason: {error.reason}",),
            )
            return
        outcome = getattr(report, "outcome", "failed")
        if outcome in {"configured", "unchanged"}:
            await self._refresh_header()
            self.say(
                Level.VERIFIED,
                "Privacy setup complete",
                (f"Effective profile: {getattr(report, 'profile', recipe)}",),
            )
            return
        self.say(
            Level.BLOCKED,
            "Privacy setup did not complete",
            (f"Reason: {getattr(report, 'reason', 'privacy_setup_failed')}",),
        )

    async def command_provider(self) -> None:
        options = self.runtime.provider_options()
        chosen = await self.ask(
            SelectionView(
                name="provider",
                title="Choose a provider for optional deeper review",
                options=[
                    Option(option.choice, option.label, option.endpoint_text) for option in options
                ],
                searchable=True,
                hint="type to filter · enter to choose · esc to cancel",
            )
        )
        if chosen is None:
            self.say(Level.OPTIONAL, "No provider was configured.")
            return
        option = next(item for item in options if item.choice == chosen)
        origin: str | None = None
        if option.requires_origin:
            entry = TextEntryView(
                name="provider-origin",
                title=option.label,
                label="HTTPS origin of your endpoint",
                placeholder="https://example.internal",
            )
            if await self.ask(entry) is None:
                return
            origin = entry.value.strip()
        model_entry = TextEntryView(
            name="provider-model",
            title=option.label,
            label="Model identifier",
            initial=option.default_model,
            placeholder="model id",
        )
        if await self.ask(model_entry) is None:
            self.say(Level.OPTIONAL, "No provider was configured.")
            return
        model = model_entry.value.strip()

        confirmed = await self.ask(
            ApprovalView(
                name="provider-confirm",
                title="",
                body=render_provider_endpoint(option, model, self.body_width),
                approve_label="Save this provider binding",
                decline_label="Cancel",
            )
        )
        if confirmed != "approve":
            self.say(Level.OPTIONAL, "No provider was configured.")
            return
        try:
            self.runtime.save_provider_binding(option, model, https_origin=origin)
        except RuntimeError_ as error:
            self._report(error)
            return
        status = await self._run_confidential(
            "Provider API key",
            self.runtime.store_provider_credential,
            fallback_command="yoetz provider credential set",
        )
        posture = await self.runtime.provider_posture()
        if status is None:
            self.say(
                Level.UNPROVEN,
                "Provider binding saved, without a key",
                ("The endpoint is recorded. No API key was stored.",),
            )
            return
        self.say(Level.ACTIVE, "", render_provider_stored(posture, self.body_width))
        await self._offer_provider_test()

    async def _offer_provider_test(self) -> None:
        choice = await self.ask(
            SelectionView(
                name="provider-test",
                title="Test the connection now?",
                options=[
                    Option("test", "Run a bounded connection test"),
                    Option("skip", "Not now", "Local verification is unaffected either way."),
                ],
                hint="enter to choose · esc to skip",
            )
        )
        if choice != "test":
            self.say(
                Level.UNPROVEN,
                "The provider connection has not been tested",
                ("Local deterministic verification is ready regardless.",),
            )
            return
        # No bounded live-probe operation is exposed by the service yet, and a
        # test that cannot run must never be reported as one that passed.
        self.say(
            Level.UNPROVEN,
            "A live provider test is not available from this build",
            (
                "Yoetz will not report a connection as working without probing it.",
                "Your binding and key are stored; deeper review stays off until",
                "your privacy choice allows it and a probe succeeds.",
            ),
            details=("no bounded provider probe operation is exposed by the local service",),
        )

    async def command_service(self) -> None:
        posture = await self.runtime.vault_posture()
        self.say(
            Level.ACTIVE,
            "Local service",
            (
                f"Reachable: {'yes' if posture.reachable else 'no'}",
                f"State: {posture.state or 'not running'}",
                f"Secure storage: {posture.vault_mode or 'not set up'}",
            ),
        )
        choice = await self.ask(
            SelectionView(
                name="service",
                options=[
                    Option("unlock", "Unlock", "Uses the secure prompt on this terminal."),
                    Option("init", "Set up a passphrase", "First install only."),
                    Option("lock", "Lock now"),
                    Option("stop", "Stop the service", "Yoetz will not restart it for you."),
                ],
                hint="enter to choose · esc to go back",
            )
        )
        if choice is None:
            return
        if choice == "unlock":
            await self._run_confidential("Unlock Yoetz", self.runtime.unlock_vault)
        elif choice == "init":
            await self._run_confidential(
                "Choose a Yoetz passphrase",
                self.runtime.initialize_passphrase_vault,
                fallback_command="yoetz service unlock --initialize",
            )
        elif choice == "lock":
            state = await self.runtime.service_lock()
            self.say(Level.VERIFIED, f"Locked. Service state: {state}")
        else:
            confirmed = await self.ask(
                ApprovalView(
                    name="service-stop",
                    title="Stop the local service?",
                    body=(
                        "Yoetz will stop responding to checks and receipts until",
                        "you start it again with 'yoetz service run'.",
                    ),
                    approve_label="Yes, stop it",
                    decline_label="No, leave it running",
                    default_to_safe=True,
                )
            )
            if confirmed == "approve":
                state = await self.runtime.service_stop()
                self.say(Level.VERIFIED, f"Stopping. Service state: {state}")
            else:
                self.say(Level.OPTIONAL, "The service is still running.")

    async def command_work(self) -> None:
        recent = self.runtime.opened_titles
        rows = [Option(f"task:{title}", title, "opened in this session") for title in recent]
        rows.append(Option("open", "Open a task by name"))
        chosen = await self.ask(
            SelectionView(
                name="work",
                title="Your work",
                body=()
                if recent
                else (
                    "Yoetz records work per task. The local service does not keep",
                    "a browsable index of every task, so open one by the title the",
                    "agent used for it.",
                ),
                options=rows,
                searchable=bool(recent),
                hint="enter to choose · esc to go back",
            )
        )
        if chosen is None:
            return
        title = chosen[5:] if chosen.startswith("task:") else None
        if title is None:
            entry = TextEntryView(
                name="work-open",
                title="Open a task",
                label="Task title",
                placeholder="the title the agent used",
            )
            if await self.ask(entry) is None:
                return
            title = entry.value.strip()
        self.say(Level.ACTIVE, f"Opening {title}")
        detail = await self.runtime.open_task(title)
        self._active_task_title = title
        self.settle(Level.ACTIVE, title, render_work_detail(detail, self.body_width))

    async def command_check(self) -> None:
        title = await self._require_task()
        if title is None:
            return
        chosen = await self.ask(
            SelectionView(
                name="check-mode",
                title="How should Yoetz check this?",
                options=[Option(mode.value, mode.label, mode.description) for mode in CheckMode],
                hint="enter to choose · esc to cancel",
            )
        )
        if chosen is None:
            return
        mode = CheckMode(chosen)
        self.say(Level.ACTIVE, f"Checking {title} ({mode.label.lower()})")
        verdict, lines = await self.runtime.run_check(title, mode)
        level = Level.VERIFIED if verdict == "pass" else Level.UNPROVEN
        self.settle(level, f"Check complete: {verdict}", lines)

    async def command_receipt(self) -> None:
        title = await self._require_task()
        if title is None:
            return
        chosen = await self.ask(
            SelectionView(
                name="receipt-format",
                title="Receipt format",
                options=[
                    Option("markdown", "Markdown", "Readable, good for sharing."),
                    Option("text", "Plain text"),
                    Option("json", "JSON", "Exact machine-readable record."),
                ],
                hint="enter to choose · esc to cancel",
            )
        )
        if chosen is None:
            return
        self.say(Level.ACTIVE, f"Building a receipt for {title}")
        summary = await self.runtime.build_receipt(title, chosen)
        self.settle(
            Level.ACTIVE,
            f"Receipt for {title}",
            render_receipt(summary, self.body_width),
            details=(f"format={chosen}", f"task_id={summary.subject_id}"),
        )

    async def _require_task(self) -> str | None:
        if self._active_task_title is not None:
            return self._active_task_title
        await self.command_work()
        return self._active_task_title
