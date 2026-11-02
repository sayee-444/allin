"""The minimum Alcance: what a Function tool's process may touch without ``@reach``
(ADR-0016), as the folders Landlock grants."""

import sys
from dataclasses import dataclass
from pathlib import Path

import allin

# The system a tool reads: its programs, libraries and configuration. On distributions without
# a merged /usr, /bin and /lib are folders of their own; where they are links into /usr, the
# rule on /usr already covers them.
SYSTEM: tuple[Path, ...] = tuple(
    Path(p) for p in ("/usr", "/etc", "/bin", "/sbin", "/lib", "/lib64") if not Path(p).is_symlink()
)
# Harmless devices many programs open; /dev/null is also written (git needs it, #99).
_READ_DEVICES = (Path("/dev/zero"), Path("/dev/urandom"), Path("/dev/random"))
_DEV_NULL = Path("/dev/null")


def installation() -> tuple[Path, ...]:
    """AllIn's code and its Python: read-only for tools (ADR-0016)."""
    code = Path(allin.__file__).resolve().parent.parent
    return tuple(dict.fromkeys((code, Path(sys.prefix), Path(sys.base_prefix))))


@dataclass(frozen=True)
class ReachPaths:
    """The folders and files one call's process may read, and those it may also write."""

    read: tuple[Path, ...]
    write: tuple[Path, ...]


def minimum_reach(
    *,
    workspace: Path,
    tmp: Path,
    package: Path,
    data_dir: Path,
    installation: tuple[Path, ...],
    system: tuple[Path, ...] = SYSTEM,
) -> ReachPaths:
    """Read the system, the installation and the function's own package; write the Session's
    Carpeta de trabajo and the call's ``TMPDIR``. No broad root reaches the data folder: one
    that holds it is trimmed down to the siblings of the branch that leads to it."""
    broad = [trimmed for root in (*system, *installation) for trimmed in _trim(root, data_dir)]
    read = (*dict.fromkeys(broad), package, *_READ_DEVICES)
    return ReachPaths(read=read, write=(workspace, tmp, _DEV_NULL))


def _trim(root: Path, keep_out: Path) -> list[Path]:
    """``root`` minus the branch to ``keep_out``: each sibling on the way down to it, so the
    grant covers everything but that branch (ADR-0016). Computed at each launch, since a new
    sibling made later is not covered."""
    if root == keep_out or root.is_relative_to(keep_out):
        return []
    if not keep_out.is_relative_to(root):
        return [root]
    out: list[Path] = []
    here = root
    for part in keep_out.relative_to(root).parts:
        out += sorted(entry for entry in here.iterdir() if entry.name != part)
        here = here / part
    return out
