"""What a Notice module in ``<data-dir>/notices/`` imports to declare its type (ADR-0009).

A Notice module is part of Main, like ``src/``: it runs inside Main, with no enclosure and no
quarantine, so only the human writes it; it never travels in a Kit, every Profile shares it
and Main loads it when it starts (ADR-0016). For example::

    from allin.notices import NoticeEvent, notice

    @notice("context", title="Context", text="The Context is at {pct}%", threshold=80)
    def context(event: NoticeEvent, params: dict[str, object]) -> dict[str, object] | None:
        if event.context is None or event.limit is None:
            return None
        pct = 100 * event.context // event.limit
        threshold = params["threshold"]
        return {"pct": pct} if isinstance(threshold, int) and pct >= threshold else None

Each Session has the type's Settings as ``notices.<type>.enabled``, ``.title``, ``.text`` and
one per parameter of its own, copied by ``/new`` and changed with ``/session set``. ``text``
takes the variables the function returns, as ``{name}``.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import JsonValue

from allin.core.cut import CutReason
from allin.core.ids import SessionId


@dataclass(frozen=True)
class NoticeEvent:
    """What happened: in F1, a Step of a Session's Turn ended."""

    session_id: SessionId
    context: int | None  # tokens the Session's Context fills now
    limit: int | None  # each slot's context, once the Server said it
    cut: CutReason | None = None  # why the Step was cut, if it was (ADR-0004)
    output_cap: int | None = None  # the most it could generate
    # What counts against ``limit`` now and after the Session's Step before: its Context, plus
    # with a shared pool what other Sessions' Steps in course reserve of it.
    used: int | None = None
    used_before: int | None = None
    shared: bool = False  # whether ``limit`` is a ``--kv-unified`` pool
    compacts: bool = True  # whether the Session compacts on its own (``compact.auto``)


# Gets what happened and the Session's Settings of the type; gives the variables of its text,
# or ``None`` when there is nothing to tell.
type NoticeCheck = Callable[[NoticeEvent, dict[str, JsonValue]], Mapping[str, object] | None]


@dataclass(frozen=True)
class NoticeType:
    name: str
    title: str
    text: str
    params: dict[str, JsonValue]  # its own Settings, with their defaults
    check: NoticeCheck

    def defaults(self) -> dict[str, JsonValue]:
        """Every Setting of the type with its default, ``enabled`` first."""
        return {"enabled": True, "title": self.title, "text": self.text, **self.params}


# Filled while Main loads the modules; read by ``allin.main.notices``.
declared: list[NoticeType] = []


def notice(
    name: str, *, title: str, text: str, **params: JsonValue
) -> Callable[[NoticeCheck], NoticeCheck]:
    """Declare a Notice type; ``params`` are its own Settings, with their defaults."""

    def register(check: NoticeCheck) -> NoticeCheck:
        declared.append(NoticeType(name, title, text, dict(params), check))
        return check

    return register
