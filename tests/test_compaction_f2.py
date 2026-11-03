"""The second part of the Compactación (ADR-0017, #119): pruning old tool outputs, compacting
between the Steps of a Turn, ``@compact``, its event and the Notificación of live state."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.ids import PieceId
from allin.main.compaction import CLOSING
from allin.main.conversation import Conversation, Step, Turn, summary_item
from allin.main.descriptions import Descriptions
from allin.main.events import InputReceived, MainCommandEntered, Tick
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


@pytest.fixture
def tooled_main(main: MainHarness, tmp_path: Path) -> Iterator[MainHarness]:
    """The same Main with the real confined ``voice`` package, approved."""
    if abi_version() < 6:
        pytest.skip("needs Landlock ABI 6 (kernel 6.12)")
    main.describer = ToolLauncher(tmp_path)
    main.describer.start()
    main.threaded_turns = True
    folder = main.profile.folder / "functions" / "voice"
    folder.mkdir(parents=True)
    (folder / "shout.py").write_text(SHOUT)
    descriptions = Descriptions(main.profile.folder.parents[1], main.describer, main.confinement)
    pieces = Quarantine(main.profile.folder, descriptions)
    ref = PieceRef("function", "voice")
    pieces.approve(ref, pieces.piece(ref).hash)
    yield main
    main.describer.stop()


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
    main.run(InputReceived(session.id, "lee"), settle=True)
    first, summary_step, second = (r["input"] for r in main.llm.requests)
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


def test_a_turn_that_cannot_make_room_mid_turn_goes_on_if_it_fits_and_yields_once(
    tooled_main: MainHarness,
):
    main = tooled_main
    session = main.sessions.save_functions(main.create_session(), (PieceId("function:voice"),))
    seed(main, session, [talked(0)])  # one Turn, which compact.keep keeps: nothing to compact
    # Its next Step needs about 889 of the 1 000 tokens: past the threshold, but it fits.
    main.llm = FakeLlm(tool_call("shout", json.dumps({"text": LONG})), reply("done"))
    main.run(InputReceived(session.id, "lee"), settle=True)
    after = conversation(main, session)
    assert [turn.prompt for turn in after.turns] == ["p0", "lee"]
    assert len(after.turns[-1].steps) == 2
    logged = events(main, session)
    assert logged.count("turn_yielded") == 1 and "compaction_failed" in logged
    assert logged.count("turn_finished") == 1
