"""The Context Notice, ``context``: it shows once when what counts against a Session's limit
crosses ``notices.context.threshold`` of it (0.8 by default), and again only after falling
back below, e.g. after a Rewind. Without ``--kv-unified`` the limit is the slot's and what
counts is the Session's Context; with it, the pool's, and what the other Sessions' Steps in
course reserve counts too (#29). With ``compact.auto`` off it suggests ``/session compact``.

It is built into Main like a module of ``notices/`` (ADR-0009): it has the same Settings, and
a module declaring ``context`` replaces it.
"""

from collections.abc import Mapping

from pydantic import JsonValue

from allin.notices import NoticeEvent, NoticeType

_THRESHOLD = 0.8


def _check(event: NoticeEvent, params: dict[str, JsonValue]) -> Mapping[str, object] | None:
    threshold = params.get("threshold")
    share = _share(event)
    if not isinstance(threshold, int | float) or share is None:
        return None
    used, limit = share
    mark = threshold * limit
    if used < mark or (event.used_before is not None and event.used_before >= mark):
        return None  # below it, or already above it at the Step before
    # With ``compact.auto`` off nobody compacts it but a human (ADR-0017).
    hint = "" if event.compacts else f"; /session compact {event.session_id} makes room"
    return {"what": _what(event), "pct": 100 * used // limit, "limit": limit, "hint": hint}


CONTEXT = NoticeType(
    name="context",
    title="Context",
    text="{what} is at {pct}% of {limit} tokens{hint}",
    params={"threshold": _THRESHOLD},
    check=_check,
)


def context_left(event: NoticeEvent) -> str | None:
    """The line the LLM is told once the Context Notice showed in a Turn: how much is left
    when it ended, which it cannot see (#120)."""
    share = _share(event)
    if share is None:
        return None
    used, limit = share
    what = "The shared pool" if event.shared else "Your Context"
    left = max(0, limit - used)
    return f"{what} is at {100 * used // limit}% of {limit} tokens: about {left} tokens are left."


def _share(event: NoticeEvent) -> tuple[int, int] | None:
    """What counts against its limit, and that limit; ``None`` if either is unknown."""
    if event.used is None or not event.limit:
        return None
    return event.used, event.limit


def _what(event: NoticeEvent) -> str:
    return "the shared pool" if event.shared else "the Context"
