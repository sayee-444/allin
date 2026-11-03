"""The permanent marks the Windows and Main's Chat show on their border, before the title."""

from collections.abc import Callable
from typing import Literal

from allin.ui.screen import ScreenUpdate, TitleMark

DEVASTATION_MARK = "--devastation: Main answers its Confirmations"
DEVASTATION_INSIDE_MARK = "--devastation inside its Subperfil: Main answers what stays in it"
GRANT_MARK = "@grant: it may give functions and Command sessions"

type UnconfinedArea = Literal["files", "network", "signals and abstract sockets"]
"""What a kernel's Landlock may be unable to confine tools in (ADR-0016)."""


class BorderMarks:
    """Each mark is set by what owns it; every change shows them all, in a fixed order."""

    def __init__(self, show: Callable[[ScreenUpdate], None]) -> None:
        self._show = show
        self._devastation = False  # Main answers its Confirmations
        self._inside = False  # only those of what stays inside its Subperfil (ADR-0026)
        self._grant = False  # the Session has Otorgar (ADR-0025)
        self._unconfined: tuple[UnconfinedArea, ...] = ()  # for as long as Main runs

    def devastation(self, on: bool, inside: bool = False) -> None:
        self._devastation = on
        self._inside = inside
        self._show(TitleMark(self._text()))

    def grant(self, held: bool) -> None:
        self._grant = held
        self._show(TitleMark(self._text()))

    def unconfined(self, missing: tuple[UnconfinedArea, ...]) -> None:
        self._unconfined = missing
        self._show(TitleMark(self._text()))

    def _text(self) -> str:
        devastation = DEVASTATION_INSIDE_MARK if self._inside else ""
        parts = [DEVASTATION_MARK if self._devastation else devastation]
        parts.append(GRANT_MARK if self._grant else "")
        if self._unconfined:
            parts.append(f"tools not fully confined: no {', '.join(self._unconfined)}")
        return " · ".join(part for part in parts if part)
