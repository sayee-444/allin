"""The one way a Turn leaves Main's queue (#196): whatever ends it, running or queued, what a
human typed goes back to the Draft once, what queued behind it falls with it, its falls are
forgotten, what it opened is cancelled and who sent it hears."""

from collections.abc import Callable
from pathlib import Path

import pytest

from allin.core.errors import LlmUnavailable, TurnCancelled, TurnCrashed
from allin.core.ids import SessionId
from allin.core.origin import HUMAN
from allin.core.origin import from_session as chained
from allin.main.chat_rebuild import FAILED_BEFORE, returned_prompts
from allin.main.conversation import Item
from allin.main.events import (
    DraftReceived,
    InputReceived,
    MainCommandEntered,
    MainEvent,
    QueuedWithdrawn,
    StepEnded,
    Tick,
    ToolsEnded,
    ToolsStarted,
    TurnEnded,
    WindowClosed,
)
from allin.main.log import LogEntry
from allin.main.prompts import Prompt
from allin.main.sessions import Session
from allin.main.state import MainState, MainStateStore
from allin.main.turn_exit import Leaving
from tests.test_dispatcher import MainHarness, ended, restored, server, server_ready, together
from tests.test_errands import inbox
from tests.test_profile_switch import other_profile, switch
from tests.test_restart import restarting
from tests.test_subscriptions import from_session, logged, woken
from tests.test_write_during_turn import CALLED, RETURNED

FELL = LlmUnavailable(reason="Connection error.", reached=True)
HALF = LlmUnavailable(reason="Connection error.")  # it fell before its Step reached the Server


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.hold_turns = True
    return harness


def with_a_text_typed_in(session: Session) -> list[MainEvent]:
    """A human Turn «duerme» whose tools run while «y luego esto» enters it (ADR-0024)."""
    return [
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "duerme"),
        ToolsStarted(session.id, ("nap",), CALLED),
        InputReceived(session.id, "y luego esto"),
        ToolsEnded(session.id, RETURNED, 10),
        StepEnded(session.id, RETURNED.interjecting(["y luego esto"])),
    ]


def events(main: MainHarness, session: Session) -> list[str]:
    return [entry.event for entry in main.session_log(session)]


def drafts(main: MainHarness) -> dict[SessionId, list[str]]:
    return main.state.load().drafts


# A Turn suspended or yielded that no longer fits as it resumes (#176, #16)


def test_a_suspended_turn_that_no_longer_fits_gives_back_its_texts_and_its_queue_falls(
    main: MainHarness,
):
    session = main.create_session()

    def outputs_grow() -> None:  # what its tools gave back no longer fits; a new Prompt does
        def tokens_of(items: list[Item]) -> int:
            fed = any(item.get("type") == "function_call_output" for item in items)
            return 10_000_000 if fed else 100

        main.servers.tokens_of = tokens_of

    main.run(
        *with_a_text_typed_in(session),
        InputReceived(session.id, "a2"),  # queued behind its Turn
        outputs_grow,
        TurnEnded(session.id, error=FELL),
        server_ready(server()),
        settle=True,
    )
    [window] = main.windows(session.id)
    assert restored(window) == ["duerme", "y luego esto", "a2"]
    assert len(main.held) == 1, "a2 ran without the Turn before it in the Conversation"
    [too_big] = [e.fields for e in main.session_log(session) if e.event == "prompt_too_big"]
    assert too_big["joined"] == ["y luego esto"]
    [dropped] = [e.fields for e in main.session_log(session) if e.event == "prompts_dropped"]
    assert dropped["texts"] == ["a2"]


# Closing the Window, /close or Shift+Enter cancel for good, however the Turn's end comes


def window_closed(session_id: SessionId) -> MainEvent:
    return WindowClosed(session_id, returncode=0)


def closed_from_main_chat(session_id: SessionId) -> MainEvent:
    return MainCommandEntered(f"/close {session_id}")


CANCELS = [
    pytest.param(window_closed, id="window-closed"),
    pytest.param(closed_from_main_chat, id="close"),
]


@pytest.mark.parametrize("cancel", CANCELS)
def test_cancelling_during_a_restart_now_does_not_resume_the_turn(
    main: MainHarness, cancel: Callable[[SessionId], MainEvent]
):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "hola"),
        restarting(now=True),  # Main cancels its Step to repeat it once the Server is back
        cancel(session.id),
        TurnEnded(session.id, error=TurnCancelled(reason="the Server restarts now")),
        server_ready(main.server),
        MainCommandEntered("/exit"),
        settle=True,
    )
    assert len(main.held) == 1, "it ran again after it was cancelled"
    assert "turn_suspended" not in events(main, session)
    assert drafts(main) == {session.id: ["hola"]}


