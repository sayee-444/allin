"""The decorators a function declares how Main treats it with (ADR-0009). They only mark it:
Main never imports a function, and a separate process reads the marks (ADR-0016).

They run inside the tool's own process, so they use the standard library alone.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from allin.core.errors import InvalidReach

type Root = Literal["work", "home", "/"]
ROOTS: tuple[Root, ...] = ("work", "home", "/")
REACH_MARK = "__allin_reach__"  # where ``@reach`` leaves the Alcance on its target
GRANT = "grant"  # Otorgar's Command session (ADR-0025)
# Writing under these, outside a dotted or ``bin`` folder, is not where the user runs things.
_HARMLESS_WRITES = (PurePosixPath("/dev/shm"),)  # noqa: S108 - a path an Alcance names


@dataclass(frozen=True)
class Reach:
    """A function's Alcance: what its process may touch (ADR-0016).

    Paths are a symbolic root (``work``, the Session's Carpeta de trabajo; ``home``; ``/``)
    plus a subpath, such as ``home/Documents``. ``net`` is ``False``, ``True`` (every port but
    AllIn's Servers') or the TCP ports it connects to; UDP stays open unless it is ``False``.
    ``display`` opens the X11 or Wayland socket, which is acting as the user. ``commands`` are
    the Command sessions it runs, which Main will enforce (ADR-0025); for now it is
    only kept."""

    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()
    net: bool | tuple[int, ...] = False
    display: bool = False
    commands: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """However it is made, by ``@reach``, by hand or parsed by Main: raises ``InvalidReach``
        for what is not an Alcance."""
        _paths("read", self.read)
        _paths("write", self.write)
        _net(self.net)
        _flag("display", self.display)
        _strings("commands", self.commands)

    @property
    def network(self) -> bool:
        """Whether it has any network: without it, UDP is closed too."""
        return self.net is not False

    @property
    def acts_as_user(self) -> bool:
        """``display``, or writing where the user runs things: what the tool leaves there runs
        as them, out of the Encierro (ADR-0016). Their home or the whole system, a dotted
        folder or file (``.config``, ``.bashrc``, a repo's ``.git``) or a ``bin`` folder."""
        return self.display or any(writes_as_user(path) for path in self.write)

    @property
    def grants(self) -> bool:
        """Whether it runs Otorgar, whose approval never goes in bulk (ADR-0025)."""
        return GRANT in self.commands

    def within(self, declared: "Reach") -> bool:
        """Whether it asks for nothing ``declared`` does not: what an Approval may reduce an
        Alcance to (ADR-0016)."""
        return (
            _covered(self.read, declared.read)
            and _covered(self.write, declared.write)
            and _net_within(self.net, declared.net)
            and (declared.display or not self.display)
            and set(self.commands) <= set(declared.commands)
        )

    def tcp_ports(self, servers: frozenset[int]) -> tuple[int, ...] | None:
        """The TCP ports it connects to, never a Server's; ``None`` for every port but those,
        which a shared layer leaves out (``net=True``)."""
        if self.net is True:
            return None
        return tuple(sorted(set(self.net or ()) - servers))


def reach[T](
    *,
    read: Sequence[str] = (),
    write: Sequence[str] = (),
    net: bool | Sequence[int] = False,
    display: bool = False,
    commands: Sequence[str] = (),
) -> Callable[[T], T]:
    """Declare the Alcance of a function, or of an fproc's class. Raises ``InvalidReach``."""
    declared = Reach(
        read=_paths("read", read),
        write=_paths("write", write),
        net=_net(net),
        display=_flag("display", display),
        commands=_strings("commands", commands),
    )

    def mark(target: T) -> T:
        setattr(target, REACH_MARK, declared)
        return target

    return mark


def reach_of(target: object) -> Reach:
    """The Alcance a function or class declares; the minimum without ``@reach``."""
    declared: object = getattr(target, REACH_MARK, MINIMUM)
    return declared if isinstance(declared, Reach) else MINIMUM


def writes_as_user(written: str) -> bool:
    """Whether writing ``written`` (an Alcance's path) is writing where the user runs things:
    their home or the whole system, a dotted folder or file, or a ``bin`` folder (ADR-0016)."""
    path = PurePosixPath(written)
    if any(part.startswith(".") or part == "bin" for part in path.parts[1:]):
        return True
    if path.parts[0] == "work":
        return False
    if path.parts[0] == "home":
        return len(path.parts) == 1
    return not any(path.is_relative_to(harmless) for harmless in _HARMLESS_WRITES)


def _covered(paths: tuple[str, ...], declared: tuple[str, ...]) -> bool:
    # ``/`` holds the symbolic roots too: ``work`` and ``home`` are folders of the system.
    return all(
        any(d == "/" or PurePosixPath(path).is_relative_to(PurePosixPath(d)) for d in declared)
        for path in paths
    )


def _net_within(net: bool | tuple[int, ...], declared: bool | tuple[int, ...]) -> bool:
    if net is False or declared is True:
        return True
    if net is True or declared is False:
        return False
    return set(net) <= set(declared)


def _paths(field: str, paths: object) -> tuple[str, ...]:
    checked = _strings(field, paths)
    for path in checked:
        parts = PurePosixPath(path).parts
        if not parts or parts[0] not in ROOTS or ".." in parts:
            raise InvalidReach(field=field, value=path)
    return checked


def _strings(field: str, values: object) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise InvalidReach(field=field, value=repr(values))
    items: Sequence[object] = values  # pyright: ignore[reportUnknownVariableType] - checked below
    texts = tuple(item for item in items if isinstance(item, str))
    if len(texts) != len(items):
        raise InvalidReach(field=field, value=repr(items))
    return texts


def _net(net: object) -> bool | tuple[int, ...]:
    if isinstance(net, bool):
        return net
    if isinstance(net, str) or not isinstance(net, Sequence):
        raise InvalidReach(field="net", value=repr(net))
    items: Sequence[object] = net  # pyright: ignore[reportUnknownVariableType] - checked below
    ports = tuple(item for item in items if isinstance(item, int) and not isinstance(item, bool))
    if len(ports) != len(items) or not all(1 <= port <= 65535 for port in ports):
        raise InvalidReach(field="net", value=repr(items))
    return ports


def _flag(field: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise InvalidReach(field=field, value=repr(value))
    return value


# Made last: building a Reach checks it with the functions above.
MINIMUM = Reach()  # its Carpeta de trabajo, reading the system and its package, no network
