"""The boxes Main has on screen: Notices, Confirmations and the boxes that take a Secreto's
value, until they are answered.

Each one shows in Main's Chat and, if it concerns a Session, in that Session's Window; whichever
answers first counts, and it leaves both. One whose Window is closed shows again when the
Window opens, as long as nobody answered it. A Secreto's box shows in Main's Chat alone.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from itertools import count

from allin.core.ids import BoxId, SessionId
from allin.ipc.messages import BoxHide, BoxShow
from allin.main.open_windows import OpenWindows
from allin.ui.boxes import Box, BoxKind
from allin.ui.screen import HideBox, ShowBox

type MainScreenBox = Callable[[ShowBox | HideBox], None]
type OnAnswer = Callable[[bool], None]
type OnValue = Callable[[str | None], None]

# A Session a Session's LLM asked to bring into view, with no room for it (ADR-0020): its mark
# is this Notice, until the human brings it or dismisses it.
ATTENTION = "attention"
ATTENTION_TITLE = "Asks for attention"


@dataclass(frozen=True)
class _Pending:
    box: Box
    session_id: SessionId | None
    on_answer: OnAnswer | None  # only a Confirmation's
    kind: str | None  # a Notice's type
    on_value: OnValue | None = None  # only a Secreto's box


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
        return self._show("confirm", session_id, title, text, on_answer, None)

    def ask_secret(self, title: str, text: str, on_value: OnValue) -> BoxId:
        """Ask the human in Main's Chat for a Secreto's value, which the box never shows;
        ``on_value`` gets it, or ``None`` if they left it empty (#124)."""
        box = Box(next(self._ids), "secret", title, text)
        pending = _Pending(box, session_id=None, on_answer=None, kind=None, on_value=on_value)
        self._pending[box.box_id] = pending
        self._main_screen(ShowBox(box))
        return box.box_id

    def asking(self, session_id: SessionId) -> bool:
        """Whether a Confirmation of the Session waits for its answer."""
        return any(
            pending.session_id == session_id and pending.on_answer is not None
            for pending in self._pending.values()
        )

    def noticing(self, session_id: SessionId) -> bool:
        """Whether a Notice of the Session is on screen (#53), besides its mark asking for
        attention."""
        return any(
            pending.session_id == session_id
            and pending.box.kind == "notice"
            and pending.kind != ATTENTION
            for pending in self._pending.values()
        )

    def ask_attention(self, session_id: SessionId, text: str) -> None:
        """Mark a Session as asking the human for attention (ADR-0020); asked again, the
        mark is the same one."""
        self.notify(session_id, ATTENTION_TITLE, text, ATTENTION)

    def wants_attention(self, session_id: SessionId) -> bool:
        """Whether the Session asks the human for attention, until they bring it or dismiss
        its Notice."""
        return any(
            pending.session_id == session_id and pending.kind == ATTENTION
            for pending in self._pending.values()
        )

    def drop_attention(self, session_id: SessionId) -> None:
        """The human brought the Session into view, or closed it: its mark goes."""
        for box_id, pending in list(self._pending.items()):
            if pending.session_id == session_id and pending.kind == ATTENTION:
                self.withdraw(box_id)

    def notify(
        self, session_id: SessionId | None, title: str, text: str, kind: str | None = None
    ) -> BoxId:
        """Tell the user something; it never waits for anything.

        A Notice of the same ``kind`` and Session still on screen gives way to this one, so a
        Notice that fires again never piles up.
        """
        if kind is not None:
            for box_id, pending in list(self._pending.items()):
                if pending.session_id == session_id and pending.kind == kind:
                    self.withdraw(box_id)
        return self._show("notice", session_id, title, text, None, kind)

    def answered(
        self,
        box_id: BoxId,
        answer: bool | None,
        value: str | None = None,
        window: SessionId | None = None,
    ) -> None:
        """Someone answered a box, in Main's Chat or in the Window of ``window``; a late second
        answer to the same box does nothing, and nor does one from a Window the box does not
        show in. ``value`` is what Main's Chat took in a Secreto's box."""
        pending = self._pending.get(box_id)
        if pending is None or (window is not None and window != pending.session_id):
            return
        del self._pending[box_id]
        self._hide(pending)
        if pending.on_answer is not None:
            pending.on_answer(bool(answer))
        if pending.on_value is not None:
            pending.on_value(value)

    def withdraw(self, box_id: BoxId) -> None:
        """A box no longer valid, e.g. its Turn was cancelled: nobody answers it."""
        pending = self._pending.pop(box_id, None)
        if pending is not None:
            self._hide(pending)

    def renamed(self, old: SessionId, new: SessionId) -> None:
        """A Session took a new id (#59): its boxes follow it, its mark among them."""
        for box_id, pending in list(self._pending.items()):
            if pending.session_id == old:
                self._pending[box_id] = replace(pending, session_id=new)

    def window_opened(self, session_id: SessionId) -> None:
        """Show a Window that just opened the boxes of its Session still waiting; it is in
        view now, so it no longer asks for attention."""
        self.drop_attention(session_id)
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
        notice_kind: str | None,
    ) -> BoxId:
        box = Box(next(self._ids), kind, title, text)
        self._pending[box.box_id] = _Pending(box, session_id, on_answer, notice_kind)
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
