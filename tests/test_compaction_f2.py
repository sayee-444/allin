"""The second part of the Compactación (ADR-0017, #119): pruning old tool outputs, compacting
between the Steps of a Turn, ``@compact``, its event and the Notificación of live state."""

import json
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.errors import TurnCancelled, TurnYielded
from allin.core.ids import CommandName, PieceId
from allin.ipc.messages import ContextUsage
from allin.main.compaction import CLOSING
from allin.main.conversation import Conversation, Step, Turn, summary_item
from allin.main.descriptions import Descriptions
from allin.main.events import (
    InputReceived,
    MainCommandEntered,
    MainEvent,
    StepEnded,
    Tick,
    ToolsEnded,
    ToolsStarted,
    TurnEnded,
    WindowClosed,
)
from allin.main.quarantine import PieceRef, Quarantine
from allin.main.sessions import Session
from tests.llm_fakes import FakeLlm, tool_call
from tests.test_compaction_turns import conversation, events, reply, words
from tests.test_dispatcher import MainHarness, server
from tests.test_subscriptions import from_session

LONG = " ".join(["palabra"] * 150)  # a tool output of about 150 tokens, long enough to prune


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.servers.tokens_of = words
    harness.server = server(context=1_000)  # an Output cap of 250
    return harness


def tooled(n: int, output: str = LONG) -> Turn:
    call = {"type": "function_call", "call_id": f"c{n}", "name": "read", "arguments": "{}"}
    result = {"type": "function_call_output", "call_id": f"c{n}", "output": output}
    answer = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "done"}],
    }
    return Turn(
        prompt=f"p{n}", steps=[Step(output=[call], results=[result]), Step(output=[answer])]
    )


def seed(main: MainHarness, session: Session, turns: list[Turn]) -> None:
    sysprompt = "I am AllIn; you are ana."
    main.sessions.save_conversation(session, Conversation(sysprompt=sysprompt, turns=turns))


def tool_outputs(items: list[dict[str, object]]) -> list[object]:
    return [item["output"] for item in items if item.get("type") == "function_call_output"]


def test_pruning_old_tool_outputs_that_makes_room_summarizes_nothing(main: MainHarness):
    session = main.create_session()
    seed(main, session, [tooled(n) for n in range(5)])
    main.llm = FakeLlm(reply("ok"))  # no summary Step: only the Turn
    main.run(InputReceived(session.id, "next"), settle=True)
    after = conversation(main, session)
    # compact.keep, 250 of 1 000 tokens, keeps the last Turn: the four before it are pruned.
    assert after.summary is None and after.pruned == 4
    [request] = main.llm.requests
    pruned = "[tool output omitted: ~299 tokens; archived]"
    assert tool_outputs(request["input"]) == [pruned] * 4 + [LONG]
    assert tool_outputs(after.turns[0].steps[0].results) == [LONG], "kept whole on disk"
    assert "pruned" in events(main, session)


def test_when_pruning_is_not_enough_the_summary_reads_the_old_turns_pruned(main: MainHarness):
    session = main.create_session()
    seed(main, session, [tooled(n, LONG) for n in range(5)])
    main.llm = FakeLlm(reply("We read five files."), reply("ok"))
    main.run(MainCommandEntered(f"/session compact {session.id}"), settle=True)
    summary_step = main.llm.requests[0]["input"]
    assert tool_outputs(summary_step) == ["[tool output omitted: ~299 tokens; archived]"] * 4
    after = conversation(main, session)
    assert after.summary == "We read five files." and after.summarized == 4
    assert after.pruned == 0


def test_compact_typed_in_its_window_compacts_its_own_conversation(main: MainHarness):
    session = main.create_session()
    seed(main, session, [tooled(n) for n in range(5)])
    main.llm = FakeLlm(reply("We read five files."))
    main.run(InputReceived(session.id, "@compact keep the file names"), settle=True)
    closing = main.llm.requests[0]["input"][-1]["content"]
    assert closing.endswith("What the human asks of this summary: keep the file names")
    assert conversation(main, session).summarized == 4


