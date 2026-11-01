"""The boxes Main has on screen: Notices and Confirmations, until they are answered.

Each one shows in Main's Chat and, if it concerns a Session, in that Session's Window; whichever
answers first counts, and it leaves both. One whose Window is closed shows again when the
Window opens, as long as nobody answered it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from itertools import count

from allin.core.ids import BoxId, SessionId
from allin.ipc.messages import BoxHide, BoxShow
from allin.main.open_windows import OpenWindows
from allin.ui.boxes import Box, BoxKind
from allin.ui.screen import HideBox, ShowBox

type MainScreenBox = Callable[[ShowBox | HideBox], None]
type OnAnswer = Callable[[bool], None]


@dataclass(frozen=True)
class _Pending:
    box: Box
    session_id: SessionId | None
    on_answer: OnAnswer | None  # only a Confirmation's


class MainBoxes:
    def __init__(self, main_screen: MainScreenBox, windows: OpenWindows) -> None:
        self._main_screen = main_screen
        self._windows = windows
        self._pending: dict[BoxId, _Pending] = {}
        self._ids = (BoxId(f"b{n}") for n in count(1))

    def confirm(
        self, session_id: SessionId | None, title: str, text: str, on_answer: OnAnswer
    ) -> BoxId:
        """Ask the user yes or no; ``on_answer`` gets the answer once someone gives it."""
        return self._show("confirm", session_id, title, text, on_answer)

    def notify(self, session_id: SessionId | None, title: str, text: str) -> BoxId:
        """Tell the user something; it never waits for anything."""
        return self._show("notice", session_id, title, text, None)

    def answered(self, box_id: BoxId, answer: bool | None) -> None:
        """Someone answered a box, in Main's Chat or in a Window; a late second answer to the
        same box does nothing."""
        pending = self._pending.pop(box_id, None)
        if pending is None:
            return
        self._hide(pending)
        if pending.on_answer is not None:
            pending.on_answer(bool(answer))

    def withdraw(self, box_id: BoxId) -> None:
        """A box no longer valid, e.g. its Turn was cancelled: nobody answers it."""
        pending = self._pending.pop(box_id, None)
        if pending is not None:
            self._hide(pending)

    def window_opened(self, session_id: SessionId) -> None:
        """Show a Window that just opened the boxes of its Session still waiting."""
        for pending in self._pending.values():
            if pending.session_id == session_id:
                self._windows.send(session_id, _show_message(pending.box))

    def _show(
        self,
        kind: BoxKind,
        session_id: SessionId | None,
        title: str,
        text: str,
        on_answer: OnAnswer | None,
    ) -> BoxId:
        box = Box(next(self._ids), kind, title, text)
        self._pending[box.box_id] = _Pending(box, session_id, on_answer)
        self._main_screen(ShowBox(_in_main_chat(box, session_id)))
        if session_id is not None:
            self._windows.send(session_id, _show_message(box))
        return box.box_id

    def _hide(self, pending: _Pending) -> None:
        self._main_screen(HideBox(pending.box.box_id))
        if pending.session_id is not None:
            self._windows.send(pending.session_id, BoxHide(box_id=pending.box.box_id))


def _in_main_chat(box: Box, session_id: SessionId | None) -> Box:
    """Main's Chat shows whose box it is, like its other lines."""
    if session_id is None:
        return box
    return Box(box.box_id, box.kind, f"[{session_id}] {box.title}", box.text)


def _show_message(box: Box) -> BoxShow:
    return BoxShow(box_id=box.box_id, kind=box.kind, title=box.title, text=box.text)
