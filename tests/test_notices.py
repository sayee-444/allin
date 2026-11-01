from pathlib import Path

import pytest

from allin.core.ids import SessionId
from allin.core.settings import SessionSettings
from allin.ipc.messages import BoxShow
from allin.main.events import InputReceived, MainCommandEntered, WindowConnected
from allin.main.notices import load_notice_types
from allin.notices import NoticeEvent
from allin.ui.screen import HideBox, ShowBox
from tests.llm_fakes import FakeLlm, full_reply
from tests.test_dispatcher import MainHarness

# Shows on every Step, with the Context it fills.
EVERY_STEP = """
from allin.notices import notice

@notice("step", title="Step done", text="{tokens} tokens, {missing} stays", every=1)
def step(event, params):
    return {"tokens": event.context}
"""
BROKEN = "raise RuntimeError('broken on purpose')\n"
EVENT = NoticeEvent(SessionId("abc123"), context=10, limit=100)


def write(folder: Path, name: str, source: str) -> None:
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(source)


def shown(types_folder: Path, settings: SessionSettings) -> list[tuple[str, str]]:
    notes: list[tuple[str, str]] = []
    failed = load_notice_types(types_folder).types.happened(
        EVENT, settings, lambda _kind, title, text: notes.append((title, text))
    )
    assert failed == []
    return notes


def test_a_module_in_notices_declares_a_type_with_its_settings(tmp_path: Path):
    write(tmp_path, "step.py", EVERY_STEP)
    loaded = load_notice_types(tmp_path)
    assert loaded.problems == []
    keys = {key.name: key.default for key in loaded.types.setting_keys()}
    assert keys == {
        "notices.step.enabled": True,
        "notices.step.title": "Step done",
        "notices.step.text": "{tokens} tokens, {missing} stays",
        "notices.step.every": 1,
    }


def test_a_notice_shows_its_title_and_text_with_its_variables(tmp_path: Path):
    write(tmp_path, "step.py", EVERY_STEP)
    assert shown(tmp_path, SessionSettings()) == [("Step done", "10 tokens, {missing} stays")]


def test_the_sessions_settings_change_its_title_and_text_or_turn_it_off(tmp_path: Path):
    write(tmp_path, "step.py", EVERY_STEP)
    own = SessionSettings(notices={"step": {"title": "Mine", "text": "now {tokens}"}})
    assert shown(tmp_path, own) == [("Mine", "now 10")]
    off = SessionSettings(notices={"step": {"enabled": False}})
    assert shown(tmp_path, off) == []


def test_a_module_that_fails_to_load_is_told_and_the_others_load(tmp_path: Path):
    write(tmp_path, "a_broken.py", BROKEN)
    write(tmp_path, "step.py", EVERY_STEP)
    loaded = load_notice_types(tmp_path)
    [problem] = loaded.problems
    assert problem.fields["notice"] == "a_broken.py"
    assert len(loaded.types.setting_keys()) == 4


def test_no_notices_folder_means_no_types(tmp_path: Path):
    loaded = load_notice_types(tmp_path / "missing")
    assert loaded.types.setting_keys() == [] and loaded.problems == []


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    write(harness.notices_dir, "step.py", EVERY_STEP)
    return harness


def test_a_notice_set_off_by_a_step_shows_in_its_window_and_mains_chat(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(full_reply("", "ok"))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        WindowConnected(session.id),
        InputReceived(session.id, "hi"),
        settle=True,
    )
    [window] = main.windows(session.id)
    [box] = [message for message in window.sent if isinstance(message, BoxShow)]
    assert box.kind == "notice" and box.title == "Step done"
    assert [update.box.title for update in main.main_screen if isinstance(update, ShowBox)] == [
        f"[{session.id}] Step done"
    ]


def test_session_set_turns_a_notice_off_and_set_changes_the_template(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(full_reply("", "ok"))
    main.run(
        MainCommandEntered(f"/session set {session.id} notices.step.enabled=false"),
        MainCommandEntered("/set session.notices.step.title=Hecho"),
        InputReceived(session.id, "hi"),
        settle=True,
    )
    assert not any(isinstance(update, ShowBox) for update in main.main_screen)
    stored = main.sessions.load(session.id).settings.notices
    assert stored == {"step": {"enabled": False}}
    assert main.settings.session.notices == {"step": {"title": "Hecho"}}


def test_an_unknown_notice_key_is_an_error(main: MainHarness):
    session = main.create_session()
    chat = main.run(MainCommandEntered(f"/session set {session.id} notices.nope.enabled=false"))
    assert any("error unknown_setting" in line for line in chat)


def test_the_settings_of_each_notice_type_are_shown(main: MainHarness):
    session = main.create_session()
    chat = main.run(MainCommandEntered(f"/session {session.id}"))
    assert f"[{session.id}] notices.step.enabled=True" in chat


def test_a_notice_that_fires_again_replaces_the_one_still_shown(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(full_reply("", "ok"), full_reply("", "ok"))
    main.run(InputReceived(session.id, "one"), InputReceived(session.id, "two"), settle=True)
    shown_ids = [u.box.box_id for u in main.main_screen if isinstance(u, ShowBox)]
    hidden_ids = [u.box_id for u in main.main_screen if isinstance(u, HideBox)]
    assert len(shown_ids) == 2 and hidden_ids == shown_ids[:1]
