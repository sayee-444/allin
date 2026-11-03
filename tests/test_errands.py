"""Encargos and Lotes (#117, ADR-0011): a Session hands tasks out to others and wakes once,
with every result.

The Turns are held, and each one ends when the test says; a tick is when what came wakes a
Session with no Turn, and when deadlines run."""

from pathlib import Path

import pytest
from pydantic import JsonValue

from allin.core.errors import LlmUnavailable, TurnCancelled, TurnCrashed
from allin.main.events import MainCommandEntered, Tick, TurnEnded
from allin.main.sessions import Session
from tests.test_dispatcher import (
    DrainingInbox,
    MainHarness,
    server,
    server_down,
    server_ready,
)
from tests.test_subscriptions import answers, from_session, logged, woken


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)  # one slot, as ``server()`` says
    harness.hold_turns = True
    return harness


def tasks(main: MainHarness, session: Session) -> list[str]:
    """The task each Prompt it got carried, under its header."""
    return [str(f["text"]).split("\n", 1)[1] for f in logged(main, session, "prompt")]


def told(main: MainHarness, session: Session) -> list[str]:
    return [
        str(f["text"]) for f in logged(main, session, "notification") if f["source"] == "errand"
    ]


def later(main: MainHarness, seconds: float):
    def step() -> None:
        main.now += seconds

    return step


def answer_running(main: MainHarness, text: str):
    """The Turn that runs now, with one slot the last one held, ends with ``text``."""

    def step() -> None:
        inbox(main).put(answers(main.sessions.load(main.held[-1].session_id), text))

    return step


def inbox(main: MainHarness) -> DrainingInbox:
    """Where a step puts an event, to come after the ones already there."""
    queue = main.servers.inbox
    assert isinstance(queue, DrainingInbox)
    return queue


def cancelled_for(main: MainHarness, session: Session) -> list[str]:
    """Why each of its held Turns was cancelled, if it was."""
    return [
        str(job.cancellation.reason)
        for job in main.held
        if job.session_id == session.id and job.cancellation.reason
    ]


def wake_text(main: MainHarness, session: Session) -> str:
    [wake] = woken(main, session)
    return str(wake["text"])


def test_an_errand_to_three_sessions_wakes_its_session_once_with_the_three_results(
    main: MainHarness,
):
    c, a, b, d = (main.create_session() for _ in range(4))
    main.run(
        MainCommandEntered(f"/errand {c.id} --to={a.id},{b.id},{d.id} cuenta hasta tres"),
        answer_running(main, "uno"),  # one slot: they take turns, in no set order
        Tick(),
        answer_running(main, "dos"),
        Tick(),
        answer_running(main, "tres"),
        Tick(),
        settle=True,
    )
    assert tasks(main, a) == tasks(main, b) == tasks(main, d) == ["cuenta hasta tres"]
    [prompt] = logged(main, a, "prompt")
    assert str(prompt["text"]).startswith(f"[from Session {c.id} · Encargo e")
    text = wake_text(main, c)
    assert "3 of 3 answered" in text
    order = [main.sessions.load(job.session_id) for job in main.held[:3]]
    for worker, result in zip(order, ("uno", "dos", "tres"), strict=True):
        assert f"- Session {worker.id} answered:\n{result}" in text
    assert [t.split(": ")[-1] for t in told(main, c)] == [
        "1 of 3 answered",
        "2 of 3 answered",
        "3 of 3 answered",
    ]


def test_each_worker_may_have_its_own_task(main: MainHarness):
    c, a, b = (main.create_session() for _ in range(3))
    main.run(
        MainCommandEntered(f'/errand {c.id} {{"{a.id}": "suma", "{b.id}": "resta"}}'),
        settle=True,
    )
    assert tasks(main, a) == ["suma"]
    assert tasks(main, b) == ["resta"]


def test_with_k_1_the_first_that_ends_well_cancels_the_others(main: MainHarness):
    main.server = server(slots=2)
    c, a, b, d = (main.create_session() for _ in range(4))
    main.run(
        MainCommandEntered(f"/errand {c.id} --k=1 --to={a.id},{b.id},{d.id} corre"),
        answer_running(main, "llegué"),  # two run, the third waits for a slot
        Tick(),
        settle=True,
    )
    first, second = (main.sessions.load(job.session_id) for job in main.held[:2])
    [third] = [s for s in (a, b, d) if s.id not in (first.id, second.id)]
    text = wake_text(main, c)
    assert "1 of 3 answered" in text
    assert f"- Session {second.id} answered:\nllegué" in text
    assert f"- Session {first.id}: cancelled, its quorum was met" in text
    assert f"- Session {third.id}: cancelled, its quorum was met" in text
    [dropped] = logged(main, third, "prompts_dropped")
    assert "has what it waited for" in str(dropped["reason"])
    assert cancelled_for(main, first) != []


