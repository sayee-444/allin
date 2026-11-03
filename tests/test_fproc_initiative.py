"""An fproc's initiative between calls (#123, ADR-0026): from a thread of its own, not one that
serves a call, it asks Main for Command sessions over its channel, and hears how each went. It
creates Sessions only in its Subperfil, from the Moldes of its package, and acts only on those.
The processes run for real on this kernel; the LLM is fake."""

import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pydantic import JsonValue

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import AttachedProcess, FprocStart, ToolLauncher
from allin.core.ids import AttachedName, CommandName, FprocName, PieceId
from allin.core.origin import HUMAN, Link, Origin
from allin.main.descriptions import Descriptions
from allin.main.events import InputReceived, MainCommandEntered
from allin.main.quarantine import PieceRef, Quarantine
from allin.main.state import MainState, MainStateStore
from tests.llm_fakes import FakeLlm, full_reply
from tests.test_attached import from_session
from tests.test_attached_methods import calls
from tests.test_dispatcher import (
    TURNS_ENDED,
    DrainingInbox,
    MainHarness,
    Until,
    ended,
    turn_lines,
)

pytestmark = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

HERALD = '''
import threading

from allin.channel import command
from allin.fproc import Fproc


class Herald(Fproc):
    """Acts on its own, between calls."""

    def __init__(self):
        self.heard = []
        threading.Thread(target=self._say, args=("@send early hi",), daemon=True).start()

    def later(self, text: str) -> None:
        """Ask for ``text`` from a thread of its own, once this call is over."""
        threading.Thread(target=self._say, args=(text,), daemon=True).start()

    def heard_so_far(self) -> list:
        return self.heard

    def _say(self, text):
        self.heard.append(command(text))
'''


@pytest.fixture
def package(tmp_path: Path) -> Path:
    folder = tmp_path / "var" / "profiles" / "default" / "functions" / "heralds"
    folder.mkdir(parents=True)
    (folder / "herald.py").write_text(HERALD)
    return folder


@pytest.fixture
def launcher(tmp_path: Path) -> Iterator[ToolLauncher]:
    launcher = ToolLauncher(tmp_path / "var", grace=1.0)
    launcher.start()
    yield launcher
    launcher.stop()


def attach(
    launcher: ToolLauncher, package: Path, on_initiative: Callable[[str], JsonValue] | None
) -> AttachedProcess:
    workspace = package.parents[3] / "attached" / "default" / "herald"
    workspace.mkdir(parents=True, exist_ok=True)
    start = FprocStart(package, FprocName("herald"), AttachedName("herald"), workspace)
    return launcher.attach(start, timeout=20, on_initiative=on_initiative)


def heard(process: AttachedProcess, count: int) -> list[JsonValue]:
    """What its own threads heard back, once there are ``count`` answers."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = process.call("heard_so_far", {}, timeout=10).value
        if isinstance(value, list) and len(value) >= count:
            return value
        time.sleep(0.05)
    pytest.fail(f"it heard fewer than {count} answers")


def test_from_a_thread_of_its_own_it_runs_command_sessions_between_calls(
    launcher: ToolLauncher, package: Path
):
    asked: list[str] = []

    def on_initiative(text: str) -> JsonValue:
        asked.append(text)
        return {"result": f"ran {text}"}

    process = attach(launcher, package, on_initiative)
    try:
        assert heard(process, 1) == [{"result": "ran @send early hi"}]
        assert process.call("later", {"text": "@new guide"}, timeout=10).value is None
        assert heard(process, 2)[1] == {"result": "ran @new guide"}
    finally:
        process.detach()
    assert asked == ["@send early hi", "@new guide"]


def test_without_anyone_to_hear_it_its_initiative_is_refused(launcher: ToolLauncher, package: Path):
    process = attach(launcher, package, None)
    try:
        answer = heard(process, 1)[0]
        assert isinstance(answer, dict) and "error" in answer
    finally:
        process.detach()


GUIDE = '''
import threading

from allin.channel import command
from allin.decorators import reach, subprofile
from allin.fproc import Fproc


@subprofile
@reach(commands=["new", "send", "open", "close"], molds=["helper", "granter"])
class Guide(Fproc):
    """Brings the people of a channel to Sessions of its own."""

    def __init__(self):
        self.heard = []
        threading.Thread(target=self._greet, daemon=True).start()

    def act(self, text: str) -> None:
        """Ask for ``text`` on its own, once this call is over."""
        threading.Thread(target=self._act, args=(text,), daemon=True).start()

    def heard_so_far(self) -> list:
        return self.heard

    def _act(self, text):
        self.heard.append(command(text))

    def _greet(self):
        made = command("@new helper")
        self.heard.append(made)
        if "state" in made:
            sid = made["state"]["session_id"]
            self.heard.append(command(f"@send {sid} --outsider=chat:juan -- hola, soy Juan"))
'''

LONER = '''
import threading

from allin.channel import command
from allin.decorators import reach
from allin.fproc import Fproc


@reach(commands=["new"], molds=["helper"])
class Loner(Fproc):
    """Declares no Subperfil."""

    def __init__(self):
        self.heard = []

    def act(self, text: str) -> None:
        """Ask for ``text`` on its own, once this call is over."""
        threading.Thread(target=lambda: self.heard.append(command(text)), daemon=True).start()

    def heard_so_far(self) -> list:
        return self.heard
'''

RELAY = '''
import threading

from allin.channel import command
from allin.decorators import reach, subprofile
from allin.fproc import Fproc


@subprofile
@reach(commands=["new", "send"], molds=["helper", "tasked"])
class Relay(Fproc):
    """Relays what its channel says: on its own, or while it serves a call."""

    def __init__(self):
        self.heard = []

    def act(self, text: str) -> None:
        """Ask for ``text`` on its own, once this call is over."""
        threading.Thread(target=lambda: self.heard.append(command(text)), daemon=True).start()

    def heard_so_far(self) -> list:
        return self.heard

    def tell(self, sid: str, text: str) -> object:
        """Send a Session a Prompt while it serves this call."""
        return command(f"@send {sid} {text}")
'''

HELPER = """
functions = ["function:alpha", "function:beta", "function:giver"]
commands = ["send", "grant"]

