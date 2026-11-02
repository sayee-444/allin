"""Command sessions (ADR-0025, #108): ``@…`` typed alone in a Window, and the channel through
which a confined tool asks Main for one. The tools run for real on this kernel; the LLM is
fake."""

import json
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.ids import CommandName, PieceId, ServerKey, SessionId
from allin.core.origin import HUMAN, Link, Origin
from allin.core.themes import Theme
from allin.main.conversation import Item
from allin.main.descriptions import Descriptions
from allin.main.events import (
    CommandOutcome,
    InputReceived,
    MainCommandEntered,
    ServerFailed,
    ServerReady,
    SessionCommandResult,
)
from allin.main.links import LinkResolver
from allin.main.log import Log
from allin.main.notifications import Notifications
from allin.main.open_windows import OpenWindows
from allin.main.quarantine import PieceRef, Quarantine
from allin.main.reporting import Reporter
from allin.main.session_commands import (
    BUILT_IN,
    SessionCommands,
    load_session_command_types,
)
from allin.main.sessions import Session
from allin.ui.screen import ShowBox
from tests.llm_fakes import FakeLlm, full_reply, tool_call
from tests.origins import A_SESSION
from tests.test_dispatcher import FakeWindowLink, MainHarness, server

TELLER = PieceId("function:teller")
TOOLS = {
    "tell": '''
from allin.channel import command
from allin.decorators import reach


@reach(commands=["send"])
def tell(sid: str, text: str) -> object:
    """Send a Prompt to another Session."""
    return command(f"@send {sid} {text}")
''',
    "sneak": '''
from allin.channel import command


def sneak(sid: str) -> object:
    """Send without declaring it."""
    return command(f"@send {sid} hola")
''',
}


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


@pytest.fixture
def tools(main: MainHarness, tmp_path: Path) -> Iterator[MainHarness]:
    """Main with real confined tools: the ``teller`` package, approved."""
    if abi_version() < 6:
        pytest.skip("needs Landlock ABI 6 (kernel 6.12)")
    main.describer = ToolLauncher(tmp_path)
    main.describer.start()
    main.threaded_turns = True
    folder = main.profile.folder / "functions" / "teller"
    folder.mkdir(parents=True)
    for tool, source in TOOLS.items():
        (folder / f"{tool}.py").write_text(source)
    descriptions = Descriptions(main.profile.folder.parents[1], main.describer, main.confinement)
    pieces = Quarantine(main.profile.folder, descriptions)
    ref = PieceRef("function", "teller")
    pieces.approve(ref, pieces.piece(ref).hash)
    yield main
    main.describer.stop()


def session(main: MainHarness, *commands: str, teller: bool = False) -> Session:
    created = main.create_session()
    if teller:
        created = main.sessions.save_functions(created, (TELLER,))
    return main.sessions.save_commands(created, tuple(CommandName(c) for c in commands))


def outputs(request: dict[str, Any]) -> list[Any]:
    items: list[Item] = request["input"]
    return [
        json.loads(item["output"]) if item["output"].startswith(("{", "[", '"')) else item["output"]
        for item in items
        if item.get("type") == "function_call_output"
    ]


def prompts(main: MainHarness, of: Session) -> list[dict[str, Any]]:
    return [entry.fields for entry in main.session_log(of) if entry.event == "prompt"]


# Typed in a Window


def test_typed_in_a_window_it_runs_its_command_main_as_a_human_and_is_logged(main: MainHarness):
    a, b = session(main), session(main)  # a human needs nothing in the list
    main.llm = FakeLlm(full_reply("", "hola"))
    main.run(InputReceived(a.id, f"@send {b.id} hola"), settle=True)
    [sent] = prompts(main, b)
    assert sent["text"] == "hola" and sent["origin"] == ["human"]
    [logged] = [e.fields for e in main.session_log(a) if e.event == "session_command"]
    assert logged["runs"] == f"/send {b.id} hola"
    assert prompts(main, a) == [], "a Command session is never a Prompt"


def test_server_shows_its_sessions_server(main: MainHarness):
    a = main.create_session(server="gpu")
    main.run(InputReceived(a.id, "@server"), settle=True)
    [logged] = [e.fields for e in main.session_log(a) if e.event == "session_command"]
    assert logged["runs"] == "/server gpu"


# From a tool


