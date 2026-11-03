"""What the Organizer shows and what its keys and clicks do (#53), without curses.

The rows come from Main; each click or key on one becomes an ``Act`` for Main, which runs it as
the Command main of the same name. The same text makes ``/windows list`` in Main's Chat. The
Sessions of each Subperfil go folded under their instance, apart from the Profile's (ADR-0026).
"""

from dataclasses import dataclass, field
from typing import Literal

from allin.ipc.messages import OrganizerAction, OrganizerRow, Place
from allin.ui.sanitize import visible
from allin.ui.wrap import cell_width

_PLACES: dict[Place, str] = {
    "view": "in view",
    "background": "in the background",
    "closed": "closed",
}


def row_state(row: OrganizerRow) -> str:
    """What the row says besides its title, as short parts joined by ``·``."""
    parts = [_PLACES[row.place]]
    if row.running:
        parts.append("turn running")
    if row.queued:
        parts.append(f"{row.queued} queued")
    if row.asking:
        parts.append("asks a confirmation")
    if row.notice:
        parts.append("notice")
    if row.attention:
        parts.append("asks for attention")
    if row.context is not None:
        limit = "?" if row.limit is None else f"{row.limit // 1000}k"
        parts.append(f"context {row.context} / {limit}")
    parts.append(f"server {row.supervisor or 'base_url'}")
    return " · ".join(parts)


def row_text(row: OrganizerRow) -> str:
    """One row as one line, with control characters made visible (#28)."""
    title = visible(row.title).replace("\n", "↵").replace("\t", " ")
    shown_id = "" if title.startswith(row.session_id) else f"{row.session_id} "
    return f"{shown_id}{title} — {row_state(row)}"


def subprofile_groups(rows: list[OrganizerRow]) -> dict[str, list[OrganizerRow]]:
    """The rows of each Subperfil, by its instance's name, in name order."""
    groups: dict[str, list[OrganizerRow]] = {}
    for row in rows:
        if row.subprofile:
            groups.setdefault(row.subprofile, []).append(row)
    return dict(sorted(groups.items()))


def _subprofile_title(name: str, group: list[OrganizerRow]) -> str:
    exempt = ", exempt" if any(row.exempt for row in group) else ""
    return f"subprofile {name}{exempt}: {len(group)}"


def windows_list(rows: list[OrganizerRow]) -> list[str]:
    """What ``/windows list`` writes in Main's Chat: the Organizer's rows, all unfolded."""
    profile = [row for row in rows if not row.subprofile]
    open_rows = [row for row in profile if row.place != "closed"]
    closed = [row for row in profile if row.place == "closed"]
    lines = [f"open sessions: {len(open_rows)}"]
    lines += [f"  {row_text(row)}" for row in open_rows]
    if closed:
        lines.append(f"closed sessions: {len(closed)}, most recent first")
        lines += [f"  {row_text(row)}" for row in closed]
    for name, group in subprofile_groups(rows).items():
        lines.append(_subprofile_title(name, group))
        lines += [f"  {row_text(row)}" for row in group]
    return lines


@dataclass(frozen=True)
class Act:
    """An action the human asked for on a Session."""

    action: OrganizerAction
    session_id: str


QUIT: Literal["quit"] = "quit"

type LineKind = Literal["header", "row", "fold", "help", "blank"]

# Each row ends in its buttons; a click elsewhere on it brings it.
_BUTTONS: tuple[tuple[str, OrganizerAction], ...] = (("[bg]", "bg"), ("[x]", "close"))
_BUTTONS_WIDTH = sum(len(label) + 1 for label, _ in _BUTTONS) + 1
HELP = "↑↓ move · Enter or click: bring · b: background · x: close · c: closed · Esc"


@dataclass(frozen=True)
class Fold:
    """A fold of the list: the Profile's closed Sessions, or those of one Subperfil."""

    subprofile: str = ""  # empty: the closed ones


type Item = OrganizerRow | Fold


@dataclass(frozen=True)
class Line:
    """One line of the Organizer's list. ``item`` is what selecting it selects, if anything."""

    text: str
    kind: LineKind
    item: int | None = None
    selected: bool = False
    hovered: bool = False
    place: Place | None = None


def fit(text: str, width: int) -> str:
    """``text`` cut to ``width`` terminal cells, ending in ``…`` if cut, and padded."""
    if width <= 0:
        return ""
    used, kept = 0, list[str]()
    total = sum(cell_width(char) for char in text)
    room = width if total <= width else width - 1
    for char in text:
        size = cell_width(char)
        if used + size > room:
            break
        kept.append(char)
        used += size
    if total > width:
        kept.append("…")
        used += 1
    return "".join(kept) + " " * (width - used)


