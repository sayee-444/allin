"""When a Window tells Main what its input holds: once the user stops typing for a moment.

Main keeps it in MainState, so a restarted Main gives the draft back; reporting it on every key
would rewrite that state on every key.
"""

from collections.abc import Callable

_DELAY = 0.5  # seconds without a change


class DraftReporter:
    def __init__(self, report: Callable[[str], None], delay: float = _DELAY) -> None:
        self._report = report
        self._delay = delay
        self._text = ""
        self._changed_at = 0.0
        # None until the first report: Main may have put text back that the user then cleared.
        self._reported: str | None = None

    def update(self, text: str, now: float) -> None:
        """Call with the input's text as often as the UI loop runs."""
        if text != self._text:
            self._text, self._changed_at = text, now
        elif text != self._reported and now - self._changed_at >= self._delay:
            self._reported = text
            self._report(text)
