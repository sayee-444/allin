"""Main's single dispatcher: the only writer of Main's state (ADR-0001).

It takes Main's events in arrival order, on one thread, and hands each to the collaborator it
owns and calls from that same thread: ``Reporter`` (Main's Chat and the Logs), ``OpenWindows``
(the Windows and their Drafts), ``TurnQueue`` (the queue and the running Turns), ``Recovery``
(MainState and what a dead Main left) and ``MainCommands``.
"""

import queue
import time
from collections.abc import Callable

from allin.core.errors import AllInError
from allin.core.ids import MainId, SessionId, SupervisorName, parse_supervisor_name
from allin.core.origin import HUMAN
from allin.core.settings import SessionSettings
from allin.main.boxes import MainBoxes
from allin.main.commands import MainCommands
from allin.main.devastation import Devastation
from allin.main.events import (
    BoxAnswered,
    ConfirmationRequested,
    DraftReceived,
    ErrorOccurred,
    InputMeasured,
    InputReceived,
    MainCommandEntered,
    MainEvent,
    OrganizerActed,
    OrganizerClosed,
    OrganizerConnected,
    QueuedWithdrawn,
    ServerChanged,
    ServerProgress,
    Shutdown,
    SlotFreed,
    StepEnded,
    SupervisorAdopted,
    ThemesChanged,
    Tick,
    TurnEnded,
    TurnOutput,
    TurnRaw,
    WindowClosed,
    WindowConnected,
    WindowUsed,
)
from allin.main.key_commands import KeyCommands
from allin.main.kit_commands import KitCommands
from allin.main.kits import Kits
from allin.main.links import Link, LinkResolver
from allin.main.log import Log
from allin.main.notices import LoadedNotices
from allin.main.open_windows import OpenWindow, OpenWindows
from allin.main.organizer import OpenOrganizer, Organizer, RowSource, command_for, no_organizer
from allin.main.profile_commands import ProfileCommands
from allin.main.profile_switch import ProfileSwitch
from allin.main.profiles import Profile, ProfileStore
from allin.main.recovery import CloseOrphanWindow, Recovery
from allin.main.reporting import Reporter
from allin.main.server_commands import ServerCommands
from allin.main.sessions import SessionStore
from allin.main.settings_commands import SettingsCommands
from allin.main.settings_files import LoadedSettings, MainSettingsFiles
from allin.main.state import MainStateStore
from allin.main.themes import NO_FONT_PROBE, FontProbe, Looks, ThemeBook, ThemeKeeper
from allin.main.turn_queue import Servers, SubmitTurn, TurnQueue
from allin.ui.screen import KeyBindings, ScreenUpdate
from allin.ui.shortcuts import effective_keys


