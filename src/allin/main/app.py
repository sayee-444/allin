"""Main's process: curses UI thread, dispatcher thread, executor threads, and one Window per
open Session."""

import curses
import queue
import sys
import threading
from pathlib import Path

from allin.core.errors import AllInError, MainAlreadyRunning
from allin.core.ids import ROOT_MAIN_ID
from allin.ipc.channel import remove_stale_fifos
from allin.main.dispatcher import Dispatcher
from allin.main.events import (
    BoxAnswered,
    MainCommandEntered,
    MainEvent,
    Shutdown,
    ThemesChanged,
)
from allin.main.executors import TurnExecutors
from allin.main.links import LinkResolver
from allin.main.lock import MainLock
from allin.main.log import Log
from allin.main.notices import load_notice_types
from allin.main.profiles import Profile, ProfileStore
from allin.main.server_links import ServerLinks
from allin.main.sessions import SessionStore
from allin.main.settings_files import MainSettingsFiles
from allin.main.signals import ShutdownSignals
from allin.main.state import MainStateStore
from allin.main.themes import ThemeWatch, fontconfig_probe
from allin.main.ticker import Ticker
from allin.main.turn import OpenAiLlm
from allin.main.windows import OrganizerOpener, WindowOpener, close_orphan_window
from allin.ui.screen import ChatScreen

_SHUTDOWN_GRACE = 2.0


def run(
    data_dir: Path,
    seed_sysprompts: Path,
    profile_name: str | None,
    devastation: bool = False,
    *,
    seed_themes: Path | None = None,
) -> None:
    """Start the root Main on ``profile_name``'s Profile, the last one used if ``None``."""
    inbox: queue.Queue[MainEvent] = queue.Queue()
    # Caught from the first moment (#19): a signal while Main starts waits in the inbox, and
    # its Dispatcher, once it runs, shuts down as /exit does. Without this, the default action
    # kills Main, or ncurses' own SIGTERM handler ends it, with no main_stopped in its Log.
    shutdown_signals = ShutdownSignals(lambda: inbox.put(Shutdown()))
    shutdown_signals.install()
    try:
        try:
            lock, profile = start_root(data_dir, seed_sysprompts, profile_name, seed_themes)
        except MainAlreadyRunning as error:
            sys.exit(f"{error}: another root Main already runs on {data_dir}")
        except AllInError as error:
            sys.exit(str(error))
        profiles = ProfileStore(data_dir, seed_sysprompts, seed_themes)
        curses.wrapper(_run, inbox, data_dir, profiles, profile, devastation)
        del lock  # held until here, and dropped by the kernel however the process ends
    finally:
        shutdown_signals.uninstall()


def start_root(
    data_dir: Path,
    seed_sysprompts: Path,
    profile_name: str | None,
    seed_themes: Path | None = None,
) -> tuple[MainLock, Profile]:
    """Take the root Main's lock and open its Profile, migrating what there was before
    Profiles the first time; a Profile that does not exist is an error that creates nothing."""
    profiles = ProfileStore(data_dir, seed_sysprompts, seed_themes)
    profiles.check(profile_name)  # before the lock, which is a file too
    # One root Main per data dir, whatever its Profile, before anything touches the state
    # another Main may be using (ADR-0007).
    lock = MainLock(data_dir / "state" / "main.lock")
    lock.acquire()
    profiles.migrate()
    return lock, profiles.open(profile_name)


def _run(
    stdscr: curses.window,
    inbox: queue.Queue[MainEvent],
    data_dir: Path,
    profiles: ProfileStore,
    profile: Profile,
    devastation: bool,
) -> None:
    settings_files = MainSettingsFiles(profile.settings)
    loaded = settings_files.load()  # a broken file is told by the Dispatcher, on start
    settings = loaded.current
    title = "AllIn · Main  (/new, /open <sid>, /send <sid> <text>, /raw on|off [sid], /set, /session <sid>, /key, /help, /exit)"
    screen = ChatScreen(stdscr, title, commands=True)  # its Atajos may run Command mains
    log_dir = data_dir / "logs" / "main"
    log_dir.mkdir(parents=True, exist_ok=True)
    remove_stale_fifos(data_dir / "fifo")  # no Window outlives the Main that opened it
    executors = TurnExecutors(OpenAiLlm, inbox)
    # Main starts without waiting for any Server: each Session waits until its own is ready.
    server_links = ServerLinks(data_dir, inbox)
    dispatcher = Dispatcher(
        inbox,
        screen.inbox.put,
        Log(log_dir / "log.jsonl"),
        SessionStore(data_dir / "sessions"),
        WindowOpener(data_dir, inbox),
        executors.submit,
        ROOT_MAIN_ID,
        loaded,
        settings_files,
        profiles,
        profile,
        MainStateStore(profile.state),
        close_orphan_window,
        LinkResolver(data_dir / "servers", settings.base_url),
        server_links,
        screen.inbox.put,
        devastation,
        load_notice_types(data_dir / "notices"),  # part of Main: loaded once, here (ADR-0016)
        fonts=fontconfig_probe(),
        open_organizer=OrganizerOpener(data_dir, inbox),
    )
    # Main looks once a second at its Themes' files (#47), as at the Servers'.
    theme_watch = ThemeWatch(dispatcher.theme_stamps, lambda: inbox.put(ThemesChanged()))
    theme_watch.start()

    def dispatch() -> None:
        dispatcher.run()  # closes every Window before returning
        executors.shutdown(_SHUTDOWN_GRACE)  # running Turns are cancelled, never saved
        server_links.stop()
        ticks.stop()
        theme_watch.stop()
        screen.inbox.put(None)

    ticks = Ticker(inbox)  # Main looks for stalled Steps once a second (ADR-0023)
    ticks.start()
    dispatcher_thread = threading.Thread(target=dispatch, name="dispatcher")
    dispatcher_thread.start()
    try:
        screen.run(
            lambda text: inbox.put(MainCommandEntered(text)),
            on_box_closed=lambda closed: inbox.put(BoxAnswered(closed.box_id, closed.answer)),
        )
    except KeyboardInterrupt:
        pass
    finally:
        inbox.put(Shutdown())
        dispatcher_thread.join()
