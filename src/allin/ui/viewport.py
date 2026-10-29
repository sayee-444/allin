"""Which slice of a pad's lines is on screen."""


class Viewport:
    """Scroll position over ``total`` lines shown ``rows`` at a time.

    Follows the last lines until the user scrolls up; scrolling back to the bottom follows again.
    """

    def __init__(self) -> None:
        self._offset = 0
        self._follow = True

    def top(self, total: int, rows: int) -> int:
        """First visible line."""
        last_top = max(0, total - rows)
        self._offset = last_top if self._follow else min(self._offset, last_top)
        return self._offset

    def scroll(self, delta: int, total: int, rows: int) -> None:
        last_top = max(0, total - rows)
        self._offset = min(max(0, self.top(total, rows) + delta), last_top)
        self._follow = self._offset == last_top
