"""The Session's earlier Prompts reach its Window as it opens, from its Log (#22)."""

from pathlib import Path

from allin.ipc.messages import PromptHistory
from allin.main.chat_rebuild import prompt_history
from allin.main.events import InputReceived, MainCommandEntered, WindowClosed
from allin.main.log import LogEntry
from tests.llm_fakes import FakeLlm, full_reply
from tests.test_dispatcher import MainHarness


def entry(event: str, **fields: object) -> LogEntry:
    return LogEntry.model_validate({"ts": "2026-11-01T00:00:00Z", "event": event, **fields})


def test_the_history_is_every_prompt_sent_as_it_was_typed():
    log = [
        entry("prompt", text="hola"),
        entry("turn_finished", turn=0),
        entry("prompt", text="lee .allin/adjuntos/a1/x.txt", typed="lee @./x.txt @top_k=5"),
        entry("prompt_returned", texts=["otra"]),
    ]
    assert prompt_history(log) == ["hola", "lee @./x.txt @top_k=5"]


def test_a_reopened_window_gets_the_history_of_its_session(tmp_path: Path):
    main = MainHarness(tmp_path)
    session = main.create_session()
    main.llm = FakeLlm(full_reply("", "uno"), full_reply("", "dos"))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "primero"),
        InputReceived(session.id, "segundo @temperature=0.2"),
        WindowClosed(session.id, returncode=0),
        MainCommandEntered(f"/open {session.id}"),
        settle=True,
    )
    first, reopened = main.windows(session.id)
    assert not any(isinstance(m, PromptHistory) for m in first.sent)
    [history] = [m for m in reopened.sent if isinstance(m, PromptHistory)]
    assert history.prompts == ["primero", "segundo @temperature=0.2"]