def test_a_session_compacts_only_its_own_conversation(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    for session in (a, b):
        seed(main, session, [tooled(n) for n in range(5)])
    main.llm = FakeLlm(reply("We read five files."))
    chat = main.run(
        MainCommandEntered(f"/session compact {b.id}", origin=from_session(a)),
        MainCommandEntered(f"/session compact {a.id}", origin=from_session(a)),
        settle=True,
    )
    assert any("error compaction_not_own" in line for line in chat)
    assert conversation(main, b).summarized == 0
    assert conversation(main, a).summarized == 4


def test_after_compacting_its_session_is_told_what_it_still_has_open(main: MainHarness):
    session = main.create_session()
    seed(main, session, [tooled(n) for n in range(5)])
    main.llm = FakeLlm(reply("We read five files."), reply("ok"))
    main.run(
        MainCommandEntered(f"/subscribe {session.id} timer:600"),
        MainCommandEntered(f"/session compact {session.id}"),
        InputReceived(session.id, "next"),
        settle=True,
    )
    prompt = main.llm.requests[1]["input"][-1]["content"]
    assert "after compacting, still open: no Encargos, Suscripciones: s" in prompt
    assert "(timer:" in prompt and prompt.endswith("next")


def test_pruning_wakes_who_subscribed_with_the_prompts_it_pruned(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    seed(main, a, [tooled(n) for n in range(5)])
    main.llm = FakeLlm(reply("ok"), reply("visto"))
    main.run(
        MainCommandEntered(f"/subscribe {b.id} compact:{a.id}"),
        InputReceived(a.id, "next"),
        Tick(),
        settle=True,
    )
    [woke] = [e.fields["text"] for e in main.session_log(b) if e.event == "prompt"]
    assert f"Session {a.id} compacted: the tool outputs of Prompts 1\N{EN DASH}4 pruned" in woke


SHOUT = '''
def shout(text: str) -> str:
    """Say it louder."""
    return text.upper()
'''


@contextmanager
def with_voice(main: MainHarness, tmp_path: Path) -> Generator[MainHarness]:
    """The same Main with the real confined ``voice`` package, approved."""
    if abi_version() < 6:
        pytest.skip("needs Landlock ABI 6 (kernel 6.12)")
    main.describer = ToolLauncher(tmp_path)
    main.describer.start()
    main.threaded_turns = True
    folder = main.profile.folder / "functions" / "voice"
    folder.mkdir(parents=True)
    (folder / "shout.py").write_text(SHOUT)
    approve_voice(main)
    yield main
    main.describer.stop()


@pytest.fixture
def tooled_main(main: MainHarness, tmp_path: Path) -> Iterator[MainHarness]:
    with with_voice(main, tmp_path) as tooled:
        yield tooled


def approve_voice(main: MainHarness) -> None:
    """Approve the ``voice`` package as its files are now."""
    assert main.describer is not None
    descriptions = Descriptions(main.profile.folder.parents[1], main.describer, main.confinement)
    pieces = Quarantine(main.profile.folder, descriptions)
    ref = PieceRef("function", "voice")
    pieces.approve(ref, pieces.piece(ref).hash)


def talked(n: int) -> Turn:
    answer = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": " ".join(["w"] * 99)}],
    }
    return Turn(prompt=f"p{n}", steps=[Step(output=[answer])])


def test_a_turn_whose_tools_fill_its_slot_compacts_between_its_steps_and_goes_on(
    tooled_main: MainHarness,
):
    main = tooled_main
    session = main.sessions.save_functions(main.create_session(), (PieceId("function:voice"),))
    seed(main, session, [talked(n) for n in range(3)])  # 300 tokens: its first Step fits
    main.llm = FakeLlm(
        tool_call("shout", json.dumps({"text": LONG})),  # gives back 150 tokens more
        reply("We talked."),  # the summary, between its two Steps
        reply("done"),
    )
    main.run(
        MainCommandEntered(f"/subscribe {session.id} timer:600"),
        InputReceived(session.id, "lee"),
        settle=True,
    )
    first, summary_step, second = (r["input"] for r in main.llm.requests)
    # It goes on reading what it still has open, which the summary may have lost.
    [prompt] = [i["content"] for i in second if str(i.get("content", "")).endswith("lee")]
    assert "after compacting, still open: no Encargos, Suscripciones: s" in prompt
    assert summary_step[0]["role"] == "developer" and "You summarize" in summary_step[0]["content"]
    assert summary_step[-1]["content"].startswith(CLOSING)
    after = conversation(main, session)
    # compact.keep, 250 of 1 000 tokens, keeps the last two Turns literal.
    assert after.summary == "We talked." and after.summarized == 1
    assert [turn.prompt for turn in after.turns] == ["p1", "p2", "lee"]
    assert len(after.turns[-1].steps) == 2
    assert second[1] == summary_item("We talked.", 1)
    assert tool_outputs(second) == [json.dumps(LONG.upper())], "it ran once, before compacting"
    assert first[1] == {"role": "user", "content": "p0"}  # before it compacted
    assert events(main, session).count("tools_called") == 1
    assert "turn_yielded" in events(main, session)


def test_a_turn_that_yields_to_compact_shows_the_compacted_context_until_its_next_step(
    tooled_main: MainHarness,
):
    main = tooled_main
    session = main.sessions.save_functions(main.create_session(), (PieceId("function:voice"),))
    seed(main, session, [talked(n) for n in range(3)])
    main.llm = FakeLlm(
        tool_call("shout", json.dumps({"text": LONG})), reply("We talked."), reply("done")
    )
    main.run(
        MainCommandEntered(f"/open {session.id}"), InputReceived(session.id, "lee"), settle=True
    )
    assert "turn_yielded" in events(main, session)
    [window] = main.windows(session.id)
    borders = [m.context for m in window.sent if isinstance(m, ContextUsage)]
    # Its Sysprompt, the summary of p0 (5 + 2) and p1 and p2 literal, not its own Turn yet
    # (#157); then its second Step's own.
    assert borders[-3:] == [None, 6 + 7 + 200, 101]


def test_a_turn_whose_saved_turns_fit_in_keep_does_not_stop_for_nothing(
    tooled_main: MainHarness,
):
    main = tooled_main
    session = main.sessions.save_functions(main.create_session(), (PieceId("function:voice"),))
    seed(main, session, [talked(0)])  # one Turn, which compact.keep keeps: nothing to compact
    big = " ".join(["palabra"] * 400)  # past the threshold, but it still fits
    main.llm = FakeLlm(tool_call("shout", json.dumps({"text": big})), reply("done"))
    main.run(InputReceived(session.id, "lee"), settle=True)
    after = conversation(main, session)
    assert [turn.prompt for turn in after.turns] == ["p0", "lee"]
    assert len(after.turns[-1].steps) == 2
    logged = events(main, session)
    assert "turn_yielded" not in logged and "compaction_failed" not in logged


def test_a_turn_cut_as_it_stops_to_compact_is_cut_and_does_not_come_back(main: MainHarness):
    session = main.create_session()
    seed(main, session, [talked(n) for n in range(5)])
    main.threaded_turns, main.hold_turns = False, True
    main.run(
        InputReceived(session.id, "lee"),
        ToolsStarted(session.id, ("shout",), Step(output=[])),
        ToolsEnded(
            session.id, Step(output=[]), 600
        ),  # its next Step would pass the threshold: it stops
        InputReceived(session.id, "", interrupt=True),  # and is cut before it did
        TurnEnded(session.id, error=TurnYielded()),
        settle=True,
    )
    logged = events(main, session)
    assert "turn_yielded" not in logged and "compaction_started" not in logged
    assert [turn.prompt for turn in conversation(main, session).turns][-1] == "lee"
    assert len(main.held) == 1, "it was not taken up again"


CALLED_X = Step(output=[{"type": "function_call", "call_id": "c1", "name": "x", "arguments": ""}])
# What it reads next passes the threshold: it compacts before it goes on.
RETURNED_X = CALLED_X.model_copy(
    update={"results": [{"type": "function_call_output", "call_id": "c1", "output": LONG}]}
)
SUMMARY = Step(
    output=[
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "We talked."}],
        }
    ]
)


