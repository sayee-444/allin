"""The methods of a Proceso adherido (#114, ADR-0021, ADR-0013, ADR-0025): the LLM calls them
as ``<name>_<method>`` tools, a human with ``/call`` or ``@call``; ``@human`` keeps one from the
LLM; and what a method runs through Main is bounded by its fproc's Alcance, by the list of every
Session of its chain and by the Proceso adherido's link, which never counts as human. The
processes run for real on this kernel; the LLM is fake."""

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.ids import BoxId, CommandName
from allin.main.attached_methods import held_names
from allin.main.events import BoxAnswered, InputReceived, MainCommandEntered
from allin.main.sessions import Session
from allin.ui.screen import ShowBox
from tests.llm_fakes import FakeLlm, full_reply, tool_call
from tests.test_attached import approve, from_session
from tests.test_dispatcher import TURNS_ENDED, DrainingInbox, MainHarness, Until
from tests.test_session_commands import outputs, prompts

pytestmark = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

BOARD = '''
from allin.channel import command
from allin.decorators import confirm, human, reach
from allin.fproc import Fproc


@reach(commands=["send", "grant", "call"])
class Board(Fproc):
    """A board of notes."""

    def __init__(self):
        self.notes = []

    def pin(self, text: str) -> int:
        """Pin a note; gives how many there are."""
        self.notes.append(text)
        return len(self.notes)

    @human
    def clear(self) -> int:
        """Unpin every note."""
        self.notes.clear()
        return 0

    @confirm
    def burn(self) -> str:
        """Burn the board."""
        return "burnt"

    def tell(self, sid: str, text: str) -> object:
        """Send a Session a note."""
        return command(f"@send {sid} {text}")

    def give(self, sid: str) -> object:
        """Give a Session @send."""
        return command(f"@grant {sid} @send")

    def sneak(self, sid: str) -> object:
        """Attach something, which it does not declare."""
        return command(f"@attach board other")

    def again(self) -> object:
        """Call its own process."""
        return command("@call board pin text=x")

    def fail(self) -> int:
        """Fail."""
        raise ValueError("no board")
'''


@pytest.fixture
def main(tmp_path: Path) -> Iterator[MainHarness]:
    """Main with real descriptions and the approved fproc ``board``, attached by a human."""
    harness = MainHarness(tmp_path)
    harness.describer = ToolLauncher(tmp_path, grace=1.0)
    harness.describer.start()
    harness.threaded_turns = True
    folder = harness.profile.folder / "functions" / "counting"
    folder.mkdir(parents=True)
    (folder / "board.py").write_text(BOARD)
    approve(harness, harness.describer)
    yield harness
    harness.describer.stop()


def session(main: MainHarness, *commands: str) -> Session:
    created = main.create_session()
    return main.sessions.save_commands(created, tuple(CommandName(c) for c in commands))


def calls(main: MainHarness) -> None:
    """Have Main wait, handling what comes in, for the calls a human made, which answer from
    their own threads; ``TURNS_ENDED`` must follow."""
    inbox = main.servers.inbox
    assert isinstance(inbox, DrainingInbox)
    inbox.workers += [t for t in threading.enumerate() if t.name.startswith("call-")]


def held(main: MainHarness, s: Session) -> tuple[str, ...]:
    return held_names(main.sessions.load(s.id))


def tool_names(request: dict[str, Any]) -> set[str]:
    return {tool["name"] for tool in request.get("tools", [])}


def confirms(main: MainHarness) -> list[ShowBox]:
    return [u for u in main.main_screen if isinstance(u, ShowBox) and u.box.kind == "confirm"]


def called(main: MainHarness) -> list[dict[str, object]]:
    return [e.fields for e in main.main_log() if e.event == "method_called"]


def test_the_llm_calls_a_method_as_a_tool_named_after_its_process(main: MainHarness):
    a = session(main)
    main.llm = FakeLlm(tool_call("board_pin", json.dumps({"text": "hola"})), full_reply("", "ok"))
    main.run(
        MainCommandEntered(f"/attach board --to={a.id}"),
        InputReceived(a.id, "clava una nota"),
        settle=True,
    )
    first, second = main.llm.requests
    assert {"board_pin", "board_burn", "board_tell"} <= tool_names(first)
    assert "board_clear" not in tool_names(first), "@human never reaches tools=[...]"
    assert outputs(second) == ["1"]


def test_call_and_at_call_reach_the_same_method_and_call_alone_lists_them(main: MainHarness):
    a = session(main)
    chat = main.run(
        MainCommandEntered("/attach board"),
        MainCommandEntered("/call board pin text=uno"),
        lambda: calls(main),
        TURNS_ENDED,
        InputReceived(a.id, "@call board pin text='dos y tres'"),
        lambda: calls(main),
        TURNS_ENDED,
        MainCommandEntered("/call board"),
        settle=True,
    )
    assert [c["result"] for c in called(main)] == ["1", "2"]
    assert [c["arguments"] for c in called(main)] == [{"text": "uno"}, {"text": "dos y tres"}]
    [logged] = [e.fields for e in main.session_log(a) if e.event == "method_called"]
    assert logged["result"] == "2"
    [told] = main.sessions.load(a.id).notifications
    assert told.source == "call" and "board.pin" in told.text
    assert any("pin(text)" in line for line in chat), "\n".join(chat[-14:])
    assert any("clear() @human" in line for line in chat)
    assert any("burn() @confirm" in line for line in chat)