[settings]
title = "helper"
"""

TOOL = '''
def {name}() -> str:
    """A tool."""
    return "{name}"
'''

GIVER = '''
from allin.channel import command
from allin.decorators import reach


@reach(commands=["grant"])
def giver(sid: str) -> object:
    """Declares Otorgar."""
    return command(f"@grant {sid} @send")
'''


@pytest.fixture
def main(tmp_path: Path) -> Iterator[MainHarness]:
    """Main with real descriptions, the approved fproc ``guide`` with its Moldes, and three
    approved fsources: ``alpha``, ``beta`` and ``giver``, which declares Otorgar."""
    harness = MainHarness(tmp_path)
    harness.describer = ToolLauncher(tmp_path, grace=1.0)
    harness.describer.start()
    functions = harness.profile.folder / "functions"
    guides = functions / "guides"
    (guides / "molds").mkdir(parents=True)
    (guides / "guide.py").write_text(GUIDE)
    (guides / "loner.py").write_text(LONER)
    (guides / "relay.py").write_text(RELAY)
    (guides / "molds" / "helper.toml").write_text(HELPER)
    (guides / "molds" / "other.toml").write_text(HELPER)
    (guides / "molds" / "tasked.toml").write_text(f'task = "Saluda."\n{HELPER}')
    for name in ("alpha", "beta"):
        (functions / name).mkdir()
        (functions / name / f"{name}.py").write_text(TOOL.format(name=name))
    (functions / "giver").mkdir()
    (functions / "giver" / "giver.py").write_text(GIVER)
    descriptions = Descriptions(tmp_path, harness.describer, harness.confinement)
    pieces = Quarantine(harness.profile.folder, descriptions)
    for package in ("guides", "alpha", "beta", "giver"):
        ref = PieceRef("function", package)
        pieces.approve(ref, pieces.piece(ref).hash)
    yield harness
    harness.describer.stop()


def guide_heard(main: MainHarness, name: str = "guide") -> list[JsonValue]:
    value = main.dispatcher.attached.attached_fproc(name).process.call("heard_so_far", {}, 10)
    return value.value if isinstance(value.value, list) else []


def hearing(main: MainHarness, count: int, name: str = "guide") -> Callable[[], None]:
    """A step: Main goes on handling what comes in until the fproc heard ``count`` answers to
    what it asked on its own; follow it with ``Until``."""

    def step() -> None:
        def wait() -> None:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and len(guide_heard(main, name)) < count:
                time.sleep(0.05)

        inbox = main.servers.inbox
        assert isinstance(inbox, DrainingInbox)
        worker = threading.Thread(target=wait)
        inbox.workers.append(worker)
        worker.start()

    return step


def act(
    main: MainHarness, text: str | Callable[[], str], name: str = "guide"
) -> Callable[[], None]:
    """A step: the fproc asks for ``text`` on its own, as when someone writes in its channel;
    ``text`` may be known only once the run is under way."""

    def step() -> None:
        asked = text if isinstance(text, str) else text()
        main.dispatcher.attached.attached_fproc(name).process.call("act", {"text": asked}, 10)

    return step


def subprofile_sessions(main: MainHarness, name: str = "guide") -> list[str]:
    path = main.profile.folder / "subprofiles" / f"{name}.json"
    return json.loads(path.read_text())["sessions"] if path.exists() else []


def test_an_fproc_creates_a_session_from_its_molde_and_sends_it_a_prompt_on_its_own(
    main: MainHarness,
):
    main.llm = FakeLlm(full_reply("", "hola, Juan"))
    heard: list[JsonValue] = []
    main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main)),
        settle=True,
    )
    made, sent = heard
    [sid] = subprofile_sessions(main)
    assert made == {"result": "ok", "state": {"session_id": sid, "place": "background"}}
    assert sent == {"result": "ok"}
    session = main.sessions.load(sid)
    assert session.settings.title == "helper"
    assert sid not in main.profiles.sessions(main.profile), "not in the Profile's list"
    assert main.windows(sid) == [], "born in the background"
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None
    [turn] = conversation.turns
    assert turn.origin.labels == ["human", "process:guide"]
    assert "hola, soy Juan" in str(main.llm.requests[0]["input"])


def test_an_fproc_without_a_subprofile_creates_no_session(main: MainHarness):
    heard: list[JsonValue] = []
    main.run(
        MainCommandEntered("/attach loner"),
        act(main, "@new helper", "loner"),
        hearing(main, 1, "loner"),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main, "loner")),
        settle=True,
    )
    [refused] = heard
    assert "no_subprofile" in str(refused)


def test_it_creates_sessions_only_from_the_moldes_its_reach_declares(main: MainHarness):
    heard: list[JsonValue] = []
    main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        act(main, "@new other"),
        hearing(main, 3),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main)),
        settle=True,
    )
    assert "mold_not_declared" in str(heard[2])
    assert len(subprofile_sessions(main)) == 1


def test_its_sessions_get_the_functions_of_its_molde_never_more_than_who_attached_it(
    main: MainHarness,
):
    jefa = main.create_session()
    jefa = main.sessions.save_commands(
        jefa, tuple(CommandName(c) for c in ("attach", "new", "send"))
    )
    jefa = main.sessions.save_functions(jefa, (PieceId("function:alpha"),))
    main.run(
        MainCommandEntered("/attach guide", from_session(jefa)),
        hearing(main, 2),
        TURNS_ENDED,
        MainCommandEntered("/attach guide second"),
        hearing(main, 2, "second"),
        TURNS_ENDED,
        settle=True,
    )
    [by_jefa] = subprofile_sessions(main)
    [by_human] = subprofile_sessions(main, "second")
    assert main.sessions.load(by_jefa).functions == (PieceId("function:alpha"),)
    assert main.sessions.load(by_human).functions == (
        PieceId("function:alpha"),
        PieceId("function:beta"),
    ), "never Otorgar, even if its Molde names it"
    assert main.sessions.load(by_human).commands == (CommandName("send"),)


def enter(
    main: MainHarness, text: Callable[[], str], origin: Callable[[], Origin] = lambda: HUMAN
) -> Callable[[], None]:
    """A step: the Command main ``text`` gives comes in with the Origin ``origin`` gives, both
    known only once the run is under way."""

    def step() -> None:
        inbox = main.servers.inbox
        assert inbox is not None
        inbox.put(MainCommandEntered(text(), origin()))

    return step


def heard_state(heard: list[JsonValue], index: int) -> str:
    """The id of the Session the ``index``-th answer it heard created."""
    made = heard[index]
    assert isinstance(made, dict)
    state = made["state"]
    assert isinstance(state, dict)
    return str(state["session_id"])


def test_a_prompt_of_the_fproc_waits_behind_a_humans_and_past_its_cap_it_hears_busy(
    main: MainHarness,
):
    x, b = main.create_session(), main.create_session()
    main.hold_turns = True
    heard: list[JsonValue] = []
    chat = main.run(
        InputReceived(x.id, "x1"),
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        InputReceived(b.id, "b1"),
        ended(x),
        act(main, "@new helper"),
        hearing(main, 3),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main)),
        act(main, lambda: f"@send {heard_state(heard, 2)} otra persona"),
        hearing(main, 4),
        TURNS_ENDED,
        act(main, lambda: f"@send {heard_state(heard, 0)} --outsider=chat:juan -- sigo"),
        hearing(main, 5),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main)[3:]),
        settle=True,
    )
    first = heard_state(heard, 0)
    started = [line for line in turn_lines(chat) if line.endswith("turn started")]
    assert started == [f"[{x.id}] turn started", f"[{b.id}] turn started"], "the human first"
    assert "subprofile_busy" in str(heard[3]), "one Session of its Subperfil at a time"
    assert heard[4] == {"result": "ok"}, "the one that already counts may get more"
    assert first in subprofile_sessions(main)


def test_it_acts_only_on_the_sessions_it_created(main: MainHarness):
    a = main.create_session()
    heard: list[JsonValue] = []
    main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        act(main, f"@send {a.id} hola"),
        hearing(main, 3),
        act(main, f"@open {a.id}"),
        hearing(main, 4),
        act(main, f"@close {a.id}"),
        hearing(main, 5),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main)),
        act(main, lambda: f"@close {heard_state(heard, 0)}"),
        hearing(main, 6),
        TURNS_ENDED,
        act(main, lambda: f"@open {heard_state(heard, 0)}"),
        hearing(main, 7),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main)[5:]),
        settle=True,
    )
    for refused in heard[2:5]:
        assert "outside_subprofile" in str(refused), refused
    sid = heard_state(heard, 0)
    assert heard[5] == {"result": "ok"}, "it closes its own"
    assert heard[6] == {"result": "ok", "state": {"place": "background", "attention": False}}
    assert main.windows(sid) == [], "it opens its own only in the background"
    assert main.windows(a.id) == []


def test_send_never_crosses_the_subprofile_but_a_human_does(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "uno"), full_reply("", "dos"))
    a = main.create_session()
    a = main.sessions.save_commands(a, (CommandName("send"),))
    heard: list[JsonValue] = []
    sid: list[str] = []
    chat = main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main)),
        lambda: sid.append(heard_state(heard, 0)),
        enter(main, lambda: f"/send {sid[0]} desde el Perfil", lambda: from_session(a)),
        enter(
            main,
            lambda: f"/send {a.id} desde el Subperfil",
            lambda: Origin(chain=(*HUMAN.chain, Link("process", "guide"), Link("session", sid[0]))),
        ),
        enter(main, lambda: f"/send {sid[0]} soy el humano"),
        TURNS_ENDED,
        settle=True,
    )
    crossed = [line for line in chat if "outside_subprofile" in line]
    assert len(crossed) == 2, "\n".join(chat[-12:])
    mine = main.sessions.load_conversation(a)
    assert mine is None or not mine.turns, "nothing came in from the Subperfil"
    assert sid[0] not in main.profiles.sessions(main.profile)
    conversation = main.sessions.load_conversation(main.sessions.load(sid[0]))
    assert conversation is not None
    assert [turn.origin.labels for turn in conversation.turns] == [
        ["human", "process:guide"],
        ["human"],
    ], "only a human crosses"


def test_an_outsiders_prompt_carries_the_fprocs_origin_and_never_y(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "hola, Juan"))
    a = main.create_session()
    a = main.sessions.save_commands(a, (CommandName("send"),))
    heard: list[JsonValue] = []
    chat = main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main)),
        act(main, lambda: f"@send -y {heard_state(heard, 0)} hazlo sin preguntar"),
        hearing(main, 3),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main)[2:]),
        MainCommandEntered(f"/send {a.id} --outsider=chat:ana -- soy Ana", from_session(a)),
        settle=True,
    )
    sid = heard_state(heard, 0)
    session = main.sessions.load(sid)
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None
    [turn] = conversation.turns
    assert turn.origin.labels == ["human", "process:guide"]
    assert turn.prompt == "[from attached process guide]\nhola, soy Juan", "never who wrote it"
    assert "chat:juan" not in str(main.llm.requests[0]["input"])
    assert turn.outsider == "chat:juan", "for the memory"
    [started] = [e.fields for e in main.session_log(session) if e.event == "turn_started"]
    assert started["outsider"] == "chat:juan"
    assert "human_only" in str(heard[2]), "never -y"
    assert any("outsider_not_from_process" in line for line in chat)


def started_sessions(chat: list[str]) -> list[str]:
    """The Sessions whose Turns started, in order."""
    return [line.split("]")[0][1:] for line in turn_lines(chat) if line.endswith("turn started")]


def at(main: MainHarness, now: float) -> Callable[[], None]:
    """A step: Main's clock reads ``now``, so what comes next arrives later."""
    return lambda: setattr(main, "now", now)


