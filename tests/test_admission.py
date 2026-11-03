import pytest

from allin.core.ids import MainId, SessionId
from allin.core.wait_list import Reservation, WaitClass, Waiter, WaitListView
from allin.main.admission import Admission, Waiting, admit, server_waiting
from allin.main.events import (
    ServerAsleep,
    ServerDown,
    ServerFailed,
    ServerLoading,
    ServerReady,
    ServerRestarting,
    ServerState,
)
from allin.main.slots import ServerProps

A, B = MainId("root"), MainId("other")
PATIENCE = 60.0


def waiter(
    session: str,
    since: float,
    need: int = 100,
    kind: WaitClass = "prompt",
    main: MainId = A,
) -> Waiter:
    return Waiter(
        main=main,
        pid=1,
        session=SessionId(session),
        kind=kind,
        since=since,
        need=need,
        firm_at=since + PATIENCE,
    )


def held(tokens: int, session: str = "busy") -> Reservation:
    return Reservation(main=B, pid=1, session=SessionId(session), tokens=tokens)


def view(*waiting: Waiter, reserved: tuple[Reservation, ...] = ()) -> WaitListView:
    return WaitListView(waiting=waiting, reserved=reserved)


def test_alone_it_goes() -> None:
    me = waiter("s1", 1.0)
    assert admit(me, view(me), pool=None, now=2.0) == Admission("go", 1)


def test_without_a_shared_pool_the_first_to_arrive_goes_first() -> None:
    first, me = waiter("s1", 1.0, main=B), waiter("s2", 2.0)
    assert admit(me, view(first, me), pool=None, now=3.0) == Admission("queued", 2)
    assert admit(first, view(first, me), pool=None, now=3.0) == Admission("go", 1)


def test_continuations_go_before_new_prompts_and_describe_last() -> None:
    describe = waiter("d", 0.5, kind="describe")
    prompt = waiter("p", 1.0)
    later = waiter("c", 9.0, kind="continuation", main=B)
    listed = view(describe, prompt, later)
    assert admit(later, listed, pool=None, now=10.0).verdict == "go"
    assert admit(prompt, listed, pool=None, now=10.0) == Admission("queued", 2)
    assert admit(describe, listed, pool=None, now=10.0) == Admission("queued", 3)


def test_a_prompt_an_fproc_brings_goes_after_the_new_prompts_of_a_human() -> None:
    fproc = waiter("f", 0.5, kind="fproc")
    human = waiter("h", 9.0)
    describe = waiter("d", 0.1, kind="describe")
    listed = view(fproc, human, describe)
    assert admit(human, listed, pool=None, now=10.0) == Admission("go", 1)
    assert admit(fproc, listed, pool=None, now=10.0) == Admission("queued", 2)
    assert admit(describe, listed, pool=None, now=10.0) == Admission("queued", 3)


def test_a_candidate_not_yet_listed_counts_in_its_place() -> None:
    first, me = waiter("s1", 1.0), waiter("s2", 0.5, main=B)
    assert admit(me, view(first), pool=None, now=3.0) == Admission("go", 1)


def test_with_a_pool_a_step_goes_only_if_it_fits_in_what_is_free() -> None:
    me = waiter("s2", 2.0, need=50_000)
    pool = 128_000
    assert admit(me, view(me, reserved=(held(80_000),)), pool, now=3.0) == Admission("pool", 1)
    small = waiter("s3", 2.0, need=10_000)
    assert admit(small, view(small, reserved=(held(80_000),)), pool, now=3.0).verdict == "go"


def test_one_that_fits_overtakes_a_big_one_that_does_not() -> None:
    big, small = waiter("big", 1.0, need=60_000, main=B), waiter("small", 2.0, need=10_000)
    listed = view(big, small, reserved=(held(80_000),))
    assert admit(small, listed, pool=128_000, now=3.0) == Admission("go", 2)
    assert admit(big, listed, pool=128_000, now=3.0) == Admission("pool", 1)