@pytest.mark.parametrize("fell_before", [False, True])
@pytest.mark.parametrize("cancel", CANCELS)
def test_cancelling_while_its_fall_is_in_the_inbox_cancels_the_turn(
    main: MainHarness, cancel: Callable[[SessionId], MainEvent], fell_before: bool
):
    session = main.create_session()
    before = [TurnEnded(session.id, error=FELL), server_ready(server())] if fell_before else []
    main.run(
        *with_a_text_typed_in(session),
        *before,  # its Step already fell once: one more fall fails it (ADR-0023)
        together(main, cancel(session.id), TurnEnded(session.id, error=FELL)),
        server_ready(server()),
        MainCommandEntered("/exit"),
        settle=True,
    )
    assert len(main.held) == 1 + fell_before, "it ran again after it was cancelled"
    logged_events = events(main, session)
    assert logged_events.count("turn_suspended") == fell_before
    assert "turn_cancelled" in logged_events and "turn_failed" not in logged_events
    assert drafts(main) == {session.id: ["duerme", "y luego esto"]}


def test_switching_profile_now_during_a_restart_now_does_not_resume_the_turn(main: MainHarness):
    a = main.create_session()
    other_profile(main)
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        InputReceived(a.id, "a1"),
        restarting(now=True),
        switch("tesis", now=True),
        TurnEnded(a.id, error=TurnCancelled(reason="the Server restarts now")),
        settle=True,
    )
    assert "turn_suspended" not in events(main, a)
    saved = MainStateStore(main.profile.state).load()
    assert saved.drafts == {a.id: ["a1"]} and saved.queue == []


def test_shift_enter_while_its_fall_is_in_the_inbox_saves_the_turn_cut(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "hola"),
        together(
            main,
            InputReceived(session.id, "", interrupt=True),
            TurnEnded(session.id, error=HALF),
        ),
        server_ready(server()),
        settle=True,
    )
    assert "turn_suspended" not in events(main, session)
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None
    [turn] = conversation.turns
    assert turn.cut == "interrupted"


def test_a_fall_after_closing_still_tells_its_server_watch(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "hola"),
        together(main, window_closed(session.id), TurnEnded(session.id, error=FELL)),
        settle=True,
    )
    assert main.servers.rechecks == 1, "the watch may not have seen its Server fall"


# What entered a Turn survives a Main killed outright (#176)


def test_a_kill_mid_turn_keeps_its_interjected_texts(main: MainHarness):
    session = main.create_session()
    saved: list[MainState] = []
    main.run(
        *with_a_text_typed_in(session)[:5],
        DraftReceived(session.id, ("typing",)),
        lambda: saved.append(main.state.load()),  # what a kill now would leave
        settle=True,
    )
    main.state.save(saved[0])  # killed: no orderly stop came after it
    main.run(MainCommandEntered(f"/open {session.id}"), MainCommandEntered("/exit"), settle=True)
    assert restored(main.windows(session.id)[-1]) == ["duerme", "y luego esto", "typing"]


# A suspended Turn that leaves the queue


def test_an_errand_cancelled_while_its_turn_is_suspended_gives_back_what_a_human_typed(
    main: MainHarness,
):
    c, a = main.create_session(), main.create_session()

    def unsubscribe() -> None:
        [opened] = logged(main, c, "subscribed")
        inbox(main).put(MainCommandEntered(f"/unsubscribe {c.id} {opened['subscription']}"))

    main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered(f"/errand {c.id} --to={a.id} trabaja"),
        ToolsStarted(a.id, ("nap",), CALLED),
        InputReceived(a.id, "y luego esto"),
        ToolsEnded(a.id, RETURNED, 10),
        StepEnded(a.id, RETURNED.interjecting(["y luego esto"])),
        TurnEnded(a.id, error=FELL),  # suspended, it waits in the queue for its Server
        unsubscribe,
        MainCommandEntered("/exit"),
        settle=True,
    )
    assert "prompts_dropped" in events(main, a)
    assert drafts(main) == {a.id: ["y luego esto"]}


def test_a_suspended_turn_that_leaves_the_queue_cancels_what_it_opened(main: MainHarness):
    a = main.create_session()
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered(f"/send {a.id} uno"),
        MainCommandEntered(f"/subscribe {a.id} timer:100"),  # while its Turn is under way
        TurnEnded(a.id, error=FELL),
        window_closed(a.id),
        settle=True,
    )
    [cancelled] = logged(main, a, "subscription_cancelled")
    assert "its Turn was not saved" in str(cancelled)


# Up takes back only what a human queued


def test_up_leaves_a_prompt_of_another_origin_in_the_queue(main: MainHarness):
    a, sender = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        InputReceived(a.id, "a1"),
        MainCommandEntered(f"/send {a.id} trabajo", origin=from_session(sender)),
        QueuedWithdrawn(a.id),
        ended(a),
        settle=True,
    )
    [window] = main.windows(a.id)
    assert restored(window) == []
    assert len(main.held) == 2, "its Prompt ran"


