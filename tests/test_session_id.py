"""Choosing and changing a Session's id (#59): ``/new --id=<id>`` and ``/session id``; the old
id stays as an alias that still reaches it."""

from pathlib import Path

import pytest

from allin.core.errors import SessionIdInvalid, SessionIdTaken, SessionStorageFailed
from allin.core.ids import SessionId, parse_session_id
from allin.core.settings import SessionSettings
from allin.ipc.messages import Close
from allin.main.events import DraftReceived, InputReceived, MainCommandEntered
from allin.main.sessions import Session, SessionStore
from allin.main.state import MainState, MainStateStore
from tests.llm_fakes import FakeLlm
from tests.origins import A_SESSION
from tests.test_compaction_turns import answers
from tests.test_dispatcher import ME, MainHarness, ended


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def results(main: MainHarness, command: str) -> list[str]:
    return [
        str(entry.fields["result"])
        for entry in main.main_log()
        if entry.event == "command" and str(entry.fields.get("command", "")).startswith(command)
    ]


def prompts(main: MainHarness, session_id: str) -> list[str]:
    loaded = main.sessions.load_conversation(main.sessions.load(session_id))
    return [] if loaded is None else [turn.prompt for turn in loaded.turns]


@pytest.mark.parametrize("text", ["tesis", "abcd", "a1b2c3d4e5f6", "zz99"])
def test_an_id_is_4_to_12_characters_of_a_z_and_0_9(text: str):
    assert parse_session_id(text) == text


@pytest.mark.parametrize("text", ["ab", "abc", "a1b2c3d4e5f6g", "Tesis", "te-sis", "tésis", ""])
def test_anything_else_is_no_id(text: str):
    assert parse_session_id(text) is None