def relay_session(main: MainHarness, heard: list[JsonValue]) -> list[Callable[[], None] | Until]:
    """Steps: the fproc ``relay``, attached, creates a Session on its own, whose answer lands
    in ``heard``."""
    return [
        act(main, "@new helper", "relay"),
        hearing(main, 1, "relay"),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main, "relay")),
    ]


def test_a_prompt_of_its_initiative_waits_behind_a_humans_and_a_sessions_that_came_later(
    main: MainHarness,
):
    x, b, c = main.create_session(), main.create_session(), main.create_session()
    a = main.sessions.save_commands(main.create_session(), (CommandName("send"),))
    main.hold_turns = True
    heard: list[JsonValue] = []
    chat = main.run(
        InputReceived(x.id, "x1"),
        MainCommandEntered("/attach relay"),
        *relay_session(main, heard),
        act(main, lambda: f"@send {heard_state(heard, 0)} hola, soy Juan", "relay"),
        hearing(main, 2, "relay"),
        TURNS_ENDED,
        at(main, 1.0),
        InputReceived(b.id, "b1"),
        at(main, 2.0),
        MainCommandEntered(f"/send {c.id} desde A", from_session(a)),
        ended(x),
        ended(b),
        ended(c),
        settle=True,
    )
    sid = heard_state(heard, 0)
    assert started_sessions(chat) == [x.id, b.id, c.id, sid]
    [started] = [
        e.fields for e in main.session_log(main.sessions.load(sid)) if e.event == "turn_started"
    ]
    assert started["initiative"] is True


