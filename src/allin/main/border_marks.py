"""The marks on Main's Chat border, each owned by what sets it."""

from collections.abc import Callable

from allin.ui.marks import border_mark
from allin.ui.screen import ScreenUpdate, TitleMark


class MainMarks:
    def __init__(self, main_screen: Callable[[ScreenUpdate], None]) -> None:
        self._main_screen = main_screen
        self._devastation = False
        self._missing: tuple[str, ...] = ()

    def devastation(self, on: bool) -> None:
        self._devastation = on
        self._show()

    def unconfined(self, missing: tuple[str, ...]) -> None:
        """What this kernel cannot confine tools in; it stays for the whole start of Main."""
        self._missing = missing
        self._show()

    def _show(self) -> None:
        self._main_screen(TitleMark(border_mark(self._devastation, self._missing)))
