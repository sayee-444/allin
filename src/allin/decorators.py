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
