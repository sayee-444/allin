"""Suscripciones (#116, ADR-0011, ADR-0013): an event wakes a Session's own LLM with a Prompt.

The Turns are held, and each one ends when the test says, with the answer it says; a tick is
when what came wakes a Session with no Turn."""

from pathlib import Path

import pytest
from pydantic import JsonValue

from allin.core.errors import TurnCrashed
from allin.core.origin import HUMAN, Link, Origin
from allin.main.conversation import Step
from allin.main.events import MainCommandEntered, Tick, TurnEnded
from allin.main.sessions import Session
from tests.test_dispatcher import MainHarness


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)  # one slot, as ``server()`` says
    harness.hold_turns = True
    return harness


def from_session(session: Session, origin: Origin = HUMAN) -> Origin:
    return Origin(chain=(*origin.chain, Link("session", session.id)))


def answers(session: Session, text: str) -> TurnEnded:
    """Its held Turn ends, its last Step having written ``text``."""
    message = {"type": "message", "content": [{"type": "output_text", "text": text}]}
    return TurnEnded(session.id, steps=[Step(output=[message])])


def logged(main: MainHarness, session: Session, event: str) -> list[dict[str, JsonValue]]:
    return [entry.fields for entry in main.session_log(session) if entry.event == event]


def woken(main: MainHarness, session: Session) -> list[dict[str, JsonValue]]:
    """The Prompts its Suscripciones woke it with, as its Log keeps them."""
    return [fields for fields in logged(main, session, "prompt") if "woke_by" in fields]


def woke_by(wake: dict[str, JsonValue]) -> list[str]:
    ids = wake["woke_by"]
    assert isinstance(ids, list)
    return [str(i) for i in ids]


def started(main: MainHarness, session: Session) -> int:
    return len(logged(main, session, "turn_started"))


def test_a_reply_wakes_the_sender_with_the_answer_and_waiting_holds_no_slot(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    counts: list[int] = []
    main.run(
        MainCommandEntered(f"/send {b.id} --reply-to=me hola", origin=from_session(a)),
        lambda: counts.append(started(main, b)),  # the one slot is free for it
        answers(b, "hola, A"),
        Tick(),
        settle=True,
    )
    assert counts == [1]
    [wake] = woken(main, a)
    assert wake["text"] == (
        f"[from Subscription {a.id}.reply.{b.id}]\n"
        f"reply:{b.id} ({woke_by(wake)[0]}): Session {b.id} answered:\nhola, A"
    )
    assert wake["origin"] == [
        "human",
        f"session:{a.id}",
        f"session:{b.id}",
        f"subscription:{a.id}.reply.{b.id}",
    ]
    assert started(main, a) == 1, "only once it woke"


def test_events_that_come_one_after_another_wake_it_once(main: MainHarness):
    a, b, c = main.create_session(), main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/send {b.id} --reply-to={a.id} uno"),
        MainCommandEntered(f"/send {c.id} --reply-to={a.id} dos"),
        answers(b, "de B"),
        answers(c, "de C"),
        Tick(),
        settle=True,
    )
    [wake] = woken(main, a)
    assert len(woke_by(wake)) == 2
    text = str(wake["text"])
    assert "answered:\nde B" in text and "answered:\nde C" in text