def test_up_on_a_prompt_someone_waits_for_tells_them_it_has_no_answer(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/open {b.id}"),
        InputReceived(b.id, "b1"),
        MainCommandEntered(f"/send {b.id} --reply-to={a.id} dos"),  # it waits behind b1
        QueuedWithdrawn(b.id),
        Tick(),
        settle=True,
    )
    assert restored(main.windows(b.id)[0]) == ["dos"]
    [wake] = woken(main, a)
    assert "did not answer: it was taken back to the input" in str(wake["text"])


# Reopening after a Rewind gives back in the order an open Window has


def test_a_rewind_with_the_window_closed_gives_back_in_the_order_it_has_open(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "r1"),
        ended(session),
        window_closed(session.id),
        MainCommandEntered(f"/send {session.id} a1"),
        TurnEnded(session.id, error=TurnCrashed(reason="boom")),
        MainCommandEntered(f"/rewind {session.id} 1"),
        MainCommandEntered(f"/open {session.id}"),
        settle=True,
    )
    assert restored(main.windows(session.id)[-1]) == ["r1", "a1"]


def test_one_of_another_origin_gives_back_its_texts_and_its_queue_as_the_window_opens(
    main: MainHarness,
):
    session, sender = main.create_session(), main.create_session()

    def outputs_grow() -> None:
        def tokens_of(items: list[Item]) -> int:
            fed = any(item.get("type") == "function_call_output" for item in items)
            return 10_000_000 if fed else 100

        main.servers.tokens_of = tokens_of

    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/send {session.id} trabajo", origin=from_session(sender)),
        ToolsStarted(session.id, ("nap",), CALLED),
        InputReceived(session.id, "y luego esto"),
        ToolsEnded(session.id, RETURNED, 10),
        StepEnded(session.id, RETURNED.interjecting(["y luego esto"])),
        window_closed(session.id),  # its Turn goes on: no human asked for it (ADR-0013)
        MainCommandEntered(f"/send {session.id} a2"),
        outputs_grow,
        TurnEnded(session.id, error=FELL),
        server_ready(server()),
        MainCommandEntered(f"/open {session.id}"),
        settle=True,
    )
    assert len(main.held) == 1
    assert restored(main.windows(session.id)[-1]) == ["y luego esto", "a2"]


def test_a_human_prompt_that_cannot_start_goes_back_as_its_window_opens(main: MainHarness):
    session = main.create_session(server="nowhere")  # an Enlace with no Server Settings
    main.run(
        MainCommandEntered(f"/send {session.id} hola"),
        MainCommandEntered(f"/open {session.id}"),
        settle=True,
    )
    assert "prompt_not_started" in events(main, session)
    assert restored(main.windows(session.id)[0]) == ["hola"]


def entry(event: str, **fields: object) -> LogEntry:
    return LogEntry.model_validate({"ts": "2026-11-03T00:00:00Z", "event": event, **fields})


@pytest.mark.parametrize(
    ("log", "texts"),
    [
        pytest.param(
            [
                entry("prompt_too_big", typed="duerme", origin=["human"], joined=["y esto"]),
                entry("prompts_dropped", reason=FAILED_BEFORE, texts=["a2"], kinds=["prompt"]),
            ],
            ["duerme", "y esto", "a2"],
            id="a-suspended-turn-and-its-queue",
        ),
        pytest.param(
            [
                entry(
                    "prompt_too_big",
                    typed="trabajo",
                    origin=["human", "session:ab12"],
                    joined=["y"],
                ),
            ],
            ["y"],
            id="of-another-origin-its-texts-only",
        ),
        pytest.param(
            [
                entry("prompt_not_started", typed="a1", origin=["human"]),
                entry("prompt_too_big", typed="b1", origin=["human"]),
            ],
            ["b1", "a1"],
            id="each-ahead-of-the-one-before",
        ),
    ],
)
def test_what_could_not_start_or_go_on_comes_back_from_the_log(
    log: list[LogEntry], texts: list[str]
):
    assert returned_prompts(log) == texts


# The rule, for a Prompt alone


def test_a_queued_prompt_leaves_with_the_texts_its_steps_took_if_its_turn_ran():
    step = RETURNED.interjecting(["y luego esto"])
    suspended = Prompt(SessionId("s"), "duerme", suspended=True, steps=(step,))
    leaving = Leaving.queued(suspended)
    assert leaving == Leaving(suspended, ("y luego esto",), ran=True)
    assert leaving.back == ["duerme", "y luego esto"] and leaving.unsaved
    fresh = Leaving.queued(Prompt(SessionId("s"), "a2", typed="a2 @top_k=5"))
    assert fresh.back == ["a2 @top_k=5"] and fresh.told and not fresh.unsaved


def test_what_goes_back_is_only_what_a_human_typed():
    other = Prompt(SessionId("s"), "trabajo", origin=chained(HUMAN, SessionId("t")))
    assert Leaving(other, ("y luego esto",), ran=True).back == ["y luego esto"]
    compaction = Prompt(SessionId("s"), "", "compact", suspended=True)
    leaving = Leaving.queued(compaction)
    assert leaving.back == [] and not leaving.told and not leaving.unsaved and leaving.ran