def test_a_human_method_is_a_humans_alone(main: MainHarness):
    a = session(main, "call")
    chat = main.run(
        MainCommandEntered(f"/attach board --to={a.id}"),
        MainCommandEntered("/call board clear"),
        lambda: calls(main),
        TURNS_ENDED,
        MainCommandEntered("/call board clear", from_session(a)),
        settle=True,
    )
    assert [c["result"] for c in called(main)] == ["0"]
    assert any("human_only" in line for line in chat)


def test_a_session_calls_only_the_methods_it_has(main: MainHarness):
    a = session(main, "call")
    chat = main.run(
        MainCommandEntered("/attach board"),
        MainCommandEntered("/call board pin text=x", from_session(a)),
        settle=True,
    )
    assert any("attached_methods_not_given" in line for line in chat)
    assert called(main) == []


def test_confirm_asks_a_human_and_the_llm_and_yes_skips_it_for_the_human(main: MainHarness):
    a = session(main)
    main.llm = FakeLlm(tool_call("board_burn", "{}"), full_reply("", "ok"))
    main.run(
        MainCommandEntered(f"/attach board --to={a.id}"),
        MainCommandEntered("/call board burn"),
        Until(lambda: len(confirms(main)) == 1),
        BoxAnswered(BoxId("b1"), True),
        lambda: calls(main),
        TURNS_ENDED,
        MainCommandEntered("/call -y board burn"),
        lambda: calls(main),
        TURNS_ENDED,
        InputReceived(a.id, "quémalo"),
        Until(lambda: len(confirms(main)) == 2),
        BoxAnswered(BoxId("b2"), True),
        settle=True,
    )
    assert [c["result"] for c in called(main)] == ['"burnt"', '"burnt"'], "-y answered one"
    assert len(confirms(main)) == 2, "the LLM's call asks too"
    assert outputs(main.llm.requests[1]) == ["burnt"]


def test_what_a_method_runs_goes_with_the_process_in_its_origin(main: MainHarness):
    a, b = session(main, "send"), session(main)
    main.llm = FakeLlm(
        tool_call("board_tell", json.dumps({"sid": b.id, "text": "hola"})),
        full_reply("", "enviado"),
        full_reply("", "recibido"),  # B's Turn
    )
    main.run(
        MainCommandEntered(f"/attach board --to={a.id}"),
        InputReceived(a.id, "dile hola"),
        settle=True,
    )
    [sent] = prompts(main, b)
    assert sent["origin"] == ["human", f"session:{a.id}", "process:board"]


def test_a_method_runs_neither_what_its_fproc_does_not_declare_nor_what_its_chain_lacks(
    main: MainHarness,
):
    a, b = session(main), session(main)  # A's list is empty
    main.llm = FakeLlm(
        tool_call("board_tell", json.dumps({"sid": b.id, "text": "hola"})),
        full_reply("", "no"),
        tool_call("board_sneak", json.dumps({"sid": b.id})),
        full_reply("", "no"),
    )
    main.run(
        MainCommandEntered(f"/attach board --to={a.id}"),
        InputReceived(a.id, "dile hola"),
        TURNS_ENDED,  # else it enters the running Turn after its tools (ADR-0024)
        InputReceived(a.id, "cuela"),
        settle=True,
    )
    not_listed = outputs(main.llm.requests[1])[-1]
    assert "session_command_not_listed" in not_listed["error"]
    not_declared = outputs(main.llm.requests[3])[-1]
    assert "session_command_not_declared" in not_declared["error"]
    assert prompts(main, b) == []


def test_what_a_method_runs_asks_a_confirmation_though_a_human_called_it(main: MainHarness):
    a, b = session(main), session(main)
    main.run(
        MainCommandEntered("/attach board"),
        InputReceived(a.id, f"@call -y board give sid={b.id}"),
        lambda: calls(main),
        Until(lambda: bool(confirms(main))),
        BoxAnswered(BoxId("b1"), False),
        TURNS_ENDED,
        settle=True,
    )
    [asked] = confirms(main)
    assert "/grant" in asked.box.text and "process:board" in asked.box.text
    assert CommandName("send") not in main.sessions.load(b.id).commands