def test_a_session_only_subscribes_itself(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    chat = main.run(
        MainCommandEntered(f"/send {b.id} --reply-to={b.id} hola", origin=from_session(a)),
        MainCommandEntered(f"/subscribe {b.id} timer:5", origin=from_session(a)),
        settle=True,
    )
    assert sum("subscription_not_own" in line for line in chat) == 2, chat
    assert logged(main, b, "prompt") == [], "nothing was sent"


def test_a_cycle_a_b_a_is_cut_at_its_cap_with_a_notification(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    origins: list[Origin] = []

    def a_sends_again() -> None:
        """What A's Turn woken by the reply does: send B the same again."""
        [*_, wake] = woken(main, a)
        origin = Origin.model_validate(wake["origin"])
        origins.append(origin)
        sent = MainCommandEntered(
            f"/send {b.id} --reply-to=me otra", origin=from_session(a, origin)
        )
        inbox = main.servers.inbox
        assert inbox is not None
        inbox.put(sent)

    main.run(
        MainCommandEntered("/set subscription.repeats=2"),
        MainCommandEntered(f"/send {b.id} --reply-to=me hola", origin=from_session(a)),
        answers(b, "1"),
        Tick(),
        answers(a, "a1"),
        a_sends_again,
        answers(b, "2"),
        Tick(),
        answers(a, "a2"),
        a_sends_again,
        answers(b, "3"),
        Tick(),
        settle=True,
    )
    assert len(woken(main, a)) == 2, "the third would be its third time in one chain"
    link = f"subscription:{a.id}.reply.{b.id}"
    assert origins[-1].labels.count(link) == 2
    [cut] = logged(main, a, "subscription_cut")
    [told] = [n for n in logged(main, a, "notification") if n["source"] == "subscription"]
    assert "did not wake you" in str(told["text"]) and "subscription.repeats is 2" in str(
        told["text"]
    )
    assert cut["origin"] == [*origins[-1].labels, f"session:{a.id}", f"session:{b.id}"]


def test_a_rewind_to_before_it_was_opened_cancels_it(main: MainHarness):
    a = main.create_session()
    chat = main.run(
        MainCommandEntered(f"/subscribe {a.id} timer:100"),  # before its first Turn: it stays
        MainCommandEntered(f"/send {a.id} uno"),
        answers(a, "1"),
        MainCommandEntered(f"/subscribe {a.id} timer:200"),  # after it
        MainCommandEntered(f"/rewind {a.id} 1"),
        MainCommandEntered(f"/subscribe {a.id}"),
        settle=True,
    )
    [cancelled] = logged(main, a, "subscription_cancelled")
    assert cancelled["reason"] == "rewound"
    listed = [line for line in chat if "  timer:" in line]  # what /subscribe <sid> lists
    assert len(listed) == 1 and "timer:100" in listed[0], chat


def test_a_rewind_of_another_session_wakes_it_with_the_prompts_undone(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/subscribe {a.id} rewind:{b.id}"),
        MainCommandEntered(f"/send {b.id} uno"),
        answers(b, "1"),
        MainCommandEntered(f"/send {b.id} dos"),
        answers(b, "2"),
        MainCommandEntered(f"/send {b.id} tres"),
        answers(b, "3"),
        MainCommandEntered(f"/rewind {b.id} 2"),
        Tick(),
        settle=True,
    )
    ids = [str(p["prompt_id"]) for p in logged(main, b, "prompt")]
    [wake] = woken(main, a)
    assert f"Session {b.id} rewound Prompts 2\N{EN DASH}3 (ids {ids[1]}, {ids[2]})" in str(
        wake["text"]
    )
    assert wake["origin"] == ["human", f"subscription:{woke_by(wake)[0]}"]


def test_a_turn_that_leaves_a_reply_open_answers_only_once_it_came(main: MainHarness):
    a, b, c = main.create_session(), main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/send {b.id} --reply-to={a.id} reparte"),
        # B's Turn, under way, asks C and leaves the reply open.
        MainCommandEntered(f"/send {c.id} --reply-to=me parte", origin=from_session(b)),
        answers(b, "repartí el trabajo"),
        Tick(),
        answers(c, "hecho por C"),
        Tick(),
        answers(b, "todo hecho"),
        Tick(),
        settle=True,
    )
    assert len(woken(main, b)) == 1
    [wake] = woken(main, a)
    assert "answered:\ntodo hecho" in str(wake["text"])
    assert "repartí" not in str(wake["text"])


def test_a_prompt_that_fails_or_leaves_the_queue_still_wakes_who_waits(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/send {b.id} --reply-to={a.id} uno"),
        MainCommandEntered(f"/send {b.id} --reply-to={a.id} dos"),  # it waits behind uno
        TurnEnded(b.id, error=TurnCrashed(reason="boom")),
        Tick(),
        settle=True,
    )
    [wake] = woken(main, a)
    text = str(wake["text"])
    assert "did not answer: its Turn failed" in text
    assert "did not answer: it left the queue" in text


def test_a_timer_wakes_it_once_its_time_went_by(main: MainHarness):
    a = main.create_session()

    def later(seconds: float) -> None:
        main.now += seconds

    main.run(
        MainCommandEntered(f"/subscribe {a.id} timer:10"),
        lambda: later(6),
        Tick(),
        lambda: later(6),
        Tick(),
        settle=True,
    )
    [wake] = woken(main, a)
    assert "its time came" in str(wake["text"])


def test_with_its_profile_inactive_it_is_frozen_and_what_came_wakes_it_on_return(
    main: MainHarness,
):
    a, b = main.create_session(), main.create_session()
    other = main.profiles.create("tesis", main.profile)
    main.profiles.add_session(other, b.id)  # both Profiles share B

    def later(seconds: float) -> None:
        main.now += seconds

    main.run(
        MainCommandEntered(f"/subscribe {a.id} rewind:{b.id}"),
        MainCommandEntered(f"/subscribe {a.id} timer:10"),
        MainCommandEntered(f"/send {b.id} uno"),
        answers(b, "1"),
        MainCommandEntered("/profile switch tesis"),
        MainCommandEntered(f"/rewind {b.id} 1"),
        lambda: later(60),
        Tick(),
        MainCommandEntered("/profile switch default"),
        Tick(),
        settle=True,
    )
    [wake] = woken(main, a)
    assert f"Session {b.id} rewound Prompt 1" in str(wake["text"])
    assert "its time came" not in str(wake["text"]), "the timer ran only with its Profile"


def test_suscripciones_survive_a_restart_and_a_prompt_cut_by_it_still_answers(
    main: MainHarness,
):
    a, b = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/subscribe {a.id} timer:5"),
        MainCommandEntered(f"/send {b.id} --reply-to={a.id} hola"),  # its Turn dies with Main
        settle=True,
    )

    def later() -> None:
        main.now += 6

    main.run(later, Tick(), settle=True)
    [wake] = woken(main, a)
    text = str(wake["text"])
    assert "its time came" in text
    assert f"Session {b.id} did not answer" in text


