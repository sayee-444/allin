"""An fproc of AllIn's installation: a shared text file with its cursor (ADR-0012, #113).

Attach it with ``/attach shared_file [name] --file=<path>``. The Sessions that get its methods
read the file from its cursor, search it and write at it, one call at a time. The cursor is a
line, kept in its Carpeta de trabajo: attached again with the same name, it is where it was.

It reaches its file by the path it was attached with, and its encierro was fixed on the file
that was there then. A program that saves the file by replacing it, as many editors do, leaves
a new file it cannot reach: detach it and attach it again.
"""

import json
from pathlib import Path

from allin.fproc import Fproc

_CURSOR = Path("cursor.json")  # in its Carpeta de trabajo, its current folder


class SharedFile(Fproc):
    """A text file several Sessions share, read and written at a cursor, which is a line."""

    def __init__(self) -> None:
        if self.file is None:
            msg = "attach it with --file=<path>"
            raise ValueError(msg)
        self._cursor = self._kept_cursor()
        self._lines()  # it fails now, rather than at the first call, if it cannot read it

    def read(self, lines: int = 50) -> dict[str, object]:
        """Read up to ``lines`` lines from the cursor and move the cursor past them. Gives
        the first line's number (1 is the file's first), the lines, and whether the file
        ends there."""
        text = self._lines()
        start = min(self._cursor, len(text))
        shown = text[start : start + max(lines, 0)]
        self._move(start + len(shown))
        return {"from": start + 1, "lines": shown, "end": start + len(shown) >= len(text)}

    def search(self, text: str) -> dict[str, object]:
        """Find the next line, from the cursor on and then from the start, that holds
        ``text``, and move the cursor to it. Gives its number and the line, or no number if
        no line holds it."""
        lines = self._lines()
        here = min(self._cursor, len(lines))
        for number in [*range(here, len(lines)), *range(here)]:
            if text in lines[number]:
                self._move(number)
                return {"line": number + 1, "text": lines[number]}
        return {"line": None}

    def seek(self, line: int) -> int:
        """Move the cursor to the line ``line`` (1 is the file's first); gives where it is."""
        self._move(min(max(line - 1, 0), len(self._lines())))
        return self._cursor + 1

    def write(self, text: str) -> dict[str, object]:
        """Insert ``text`` at the cursor, as whole lines, and move the cursor past them. Gives
        the first new line's number and how many lines the file has now."""
        lines = self._lines()
        start = min(self._cursor, len(lines))
        new = text.splitlines()
        lines[start:start] = new
        self._shared().write_text("".join(f"{line}\n" for line in lines))  # in place
        self._move(start + len(new))
        return {"from": start + 1, "lines": len(lines)}

    def _shared(self) -> Path:
        assert self.file is not None  # made only with one
        return self.file

    def _lines(self) -> list[str]:
        try:
            return self._shared().read_text().splitlines()
        except PermissionError as error:
            msg = (
                "the shared file was replaced on disk, as an editor that saves it anew does,"
                " and this Proceso adherido cannot reach the new one: detach it and attach it"
                " again"
            )
            raise PermissionError(msg) from error

    def _kept_cursor(self) -> int:
        try:
            kept = json.loads(_CURSOR.read_text())
        except (OSError, ValueError):
            return 0
        if not isinstance(kept, dict) or kept.get("file") != str(self.file):
            return 0  # another file's, or none
        line = kept.get("line")
        return line if isinstance(line, int) and line >= 0 else 0

    def _move(self, line: int) -> None:
        self._cursor = line
        _CURSOR.write_text(json.dumps({"file": str(self.file), "line": line}))