@dataclass
class OrganizerView:
    """The rows, which one is selected, which folds are open, and which line the mouse is
    over."""

    rows: list[OrganizerRow] = field(default_factory=list[OrganizerRow])
    selected: int = 0  # among the items: the open rows, then each fold and its rows if unfolded
    unfolded: bool = False  # the Profile's closed Sessions
    unfolded_subprofiles: set[str] = field(default_factory=set[str])
    hover: int | None = None  # the line under the mouse

    def set_rows(self, rows: list[OrganizerRow]) -> None:
        """New rows from Main; the same Session stays selected if it is still there."""
        chosen = self._item_row(self.selected)
        self.rows = rows
        items = self._items()
        if chosen is not None:
            for index, item in enumerate(items):
                if isinstance(item, OrganizerRow) and item.session_id == chosen.session_id:
                    self.selected = index
                    return
        self.selected = min(self.selected, len(items) - 1)

    def _profile_rows(self) -> list[OrganizerRow]:
        return [row for row in self.rows if not row.subprofile]

    def _open_rows(self) -> list[OrganizerRow]:
        return [row for row in self._profile_rows() if row.place != "closed"]

    def _closed_rows(self) -> list[OrganizerRow]:
        return [row for row in self._profile_rows() if row.place == "closed"]

    def _items(self) -> list[Item]:
        """What can be selected, in order: the open rows, the fold of the closed ones and
        each Subperfil's, each followed by its rows if unfolded."""
        closed = self._closed_rows() if self.unfolded else []
        items: list[Item] = [*self._open_rows(), Fold(), *closed]
        for name, group in subprofile_groups(self.rows).items():
            items.append(Fold(name))
            if name in self.unfolded_subprofiles:
                items += group
        return items

    def _item(self, index: int) -> Item | None:
        items = self._items()
        return items[index] if 0 <= index < len(items) else None

    def _item_row(self, index: int) -> OrganizerRow | None:
        item = self._item(index)
        return item if isinstance(item, OrganizerRow) else None

    def lines(self, width: int) -> list[Line]:
        """The list, ``width`` cells wide."""
        open_rows, closed = self._open_rows(), self._closed_rows()
        lines = [Line(fit(f"Open sessions ({len(open_rows)})", width), "header")]
        if not open_rows:
            lines.append(Line(fit("  none", width), "blank"))
        item = 0
        for row in open_rows:
            lines.append(self._row_line(row, item, width))
            item += 1
        lines.append(Line("", "blank"))
        mark = "▾" if self.unfolded else "▸"
        fold = fit(f"{mark} Closed sessions ({len(closed)}), most recent first", width)
        lines.append(Line(fold, "fold", item, selected=item == self.selected))
        item += 1
        if self.unfolded:
            for row in closed:
                lines.append(self._row_line(row, item, width))
                item += 1
        for name, group in subprofile_groups(self.rows).items():
            unfolded = name in self.unfolded_subprofiles
            exempt = " · exempt" if any(row.exempt for row in group) else ""
            title = f"{'▾' if unfolded else '▸'} Subprofile {name} ({len(group)}){exempt}"
            lines.append(Line("", "blank"))
            lines.append(Line(fit(title, width), "fold", item, selected=item == self.selected))
            item += 1
            if unfolded:
                for row in group:
                    lines.append(self._row_line(row, item, width))
                    item += 1
        lines += [Line("", "blank"), Line(fit(HELP, width), "help")]
        return [
            Line(line.text, line.kind, line.item, line.selected, y == self.hover, line.place)
            for y, line in enumerate(lines)
        ]

    def _row_line(self, row: OrganizerRow, item: int, width: int) -> Line:
        buttons = " ".join(label for label, _ in _BUTTONS)
        text = fit(f"  {row_text(row)}", max(0, width - _BUTTONS_WIDTH))
        line = text + fit(f" {buttons} ", min(width, _BUTTONS_WIDTH))
        return Line(line, "row", item, selected=item == self.selected, place=row.place)

    def key(self, key: str) -> Act | Literal["quit"] | None:
        """What a key does; ``key`` is curses' name for it (``KEY_UP``) or the character."""
        items = self._items()
        match key:
            case "\x1b":
                return QUIT
            case "KEY_UP":
                self.selected = max(0, self.selected - 1)
            case "KEY_DOWN":
                self.selected = min(len(items) - 1, self.selected + 1)
            case "c":
                self._toggle(Fold())
            case "\n" | "\r" | "KEY_ENTER":
                return self._bring(self.selected)
            case "b" | "x":
                row = self._item_row(self.selected)
                if row is not None:
                    return Act("bg" if key == "b" else "close", row.session_id)
            case _:
                pass
        return None

    def click(self, y: int, x: int, width: int) -> Act | None:
        """A press of the left button on line ``y``, column ``x`` of the list."""
        lines = self.lines(width)
        if not 0 <= y < len(lines) or lines[y].item is None:
            return None
        line = lines[y]
        assert line.item is not None
        self.selected = line.item
        item = self._item(line.item)
        if isinstance(item, Fold):
            self._toggle(item)
            return None
        row = self._item_row(line.item)
        if row is None:
            return None
        start = max(0, width - _BUTTONS_WIDTH) + 1
        for label, action in _BUTTONS:
            if start <= x < start + len(label):
                return Act(action, row.session_id)
            start += len(label) + 1
        return self._bring(line.item)

    def move(self, y: int) -> None:
        """The mouse moved over line ``y``."""
        self.hover = y

    def _bring(self, index: int) -> Act | None:
        item = self._item(index)
        if isinstance(item, Fold):
            self._toggle(item)
            return None
        return None if item is None else Act("open", item.session_id)

    def _toggle(self, fold: Fold) -> None:
        if not fold.subprofile:
            self.unfolded = not self.unfolded
        else:
            self.unfolded_subprofiles ^= {fold.subprofile}
        self.selected = min(self.selected, len(self._items()) - 1)