def test_a_rewind_cancels_a_reply_that_came_but_has_not_woken_it_yet(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/send {a.id} uno"),
        # A's Turn, under way, asks B; B answers before A's Turn ends.
        MainCommandEntered(f"/send {b.id} --reply-to=me hola", origin=from_session(a)),
        answers(a, "1"),
        answers(b, "de B"),
        MainCommandEntered(f"/rewind {a.id} 1"),
        Tick(),
        settle=True,
    )
    assert woken(main, a) == []
    [cancelled] = logged(main, a, "subscription_cancelled")
    assert cancelled["reason"] == "rewound"


def test_a_reply_to_an_old_id_wakes_the_session_by_its_new_one(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    old = a.id
    main.run(
        MainCommandEntered(f"/session id {old} nuevaa"),
        MainCommandEntered(f"/send {b.id} --reply-to={old} hola"),
        answers(b, "hola"),
        Tick(),
        settle=True,
    )
    renamed = main.sessions.load(a.id)
    assert renamed.id == "nuevaa"
    assert len(woken(main, renamed)) == 1


def test_only_a_session_of_the_active_profile_subscribes(main: MainHarness):
    main.sessions_join_profile = False
    a = main.create_session()
    chat = main.run(MainCommandEntered(f"/subscribe {a.id} timer:5"), settle=True)
    assert any("outside_profile" in line for line in chat), chat
