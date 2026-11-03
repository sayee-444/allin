"""Sharing a Proceso adherido without stepping on each other (#115, ADR-0012): the revision
each Session saw, which a Rewind forgets; a method's progress where its call was made; and a
call that does not end after its timeout, which takes the process down with a Notice. The
processes run for real on this kernel; the LLM is fake."""

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from openai.types.responses import ResponseStreamEvent

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.settings import ToolSettings
from allin.main.events import InputReceived, MainCommandEntered
from allin.main.sessions import Session
from tests.llm_fakes import FakeLlm, full_reply, tool_call
from tests.test_attached import approve, events, notices
from tests.test_attached_methods import calls
from tests.test_dispatcher import TURNS_ENDED, DrainingInbox, MainHarness, Until
from tests.test_session_commands import outputs

pytestmark = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

TEXT = '''
import time

from allin.decorators import writes
from allin.fproc import Fproc


class Text(Fproc):
    """A text Sessions share."""

    def __init__(self):
        self.text = ""

    def read(self) -> str:
        """The text."""
        return self.text

    @writes
    def write(self, text: str) -> str:
        """Write the text anew."""
        self.text = text
        return text

    def long(self, steps: int) -> int:
        """Something slow, that tells how it goes."""
        for step in range(steps):
            time.sleep(0.1)
            self.progress(f"step {step + 1} of {steps}")
        return steps

    def stubborn(self) -> None:
        """Never ends."""
        time.sleep(600)
'''


@pytest.fixture
def main(tmp_path: Path) -> Iterator[MainHarness]:
    """Main with the approved fproc ``text``, a call past its timeout ending within 0.3 s."""
    harness = MainHarness(tmp_path)
    harness.describer = ToolLauncher(tmp_path, grace=1.0)
    harness.describer.start()
    harness.threaded_turns = True
    folder = harness.profile.folder / "functions" / "counting"
    folder.mkdir(parents=True)
    (folder / "text.py").write_text(TEXT)
    approve(harness, harness.describer)
    yield harness
    harness.describer.stop()


def crashed(main: MainHarness) -> list[dict[str, object]]:
    return events(main, "attached_crashed")


def wait_for_crash(main: MainHarness) -> None:
    """Have Main wait, handling what comes in, until a Proceso adherido crashed (or 10 s)."""

    def watch() -> None:
        ends = time.monotonic() + 10
        while not crashed(main) and time.monotonic() < ends:
            time.sleep(0.05)

    inbox = main.servers.inbox
    assert isinstance(inbox, DrainingInbox)
    watcher = threading.Thread(target=watch)
    watcher.start()
    inbox.workers.append(watcher)


type Replies = list[list[ResponseStreamEvent]]


def read() -> Replies:
    return [tool_call("text_read", "{}"), full_reply("", "leído")]


def write(text: str) -> Replies:
    return [tool_call("text_write", json.dumps({"text": text})), full_reply("", "escrito")]


def last_output(main: MainHarness, request: int) -> object:
    return outputs(main.llm.requests[request])[-1]


def test_two_sessions_read_one_writes_and_the_other_reads_again_before_it_writes(
    main: MainHarness,
):
    a, b = main.create_session(), main.create_session()
    main.llm = FakeLlm(*read(), *read(), *write("de A"), *write("de B"), *read(), *write("de B"))
    main.run(
        MainCommandEntered(f"/attach text --to={a.id},{b.id}"),
        InputReceived(a.id, "lee"),
        TURNS_ENDED,
        InputReceived(b.id, "lee"),
        TURNS_ENDED,
        InputReceived(a.id, "escribe"),
        TURNS_ENDED,
        InputReceived(b.id, "escribe"),
        TURNS_ENDED,
        InputReceived(b.id, "lee"),
        TURNS_ENDED,
        InputReceived(b.id, "escribe"),
        settle=True,
    )
    assert last_output(main, 5) == "de A"
    refused = str(last_output(main, 7))
    assert "attached_changed" in refused and "read it again" in refused
    assert last_output(main, 9) == "de A", "B's refused write never ran"
    assert last_output(main, 11) == "de B"


def test_a_rewind_forgets_what_the_session_read_since(main: MainHarness):
    a = main.create_session()
    main.llm = FakeLlm(*read(), full_reply("", "nada"), *read(), *write("de A"))
    main.run(
        MainCommandEntered(f"/attach text --to={a.id}"),
        InputReceived(a.id, "lee"),
        TURNS_ENDED,
        MainCommandEntered("/call text write text=humano"),
        lambda: calls(main),
        TURNS_ENDED,
        InputReceived(a.id, "nada"),
        TURNS_ENDED,
        InputReceived(a.id, "lee otra vez"),
        TURNS_ENDED,
        MainCommandEntered(f"/rewind {a.id} 2"),
        InputReceived(a.id, "escribe"),
        settle=True,
    )
    assert last_output(main, 4) == "humano", "it read the human's change"
    assert "attached_changed" in str(last_output(main, 6)), "it forgot it read the change"


def test_a_methods_progress_shows_where_its_call_was_made(main: MainHarness):
    chat = main.run(
        MainCommandEntered("/attach text"),
        MainCommandEntered("/call text long steps=3"),
        lambda: calls(main),
        TURNS_ENDED,
        settle=True,
    )
    shown = [line for line in chat if "text.long: step" in line]
    assert shown == [f"text.long: step {n} of 3" for n in (1, 2, 3)]


def test_a_call_that_does_not_end_after_its_timeout_kills_the_process_with_a_notice(
    main: MainHarness,
):
    a: Session = main.create_session(tools=ToolSettings(timeout=0.3))
    main.llm = FakeLlm(tool_call("text_stubborn", "{}"), full_reply("", "colgado"))
    chat = main.run(
        MainCommandEntered("/set attached.release=0.3"),
        MainCommandEntered(f"/attach text --to={a.id}"),
        InputReceived(a.id, "cuelga"),
        TURNS_ENDED,
        lambda: wait_for_crash(main),
        Until(lambda: bool(crashed(main))),
        settle=True,
    )
    assert "tool_timed_out" in str(last_output(main, 1))
    assert any("/set attached.release=0.3: ok" in line for line in chat), "\n".join(chat[:8])
    [ended] = crashed(main)
    assert "stubborn" in str(ended["killed"])
    assert "text crashed" in notices(main)
