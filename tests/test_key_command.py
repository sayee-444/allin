"""``/key`` (#57): who changes the Atajos, where they are kept and who gets them."""

from pathlib import Path

import pytest

from allin.ipc.messages import KeyTable
from allin.main.events import MainCommandEntered
from allin.ui.screen import KeyBindings
from tests.test_dispatcher import MainHarness


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def results(main: MainHarness) -> list[str]:
    return [str(e.fields["result"]) for e in main.main_log() if e.event == "command"]


def tables(main: MainHarness, session_id: str) -> list[dict[str, str]]:
    return [m.bindings for w in main.windows(session_id) for m in w.sent if isinstance(m, KeyTable)]


def test_key_lists_mains_and_the_templates_marking_the_changed_ones(main: MainHarness):
    chat = main.run(
        MainCommandEntered("/key --template alt+enter send"), MainCommandEntered("/key")
    )
    assert "main: enter send" in chat
    assert "template: enter send" in chat
    assert "template: alt+enter send *" in chat


def test_another_key_to_send_in_a_session_then_enter_off(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/key {session.id} alt+enter send"),
        MainCommandEntered(f"/key {session.id} enter off"),
        settle=True,
    )
    assert results(main)[-2:] == ["ok", "ok"]
    assert main.sessions.load(session.id).settings.keys == {"alt+enter": "send", "enter": "off"}
    first, second = tables(main, session.id)
    assert first["alt+enter"] == "send" and first["enter"] == "send"
    assert second["alt+enter"] == "send" and "enter" not in second


def test_a_taken_key_is_an_error_that_says_which_unless_replaced(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/key {session.id} up older"),
        MainCommandEntered(f"/key {session.id} up older --replace"),
        settle=True,
    )
    taken, replaced = results(main)[-2:]
    assert taken.startswith("error shortcut_taken") and "bound='up'" in taken
    assert replaced == "ok"
    assert main.sessions.load(session.id).settings.keys == {"up": "older"}


def test_the_last_key_to_send_cannot_go(main: MainHarness):
    session = main.create_session()
    main.run(MainCommandEntered(f"/key {session.id} enter off"), settle=True)
    assert results(main)[-1].startswith("error last_send_key")
    assert main.sessions.load(session.id).settings.keys == {}


def test_key_from_a_session_is_refused(main: MainHarness):
    session = main.create_session()
    main.run(MainCommandEntered(f"/key {session.id} f5 older", origin="session"), settle=True)
    assert results(main)[-1].startswith("error human_only")
    assert main.sessions.load(session.id).settings.keys == {}


def test_a_shortcut_of_mains_chat_reaches_its_screen_and_its_settings(main: MainHarness):
    main.run(MainCommandEntered("/key f5 /new"), settle=True)
    assert main.settings_files.load().current.keys == {"f5": "/new"}
    shown = [u for u in main.main_screen if isinstance(u, KeyBindings)]
    assert shown[-1].table["f5"] == "/new"


def test_a_window_takes_no_command_until_f2(main: MainHarness):
    session = main.create_session()
    main.run(MainCommandEntered(f"/key {session.id} f5 /new"), settle=True)
    assert results(main)[-1].startswith("error shortcut_invalid")


def test_new_sessions_inherit_the_templates_shortcuts(main: MainHarness):
    main.run(MainCommandEntered("/key --template ctrl+p older"), MainCommandEntered("/new"))
    [(session_id, window)] = main.opened
    assert main.sessions.load(session_id).settings.keys == {"ctrl+p": "older"}
    [table] = [m.bindings for m in window.sent if isinstance(m, KeyTable)]
    assert table["ctrl+p"] == "older"


def test_a_key_the_window_cannot_tell_apart_says_why(main: MainHarness):
    main.run(MainCommandEntered("/key ctrl+up older"), settle=True)
    assert results(main)[-1].startswith("error key_name_invalid")


def test_a_mistyped_key_before_a_command_with_arguments_says_why(main: MainHarness):
    main.run(MainCommandEntered("/key ctlr+b /send abc123 hola"), settle=True)
    assert results(main)[-1].startswith("error key_name_invalid")
