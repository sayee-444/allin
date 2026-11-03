"""Once a Compactación is saved, the Window's border and the Organizer's row show the Context
of the compacted Conversation, measured on the Server as the Admission measures, never the one
before (#157, #169, ADR-0017). The fake Server counts one token per word."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from allin.core.ids import PieceId, ServerKey, SessionId
from allin.ipc.messages import ContextUsage, OrganizerRows
from allin.main.conversation import Item, summary_item, turn_items
from allin.main.events import (
    InputMeasured,
    InputReceived,
    MainCommandEntered,
    MainEvent,
    OrganizerConnected,
    ServerChanged,
    ServerReady,
    Shutdown,
    WindowClosed,
)
from allin.main.sessions import Session
from allin.ui.screen import ShowBox
from tests.llm_fakes import FakeLlm, HangingStream, Script, Unreachable, answer, tool_call
from tests.test_compaction_f2 import LONG, seed, talked, tooled, with_voice
from tests.test_compaction_turns import (
    answers,
    compact,
    conversation,
    events,
    reply,
    talk,
    words,
)
from tests.test_dispatcher import Between, MainHarness, Until, server, server_ready
from tests.test_rewind import rewind_cmd

SUMMARY = summary_item("We chose mango.", 4)
# Its Sysprompt (6 words), the summary with its header (5 + 3) and the two Turns kept
# literal (100 each), as the LLM reads them now.
COMPACTED = 6 + 8 + 200
BEFORE = 199  # what the last Turn's Step said: 100 in, 99 out


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.servers.tokens_of = words
    harness.server = server(context=1_000)  # an Output cap of 250
    return harness


@pytest.fixture
def tooled_main(main: MainHarness, tmp_path: Path) -> Iterator[MainHarness]:
    with with_voice(main, tmp_path) as tooled:
        yield tooled


def borders(main: MainHarness, session: Session, window: int = -1) -> list[int | None]:
    """Each Context a Window of the Session was shown, in order: its last one's by default."""
    sent = main.windows(session.id)[window].sent
    return [message.context for message in sent if isinstance(message, ContextUsage)]


def compacted_by_hand(main: MainHarness, session: Session, *after: MainEvent | Between) -> None:
    """Six Turns, then a Compactación that keeps the last two literal, then ``after``, all
    in one run of Main."""
    main.llm = FakeLlm(*answers(6), reply("We chose mango."), reply("w"))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        *talk(session, 6),
        compact(session),
        *after,
        settle=True,
    )


def reopened(session: Session) -> tuple[MainEvent, MainEvent]:
    return WindowClosed(session.id, 0), MainCommandEntered(f"/open {session.id}")


def test_once_a_compaction_is_saved_the_border_shows_the_compacted_context(main: MainHarness):
    session = main.create_session()
    compacted_by_hand(main, session)
    after = conversation(main, session)
    read = [{"role": "developer", "content": after.sysprompt}, SUMMARY]
    read += [item for turn in after.turns for item in turn_items(turn)]
    assert words(read) == COMPACTED
    # Unknown the moment it is saved, then what the Server measured: never the one before.
    assert borders(main, session)[-3:] == [BEFORE, None, COMPACTED]
    [usage] = [m for m in main.windows(session.id)[0].sent if isinstance(m, ContextUsage)][-1:]
    assert usage.limit == 1_000


def test_a_server_that_cannot_measure_leaves_the_border_unknown_not_as_before(
    main: MainHarness,
):
    def unmeasurable_once_compacted(items: list[Item]) -> int | None:
        return None if len(items) > 1 and items[1] == SUMMARY else words(items)

    main.servers.tokens_of = unmeasurable_once_compacted
    session = main.create_session()
    compacted_by_hand(main, session, *reopened(session))
    assert conversation(main, session).summary == "We chose mango."
    assert borders(main, session, window=0)[-3:] == [BEFORE, None, None]
    assert borders(main, session)[-1] is None  # opened again: still unknown
    assert "context_unmeasured" in [entry.event for entry in main.session_log(session)]


def test_a_window_opened_again_after_compacting_shows_the_compacted_context(
    main: MainHarness,
):
    session = main.create_session()
    compacted_by_hand(main, session, *reopened(session))
    assert borders(main, session)[-1] == COMPACTED


