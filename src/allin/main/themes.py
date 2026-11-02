"""Main's side of the Themes (#47, ADR-0018): it reads and checks them, keeps each one's last
valid version, resolves what each Window and Main's Chat shows, and watches their files.

A Window only applies what Main resolved; nothing a Theme says reaches xterm or fontconfig
without passing ``allin.core.themes``.
"""

import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, JsonValue

from allin.core.errors import AllInError, ThemeInvalid, ThemeNotFound
from allin.core.ids import SessionId
from allin.core.settings import MainSettings, SessionSettings
from allin.core.themes import (
    AUTO,
    BUILTIN,
    DEFAULT_THEME,
    PALETTE,
    GlyphSet,
    Theme,
    font_pattern,
    is_theme_name,
    parse_theme,
    with_keys,
)
from allin.ipc.messages import ThemeSet
from allin.main.sessions import Session, SessionStore
from allin.notices import NoticeEvent, NoticeType
from allin.ui.screen import ApplyTheme, ScreenUpdate


# The Notice that tells what a Theme could not do: one type more, turned off per Session like
# any other (#28).
def _never(_event: NoticeEvent, _params: dict[str, JsonValue]) -> None:
    """A Theme's problems are told when a Theme is applied, never after a Step."""


THEME_NOTICE = NoticeType(name="theme", title="Theme", text="{problem}", params={}, check=_never)

_SUFFIX = ".toml"


@dataclass
class _Known:
    stamp: float  # the file's modification time when last read
    theme: Theme | None  # its last valid version, if it ever had one
    problem: str | None = None  # why the file as it is now does not load


class ThemeBook:
    """The Themes of a Profile's ``themes/``, read again whenever their file changes."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder
        self._known: dict[str, _Known] = {}

    def use(self, folder: Path) -> None:
        """The Themes of another Profile (``/profile switch``)."""
        self._folder = folder
        self._known = {}

    def names(self) -> list[str]:
        """The Themes there are, by name."""
        if not self._folder.is_dir():
            return []
        found = (path.stem for path in self._folder.glob(f"*{_SUFFIX}") if path.is_file())
        return sorted(name for name in found if is_theme_name(name))

    def exists(self, name: str) -> bool:
        return is_theme_name(name) and (self._folder / f"{name}{_SUFFIX}").is_file()

    def check(self, name: str) -> None:
        """``ThemeNotFound``, saying which there are, unless ``name`` is one; ``default``
        always is, since AllIn has it built in."""
        if name != DEFAULT_THEME and not self.exists(name):
            raise ThemeNotFound(theme=name, available=list(self.names()))

    def stamps(self) -> dict[str, float]:
        """Each Theme file's modification time, for the watch to see what changed."""
        stamps: dict[str, float] = {}
        for name in self.names():
            try:
                stamps[name] = (self._folder / f"{name}{_SUFFIX}").stat().st_mtime
            except OSError:
                continue
        return stamps

    def theme(self, name: str) -> tuple[Theme, list[str]]:
        """The Theme called ``name`` and what went wrong finding it: one that does not exist
        is ``default``; one whose file is broken now, its last valid version, or ``default``
        if it never had one; and ``default`` itself, missing or broken, the built-in look."""
        problems: list[str] = []
        if name != DEFAULT_THEME:
            theme, problem = self._read(name)
            if theme is not None:
                return theme, [problem] if problem else []
            problems.append(problem or f"theme {name} not found; using {DEFAULT_THEME}")
        theme, problem = self._read(DEFAULT_THEME)
        if problem:
            problems.append(problem)
        return theme or BUILTIN, problems

    def _read(self, name: str) -> tuple[Theme | None, str | None]:
        if not is_theme_name(name):
            return None, None
        path = self._folder / f"{name}{_SUFFIX}"
        try:
            stamp = path.stat().st_mtime
        except OSError:
            self._known.pop(name, None)
            return None, None
        known = self._known.get(name)
        if known is None or known.stamp != stamp:
            last = known.theme if known is not None else None
            try:
                known = _Known(stamp, parse_theme(name, path.read_text(encoding="utf-8")))
            except ThemeInvalid as error:
                kept = "; it keeps its last valid version" if last is not None else ""
                known = _Known(stamp, last, f"{error}{kept}")
            except (OSError, UnicodeDecodeError) as error:
                known = _Known(stamp, last, str(ThemeInvalid(theme=name, reason=str(error))))
            self._known[name] = known
        return known.theme, known.problem