def yielded_and_cut(main: MainHarness) -> tuple[Session, list[MainEvent]]:
    """A Turn «lee» yields to compact after its tools, and its summary Step runs when the
    human cuts it with Shift+Enter and sends «otra» (#208)."""
    session = main.create_session()
    seed(main, session, [talked(n) for n in range(5)])
    main.threaded_turns, main.hold_turns = False, True
    return session, [
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "lee"),
        ToolsStarted(session.id, ("x",), CALLED_X),
        ToolsEnded(session.id, RETURNED_X, 600),  # its next Step would pass the threshold
        StepEnded(session.id, RETURNED_X),
        TurnEnded(session.id, error=TurnYielded()),  # it yields: its summary Step starts
        InputReceived(session.id, "otra", interrupt=True),
    ]


def test_shift_enter_on_a_turn_yielded_to_compact_saves_it_cut_once_its_compaction_ends(
    main: MainHarness,
):
    session, cut = yielded_and_cut(main)
    main.run(*cut, TurnEnded(session.id, steps=[SUMMARY]), settle=True)
    after = conversation(main, session)
    assert after.summary == "We talked.", "the Compaction it yielded to ends first"
    assert [turn.prompt for turn in after.turns][-1] == "lee"
    assert after.turns[-1].cut == "interrupted" and after.turns[-1].steps == [RETURNED_X]
    _, _, otra = main.held  # «lee», its summary Step, then «otra», behind it
    assert otra.request.done == [] and otra.request.input[-1]["content"].endswith("otra")