def test_a_tool_runs_one_it_declares_and_reads_its_result(tools: MainHarness):
    a, b = session(tools, "send", teller=True), session(tools)
    tools.llm = FakeLlm(
        tool_call("tell", json.dumps({"sid": b.id, "text": "hola"})),
        full_reply("", "enviado"),
        full_reply("", "recibido"),  # B's Turn
    )
    tools.run(InputReceived(a.id, "dile hola"), settle=True)
    # B's Turn may take the slot before A's goes on: look in every request.
    assert [o for r in tools.llm.requests for o in outputs(r)] == [{"result": "ok"}]
    [sent] = prompts(tools, b)
    assert sent["origin"] == ["human", f"session:{a.id}"]
    assert sent["text"].endswith("\nhola")  # data under its Origin's header (ADR-0013)


def test_one_it_does_not_declare_is_refused(tools: MainHarness):
    a, b = session(tools, "send", teller=True), session(tools)
    tools.llm = FakeLlm(tool_call("sneak", json.dumps({"sid": b.id})), full_reply("", "no"))
    tools.run(InputReceived(a.id, "cuela"), settle=True)
    [output] = outputs(tools.llm.requests[1])
    assert "session_command_not_declared" in output["error"]
    assert prompts(tools, b) == []


def test_one_its_session_does_not_have_is_refused(tools: MainHarness):
    a, b = session(tools, teller=True), session(tools)  # A's list is empty
    tools.llm = FakeLlm(
        tool_call("tell", json.dumps({"sid": b.id, "text": "x"})), full_reply("", "no")
    )
    tools.run(InputReceived(a.id, "dile"), settle=True)
    [output] = outputs(tools.llm.requests[1])
    assert "session_command_not_listed" in output["error"]
    assert prompts(tools, b) == []


def test_a_session_without_it_does_not_get_it_through_one_that_has_it(tools: MainHarness):
    # A has no @send; it asks B, which has it, to send to C: the chain is as weak as A.
    a = session(tools)
    b = session(tools, "send", teller=True)
    c = session(tools)
    origin = Origin(chain=(*HUMAN.chain, Link("session", a.id)))
    tools.llm = FakeLlm(
        tool_call("tell", json.dumps({"sid": c.id, "text": "x"})), full_reply("", "no")
    )
    tools.run(MainCommandEntered(f"/send {b.id} manda a C", origin=origin), settle=True)
    [output] = outputs(tools.llm.requests[1])
    assert "session_command_not_listed" in output["error"] and a.id in output["error"]
    assert prompts(tools, c) == []


def test_what_the_command_main_says_goes_back_to_the_tool(tools: MainHarness):
    a = session(tools, "send", teller=True)
    tools.llm = FakeLlm(
        tool_call("tell", json.dumps({"sid": "zz9zz9", "text": "x"})), full_reply("", "no")
    )
    tools.run(InputReceived(a.id, "dile"), settle=True)
    [output] = outputs(tools.llm.requests[1])
    assert output["error"].startswith("session_not_found")


# Each Session's list


def test_new_gives_the_roots_or_the_ones_named(main: MainHarness):
    main.run(MainCommandEntered("/new --bg"), settle=True)
    main.run(MainCommandEntered("/new --bg -cs=send,nope"), settle=True)
    first, second = (
        main.sessions.load(sid) for sid in sorted(main.sessions.ids(), key=_created(main))
    )
    assert first.commands == ("send", "server", "conversation_logs")
    assert second.commands == ("send",)
    notices = [u.box for u in main.main_screen if isinstance(u, ShowBox)]
    assert [box.title for box in notices] == ["Command sessions missing"]
    assert "@nope" in notices[0].text


def test_a_session_creates_one_with_no_more_than_its_whole_chain_has(main: MainHarness):
    # A (with @send only) asks B (with both) to create one: it gets what both have.
    a, creator = session(main, "send"), session(main, "send", "server")
    origin = Origin(chain=(*HUMAN.chain, Link("session", a.id), Link("session", creator.id)))
    main.run(MainCommandEntered("/new --bg", origin=origin), settle=True)
    [created] = [
        main.sessions.load(sid) for sid in main.sessions.ids() if sid not in {a.id, creator.id}
    ]
    assert created.commands == ("send",)


def test_only_a_human_widens_the_list(main: MainHarness):
    a = session(main)
    chat = main.run(MainCommandEntered(f"/func add {a.id} @send", origin=A_SESSION))
    assert any("human_only" in line for line in chat)
    assert main.sessions.load(a.id).commands == ()
    main.run(MainCommandEntered(f"/func add {a.id} @send @server"), settle=True)
    assert main.sessions.load(a.id).commands == ("send", "server")
    main.run(MainCommandEntered(f"/func remove {a.id} @server", origin=A_SESSION), settle=True)
    assert main.sessions.load(a.id).commands == ("send",)


