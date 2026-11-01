import json
from pathlib import Path

import pytest

from allin.core.settings import Sampling
from allin.ipc.messages import ChatAppend
from allin.main.conversation import Conversation, Turn
from allin.main.events import InputReceived, MainCommandEntered
from allin.main.settings_files import MainSettingsFiles
from tests.llm_fakes import FakeLlm, full_reply
from tests.test_dispatcher import SETTINGS, MainHarness, chat_messages, server


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def files(main: MainHarness) -> MainSettingsFiles:
    return MainSettingsFiles(main.settings_dir)


def snapshot(folder: Path) -> dict[str, str]:
    return {path.name: path.read_text() for path in sorted(folder.iterdir())}


def test_set_changes_the_session_template_and_the_next_new_copies_it(main: MainHarness):
    before = main.create_session()
    main.run(
        MainCommandEntered("/set session.sampling.temperature=0.3"),
        MainCommandEntered("/new"),
    )
    [(created, _)] = main.opened
    assert main.sessions.load(created).settings.sampling.temperature == 0.3
    assert main.sessions.load(before.id).settings.sampling.temperature is None
    assert files(main).load().current.session.sampling.temperature == 0.3


def test_set_keeps_its_change_across_a_restart(main: MainHarness):
    main.run(MainCommandEntered("/set session.username=bea"))
    main.run(MainCommandEntered("/new"))
    [(created, _)] = main.opened
    assert main.sessions.load(created).settings.username == "bea"


def test_set_takes_several_keys_and_quoted_values(main: MainHarness):
    main.run(MainCommandEntered('/set session.assistant_name="Mi IA" parallel=2'))
    current = files(main).load().current
    assert current.session.assistant_name == "Mi IA" and current.parallel == 2


def test_set_alone_shows_mains_current_settings(main: MainHarness):
    chat = main.run(MainCommandEntered("/set parallel=2"), MainCommandEntered("/set"))
    assert "parallel=2" in chat
    assert "session.username=ana" in chat


def test_set_default_changes_only_the_defaults(main: MainHarness):
    main.run(MainCommandEntered("/set --default parallel=3"))
    loaded = files(main).load()
    assert loaded.default.parallel == 3
    assert loaded.current.parallel is None


def test_set_default_then_reset_leaves_the_current_equal_to_the_defaults(main: MainHarness):
    main.run(
        MainCommandEntered("/set session.model=other parallel=1"),
        MainCommandEntered("/set --default session.username=bea"),
        MainCommandEntered("/reset"),
    )
    loaded = files(main).load()
    assert loaded.current == loaded.default
    assert loaded.current.session.username == "bea"
    assert loaded.current.parallel is None


@pytest.mark.parametrize(
    "command",
    [
        "/set nope=1",
        "/set parallel=0",
        "/set parallel=2 session.sampling.top_p=9",
        "/set parallel",
        "/set --default nope=1",
        "/set session.sysprompt=missing.txt",
    ],
)
def test_an_unknown_key_or_invalid_value_is_an_error_and_touches_no_file(
    main: MainHarness, command: str
):
    before = snapshot(main.settings_dir)
    chat = main.run(MainCommandEntered(command))
    assert any(line.startswith(f"{command}: error") for line in chat)
    assert snapshot(main.settings_dir) == before


def test_parallel_set_while_main_runs_lowers_the_turns_at_once(main: MainHarness):
    sessions = [main.create_session() for _ in range(2)]
    main.hold_turns = True
    main.server = server(slots=2)
    main.run(
        MainCommandEntered("/set parallel=1"),
        *(InputReceived(session.id, "hi") for session in sessions),
        settle=True,
    )
    assert len(main.held) == 1


def test_a_broken_settings_file_does_not_stop_main_and_says_which_key_fails(main: MainHarness):
    broken = SETTINGS.model_dump()
    broken["parallel"] = "many"
    (main.settings_dir / "current.json").write_text(json.dumps(broken))
    chat = main.run(MainCommandEntered("/new"))
    assert any("settings_file_invalid" in line and "'parallel'" in line for line in chat)
    assert len(main.opened) == 1, "Main runs on with the defaults"


