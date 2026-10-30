"""When a Window tells Main its Draft (``core.drafts``): once the user stops typing a moment.

Main keeps it in MainState, so a restarted Main gives the draft back; reporting it on every key
would rewrite that state on every key.
"""

from collections.abc import Callable

_DELAY = 0.5  # seconds without a change


class DraftReporter:
    def __init__(self, report: Callable[[tuple[str, ...]], None], delay: float = _DELAY) -> None:
        self._report = report
        self._delay = delay
        self._blocks: tuple[str, ...] = ()
        self._changed_at = 0.0
        # None until the first report: Main may have put text back that the user then cleared.
        self._reported: tuple[str, ...] | None = None

    def update(self, blocks: tuple[str, ...], now: float) -> None:
        """Call with the input's blocks as often as the UI loop runs."""
        if blocks != self._blocks:
            self._blocks, self._changed_at = blocks, now
        elif blocks != self._reported and now - self._changed_at >= self._delay:
            self.report_now(blocks, now)

    def report_now(self, blocks: tuple[str, ...], now: float) -> None:
        """Report without waiting, e.g. the blocks left waiting once one was sent."""
        self._blocks, self._changed_at = blocks, now
        self._reported = blocks
        self._report(blocks)