def test_a_func_line_that_fails_changes_nothing(main: MainHarness):
    a = session(main)
    main.run(MainCommandEntered(f"/func add {a.id} @server function:nope"), settle=True)
    main.run(MainCommandEntered(f"/func add {a.id} @server @nope"), settle=True)
    assert main.sessions.load(a.id).commands == ()


# The modules and @server restart


def test_the_modules_at_the_root_are_the_defaults_and_a_broken_one_does_not_stop_main(
    tmp_path: Path,
):
    folder = tmp_path / "comsession"
    (folder / "extra").mkdir(parents=True)
    module = """
from allin.session_commands import session_command


@session_command("{name}", usage="@{name}", summary="test")
def run(call):
    return "/help"
"""
    (folder / "note.py").write_text(module.format(name="note"))
    (folder / "extra" / "deep.py").write_text(module.format(name="deep"))
    (folder / "broken.py").write_text("raise RuntimeError('no')")
    loaded = load_session_command_types(folder)
    assert {"send", "server", "note", "deep"} <= set(loaded.types)
    assert loaded.root == ("send", "server", "conversation_logs", "note")
    assert [problem.code for problem in loaded.problems] == ["session_command_failed"]


@pytest.mark.parametrize(
    ("state", "answered"),
    [
        (ServerReady(server()), {"result": "ok", "server": "ready"}),
        (
            ServerFailed("crashes", "out of memory"),
            {"error": "failed: out of memory", "server": "failed"},
        ),
    ],
    ids=["ready", "failed"],
)
def test_server_restart_answers_once_its_server_is_ready_or_failed(
    main: MainHarness, state: ServerReady | ServerFailed, answered: JsonValue
):
    a = main.create_session(server="gpu")
    ran: list[str] = []

    def run_main(text: str, origin: Origin, done: Callable[[CommandOutcome], None]) -> bool:
        ran.append(text)
        done("ok")
        return True

    reporter, notifications = notifications_of(main)
    commands = SessionCommands(
        BUILT_IN, main.sessions, main.sessions.load, run_main, reporter, notifications
    )
    answers: list[JsonValue] = []

    def heard(result: SessionCommandResult) -> bool:
        answers.append(result.as_json())
        return True

    from_a = Origin(chain=(*HUMAN.chain, Link("session", a.id)))
    commands.run(a.id, "@server restart", from_a, heard)
    [refused] = answers
    assert ran == [] and "session_command_not_listed" in str(refused)
    main.sessions.save_commands(main.sessions.load(a.id), (CommandName("server"),))
    answers.clear()
    commands.run(a.id, "@server restart", from_a, heard)
    assert ran == ["/server restart gpu"] and answers == []  # it waits for its Server
    commands.server_changed(ServerKey("other"), state)
    assert answers == []
    commands.server_changed(ServerKey("gpu"), state)
    assert answers == [answered]


def test_a_result_nobody_waits_for_reaches_its_session_as_a_notification(main: MainHarness):
    # Its tool's Turn was cancelled while a human took long to answer the Confirmation.
    a = main.create_session(server="gpu")
    main.sessions.save_commands(a, (CommandName("server"),))
    reporter, notifications = notifications_of(main)
    later: list[Callable[[CommandOutcome], None]] = []

    def run_main(text: str, origin: Origin, done: Callable[[CommandOutcome], None]) -> bool:
        later.append(done)
        return True

    commands = SessionCommands(
        BUILT_IN, main.sessions, main.sessions.load, run_main, reporter, notifications
    )
    from_a = Origin(chain=(*HUMAN.chain, Link("session", a.id)))
    commands.run(a.id, "@server", from_a, lambda _: False)
    later[0]("rejected")
    [pending] = main.sessions.load(a.id).notifications
    assert pending.source == "@server"
    assert pending.text == '@server: {"error": "rejected"}'


def notifications_of(main: MainHarness) -> tuple[Reporter, Notifications]:
    """A Reporter and the Notificaciones of ``main``'s Sessions, for SessionCommands alone."""
    reporter = Reporter(lambda _: None, Log(main.main_log_path), main.sessions)

    def open_window(_: SessionId, __: Theme) -> FakeWindowLink:
        return FakeWindowLink(pid=1)

    resolver = LinkResolver(main.root, "http://127.0.0.1:9931/v1")
    windows = OpenWindows(open_window, reporter, main.sessions, resolver)
    return reporter, Notifications(main.sessions, reporter, windows)


def _created(main: MainHarness) -> Callable[[str], datetime]:
    return lambda sid: main.sessions.load(sid).created_at