@dataclass(frozen=True)
class FontProbe:
    """What fontconfig says: the family it gives for a pattern, and whether some installed
    letter has a glyph set's symbols. ``None``: fontconfig is not there to ask."""

    match: Callable[[str], str | None]
    has_glyphs: Callable[[GlyphSet], bool]


# A symbol only each glyph set has: a Nerd Font's (powerline) and a box-drawing corner.
_GLYPH_PROBES: dict[GlyphSet, str] = {"nerd": "e0b0", "unicode": "256d"}


def fontconfig_probe() -> FontProbe:
    """Ask the installed fontconfig, as xterm will when it opens a Window."""

    def match(family: str) -> str | None:
        if shutil.which("fc-match") is None:
            return None
        found = _run(["fc-match", "-f", "%{family}", font_pattern(family)])
        return None if found is None else found.split(",")[0].strip()

    def has_glyphs(glyphs: GlyphSet) -> bool:
        probe = _GLYPH_PROBES.get(glyphs)
        if probe is None or shutil.which("fc-list") is None:
            return True
        found = _run(["fc-list", f":charset={probe}", "family"])
        return bool(found and found.strip())

    return FontProbe(match, has_glyphs)


# Asks nothing: every letter is there, every glyph set shows (tests, and no fontconfig).
NO_FONT_PROBE = FontProbe(lambda _: None, lambda _: True)


def _run(command: list[str]) -> str | None:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


# fontconfig's own names for a kind of letter: always there, as whichever letter it picks.
_GENERIC_FAMILIES = frozenset({"monospace", "mono", "sans-serif", "sans", "serif", "fixed"})


@dataclass(frozen=True)
class Look:
    """What a Window or Main's Chat shows: its Theme, its own keys and its border's colour
    already in it, and what could not be done as the Theme asks."""

    theme: Theme
    problems: list[str] = field(default_factory=list[str])


class Looks:
    def __init__(self, book: ThemeBook, fonts: FontProbe) -> None:
        self.book = book
        self._fonts = fonts
        # What fontconfig said, asked once per letter and glyph set until a Window opens.
        self._matched: dict[str, str | None] = {}
        self._glyphs: dict[GlyphSet, bool] = {}

    def forget_fonts(self) -> None:
        self._matched.clear()
        self._glyphs.clear()

    def _match(self, family: str) -> str | None:
        if family not in self._matched:
            self._matched[family] = self._fonts.match(family)
        return self._matched[family]

    def _has_glyphs(self, glyphs: GlyphSet) -> bool:
        if glyphs not in self._glyphs:
            self._glyphs[glyphs] = self._fonts.has_glyphs(glyphs)
        return self._glyphs[glyphs]

    def of_session(self, settings: SessionSettings, border_color: str | None) -> Look:
        theme, problems = self.book.theme(settings.theme)
        try:
            theme = with_keys(theme, settings.loose_keys())
        except ValueError as error:  # every key was checked when set: only a stale file
            problems.append(f"its own theme keys were left out: {error}")
        if theme.border.color == AUTO:
            color = border_color or PALETTE[0]
            theme = theme.model_copy(
                update={"border": theme.border.model_copy(update={"color": color})}
            )
        return self.with_fonts(theme, problems)

    def of_main(self, settings: MainSettings) -> Look:
        # Main's Chat is no Session: with ``auto`` its border keeps the terminal's colour.
        theme, problems = self.book.theme(settings.theme)
        return self.with_fonts(theme, problems)

    def with_fonts(self, theme: Theme, problems: list[str]) -> Look:
        """Say which letter fontconfig gives instead of a missing one, and step a glyph set
        no installed letter has down to one that shows."""
        family = theme.font.family
        found = None if family.casefold() in _GENERIC_FAMILIES else self._match(family)
        if found is not None and found.casefold() != family.casefold():
            problems.append(f"font {family} is not installed; {found} is used instead")
        glyphs = theme.glyphs.set
        while glyphs != "ascii" and not self._has_glyphs(glyphs):
            lower: GlyphSet = "unicode" if glyphs == "nerd" else "ascii"
            problems.append(f"no installed font has the {glyphs} glyphs; {lower} is used")
            glyphs = lower
        if glyphs != theme.glyphs.set:
            theme = theme.model_copy(
                update={"glyphs": theme.glyphs.model_copy(update={"set": glyphs})}
            )
        return Look(theme, problems)


