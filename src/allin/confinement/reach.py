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


def code_folder() -> Path:
    """The folder Main imports ``allin`` from, which its wardens import it from too."""
    return Path(allin.__file__).resolve().parent.parent


def installation() -> tuple[Path, ...]:
    """What a tool reads of AllIn's to run: its code and its Python (ADR-0016)."""
    return tuple(dict.fromkeys((code_folder(), Path(sys.prefix), Path(sys.base_prefix))))


def install_root() -> Path:
    """The folder AllIn is installed in, never written by a tool: the project that holds its
    code (with ``.git``, its hooks and ``pyproject.toml``), or its code's folder when AllIn
    was installed as a package."""
    code = Path(allin.__file__).resolve().parent
    if code.is_relative_to(Path(sys.prefix).resolve()):
        return code  # a package in the venv's site-packages: the venv is read-only already
    return next((p for p in code.parents if (p / "pyproject.toml").is_file()), code)


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
    installation: tuple[Path, ...]  # read in the minimum: AllIn's code and its Python
    install_root: Path  # the whole installation: read through a broad root, never written
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
    out = _KeptOut.of(places)
    read = [*_minimum_read(places, out), *_declared(reach.read, places, out.of_reading)]
    write = [*_minimum_write(places, out), *_declared(reach.write, places, out.of_writing)]
    read += _extras(reach, places)
    return ReachPaths(read=tuple(dict.fromkeys(read)), write=tuple(dict.fromkeys(write)))


def description_paths(places: Places) -> tuple[Path, ...]:
    """What the encierro de descripción reads (ADR-0016): the minimum's reading alone, with no
    Carpeta de trabajo; it writes nothing. ``places.workspace`` and ``places.tmp`` are unused."""
    return tuple(dict.fromkeys(_minimum_read(places, _KeptOut.of(places))))


@dataclass(frozen=True)
class _KeptOut:
    """What no grant may reach, for reading and for writing."""

    of_reading: tuple[Path, ...]
    of_writing: tuple[Path, ...]

    @classmethod
    def of(cls, places: Places) -> "_KeptOut":
        data_dir = places.data_dir.resolve()
        installation = (places.install_root.resolve(), *(p.resolve() for p in places.installation))
        terminals = tuple(_terminals(places.dev))
        return cls((data_dir, *terminals), (data_dir, *installation, *terminals))


def _minimum_read(places: Places, out: _KeptOut) -> list[Path]:
    broad = (*places.system, *places.installation)
    return [
        *(p for root in broad for p in _trim(root, out.of_reading)),
        places.package,
        *(places.dev / name for name in _READ_DEVICES),
    ]


def _minimum_write(places: Places, out: _KeptOut) -> list[Path]:
    return [
        *_trim(places.workspace, out.of_writing, in_workspace=True),  # a human may point it
        places.tmp,
        places.dev / "null",
    ]


def _declared(paths: tuple[str, ...], places: Places, out: tuple[Path, ...]) -> list[Path]:
    granted: list[Path] = []
    for declared in paths:
        root, in_workspace = _resolve(declared, places)
        granted += _trim(root, out, in_workspace)
    return granted


def _extras(reach: Reach, places: Places) -> list[Path]:
    """What network and ``display`` need read beyond what they declare."""
    extras: list[Path] = []
    if reach.network and places.resolver is not None:
        extras.append(places.resolver)
    if reach.display and places.xauthority is not None:
        extras.append(places.xauthority)
    return extras


def _resolve(declared: str, places: Places) -> tuple[Path, bool]:
    """The real path of a symbolic root and its subpath, and whether it lies in the Carpeta de
    trabajo, which is granted although the data folder holds it. Links are followed, as a
    rule would: one a tool left in its Carpeta de trabajo counts for where it leads."""
    root, *sub = Path(declared).parts
    base = {"work": places.workspace, "home": places.home}.get(root, Path("/"))
    real = base.joinpath(*sub).resolve()
    return real, root == "work" and real.is_relative_to(places.workspace.resolve())


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