def test_after_a_restart_the_border_of_a_compacted_conversation_is_unknown_not_as_before(
    main: MainHarness,
):
    session = main.create_session()
    compacted_by_hand(main, session)
    main.run(settle=True)  # Main again: it opens the Window it left open
    assert borders(main, session)[-1] is None


def test_the_next_turn_shows_its_own_context_again(main: MainHarness):
    session = main.create_session()
    compacted_by_hand(main, session, *talk(session, 1, first=6), *reopened(session))
    assert borders(main, session, window=0)[-1] == 101  # its Step: 100 in, 1 out
    assert borders(main, session)[-1] == 101
    main.run(settle=True)  # and after a restart too
    assert borders(main, session)[-1] == 101


def test_a_rewind_after_compacting_does_not_show_what_the_compaction_measured(
    main: MainHarness,
):
    session = main.create_session()
    compacted_by_hand(main, session, rewind_cmd(session))  # p5 undone; p4 and the summary stay
    # p4's own Step ran on the Conversation before compacting: its Context is unknown.
    assert borders(main, session)[-1] is None


def test_moving_a_compacted_session_to_a_smaller_slot_weighs_what_was_measured(
    main: MainHarness,
):
    supervisors = main.slot_locks_dir
    supervisors.mkdir(parents=True, exist_ok=True)
    (supervisors / "cpu.toml").write_text('model = "m.gguf"\ncontext = 205\nport = 9102\n')
    session = main.create_session()
    compacted_by_hand(
        main,
        session,
        MainCommandEntered(f"/session set {session.id} server=cpu"),
        ServerChanged(ServerKey("cpu"), ServerReady(server(context=205))),
    )
    assert main.sessions.load(session.id).settings.server == "cpu"
    assert borders(main, session)[-1] == COMPACTED
    # Over the new limit as measured, though the Turn before compacting was not.
    [box] = [update.box for update in main.main_screen if isinstance(update, ShowBox)]
    assert f"already holds {COMPACTED}" in box.text


def test_pruning_alone_shows_the_context_its_llm_reads_pruned(main: MainHarness):
    session = main.create_session()
    seed(main, session, [tooled(n) for n in range(5)])
    main.llm = FakeLlm(reply("ok"))
    main.run(
        MainCommandEntered(f"/open {session.id}"), InputReceived(session.id, "next"), settle=True
    )
    after = conversation(main, session)
    assert after.pruned == 4 and not after.compacted_since_turn
    read: list[Item] = [{"role": "developer", "content": after.sysprompt}]
    for index, turn in enumerate(after.turns[:5]):
        read += turn_items(turn, pruned=index < 4)
    # Saved pruned, then its Prompt's Turn ran and showed its own Step.
    assert borders(main, session)[-3:] == [None, words(read), 101]


# The Organizer's row shows the same Context as the border (#169).


def organizer_contexts(main: MainHarness, session: Session) -> list[int | None]:
    """Each Context the last Organizer's row of the Session showed, without repeats."""
    shown: list[int | None] = []
    for message in main.organizers[-1].sent:
        if isinstance(message, OrganizerRows):
            [context] = [r.context for r in message.rows if r.session_id == session.id]
            if not shown or shown[-1] != context:
                shown.append(context)
    return shown


def with_organizer() -> tuple[MainEvent, MainEvent]:
    return MainCommandEntered("/windows"), OrganizerConnected()


def compacted_in_organizer(main: MainHarness, session: Session, *after: MainEvent) -> None:
    """Six Turns with the Organizer open, then a Compactación that keeps the last two, then
    ``after``, all in one run of Main."""
    main.llm = FakeLlm(*answers(6), reply("We chose mango."))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        *with_organizer(),
        *talk(session, 6),
        compact(session),
        *after,
        settle=True,
    )


def test_once_a_compaction_is_saved_the_organizer_shows_the_compacted_context(
    main: MainHarness,
):
    session = main.create_session()
    compacted_in_organizer(main, session)
    # Unknown once it is saved, then what was measured, with no other change in the Session.
    assert organizer_contexts(main, session)[-3:] == [BEFORE, None, COMPACTED]


def test_a_server_that_cannot_measure_leaves_the_organizer_unknown_not_as_before(
    main: MainHarness,
):
    def unmeasurable_once_compacted(items: list[Item]) -> int | None:
        return None if len(items) > 1 and items[1] == SUMMARY else words(items)

    main.servers.tokens_of = unmeasurable_once_compacted
    session = main.create_session()
    compacted_in_organizer(main, session)
    assert organizer_contexts(main, session)[-2:] == [BEFORE, None]


