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
from allin.main.descriptions import Descriptions
from allin.main.events import MainCommandEntered
from allin.main.quarantine import PieceRef, Quarantine
from tests.llm_fakes import FakeLlm, full_reply
from tests.test_attached import from_session
from tests.test_dispatcher import TURNS_ENDED, DrainingInbox, MainHarness

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
    (guides / "molds" / "helper.toml").write_text(HELPER)
    (guides / "molds" / "other.toml").write_text(HELPER)
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


def act(main: MainHarness, text: str, name: str = "guide") -> Callable[[], None]:
    """A step: the fproc asks for ``text`` on its own, as when someone writes in its channel."""

    def step() -> None:
        main.dispatcher.attached.attached_fproc(name).process.call("act", {"text": text}, 10)

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
