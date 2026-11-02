"""Where a Prompt or a command comes from, and the chain it came through (ADR-0013).

A chain starts with a human (what they typed in a Window or in Main's Chat, or did in the
Organizador) or with Main (what it asks on its own), and grows a link at each Session,
Proceso adherido or Suscripción it passes through. An action never has more authority than
the weakest link of its chain: a Proceso adherido never counts as human, even when a human
called it, and Main never does either.
"""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, model_serializer, model_validator

type LinkKind = Literal["human", "main", "session", "process", "subscription"]
# What a chain may do, from the most to the least: only ``human`` uses ``-y``, ``--devastation``
# and what is a human's alone; ``main`` is answered by Main's own ``--devastation``.
type Authority = Literal["human", "main", "session"]

_AUTHORITY: dict[LinkKind, Authority] = {
    "human": "human",
    "main": "main",
    "session": "session",
    "process": "session",
    "subscription": "session",
}
_KINDS: dict[str, LinkKind] = {kind: kind for kind in _AUTHORITY}  # a parsed word, typed
_RANK: dict[Authority, int] = {"session": 0, "main": 1, "human": 2}
_NAMED: frozenset[LinkKind] = frozenset({"session", "process", "subscription"})
_LINK = re.compile(rf"({'|'.join(_KINDS)})(?::([\w.-]+))?")


@dataclass(frozen=True)
class Link:
    kind: LinkKind
    id: str = ""  # the Session's, the process's or the Suscripción's; none for human or main

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}" if self.id else self.kind


class Origin(BaseModel, frozen=True):
    """A chain of links, the first where it started. Saved, and logged, as its labels:
    ``["human", "session:ab12"]``."""

    chain: tuple[Link, ...]

    @model_validator(mode="before")
    @classmethod
    def _from_labels(cls, data: object) -> object:
        if isinstance(data, str) and data in ("human", "main", "session"):
            data = [data]  # one word, as an Origin was saved before its chain (#105)
        if isinstance(data, list):
            labels: list[object] = list(data)  # pyright: ignore[reportUnknownArgumentType] - parsed next
            return {"chain": tuple(_parse_link(label) for label in labels)}
        return data

    @model_validator(mode="after")
    def _not_empty(self) -> "Origin":
        if not self.chain:
            msg = "an Origin has at least one link"
            raise ValueError(msg)
        return self

    @model_serializer
    def _to_labels(self) -> list[str]:
        return self.labels

    @property
    def labels(self) -> list[str]:
        return [str(link) for link in self.chain]

    @property
    def is_human(self) -> bool:
        """Whether a human's authority reaches the action: every link is a human."""
        return weakest_link(self) == "human"

    @property
    def sessions(self) -> tuple[str, ...]:
        """The Sessions of the chain, in order: each one bounds what the action may use."""
        return tuple(link.id for link in self.chain if link.kind == "session")

    @property
    def sender(self) -> str | None:
        """The last Session of the chain: whose LLM sent the action, maybe through a Proceso
        adherido; ``None`` if no Session did, or one saved before #105 that names none."""
        return (
            next((link.id for link in reversed(self.chain) if link.kind == "session"), "") or None
        )

    def __str__(self) -> str:
        return " > ".join(self.labels)


def weakest_link(origin: Origin) -> Authority:
    """The authority of an action: that of the weakest link of its chain (ADR-0013). The
    Confirmation, the Exemption and the Command sessions all ask it."""
    return min((_AUTHORITY[link.kind] for link in origin.chain), key=_RANK.__getitem__)


def from_session(origin: Origin, session_id: str) -> Origin:
    """What a Session sends or runs during a Turn whose Prompt had ``origin``."""
    return _through(origin, Link("session", session_id))


def from_process(origin: Origin, name: str) -> Origin:
    """What a Proceso adherido's method sends or runs while it serves a call of ``origin``."""
    return _through(origin, Link("process", name))


def from_subscription(origin: Origin, subscription_id: str) -> Origin:
    """A Prompt a Suscripción wakes, after what woke it."""
    return _through(origin, Link("subscription", subscription_id))


def _through(origin: Origin, link: Link) -> Origin:
    return Origin(chain=(*origin.chain, link))


def _parse_link(label: object) -> Link:
    match = _LINK.fullmatch(label) if isinstance(label, str) else None
    msg = f"not a link of an Origin: {label!r}"
    if match is None:
        raise ValueError(msg)
    kind = _KINDS[match.group(1)]
    name = match.group(2) or ""
    # Human and Main are no one in particular; a bare ``session`` is one saved before #105.
    if bool(name) != (kind in _NAMED) and (kind, name) != ("session", ""):
        raise ValueError(msg)
    return Link(kind, name)


HUMAN = Origin(chain=(Link("human"),))
MAIN = Origin(chain=(Link("main"),))