def test_once_the_big_one_waited_long_enough_nobody_overtakes_it() -> None:
    big, small = waiter("big", 1.0, need=60_000, main=B), waiter("small", 2.0, need=10_000)
    listed = view(big, small, reserved=(held(80_000),))
    assert admit(small, listed, pool=128_000, now=1.0 + PATIENCE) == Admission("pool", 2)
    assert admit(small, listed, pool=128_000, now=PATIENCE).verdict == "go"


def test_one_ahead_that_fits_goes_first() -> None:
    first, me = waiter("s1", 1.0, need=10_000, main=B), waiter("s2", 2.0, need=10_000)
    listed = view(first, me, reserved=(held(80_000),))
    assert admit(me, listed, pool=128_000, now=3.0) == Admission("queued", 2)


def test_every_reservation_counts_whatever_main_holds_it() -> None:
    me = waiter("s1", 1.0, need=30_000)
    reserved = (held(50_000, "x"), held(50_000, "y"))
    assert admit(me, view(me, reserved=reserved), pool=128_000, now=2.0).verdict == "pool"
    assert admit(me, view(me, reserved=reserved[:1]), pool=128_000, now=2.0).verdict == "go"


@pytest.mark.parametrize(
    ("waiting", "text"),
    [
        (Waiting("queued", 1), "queued (1st)"),
        (Waiting("queued", 2), "queued (2nd)"),
        (Waiting("queued", 3), "queued (3rd)"),
        (Waiting("queued", 4), "queued (4th)"),
        (Waiting("queued", 11), "queued (11th)"),
        (Waiting("queued", 12), "queued (12th)"),
        (Waiting("queued", 13), "queued (13th)"),
        (Waiting("queued", 21), "queued (21st)"),
        (Waiting("queued", 22), "queued (22nd)"),
        (Waiting("queued", 23), "queued (23rd)"),
        (Waiting("slot"), "waiting for a slot"),
        (Waiting("pool"), "waiting for room in the pool"),
        (Waiting("loading"), "Server loading"),
        (Waiting("restarting"), "Server restarting"),
        (Waiting("down"), "Server down"),
        (Waiting("asleep"), "Supervisor asleep"),
        (Waiting("failed"), "Supervisor failed"),
        (Waiting("link"), "waiting for its Enlace to change"),
    ],
)
def test_what_the_border_says(waiting: Waiting, text: str) -> None:
    assert waiting.text() == text


@pytest.mark.parametrize(
    ("state", "waiting"),
    [
        (None, Waiting("down")),
        (ServerDown(), Waiting("down")),
        (ServerLoading(), Waiting("loading")),
        (ServerRestarting(), Waiting("restarting")),
        (ServerAsleep(), Waiting("asleep")),
        (ServerFailed("crashes", "x", ()), Waiting("failed")),
        (ServerReady(ServerProps(1, 4096, "m")), None),
    ],
)
def test_why_a_server_holds_its_sessions(state: ServerState | None, waiting: Waiting) -> None:
    assert server_waiting(state) == waiting


def test_exactly_what_is_free_is_enough() -> None:
    first, me = waiter("s1", 1.0, need=48_000, main=B), waiter("s2", 2.0, need=48_000)
    listed = view(first, me, reserved=(held(80_000),))
    assert admit(me, listed, pool=128_000, now=3.0) == Admission("queued", 2)  # 48k fits
    assert admit(first, listed, pool=128_000, now=3.0).verdict == "go"


def test_a_candidate_already_listed_counts_once() -> None:
    listed, behind = waiter("s1", 1.0, need=100), waiter("s2", 2.0, main=B)
    me = waiter("s1", 1.0, need=200)  # what it needs now
    assert admit(me, view(listed, behind), pool=None, now=3.0) == Admission("go", 1)


def test_queued_with_no_place_yet_says_only_queued() -> None:
    assert Waiting("queued").text() == "queued"
    assert Waiting("queued", 111).text() == "queued (111th)"