def test_a_turn_cut_as_it_waits_for_its_compaction_is_saved_cut_after_a_restart(
    main: MainHarness,
):
    session, cut = yielded_and_cut(main)
    main.run(*cut, MainCommandEntered("/exit"), settle=True)
    held = len(main.held)
    main.llm = FakeLlm(reply("We talked."))
    main.hold_turns = False
    main.run(MainCommandEntered("/exit"), settle=True)  # it starts again, and stops once idle
    after = conversation(main, session)
    assert after.summary == "We talked.", "its Compaction ran again first"
    assert [turn.prompt for turn in after.turns][-1] == "lee"
    assert after.turns[-1].cut == "interrupted"
    assert len(main.held) == held


def test_closing_the_window_as_a_cut_turn_waits_for_its_compaction_cancels_both(
    main: MainHarness,
):
    session, cut = yielded_and_cut(main)
    main.run(
        *cut,
        WindowClosed(session.id, returncode=0),
        TurnEnded(session.id, error=TurnCancelled(reason="window closed")),
        MainCommandEntered("/exit"),
        settle=True,
    )
    after = conversation(main, session)
    assert after.summary is None and [turn.prompt for turn in after.turns][-1] == "p4"
    assert main.state.load().drafts == {session.id: ["lee", "otra"]}


COMPACT = '''
from allin.channel import command
from allin.decorators import reach


@reach(commands=["compact"])
def compact(instructions: str) -> object:
    """Compact this Session's Conversation."""
    return command(f"@compact {instructions}")
'''


def test_compact_from_a_tool_compacts_its_own_session_after_its_turn(tooled_main: MainHarness):
    main = tooled_main
    (main.profile.folder / "functions" / "voice" / "compact.py").write_text(COMPACT)
    approve_voice(main)
    created = main.sessions.save_functions(main.create_session(), (PieceId("function:voice"),))
    session = main.sessions.save_commands(created, (CommandName("compact"),))
    main.server = server(context=4_000)  # its tools' schemas count a character each
    seed(main, session, [tooled(n) for n in range(8)])  # under the threshold: no automatic
    main.llm = FakeLlm(
        tool_call("compact", json.dumps({"instructions": "keep the file names"})),
        reply("queued"),
        reply("We read a file."),
    )
    main.run(InputReceived(session.id, "compacta"), settle=True)
    answer = json.loads(str(tool_outputs(main.llm.requests[1]["input"])[-1]))
    assert answer["result"] == "ok", answer
    closing = main.llm.requests[-1]["input"][-1]["content"]
    assert closing.endswith("What the human asks of this summary: keep the file names")
    after = conversation(main, session)
    assert after.summary == "We read a file." and after.summarized > 0
