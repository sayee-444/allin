"""What a Chat holds: entries with a role (which picks their colour) and an optional label.

Curses-free, so the screen only paints rows. Streamed text extends the last entry while it is
still open; wrapping is cached per entry, so a delta only re-wraps the entry it grows.
"""

from dataclasses import dataclass

from allin.ui.wrap import wrap_rows


@dataclass(frozen=True)
class Row:
    role: str | None
    text: str
    joined: bool = False  # it continues the row before it: wrapped, with no line break


@dataclass
class _Entry:
    role: str | None
    label: str | None
    text: str
    streaming: bool
    rows: list[tuple[str, bool]] | None = None  # wrapped to the Chat's width (wrap_rows)

    def display_text(self, icon: str | None = None) -> str:
        shown = f"[{self.label}] {self.text}" if self.label else self.text
        return f"{icon} {shown}" if icon else shown


class Chat:
    def __init__(self) -> None:
        self._entries: list[_Entry] = []
        self._width = 0
        self._rows: list[Row] = []
        # Prompts waiting for their Turn, shown below every entry, even a streaming one.
        self._queued: list[_Entry] = []
        self._icons: dict[str, str] = {}  # each role's icon, from the Theme (#47)

    def set_icons(self, icons: dict[str, str]) -> None:
        """Each line type's icon, shown before its entries; every entry wraps again."""
        if icons != self._icons:
            self._icons = dict(icons)
            self._width = 0

    def add(self, text: str, role: str | None = None, label: str | None = None) -> None:
        self._entries.append(_Entry(role, label, text, streaming=False))

    def stream(self, text: str, role: str, label: str) -> None:
        last = self._entries[-1] if self._entries else None
        if last and last.streaming and (last.role, last.label) == (role, label):
            last.text += text
            last.rows = None
        else:
            self._entries.append(_Entry(role, label, text, streaming=True))

    def clear(self) -> None:
        """Every entry gone, for a Chat about to be rebuilt; the queued Prompts stay."""
        self._entries = []
        self._rows = []

    def set_queued(self, texts: tuple[str, ...], label: str) -> None:
        self._queued = [_Entry("user", label, text, streaming=False) for text in texts]

    def _icon(self, entry: _Entry) -> str | None:
        return self._icons.get(entry.role or "")

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
                entry.rows = wrap_rows(entry.display_text(self._icon(entry)), width)
            self._rows += [Row(entry.role, row, joined) for row, joined in entry.rows]
        if not self._queued:
            return self._rows
        queued = [
            Row(entry.role, row, joined)
            for entry in self._queued
            for row, joined in wrap_rows(entry.display_text(self._icon(entry)), width)
        ]
        return [*self._rows, *queued]