class Dispatcher:
    def __init__(
        self,
        inbox: queue.Queue[MainEvent],
        main_chat: Callable[[str], None],
        main_log: Log,
        sessions: SessionStore,
        open_window: OpenWindow,
        submit_turn: SubmitTurn,
        main_id: MainId,
        settings: LoadedSettings,
        settings_files: MainSettingsFiles,
        profiles: ProfileStore,
        profile: Profile,
        state: MainStateStore,
        close_orphan_window: CloseOrphanWindow,
        links: LinkResolver,
        servers: Servers,
        main_screen: Callable[[ScreenUpdate], None],
        devastation: bool,
        notices: LoadedNotices,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        *,
        fonts: FontProbe = NO_FONT_PROBE,
        open_organizer: OpenOrganizer = no_organizer,
    ) -> None:
        """``main_screen`` takes what Main's Chat shows besides its lines (boxes, marks);
        ``devastation`` is whether a human lit it on the command line; ``profile`` is the
        active Profile, whose Sysprompts Sessions are born with."""
        sysprompts = profile.sysprompts
        self._inbox = inbox
        self._main_id = main_id
        self._reporter = Reporter(main_chat, main_log, sessions)
        self._windows = OpenWindows(open_window, self._reporter, sessions, links)
        self._default_link = _default_link(links, settings)
        self._boxes = MainBoxes(main_screen, self._windows)
        self._notices = notices.types

        def notify_theme(
            session_id: SessionId | None, settings: SessionSettings | None, problem: str
        ) -> None:
            def show(kind: str, title: str, text: str) -> None:
                if session_id is not None:
                    fields = {"kind": kind, "title": title, "text": text}
                    self._reporter.record(session_id, "notice", f"notice: {title}", **fields)
                else:
                    self._reporter.log_main("notice", kind=kind, title=title, text=text)
                self._boxes.notify(session_id, title, text, kind)

            self._notices.tell("theme", settings, {"problem": problem}, show)

        self._themes = ThemeKeeper(
            Looks(ThemeBook(profile.folder / "themes"), fonts),
            sessions,
            self._windows.open_ids,
            self._windows.send,
            main_screen,
            notify_theme,
        )
        self._windows.use_themes(self._themes)
        self._windows.watch_asking(self._boxes.asking)
        self._windows.set_maximum(settings.current.windows.max)
        self._devastation = Devastation(
            devastation, main_screen, self._windows, sessions, self._boxes, self._reporter, main_id
        )
        self._turns = TurnQueue(
            self._reporter,
            self._windows,
            sessions,
            sysprompts,
            settings.current.parallel,
            submit_turn,
            links,
            servers,
            self._boxes,
            self._devastation,
            notices.types,
            main_id,
            settings.current.step.stall,
            settings.current.admission.patience,
            clock,
            wall_clock,
        )
        self._recovery = Recovery(
            self._reporter,
            self._windows,
            self._turns,
            sessions,
            main_id,
            state,
            close_orphan_window,
        )
        self._start_problems = [*settings.problems, *notices.problems]
        self._links = links
        self._settings_commands = settings_commands = SettingsCommands(
            self._reporter,
            self._turns,
            sessions,
            main_id,
            settings,
            settings_files,
            sysprompts,
            self._devastation,
            notices.types,
            links,
        )
        self._server_commands = ServerCommands(
            servers,
            self._reporter,
            self._turns,
            links,
            sessions,
            self._windows,
            lambda: _supervisor_of(settings_commands.current.session),
            inbox.put,
        )
        self._switch = ProfileSwitch(
            self._reporter,
            profiles,
            self._windows,
            self._turns,
            self._recovery,
            profile,
            self._enter,
        )
        self._profile_commands = ProfileCommands(
            self._reporter, profiles, self._switch, sessions, main_id, self._boxes
        )
        settings_commands.show_profiles(self._profile_commands.profiles_of)
        settings_commands.check_theme = self._themes.book.check
        settings_commands.windows_max_changed = self._windows.set_maximum
        settings_commands.looks_changed = self._looks_changed
        self._commands = MainCommands(
            self._reporter,
            self._windows,
            self._turns,
            sessions,
            main_id,
            settings_commands,
            sysprompts,
            self._boxes,
            self._devastation,
            self._server_commands,
            self._profile_commands,
            KitCommands(
                self._reporter,
                Kits(profiles.data_dir / "servers"),
                self._switch,
                settings_commands,
                notices.types,
                self._boxes,
                profiles.data_dir / "servers",
            ),
            KeyCommands(
                self._reporter, settings_commands, sessions, main_id, self._windows, main_screen
            ),
        )
        self._rows = RowSource(
            sessions,
            self._windows,
            self._turns,
            self._boxes,
            lambda: profiles.sessions(self._switch.active),
        )
        self._organizer = Organizer(
            open_organizer, self._rows.rows, lambda: self._themes.main_theme, self._reporter
        )
        self._commands.use_organizer(self._organizer, self._rows.rows)
        if settings.current.keys:  # Main's Chat starts with the defaults
            main_screen(KeyBindings(effective_keys(settings.current.keys)))
        self._themes.main(settings.current)

    def theme_stamps(self) -> dict[str, float]:
        """The Themes' modification times, for the watch on their files (any thread)."""
        return self._themes.stamps()

    def _looks_changed(self) -> None:
        self._themes.main(self._settings_commands.current)
        self._themes.refresh()

    @property
    def commands(self) -> MainCommands:
        """The Command mains, as registered: what /help is built from."""
        return self._commands

    def run(self) -> None:
        """Handle events until a Shutdown event or the /exit command."""
        self._reporter.report(None, "main_started", "main started", main_id=self._main_id)
        for problem in self._start_problems:  # Main runs on without what failed
            self._reporter.error(problem)
        self._devastation.start()
        self._turns.watch(self._default_link)  # Main tells how its Server is from the start
        self._recovery.close_left_open()
        self._recovery.restore()
        while self._handle(event := self._inbox.get()):
            self._switch.check()  # once the last Turn it waited for ended
            self._recovery.save()
            if not isinstance(event, TurnOutput | TurnRaw):  # what streams changes no row
                self._organizer.refresh()
        self._organizer.close("main stopped")
        self._turns.stop()
        self._take_turn_events()
        # Windows, queue and running Turns as they are: a restart restores them.
        self._recovery.save()
        self._turns.cancel_running()
        self._windows.close_all()
        self._reporter.log_main("main_stopped", main_id=self._main_id)

    def _enter(self, profile: Profile) -> None:
        """Use the Settings and Sysprompts of the Profile Main switched to."""
        files = MainSettingsFiles(profile.settings)
        loaded = files.load()
        for problem in loaded.problems:  # Main runs on without what failed, as on start
            self._reporter.error(problem)
        self._themes.book.use(profile.folder / "themes")
        self._settings_commands.enter(loaded, files, profile.sysprompts)
        self._turns.use_sysprompts(profile.sysprompts)
        self._commands.use_sysprompts(profile.sysprompts)
        self._default_link = _default_link(self._links, loaded)
        self._turns.watch(self._default_link)

    def _take_turn_events(self) -> None:
        """Handle what Turns and drafts reported before Main stopped, so a Turn that already
        ended is saved instead of being cancelled and the last draft is kept; no command or
        other Window event is taken any more."""
        while True:
            try:
                event = self._inbox.get(block=False)
            except queue.Empty:
                return
            if isinstance(event, TurnOutput | StepEnded | TurnEnded | DraftReceived):
                self._handle(event)

    def _handle(self, event: MainEvent) -> bool:
        match event:
            case WindowConnected(session_id):
                self._reporter.record(session_id, "window_connected", "window connected")
                self._turns.window_connected(session_id)
                self._boxes.window_opened(session_id)
                self._devastation.mark(session_id)
            case WindowUsed(session_id):
                self._windows.touch(session_id)
            case InputReceived(session_id, text, interrupt):
                self._windows.touch(session_id)
                self._windows.clear_draft(session_id)  # the Window's input is empty now
                if interrupt:  # Shift+Enter: the running Turn is cut, its text goes next
                    self._turns.interrupt(session_id)
                if text.strip():
                    queued = self._turns.queued_count(session_id)
                    self._commands.run(f"/send {session_id} {text}")
                    if interrupt and self._turns.queued_count(session_id) > queued:
                        self._turns.jump(session_id)
                self._turns.send_turn_state(session_id)
            case QueuedWithdrawn(session_id):
                self._turns.withdraw(session_id)
            case DraftReceived(session_id, blocks):
                self._windows.touch(session_id)
                self._windows.keep_draft(session_id, blocks)
            case TurnOutput(session_id, kind, text):
                self._turns.output(session_id, kind, text)
            case TurnRaw(session_id, raw):
                self._turns.progress(session_id)
                self._reporter.raw(session_id, raw)
            case ServerProgress(server):
                self._turns.server_progress(server)
            case InputMeasured(server, session_id, ticket, tokens):
                self._turns.measured(server, session_id, ticket, tokens)
            case Tick():
                self._turns.tick()
            case ThemesChanged():
                self._looks_changed()
            case StepEnded(session_id, step):
                self._turns.step_ended(session_id, step)
            case TurnEnded(session_id, steps, error):
                self._turns.turn_ended(session_id, steps, error)
            case WindowClosed(session_id, returncode):
                if self._windows.closed(session_id, returncode):  # not one Main closed itself
                    self._turns.cancel(session_id, "window closed")
            case ErrorOccurred(error, session_id):
                self._windows.show_error(error, session_id)
            case ServerChanged(server, state):
                self._turns.server_changed(server, state)
            case SlotFreed(server):
                self._turns.slot_freed(server)
            case SupervisorAdopted(name):
                self._server_commands.adopted(name)
            case ConfirmationRequested(session_id, title, text):
                self._turns.confirmation_requested(session_id, title, text)
            case BoxAnswered(box_id, answer):
                self._boxes.answered(box_id, answer)
            case MainCommandEntered(text, origin):
                return self._commands.run(text, origin)
            case OrganizerConnected():
                self._organizer.connected()
            case OrganizerActed(action, session_id):
                # The same Command main, from a human: the Organizer is part of Main (ADR-0020).
                place = self._rows.place(SessionId(session_id))
                command = command_for(action, session_id, place)
                if command is not None:
                    return self._commands.run(command, HUMAN)
            case OrganizerClosed(pid):
                self._organizer.closed(pid)
            case Shutdown():
                return False
        return True


def _default_link(links: LinkResolver, settings: LoadedSettings) -> Link:
    """Where Main's default Enlace leads; Main's ``base_url`` if it cannot be resolved, which
    each Session linked to it tells when its Prompt would start."""
    try:
        return links.resolve(settings.current.session)
    except AllInError:
        return links.observed()


def _supervisor_of(settings: SessionSettings) -> SupervisorName | None:
    """The Supervisor of an Enlace; ``None`` for Main's ``base_url``."""
    return parse_supervisor_name(settings.server) if settings.server is not None else None