def test_after_a_restart_the_organizer_shows_a_compacted_context_unknown(main: MainHarness):
    session = main.create_session()
    compacted_in_organizer(main, session)
    main.run(*with_organizer(), settle=True)
    assert organizer_contexts(main, session) == [None]


def test_after_compacting_the_next_turn_shows_its_own_context_in_the_organizer(
    main: MainHarness,
):
    session = main.create_session()
    compacted_in_organizer(main, session)
    main.llm = FakeLlm(reply("w"))
    main.run(*with_organizer(), *talk(session, 1, first=6), settle=True)
    assert organizer_contexts(main, session)[-1] == 101


def test_a_rewind_after_compacting_leaves_the_organizer_unknown_as_the_border(
    main: MainHarness,
):
    session = main.create_session()
    compacted_in_organizer(main, session, rewind_cmd(session))
    assert organizer_contexts(main, session)[-1] is None
    assert borders(main, session)[-1] is None


def test_a_compacted_session_closed_keeps_its_measured_context_in_the_organizer(
    main: MainHarness,
):
    session = main.create_session()
    compacted_in_organizer(main, session, WindowClosed(session.id, 0))
    rows = [m.rows for m in main.organizers[-1].sent if isinstance(m, OrganizerRows)][-1]
    [row] = [r for r in rows if r.session_id == session.id]
    assert (row.place, row.context) == ("closed", COMPACTED)


# What Main does to a compacted Session afterwards never brings back the Context of before
# (#177).


def test_rewinding_a_turn_after_compacting_leaves_the_border_unknown_not_as_before(
    main: MainHarness,
):
    session = main.create_session()
    # p6 undone: the summary, p4 and p5 stay, and p5's Step ran before compacting.
    compacted_by_hand(main, session, *talk(session, 1, first=6), rewind_cmd(session))
    assert [turn.prompt for turn in conversation(main, session).turns] == ["p4", "p5"]
    assert borders(main, session)[-1] is None
    main.run(settle=True)  # and after a restart too
    assert borders(main, session)[-1] is None


def test_rewinding_a_turn_after_compacting_leaves_the_organizer_unknown_as_the_border(
    main: MainHarness,
):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), reply("We chose mango."), reply("w"))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        *with_organizer(),
        *talk(session, 6),
        compact(session),
        *talk(session, 1, first=6),
        rewind_cmd(session),
        settle=True,
    )
    assert organizer_contexts(main, session)[-1] is None
    assert borders(main, session)[-1] is None


def renamed_borders(main: MainHarness) -> list[int | None]:
    [window] = main.windows("nuevo")
    return [message.context for message in window.sent if isinstance(message, ContextUsage)]


def test_a_renamed_session_keeps_the_context_measured_after_compacting(main: MainHarness):
    session = main.create_session()
    compacted_in_organizer(main, session, MainCommandEntered(f"/session id {session.id} nuevo"))
    assert borders(main, session, window=0)[-1] == COMPACTED
    assert renamed_borders(main)[-1] == COMPACTED
    rows = [m.rows for m in main.organizers[-1].sent if isinstance(m, OrganizerRows)][-1]
    assert [row.context for row in rows if row.session_id == "nuevo"] == [COMPACTED]


def test_a_measure_that_comes_after_renaming_shows_under_the_new_id(
    main: MainHarness, monkeypatch: pytest.MonkeyPatch
):
    held: list[MainEvent] = []
    measure = main.servers.measure

    def hold_the_compacted(
        server: ServerKey, session_id: SessionId, ticket: int, items: list[Item]
    ) -> None:
        if SUMMARY in items[1:]:  # what it saved, not its summary checked: once renamed
            held.append(InputMeasured(server, session_id, ticket, words(items)))
        else:
            measure(server, session_id, ticket, items)

    def release() -> None:
        assert main.servers.inbox is not None
        for event in held:
            main.servers.inbox.put(event)

    monkeypatch.setattr(main.servers, "measure", hold_the_compacted)
    session = main.create_session()
    compacted_by_hand(main, session, MainCommandEntered(f"/session id {session.id} nuevo"), release)
    assert held, "it was measured after the rename"
    assert renamed_borders(main)[-2:] == [None, COMPACTED]


