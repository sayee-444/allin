"""What an fproc's class gets from AllIn inside its Proceso adherido (ADR-0012)::

    from allin.fproc import Fproc

    class Notes(Fproc):
        def read(self) -> str:
            \"\"\"The shared file, whole.\"\"\"
            return self.file.read_text() if self.file else ""

Main makes the class once, when the Proceso adherido starts, and calls its public methods. Its
current folder is its own Carpeta de trabajo, which outlives it: what it keeps there, it finds
again when it is attached with the same name. ``Fproc``'s own members are never tools: only the
methods the class itself defines are.

It runs inside the Proceso adherido, so it uses the standard library alone.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _Attached:
    name: str
    file: Path | None


_attached: _Attached | None = None  # set by the warden before it makes the class


def attached_as(name: str, file: Path | None) -> None:
    """Set by the warden in the Proceso adherido, before it makes the fproc's class."""
    global _attached  # noqa: PLW0603 - the process's one Proceso adherido, set once
    _attached = _Attached(name, file)


class Fproc:
    """The base of an fproc's class: its name and its shared file."""

    @property
    def name(self) -> str:
        """The name it was attached with."""
        return _context().name

    @property
    def file(self) -> Path | None:
        """The shared file it was attached with, which it may read and write; ``None`` if it
        was attached without one."""
        return _context().file


def _context() -> _Attached:
    if _attached is None:
        msg = "not in a Proceso adherido: AllIn sets this before it makes the class"
        raise RuntimeError(msg)
    return _attached
