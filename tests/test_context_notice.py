import pytest
from pydantic import JsonValue

from allin.core.ids import SessionId
from allin.main.context_notice import CONTEXT
from allin.notices import NoticeEvent

PARAMS: dict[str, JsonValue] = {"threshold": 0.8}


def event(
    used: int | None, before: int | None, shared: bool = False, compacts: bool = True
) -> NoticeEvent:
    return NoticeEvent(
        SessionId("s1"),
        used,
        1000,
        used=used,
        used_before=before,
        shared=shared,
        compacts=compacts,
    )


def test_it_shows_when_the_context_crosses_the_threshold() -> None:
    assert CONTEXT.check(event(850, 700), PARAMS) == {
        "what": "the Context",
        "pct": 85,
        "limit": 1000,
        "hint": "",
    }


def test_with_compact_auto_off_it_suggests_compacting_by_hand() -> None:
    shown = CONTEXT.check(event(850, 700, compacts=False), PARAMS)
    assert shown is not None and shown["hint"] == "; /session compact s1 makes room"


@pytest.mark.parametrize(("used", "before"), [(700, 600), (850, 820), (900, 800), (None, 100)])
def test_it_does_not_show_below_it_or_again_above_it(used: int | None, before: int) -> None:
    assert CONTEXT.check(event(used, before), PARAMS) is None


def test_with_nothing_before_it_shows_once_above() -> None:
    assert CONTEXT.check(event(800, None), PARAMS) is not None


def test_with_a_shared_pool_it_names_the_pool() -> None:
    shown = CONTEXT.check(event(900, 100, shared=True), PARAMS)
    assert shown is not None and shown["what"] == "the shared pool"


def test_a_threshold_that_is_not_a_number_shows_nothing() -> None:
    assert CONTEXT.check(event(900, 100), {"threshold": "high"}) is None


def test_its_settings_are_like_a_modules() -> None:
    assert CONTEXT.defaults() == {
        "enabled": True,
        "title": "Context",
        "text": "{what} is at {pct}% of {limit} tokens{hint}",
        "threshold": 0.8,
    }


def test_a_full_limit_is_100_percent() -> None:
    shown = CONTEXT.check(event(1000, 100), PARAMS)
    assert shown is not None and shown["pct"] == 100
