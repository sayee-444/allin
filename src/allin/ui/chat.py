"""What a Chat holds: entries with a role (which picks their colour) and an optional label.

Curses-free, so the screen only paints rows. Streamed text extends the last entry while it is
still open; wrapping is cached per entry, so a delta only re-wraps the entry it grows.
"""

from dataclasses import dataclass

from allin.ui.wrap import wrap


@dataclass(frozen=True)
class Row:
    role: str | None
    text: str


@dataclass
class _Entry:
    role: str | None
    label: str | None
    text: str
    streaming: bool
    rows: list[str] | None = None  # wrapped to the Chat's width; None until wrapped

    def display_text(self) -> str:
        return f"[{self.label}] {self.text}" if self.label else self.text


class Chat:
    def __init__(self) -> None:
        self._entries: list[_Entry] = []
        self._width = 0
        self._rows: list[Row] = []

    def add(self, text: str, role: str | None = None, label: str | None = None) -> None:
        self._entries.append(_Entry(role, label, text, streaming=False))

    def stream(self, text: str, role: str, label: str) -> None:
        last = self._entries[-1] if self._entries else None
        if last and last.streaming and (last.role, last.label) == (role, label):
            last.text += text
            last.rows = None
        else:
            self._entries.append(_Entry(role, label, text, streaming=True))

    def end_stream(self) -> None:
        """Close the open entry, so the next streamed text starts a new one."""
        if self._entries:
            self._entries[-1].streaming = False

    def rows(self, width: int) -> list[Row]:
        if width != self._width:
            self._width = width
            for entry in self._entries:
                entry.rows = None
            self._rows = []
        # Entries that are still wrapped keep their rows; from the first one that is not (in
        # practice the last, streaming entry) on, rows are rebuilt.
        kept, first = 0, len(self._entries)
        for index, entry in enumerate(self._entries):
            if entry.rows is None:
                first = index
                break
            kept += len(entry.rows)
        del self._rows[kept:]
        for entry in self._entries[first:]:
            if entry.rows is None:
                entry.rows = wrap(entry.display_text(), width)
            self._rows += [Row(entry.role, row) for row in entry.rows]
        return self._rows