# A Turn that yielded to compact goes on after its Steps of before, which read the Conversation
# of before: their Context no longer holds (#177).

YIELDED = 6 + 7 + 200  # its Sysprompt, the summary of p0 (5 + 2), and p1 and p2 literal


def yielding(main: MainHarness, *after: Script) -> Session:
    """A Session with three Turns whose next one yields to compact after its tools; once it
    compacted, its LLM answers ``after``."""
    session = main.sessions.save_functions(main.create_session(), (PieceId("function:voice"),))
    seed(main, session, [talked(n) for n in range(3)])
    shout = tool_call("shout", json.dumps({"text": LONG}))
    main.llm = FakeLlm(shout, reply("We talked."), *after)
    return session


def test_a_turn_that_yielded_to_compact_shows_the_organizer_what_the_border_shows(
    tooled_main: MainHarness,
):
    main = tooled_main
    session = yielding(main, reply("done"))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        *with_organizer(),
        InputReceived(session.id, "lee"),
        settle=True,
    )
    assert "turn_yielded" in events(main, session)
    # Its first Step's, unknown once compacted, what was measured, then its second Step's:
    # never its first Step's again, nor its summary Step's.
    shown = [None, 10, None, YIELDED, 101]
    assert borders(main, session) == shown
    assert organizer_contexts(main, session) == shown


def test_a_window_reopened_while_a_yielded_turn_goes_on_shows_the_compacted_context(
    tooled_main: MainHarness,
):
    main = tooled_main
    hanging = HangingStream([answer("half")])
    session = yielding(main, hanging)
    seen: list[int | None] = []
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        *with_organizer(),
        InputReceived(session.id, "lee"),
        Until(hanging.waiting.is_set),  # its second Step runs
        MainCommandEntered(f"/bg {session.id}"),
        MainCommandEntered(f"/open {session.id}"),
        lambda: seen.extend([borders(main, session)[-1], organizer_contexts(main, session)[-1]]),
        server_ready(server(context=2_000)),  # its Server's limit changes meanwhile
        lambda: seen.append(borders(main, session)[-1]),
        InputReceived(session.id, "", interrupt=True),
        settle=True,
    )
    assert len(main.windows(session.id)) == 2
    assert seen == [YIELDED, YIELDED, YIELDED]
    [usage] = [m for m in main.windows(session.id)[-1].sent if isinstance(m, ContextUsage)][-1:]
    assert usage.limit == 2_000


def test_a_yielded_turn_that_a_fall_suspends_still_leaves_its_steps_of_before_out(
    tooled_main: MainHarness,
):
    main = tooled_main
    hanging = HangingStream([answer("half")])
    session = yielding(main, Unreachable(), hanging)
    seen: list[int | None] = []
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        *with_organizer(),
        InputReceived(session.id, "lee"),
        Until(lambda: "turn_suspended" in events(main, session)),  # its second Step fell
        server_ready(main.server),  # back: it runs again
        Until(hanging.waiting.is_set),
        MainCommandEntered(f"/bg {session.id}"),
        MainCommandEntered(f"/open {session.id}"),
        lambda: seen.extend([borders(main, session)[-1], organizer_contexts(main, session)[-1]]),
        InputReceived(session.id, "", interrupt=True),
        settle=True,
    )
    assert events(main, session).count("turn_started") == 3
    assert seen == [YIELDED, YIELDED]


def test_a_yielded_turn_whose_compaction_a_restart_repeats_leaves_its_steps_of_before_out(
    tooled_main: MainHarness,
):
    main = tooled_main
    summarizing, hanging = HangingStream([]), HangingStream([answer("half")])
    session = yielding(main)
    shout = tool_call("shout", json.dumps({"text": LONG}))
    main.llm = FakeLlm(shout, summarizing, reply("We talked."), hanging)
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "lee"),
        Until(summarizing.waiting.is_set),  # Main stops while it compacts
        Shutdown(),
        settle=True,
    )
    seen: list[int | None] = []
    main.run(  # Main again: it compacts, then its Turn goes on
        *with_organizer(),
        Until(hanging.waiting.is_set),
        lambda: seen.extend([borders(main, session)[-1], organizer_contexts(main, session)[-1]]),
        InputReceived(session.id, "", interrupt=True),
        settle=True,
    )
    assert conversation(main, session).summary == "We talked."
    assert seen == [YIELDED, YIELDED]