def test_detaching_takes_the_methods_from_every_session_and_func_gives_them_back(
    main: MainHarness,
):
    a, b = session(main), session(main)
    seen: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def look() -> None:
        seen.append((held(main, a), held(main, b)))

    main.run(
        MainCommandEntered(f"/attach board --to={a.id},{b.id}"),
        look,
        MainCommandEntered(f"/func remove {b.id} attached:board"),
        look,
        MainCommandEntered(f"/func add {b.id} attached:board"),
        look,
        MainCommandEntered("/detach board"),
        look,
        settle=True,
    )
    assert seen == [
        (("board",), ("board",)),
        (("board",), ()),
        (("board",), ("board",)),
        ((), ()),
    ]
    told = [n.text for n in main.sessions.load(a.id).notifications]
    assert told == [], "given and taken away before any Turn told them: they cancel out"


def test_only_a_human_gives_the_methods_but_a_session_gives_them_up(main: MainHarness):
    a = session(main)
    seen: list[tuple[str, ...]] = []
    chat = main.run(
        MainCommandEntered("/attach board"),
        MainCommandEntered(f"/func add {a.id} attached:board", from_session(a)),
        lambda: seen.append(held(main, a)),
        MainCommandEntered(f"/func add {a.id} attached:board"),
        MainCommandEntered(f"/func remove {a.id} attached:board", from_session(a)),
        lambda: seen.append(held(main, a)),
        settle=True,
    )
    assert any("human_only" in line for line in chat)
    assert seen == [(), ()], "it could not give them, but gave them up"


def test_called_from_mains_chat_a_method_runs_no_command_session(main: MainHarness):
    b = session(main)
    main.run(
        MainCommandEntered("/attach board"),
        MainCommandEntered(f"/call board tell sid={b.id} text=hola"),
        lambda: calls(main),
        TURNS_ENDED,
        settle=True,
    )
    [call] = called(main)
    assert "command_session_without_session" in str(call["result"])
    assert prompts(main, b) == []


def test_a_session_takes_away_only_its_own_methods(main: MainHarness):
    a, b = session(main), session(main)
    seen: list[tuple[str, ...]] = []
    chat = main.run(
        MainCommandEntered(f"/attach board --to={a.id},{b.id}"),
        MainCommandEntered(f"/func remove {b.id} attached:board", from_session(a)),
        lambda: seen.append(held(main, b)),
        settle=True,
    )
    assert any("human_only" in line for line in chat)
    assert seen == [("board",)], "Main stopping detaches it: look before"


def test_a_rewind_to_before_a_session_got_the_methods_takes_them_away(main: MainHarness):
    a = session(main)
    main.llm = FakeLlm(full_reply("", "uno"), full_reply("", "dos"))
    seen: list[tuple[str, ...] | str] = []
    main.run(
        MainCommandEntered("/attach board"),
        InputReceived(a.id, "uno"),
        TURNS_ENDED,
        MainCommandEntered(f"/func add {a.id} attached:board"),
        InputReceived(a.id, "dos"),
        TURNS_ENDED,
        MainCommandEntered(f"/rewind {a.id}"),
        lambda: seen.append(held(main, a)),
        MainCommandEntered(f"/rewind {a.id}"),
        lambda: seen.append(held(main, a)),
        lambda: seen.append(main.dispatcher.attached.attached_fproc("board").name),  # stays
        settle=True,
    )
    assert seen == [("board",), (), "board"], "given after the first Prompt: undoing it takes them"


def test_a_new_session_is_born_with_methods_and_a_session_passes_on_only_its_own(
    main: MainHarness,
):
    a, b = session(main, "create"), session(main, "create")
    born: dict[str, tuple[str, ...]] = {}
    main.run(
        MainCommandEntered(f"/attach board --to={a.id}"),
        MainCommandEntered("/new --id=humana --bg -am=board"),
        MainCommandEntered("/new --id=hijauno --bg -am=board", from_session(a)),
        MainCommandEntered("/new --id=hijados --bg -am=board", from_session(b)),
        lambda: born.update(
            {sid: held_names(main.sessions.load(sid)) for sid in ("humana", "hijauno", "hijados")}
        ),
        settle=True,
    )
    assert born == {"humana": ("board",), "hijauno": ("board",), "hijados": ()}


def test_a_method_never_calls_its_own_process(main: MainHarness):
    a = session(main)
    main.run(
        MainCommandEntered("/attach board"),
        InputReceived(a.id, "@call board again"),
        lambda: calls(main),
        TURNS_ENDED,
        settle=True,
    )
    [call] = called(main)
    assert "attached_calls_itself" in str(call["result"])


def test_a_method_that_raises_fails_the_call(main: MainHarness):
    main.run(
        MainCommandEntered("/attach board"),
        MainCommandEntered("/call board fail"),
        lambda: calls(main),
        TURNS_ENDED,
        settle=True,
    )
    [call] = called(main)
    assert "no board" in str(call["error"])
    [failed] = [
        e.fields for e in main.main_log() if e.event == "command" and "/call" in str(e.fields)
    ]
    assert "attached_method_failed" in str(failed["result"])