def test_a_suspended_turn_its_errand_no_longer_waits_for_forgets_the_falls_of_its_step(
    main: MainHarness,
):
    # Its Turn left the queue: the worker's next Turn gets its two tries (ADR-0023, #190).
    main.server = server(slots=2)
    c, a, b = (main.create_session() for _ in range(3))
    fell = LlmUnavailable(reason="Connection error.", reached=True)
    main.run(
        MainCommandEntered(f"/errand {c.id} --k=1 --to={a.id},{b.id} corre"),
        TurnEnded(a.id, error=fell),  # suspended until its Server is back
        answers(b, "llegué"),  # the quorum is met: a's Turn is no longer waited for
        Tick(),
        MainCommandEntered(f"/send {a.id} otra"),
        server_ready(main.server),
        TurnEnded(a.id, error=fell),  # its first fall: suspended, not failed
        settle=True,
    )
    [dropped] = logged(main, a, "prompts_dropped")
    assert "has what it waited for" in str(dropped["reason"])
    events = [entry.event for entry in main.session_log(a)]
    assert events.count("turn_suspended") == 2 and "turn_failed" not in events


def test_a_worker_that_fails_is_tried_again_without_waking_its_session(main: MainHarness):
    c, a = main.create_session(), main.create_session()
    woke: list[int] = []
    main.run(
        MainCommandEntered(f"/errand {c.id} --retries=1 --to={a.id} piensa"),
        TurnEnded(a.id, error=TurnCrashed(reason="boom")),
        Tick(),
        lambda: woke.append(len(woken(main, c))),
        answers(a, "pensado"),
        Tick(),
        settle=True,
    )
    assert woke == [0], "the failure woke no one"
    assert tasks(main, a) == ["piensa", "piensa"]
    assert "answered, after 2 tries:\npensado" in wake_text(main, c)


def test_once_its_retries_are_used_a_failure_is_its_result(main: MainHarness):
    c, a = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/errand {c.id} --retries=0 --to={a.id} piensa"),
        TurnEnded(a.id, error=TurnCrashed(reason="boom")),
        Tick(),
        settle=True,
    )
    assert f"- Session {a.id} did not answer: its Turn failed" in wake_text(main, c)


def test_an_errand_that_would_wait_for_its_session_or_an_ancestor_is_refused(main: MainHarness):
    c, a, b = (main.create_session() for _ in range(3))
    chat = main.run(
        MainCommandEntered(f"/errand {c.id} --to={a.id} reparte"),
        MainCommandEntered(f"/errand {a.id} --to={b.id} sigue", origin=from_session(a)),
        # b waits for nothing, but it works for a, which works for c: c is its ancestor.
        MainCommandEntered(f"/errand {b.id} --to={c.id} vuelve", origin=from_session(b)),
        MainCommandEntered(f"/errand {c.id} --to={c.id} a mí", origin=from_session(c)),
        settle=True,
    )
    refused = [line for line in chat if "errand_cycle" in line]
    assert len(refused) == 2, chat
    assert tasks(main, c) == []


def test_the_deadline_does_not_run_while_the_workers_server_is_not_ready(main: MainHarness):
    c, a = main.create_session(), main.create_session()
    woke: list[int] = []
    main.run(
        MainCommandEntered(f"/errand {c.id} --deadline=10 --to={a.id} tarda"),
        later(main, 6),
        Tick(),
        server_down(),
        later(main, 30),
        Tick(),  # down: none of these 30 s count
        server_ready(main.server),
        later(main, 1),
        Tick(),
        lambda: woke.append(len(woken(main, c))),  # 7 of its 10 s went by
        later(main, 4),
        Tick(),
        settle=True,
    )
    assert woke == [0]
    assert f"- Session {a.id}: no answer within its deadline" in wake_text(main, c)


def test_a_batch_hands_its_tasks_to_free_workers_and_wakes_once(main: MainHarness):
    c, a, b = (main.create_session() for _ in range(3))
    counts: list[int] = []
    main.run(
        MainCommandEntered(f'/batch {c.id} --to={a.id},{b.id} ["t1", "t2", "t3"]'),
        Tick(),  # one slot: b has no room while a runs
        lambda: counts.append(len(tasks(main, b))),
        answer_running(main, "r1"),
        Tick(),
        answer_running(main, "r2"),
        Tick(),
        answer_running(main, "r3"),
        Tick(),
        settle=True,
    )
    assert counts == [0]
    assert len(tasks(main, a)) + len(tasks(main, b)) == 3
    text = wake_text(main, c)
    assert "3 of 3 answered" in text
    assert "answered, on the Server (9931) · m.gguf:\nr2" in text