def test_session_set_changes_only_that_session_and_survives_a_restart(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.run(MainCommandEntered(f"/session set {a.id} sampling.temperature=0.7"))
    main.run()
    assert main.sessions.load(a.id).settings.sampling == Sampling(temperature=0.7)
    assert main.sessions.load(b.id).settings.sampling == Sampling()
    assert files(main).load().current.session.sampling == Sampling()


def test_session_set_reaches_the_next_turn_and_its_open_window(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(full_reply("", "ok"))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/session set {session.id} username=bea sampling.top_p=0.5"),
        InputReceived(session.id, "hi"),
        settle=True,
    )
    [request] = main.llm.requests
    assert request["top_p"] == 0.5
    [window] = main.windows(session.id)
    prompts = [m for m in chat_messages(window) if isinstance(m, ChatAppend) and m.role == "user"]
    assert [prompt.label for prompt in prompts] == ["bea"]


def test_session_shows_a_sessions_settings(main: MainHarness):
    session = main.create_session(sampling=Sampling(top_p=0.9))
    chat = main.run(MainCommandEntered(f"/session {session.id}"))
    assert f"[{session.id}] sampling.top_p=0.9" in chat


@pytest.mark.parametrize("assignment", ["nope=1", "session.username=bea", "sampling.top_p=2"])
def test_session_set_with_a_bad_key_or_value_is_an_error_and_changes_nothing(
    main: MainHarness, assignment: str
):
    session = main.create_session()
    chat = main.run(MainCommandEntered(f"/session set {session.id} {assignment}"))
    assert any("error" in line for line in chat)
    assert main.sessions.load(session.id) == session


def test_session_set_on_an_unknown_session_is_an_error(main: MainHarness):
    chat = main.run(MainCommandEntered("/session set zzzzzz username=bea"))
    assert any("session_not_found" in line for line in chat)


def test_a_new_sysprompt_or_name_is_rendered_again_while_there_is_no_turn(main: MainHarness):
    (main.sysprompts / "other.txt").write_text("Other $assistant_name.")
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/session set {session.id} sysprompt=other.txt assistant_name=Eva")
    )
    conversation = main.sessions.load_conversation(session)
    assert conversation == Conversation(sysprompt="Other Eva.")


def test_the_sysprompt_of_a_session_with_a_turn_cannot_change(main: MainHarness):
    (main.sysprompts / "other.txt").write_text("Other.")
    session = main.create_session()
    kept = Conversation(sysprompt="I am AllIn; you are ana.").with_turn(Turn(prompt="hi", steps=[]))
    main.sessions.save_conversation(session, kept)
    chat = main.run(MainCommandEntered(f"/session set {session.id} sysprompt=other.txt"))
    assert any("sysprompt_locked" in line for line in chat)
    assert main.sessions.load(session.id) == session
    assert main.sessions.load_conversation(session) == kept


def test_a_name_change_after_a_turn_keeps_the_rendered_sysprompt(main: MainHarness):
    session = main.create_session()
    kept = Conversation(sysprompt="I am AllIn; you are ana.").with_turn(Turn(prompt="hi", steps=[]))
    main.sessions.save_conversation(session, kept)
    main.run(MainCommandEntered(f"/session set {session.id} username=bea"))
    assert main.sessions.load(session.id).settings.username == "bea"
    assert main.sessions.load_conversation(session) == kept


def test_session_set_without_keys_shows_the_settings_and_writes_nothing(main: MainHarness):
    session = main.create_session()
    metadata = main.root / session.id / "session.json"
    before = metadata.read_text()
    chat = main.run(MainCommandEntered(f"/session set {session.id}"))
    assert f"[{session.id}] model=allin" in chat
    assert metadata.read_text() == before


def test_the_sysprompt_cannot_change_while_the_sessions_first_turn_runs(main: MainHarness):
    (main.sysprompts / "other.txt").write_text("Other.")
    session = main.create_session()
    main.hold_turns = True
    chat = main.run(
        InputReceived(session.id, "hi"),
        MainCommandEntered(f"/session set {session.id} sysprompt=other.txt"),
        settle=True,
    )
    assert any("sysprompt_locked" in line for line in chat)
    assert main.sessions.load(session.id) == session