def test_the_store_refuses_a_bad_or_taken_id_and_resolves_aliases(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    first = store.create(ME, SessionSettings(), "s", "tesis")
    with pytest.raises(SessionIdInvalid):
        store.create(ME, SessionSettings(), "s", "ab")
    with pytest.raises(SessionIdTaken):
        store.create(ME, SessionSettings(), "s", "tesis")
    renamed = store.rename(first, "final")
    assert store.load("tesis") == renamed == store.load("final")
    with pytest.raises(SessionIdTaken):  # an alias is taken too
        store.create(ME, SessionSettings(), "s", "tesis")
    again = store.rename(renamed, "otra")
    assert store.resolve("tesis") == store.resolve("final") == again.id == "otra"


def test_new_with_an_id_creates_that_session(main: MainHarness):
    main.run(
        MainCommandEntered("/new --id=tesis"),
        MainCommandEntered("/new --id=ab"),
        MainCommandEntered("/new --id=tesis"),
        MainCommandEntered("/new tesis"),
    )
    assert main.sessions.load("tesis").id == "tesis"
    ok, invalid, taken, bad = results(main, "/new")
    assert ok == "ok"
    assert invalid.startswith("error session_id_invalid")
    assert taken.startswith("error session_id_taken")
    assert bad.startswith("error invalid_arguments")
    assert [sid for sid, _ in main.opened] == ["tesis"]


def test_renaming_with_its_window_open_reopens_it_under_the_new_id(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        DraftReceived(session.id, ("a medias",)),
        MainCommandEntered(f"/session id {session.id} tesis"),
    )
    assert results(main, "/session id") == ["ok"]
    old_window, new_window = (link for _, link in main.opened)
    assert [sid for sid, _ in main.opened] == [session.id, "tesis"]
    assert Close() in old_window.sent
    assert any(getattr(m, "text", None) == "a medias" for m in new_window.sent)  # its Draft
    assert main.sessions.load("tesis").id == "tesis"
    assert not (main.root / session.id).exists() and (main.root / "tesis").is_dir()
    events = [entry.event for entry in main.session_log(main.sessions.load("tesis"))]
    # Its Log moved with it: what came before the rename is still there.
    assert events[:3] == ["window_opened", "window_closed", "session_renamed"]
    saved = main.state.saved[-1]
    assert list(saved.windows) == ["tesis"] and saved.drafts == {"tesis": ["a medias"]}
    assert main.profiles.sessions(main.profile) in ([], ["tesis"])


def test_a_send_to_the_old_id_reaches_the_renamed_session(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(*answers(1))
    main.run(
        MainCommandEntered(f"/session id {session.id} tesis"),
        MainCommandEntered(f"/send {session.id} hola"),
        settle=True,
    )
    assert prompts(main, "tesis") == ["hola"]


def test_after_a_restart_it_opens_under_the_new_id_and_the_alias_still_works(
    main: MainHarness,
):
    session = main.create_session()
    main.run(MainCommandEntered(f"/open {session.id}"))
    main.run(MainCommandEntered(f"/session id {session.id} tesis"))
    main.llm = FakeLlm(*answers(1))
    # Main reopened its Window as tesis, which sends what is typed there.
    main.run(InputReceived(SessionId("tesis"), "de vuelta"), settle=True)
    assert [sid for sid, _ in main.opened][-1] == "tesis"
    assert prompts(main, session.id) == ["de vuelta"]  # the alias names it
    main.run(MainCommandEntered(f"/open {session.id}"))
    assert results(main, "/open")[-1].startswith("error window_already_open")


def test_only_a_human_renames_a_session(main: MainHarness):
    session = main.create_session()
    main.run(MainCommandEntered(f"/session id {session.id} tesis", origin=A_SESSION))
    [result] = results(main, "/session id")
    assert result.startswith("error human_only")
    assert main.sessions.load(session.id).id == session.id


def test_with_a_turn_active_renaming_is_refused(main: MainHarness):
    session = main.create_session()
    main.hold_turns = True
    main.run(
        InputReceived(session.id, "largo"),
        MainCommandEntered(f"/session id {session.id} tesis"),
        ended(session),
        settle=True,
    )
    [result] = results(main, "/session id")
    assert result.startswith("error turn_active")
    assert main.sessions.load(session.id).id == session.id


def test_a_bad_or_taken_new_id_changes_nothing(main: MainHarness):
    one, two = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/session id {one.id} ab"),
        MainCommandEntered(f"/session id {one.id} {two.id}"),
        MainCommandEntered(f"/session id {one.id}"),
    )
    invalid, taken, missing = results(main, "/session id")
    assert invalid.startswith("error session_id_invalid")
    assert taken.startswith("error session_id_taken")
    assert missing.startswith("error invalid_arguments")
    assert main.sessions.load(one.id).id == one.id


def test_every_profile_lists_it_under_its_new_id(main: MainHarness):
    session = main.create_session()
    main.run(MainCommandEntered(f"/open {session.id}"), MainCommandEntered("/profile new otro"))
    other = main.profiles.open("otro")
    main.profiles.add_session(other, session.id)
    main.run(MainCommandEntered(f"/session id {session.id} tesis"))
    for profile in (main.profile, other):
        listed = main.profiles.sessions(profile)
        assert session.id not in listed
    assert "tesis" in main.profiles.sessions(other)


def test_a_late_message_from_the_old_window_still_reaches_its_log(main: MainHarness):
    session: Session = main.create_session()
    main.run(MainCommandEntered(f"/session id {session.id} tesis"))
    main.sessions.log(session.id).append("probe")
    assert main.session_log(main.sessions.load("tesis"))[-1].event == "probe"


def test_a_rename_that_fails_half_way_leaves_the_session_as_it_was(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    session = store.create(ME, SessionSettings(), "s", "tesis")
    workspace = store.workspace(session.id)
    workspace.mkdir(parents=True)
    store.workspace(SessionId("final")).mkdir()  # the workspace cannot move there
    (store.workspace(SessionId("final")) / "x").touch()
    with pytest.raises(SessionStorageFailed):
        store.rename(session, "final")
    assert store.load("tesis") == session
    assert workspace.is_dir() and not (tmp_path / "sessions" / "final").exists()


def test_the_active_profiles_mainstate_is_left_to_the_running_main(main: MainHarness):
    session = main.create_session()
    main.run(MainCommandEntered(f"/open {session.id}"), MainCommandEntered("/profile new otro"))
    other = main.profiles.open("otro")
    main.profiles.add_session(other, session.id)
    MainStateStore(other.state).save(MainState(drafts={session.id: ["en otro"]}))
    main.run(MainCommandEntered(f"/session id {session.id} tesis"))
    assert MainStateStore(other.state).load().drafts == {SessionId("tesis"): ["en otro"]}
    assert session.id not in main.state.load().windows