def test_free_workers_of_a_batch_take_its_tasks_at_once(main: MainHarness):
    main.server = server(slots=2)
    c, a, b = (main.create_session() for _ in range(3))
    main.run(
        MainCommandEntered(f'/batch {c.id} --to={a.id},{b.id} ["t1", "t2", "t3"]'),
        answers(b, "r2"),
        Tick(),
        settle=True,
    )
    assert tasks(main, a) == ["t1"]
    assert tasks(main, b) == ["t2", "t3"]
    assert woken(main, c) == []


def test_a_rewind_of_its_session_cancels_the_errand_and_what_its_workers_do_for_it(
    main: MainHarness,
):
    c, a = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/send {c.id} reparte"),
        MainCommandEntered(f"/errand {c.id} --to={a.id} trabaja", origin=from_session(c)),
        answers(c, "repartido"),  # its answer: the worker's still queued behind it
        MainCommandEntered(f"/rewind {c.id} 1"),
        Tick(),
        settle=True,
    )
    [cancelled] = logged(main, c, "subscription_cancelled")
    assert cancelled["reason"] == "rewound"
    [why] = cancelled_for(main, a)
    assert why.startswith("its errand e") and why.endswith(" was cancelled: rewound")
    assert woken(main, c) == []


def test_unsubscribe_cancels_an_errand_in_cascade(main: MainHarness):
    c, a, b = (main.create_session() for _ in range(3))

    def unsubscribe() -> None:
        [opened] = logged(main, c, "subscribed")
        command = f"/unsubscribe {c.id} {opened['subscription']}"
        inbox(main).put(MainCommandEntered(command))

    main.run(
        MainCommandEntered(f"/errand {c.id} --to={a.id} reparte"),
        # a, working for c, hands its part out to b in its Turn.
        MainCommandEntered(f"/errand {a.id} --to={b.id} parte", origin=from_session(a)),
        answers(a, "repartí"),  # held back: its Turn left an Encargo open
        unsubscribe,
        Tick(),
        settle=True,
    )
    # a's own Encargo went with it, and b's Turn for it.
    [why] = cancelled_for(main, b)
    assert why.count("was cancelled") == 2 and why.endswith("unsubscribed"), why
    assert woken(main, c) == []


def test_a_workers_answer_waits_for_the_errand_its_turn_opened(main: MainHarness):
    c, a, b = (main.create_session() for _ in range(3))
    main.run(
        MainCommandEntered(f"/errand {c.id} --to={a.id} reparte"),
        MainCommandEntered(f"/errand {a.id} --to={b.id} parte", origin=from_session(a)),
        answers(a, "repartí"),
        Tick(),
        answers(b, "hecho"),
        Tick(),  # a wakes with b's result
        answers(a, "todo hecho"),
        Tick(),
        settle=True,
    )
    text = wake_text(main, c)
    assert f"- Session {a.id} answered:\ntodo hecho" in text
    assert "repartí" not in text


def test_after_main_dies_the_recovery_report_shows_the_errand_and_it_goes_on(
    main: MainHarness,
):
    c, a, b = (main.create_session() for _ in range(3))
    main.run(MainCommandEntered(f"/errand {c.id} --to={a.id},{b.id} trabaja"), settle=True)
    # Main is killed with a's Turn running and b's Prompt queued.
    main.held.clear()
    main.run(Tick(), answers(b, "hecho"), Tick(), settle=True)
    report = [e.fields for e in main.main_log() if e.event == "recovery_report"]
    assert len(report) == 1
    assert "goes on" in str(report[0]["errands"])
    assert any("goes on" in t for t in told(main, c))
    text = wake_text(main, c)
    assert f"- Session {a.id} did not answer: Main stopped during its Turn" in text
    assert f"- Session {b.id} answered:\nhecho" in text


def test_a_result_for_a_cancelled_errand_stays_in_the_workers_log(main: MainHarness):
    main.server = server(slots=2)
    c, a, b = (main.create_session() for _ in range(3))
    main.run(
        MainCommandEntered(f"/errand {c.id} --k=1 --to={a.id},{b.id} corre"),
        answers(a, "primero"),
        answers(b, "segundo"),  # cancelled, but its Turn may still end with an answer
        Tick(),
        settle=True,
    )
    assert "1 of 2 answered" in wake_text(main, c)
    [dropped] = logged(main, b, "errand_result_dropped")
    assert dropped["text"] == "segundo"