def test_the_task_of_a_molde_it_creates_on_its_own_waits_behind_a_humans(main: MainHarness):
    x, b = main.create_session(), main.create_session()
    main.hold_turns = True
    heard: list[JsonValue] = []
    chat = main.run(
        InputReceived(x.id, "x1"),
        MainCommandEntered("/attach relay"),
        act(main, "@new tasked", "relay"),
        hearing(main, 1, "relay"),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main, "relay")),
        at(main, 1.0),
        InputReceived(b.id, "b1"),
        ended(x),
        ended(b),
        settle=True,
    )
    sid = heard_state(heard, 0)
    assert started_sessions(chat) == [x.id, b.id, sid]
    [started] = [
        e.fields for e in main.session_log(main.sessions.load(sid)) if e.event == "turn_started"
    ]
    assert started["initiative"] is True


def test_a_prompt_a_method_sends_during_a_call_is_admitted_by_arrival(main: MainHarness):
    x, b = main.create_session(), main.create_session()
    a = main.sessions.save_commands(main.create_session(), (CommandName("send"),))
    main.hold_turns = True
    heard: list[JsonValue] = []
    chat = main.run(
        InputReceived(x.id, "x1"),
        MainCommandEntered(f"/attach relay --to={a.id}"),
        *relay_session(main, heard),
        at(main, 1.0),
        enter(
            main,
            lambda: f"/call relay tell sid={heard_state(heard, 0)} text=hola",
            lambda: from_session(a),
        ),
        lambda: calls(main),
        TURNS_ENDED,
        at(main, 2.0),
        InputReceived(b.id, "b1"),
        ended(x),
        settle=True,
    )
    sid = heard_state(heard, 0)
    assert started_sessions(chat) == [x.id, sid], "\n".join(chat[-12:])
    session = main.sessions.load(sid)
    [started] = [e.fields for e in main.session_log(session) if e.event == "turn_started"]
    assert "initiative" not in started
    sent = [e.fields for e in main.main_log() if e.fields.get("command") == f"/send {sid} hola"]
    assert [s["origin"] for s in sent] == [["human", f"session:{a.id}", "process:relay"]]


def test_its_initiatives_mark_stays_on_a_queued_prompt_through_a_profile_switch(
    main: MainHarness,
):
    x = main.create_session()
    main.profiles.create("tesis", main.profile)
    main.hold_turns = True
    heard: list[JsonValue] = []
    left: list[MainState] = []
    main.run(
        InputReceived(x.id, "x1"),
        MainCommandEntered("/attach relay"),
        *relay_session(main, heard),
        act(main, lambda: f"@send {heard_state(heard, 0)} hola, soy Juan", "relay"),
        hearing(main, 2, "relay"),
        TURNS_ENDED,
        MainCommandEntered("/profile switch tesis"),
        ended(x),
        lambda: left.append(MainStateStore(main.profile.state).load()),
        MainCommandEntered(f"/profile switch {main.profile.name}"),
        settle=True,
    )
    sid = heard_state(heard, 0)
    [kept] = left[0].queue
    assert (kept.session_id, kept.initiative) == (sid, True)
    [started] = [
        e.fields for e in main.session_log(main.sessions.load(sid)) if e.event == "turn_started"
    ]
    assert started["initiative"] is True, "back from the switch, it is still its initiative's"
