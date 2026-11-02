"""The paths one call's process may touch: its Alcance (``@reach``) resolved against this
machine and trimmed around AllIn (ADR-0016), as the folders and files Landlock grants."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import allin
from allin.decorators import Reach

# The system a tool reads: its programs, libraries and configuration. On distributions without
# a merged /usr, /bin and /lib are folders of their own; where they are links into /usr, the
# rule on /usr already covers them.
SYSTEM: tuple[Path, ...] = tuple(
    Path(p) for p in ("/usr", "/etc", "/bin", "/sbin", "/lib", "/lib64") if not Path(p).is_symlink()
)
# Harmless devices many programs open; null is also written (git needs it, #99).
_READ_DEVICES = ("zero", "urandom", "random")
# What no Alcance ever opens, in /dev: the terminals, where a tool could read or type keys.
_TERMINALS = ("pts", "console", "tty*", "vcs*")


def installation() -> tuple[Path, ...]:
    """AllIn's code and its Python: read-only for tools (ADR-0016)."""
    code = Path(allin.__file__).resolve().parent.parent
    return tuple(dict.fromkeys((code, Path(sys.prefix), Path(sys.base_prefix))))


def resolver_folder() -> Path | None:
    """Where ``/etc/resolv.conf`` really lives, when it links out of /etc (systemd-resolved
    puts it in /run): Landlock checks the target, so DNS needs it read (#99)."""
    target = Path("/etc/resolv.conf").resolve().parent
    return None if target == Path("/etc") or not target.is_dir() else target


def x11_cookie() -> Path | None:
    """The X11 authority file a graphical client needs (``XAUTHORITY``, or ``~/.Xauthority``)."""
    named = os.environ.get("XAUTHORITY")
    cookie = Path(named) if named else Path.home() / ".Xauthority"
    return cookie if cookie.is_file() else None


@dataclass(frozen=True)
class Places:
    """Where one call's paths resolve, and what of AllIn's must stay out of them."""

    workspace: Path  # the Session's Carpeta de trabajo: the root ``work``
    tmp: Path  # the call's TMPDIR, inside the data folder
    package: Path  # the function's own package, inside the data folder
    data_dir: Path  # never read nor written, but for the three above
    installation: tuple[Path, ...]  # read, never written
    home: Path  # the root ``home``
    system: tuple[Path, ...] = SYSTEM
    dev: Path = Path("/dev")
    resolver: Path | None = None  # read when the call has network
    xauthority: Path | None = None  # read when the call has ``display``


@dataclass(frozen=True)
class ReachPaths:
    """The folders and files one call's process may read, and those it may also write."""

    read: tuple[Path, ...]
    write: tuple[Path, ...]


def allowed_paths(reach: Reach, places: Places) -> ReachPaths:
    """The minimum, plus what ``reach`` declares. Every broad root that holds something of
    AllIn's is trimmed down to the siblings of the branch that leads to it: the data folder is
    never read or written (but for the Carpeta de trabajo, the TMPDIR and the package), the
    installation never written, and the terminals never opened."""
    data_dir = places.data_dir.resolve()
    installation = tuple(p.resolve() for p in places.installation)
    terminals = tuple(_terminals(places.dev))
    read_out, write_out = (data_dir, *terminals), (data_dir, *installation, *terminals)
    read: list[Path] = [
        *(p for root in (*places.system, *places.installation) for p in _trim(root, read_out)),
        places.package,
        *(places.dev / name for name in _READ_DEVICES),
    ]
    write: list[Path] = [places.workspace, places.tmp, places.dev / "null"]
    for declared in reach.read:
        root, in_workspace = _resolve(declared, places)
        read += _trim(root, read_out, in_workspace)
    for declared in reach.write:
        root, in_workspace = _resolve(declared, places)
        write += _trim(root, write_out, in_workspace)
    if reach.net is not False and places.resolver is not None:
        read.append(places.resolver)
    if reach.display and places.xauthority is not None:
        read.append(places.xauthority)
    return ReachPaths(read=tuple(dict.fromkeys(read)), write=tuple(dict.fromkeys(write)))


def _resolve(declared: str, places: Places) -> tuple[Path, bool]:
    """The real path of a symbolic root and its subpath, and whether it lies in the Carpeta de
    trabajo, which is granted although the data folder holds it."""
    root, *sub = Path(declared).parts
    if root == "work":
        return places.workspace.joinpath(*sub), True
    base = places.home if root == "home" else Path("/")
    return base.joinpath(*sub).resolve(), False


def _terminals(dev: Path) -> list[Path]:
    return sorted(path for pattern in _TERMINALS for path in dev.glob(pattern))


def _trim(root: Path, keep_out: tuple[Path, ...], in_workspace: bool = False) -> list[Path]:
    """``root`` minus every branch in ``keep_out``: each sibling on the way down to them, so
    the grant covers everything but those branches (ADR-0016). Computed at each launch, since a
    sibling made later is not covered. Links among the siblings are left out: a rule follows
    them, and one could point into AllIn."""
    if not in_workspace and any(root.is_relative_to(out) for out in keep_out):
        return []
    inner = [out for out in keep_out if out != root and out.is_relative_to(root)]
    if not inner:
        return [root]
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []  # a folder it cannot list: no siblings to grant
    out: list[Path] = []
    for entry in entries:
        below = tuple(o for o in inner if o.is_relative_to(entry))
        if entry.is_symlink() or entry in below:
            continue
        out += _trim(entry, below, in_workspace=True) if below else [entry]
    return out