@pytest.mark.parametrize(
    "written",
    [
        "--to= hola",
        "--k=3 --to=aaaa,bbbb hola",
        "--to=aaaa,aaaa hola",
        "--deadline=-1 --to=aaaa hola",
        "--retries=11 --to=aaaa hola",
        "--when=now --to=aaaa hola",
        '{"aaaa": "x"',
    ],
)
def test_what_is_not_an_errand_says_how_one_is_written(main: MainHarness, written: str):
    c = main.create_session()
    chat = main.run(MainCommandEntered(f"/errand {c.id} {written}"), settle=True)
    assert any("errand_invalid" in line and "/errand <sid>" in line for line in chat), chat


def test_a_session_opens_one_with_at_errand_for_itself_only(main: MainHarness):
    c, a = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/errand {a.id} --to={c.id} hola", origin=from_session(c)),
        settle=True,
    )
    fields: list[dict[str, JsonValue]] = logged(main, c, "subscribed")
    assert fields == [], "a Session only opens its own"


def test_an_answer_that_comes_with_its_profile_inactive_counts_on_return(main: MainHarness):
    c, b = main.create_session(), main.create_session()
    other = main.profiles.create("tesis", main.profile)
    main.profiles.add_session(other, b.id)  # both Profiles share B
    woke: list[int] = []
    main.run(
        MainCommandEntered(f"/send {b.id} uno"),
        MainCommandEntered(f"/errand {c.id} --to={b.id} hazlo"),  # it waits behind uno
        MainCommandEntered("/profile switch tesis"),  # once uno's Turn ended
        answers(b, "1"),
        answers(b, "hecho"),  # B answers in tesis
        Tick(),
        lambda: woke.append(len(woken(main, c))),
        MainCommandEntered("/profile switch default"),
        Tick(),
        settle=True,
    )
    assert woke == [0], "frozen with its Profile"
    assert f"- Session {b.id} answered:\nhecho" in wake_text(main, c)


def test_closing_its_sessions_window_cancels_its_turn_but_not_the_errand(main: MainHarness):
    c, a = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/open {c.id}"),
        MainCommandEntered(f"/send {c.id} reparte"),
        MainCommandEntered(f"/errand {c.id} --to={a.id} trabaja", origin=from_session(c)),
        MainCommandEntered(f"/close {c.id}"),  # its Turn is cancelled, in no Conversation
        TurnEnded(c.id, error=TurnCancelled(reason="window closed")),
        answers(a, "hecho"),
        Tick(),
        settle=True,
    )
    assert logged(main, c, "subscription_cancelled") == []
    assert f"- Session {a.id} answered:\nhecho" in wake_text(main, c)


def test_a_session_of_a_batchs_list_cannot_wait_for_it_before_it_got_a_task(
    main: MainHarness,
):
    c, a, b = (main.create_session() for _ in range(3))
    chat = main.run(
        MainCommandEntered(f'/batch {c.id} --to={a.id},{b.id} ["t1", "t2"]'),  # one slot: a
        MainCommandEntered(f"/errand {b.id} --to={c.id} vuelve", origin=from_session(b)),
        settle=True,
    )
    assert any("errand_cycle" in line for line in chat), chat


def test_a_batch_task_waiting_for_a_free_worker_spends_its_deadline(main: MainHarness):
    c, a = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/send {a.id} ocupada"),  # a is busy, and stays so
        MainCommandEntered(f'/batch {c.id} --deadline=5 --to={a.id} ["t1"]'),
        later(main, 6),
        Tick(),
        settle=True,
    )
    assert "- task 1 (no worker): no answer within its deadline" in wake_text(main, c)


def test_after_main_dies_the_report_shows_an_errand_its_lost_turn_settled(main: MainHarness):
    c, a = main.create_session(), main.create_session()
    main.run(MainCommandEntered(f"/errand {c.id} --to={a.id} trabaja"), settle=True)
    main.held.clear()
    main.run(Tick(), settle=True)
    [report] = [e.fields for e in main.main_log() if e.event == "recovery_report"]
    assert f"settled; no answer from {a.id}" in str(report["errands"])
    assert f"- Session {a.id} did not answer: Main stopped during its Turn" in wake_text(main, c)


def test_cancelling_an_errand_cancels_the_turn_its_worker_was_woken_for(main: MainHarness):
    c, a, b = (main.create_session() for _ in range(3))

    def unsubscribe() -> None:
        [opened] = logged(main, c, "subscribed")
        inbox(main).put(MainCommandEntered(f"/unsubscribe {c.id} {opened['subscription']}"))

    main.run(
        MainCommandEntered(f"/errand {c.id} --to={a.id} reparte"),
        MainCommandEntered(f"/errand {a.id} --to={b.id} parte", origin=from_session(a)),
        answers(a, "repartí"),
        answers(b, "hecho"),
        Tick(),  # a wakes with b's result, to answer for c
        unsubscribe,
        settle=True,
    )
    assert any("was cancelled: unsubscribed" in why for why in cancelled_for(main, a))