class ThemeWatch:
    """Looks once a second at the modification time of the Themes, and tells Main when one
    changed, through its inbox (ADR-0001), as ``server_watch`` does for the Servers."""

    def __init__(self, stamps: Callable[[], dict[str, float]], changed: Callable[[], None]) -> None:
        self._stamps = stamps
        self._changed = changed
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="theme-watch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _watch(self) -> None:
        last = self._safe_stamps()
        while not self._stop.wait(1.0):
            now = self._safe_stamps()
            if now != last:
                last = now
                self._changed()

    def _safe_stamps(self) -> dict[str, float]:
        try:
            return self._stamps()
        except (OSError, AllInError):
            return {}


# Tells a Theme's problem to a Session's Window (with its Settings) or to Main's Chat (``None``).
type Notify = Callable[[SessionId | None, SessionSettings | None, str], None]


class ThemeKeeper:
    """What each open Window and Main's Chat show, kept as last sent: it resolves them again
    when a Theme's file or a Setting changes, sends only what differs, and tells what could not
    be done in a ``theme`` Notice: each problem once, and again whenever the look changes."""

    def __init__(
        self,
        looks: Looks,
        sessions: SessionStore,
        open_ids: Callable[[], list[SessionId]],
        send: Callable[[SessionId, BaseModel], None],
        main_screen: Callable[[ScreenUpdate], None],
        notify: Notify,
    ) -> None:
        self._looks = looks
        self._sessions = sessions
        self._open_ids = open_ids
        self._send = send
        self._main_screen = main_screen
        self._notify = notify
        self._sent: dict[SessionId, Theme] = {}
        self._told: dict[SessionId | None, list[str]] = {}
        # What a Window about to open could not show as asked, told once it is open.
        self._opening: dict[SessionId, tuple[SessionSettings, list[str]]] = {}
        self._main: Theme | None = None

    @property
    def book(self) -> ThemeBook:
        return self._looks.book

    def stamps(self) -> dict[str, float]:
        return self._looks.book.stamps()

    def opening(self, session: Session) -> Theme:
        """The Theme of a Window about to open: its xterm is born with it. A Session from
        before Themes gets its border colour now, and keeps it."""
        session = self._sessions.with_border_color(session)
        self._looks.forget_fonts()  # a letter installed since shows in this new Window
        self._told.pop(session.id, None)
        look = self._looks.of_session(session.settings, session.border_color)
        self._opening[session.id] = (session.settings, look.problems)
        return look.theme

    def opened(self, session_id: SessionId, theme: Theme) -> None:
        """Main sent the Window its Theme: what it could not do is told now, to it."""
        self._sent[session_id] = theme
        opening = self._opening.pop(session_id, None)
        if opening is not None:
            self._tell(session_id, *opening)

    def closed(self, session_id: SessionId) -> None:
        self._sent.pop(session_id, None)
        self._told.pop(session_id, None)

    def refresh(self) -> None:
        """Resolve every open Window's Theme and Main's again; send those that changed."""
        for session_id in self._open_ids():
            try:
                session = self._sessions.load(session_id)
            except AllInError:
                continue  # its Window shows what it last had
            look = self._looks.of_session(session.settings, session.border_color)
            if self._sent.get(session_id) != look.theme:
                self._sent[session_id] = look.theme
                self._told.pop(session_id, None)
                self._send(session_id, ThemeSet(theme=look.theme))
            self._tell(session_id, session.settings, look.problems)

    def main(self, settings: MainSettings) -> None:
        """Main's Chat shows its Theme; what it cannot do is told when the human chooses or
        changes it, or its file changes, never on each start."""
        look = self._looks.of_main(settings)
        started = self._main is not None
        if look.theme != self._main:
            self._main = look.theme
            self._told.pop(None, None)
            self._main_screen(ApplyTheme(look.theme, "main"))
        if started:
            self._tell(None, None, look.problems)
        else:
            self._told[None] = look.problems

    def _tell(
        self, target: SessionId | None, settings: SessionSettings | None, problems: list[str]
    ) -> None:
        told = self._told.get(target, [])
        self._told[target] = problems
        for problem in problems:
            if problem not in told:
                self._notify(target, settings, problem)
