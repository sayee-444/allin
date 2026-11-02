"""The permanent marks the Windows and Main's Chat show on their border, before the title."""

from collections.abc import Callable
from typing import Literal

from allin.ui.screen import ScreenUpdate, TitleMark

DEVASTATION_MARK = "--devastation: Main answers its Confirmations"

type UnconfinedArea = Literal["files", "network", "signals and abstract sockets"]
"""What a kernel's Landlock may be unable to confine tools in (ADR-0016)."""


class BorderMarks:
    """Each mark is set by what owns it; every change shows them all, in a fixed order."""

    def __init__(self, show: Callable[[ScreenUpdate], None]) -> None:
        self._show = show
        self._devastation = False  # Main answers its Confirmations
        self._unconfined: tuple[UnconfinedArea, ...] = ()  # for as long as Main runs

    def devastation(self, on: bool) -> None:
        self._devastation = on
        self._show(TitleMark(self._text()))

    def unconfined(self, missing: tuple[UnconfinedArea, ...]) -> None:
        self._unconfined = missing
        self._show(TitleMark(self._text()))

    def _text(self) -> str:
        parts = [DEVASTATION_MARK if self._devastation else ""]
        if self._unconfined:
            parts.append(f"tools not fully confined: no {', '.join(self._unconfined)}")
        return " · ".join(part for part in parts if part)
