"""Secretos (#124, ADR-0026): named values only a human gives, such as a bot's token. Main keeps
them in the Profile under the instance's name and hands them to its Proceso adherido, over its
channel, each time it is attached. No list shows a value, and none reaches the Log, a
Conversation, a Kit or a copy of the Profile."""

import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pydantic import JsonValue

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.ids import BoxId
from allin.ipc.messages import ChatAppend, InputRestore
from allin.main.descriptions import Descriptions
from allin.main.events import (
    BoxAnswered,
    DraftReceived,
    InputReceived,
    MainCommandEntered,
    SessionCommandAsked,
    SessionCommandResult,
)
from allin.main.quarantine import PieceRef, Quarantine
from allin.ui.boxes import Box
from allin.ui.screen import ShowBox
from tests.test_attached import from_session
from tests.test_dispatcher import MainHarness

VALUE = "xoxb-1234-very-secret"
FINGERPRINT = hashlib.sha256(VALUE.encode()).hexdigest()
TOKEN: dict[str, JsonValue] = {"name": "token"}

needs_landlock = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

KEEPER = '''
import hashlib
import os
from pathlib import Path

from allin.fproc import Fproc


class Keeper(Fproc):
    """Keeps a bot's token."""

    def fingerprint(self, name: str) -> object:
        """The SHA-256 of its Secreto ``name``, or None if it has none."""
        value = self.secret(name)
        return None if value is None else hashlib.sha256(value.encode()).hexdigest()

    def found_besides(self) -> list:
        """Where else its token could be read: its environment and the files of its folder."""
        value = self.secret("token") or "-"
        found = [key for key, each in os.environ.items() if value in each]
        for path in Path(".").rglob("*"):
            if path.is_file() and value.encode() in path.read_bytes():
                found.append(str(path))
        return found
'''


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def secret_boxes(main: MainHarness) -> list[Box]:
    """The boxes that took a Secreto's value in Main's Chat."""
    shown = [update.box for update in main.main_screen if isinstance(update, ShowBox)]
    return [box for box in shown if box.kind == "secret"]


def files_with(root: Path, value: str) -> list[Path]:
    """Every file under ``root`` whose bytes hold ``value``."""
    return [
        path for path in root.rglob("*") if path.is_file() and value.encode() in path.read_bytes()
    ]


def test_a_human_gives_its_value_in_a_box_and_no_list_shows_it(main: MainHarness):
    chat = main.run(
        MainCommandEntered("/secret set bot token"),
        BoxAnswered(BoxId("b1"), None, VALUE),
        MainCommandEntered("/secret set bot webhook"),
        BoxAnswered(BoxId("b2"), None, "https://hook.example/abc"),
        MainCommandEntered("/secret set other key"),
        BoxAnswered(BoxId("b3"), None, "k"),
        MainCommandEntered("/secret"),
        MainCommandEntered("/secret list bot"),
    )
    first = secret_boxes(main)[0]
    assert first.title == "Secret token of bot"
    assert VALUE not in first.text
    assert "secret token of bot: set; it reaches bot from its next attach" in chat
    assert chat[-6:] == [
        "secrets: 3",
        "  bot: token, webhook",
        "  other: key",
        "/secret: ok",
        "bot: token, webhook",
        "/secret list bot: ok",
    ]
    assert all(VALUE not in line for line in chat)


def test_its_value_is_in_no_file_but_its_own_which_only_the_user_reads(main: MainHarness):
    main.run(MainCommandEntered("/secret set bot token"), BoxAnswered(BoxId("b1"), None, VALUE))
    own = main.profile.folder / "secrets" / "bot.json"
    assert files_with(main.root.parent, VALUE) == [own]
    assert own.stat().st_mode & 0o777 == 0o600
    assert own.parent.stat().st_mode & 0o777 == 0o700


def test_a_box_left_empty_sets_nothing(main: MainHarness):
    chat = main.run(
        MainCommandEntered("/secret set bot token"),
        BoxAnswered(BoxId("b1"), None, None),
        MainCommandEntered("/secret"),
    )
    assert "secret token of bot: not set, the box was left empty" in chat
    assert "secrets: 0" in chat


def test_a_human_removes_one_and_an_unknown_one_is_an_error(main: MainHarness):
    chat = main.run(
        MainCommandEntered("/secret set bot token"),
        BoxAnswered(BoxId("b1"), None, VALUE),
        MainCommandEntered("/secret remove bot token"),
        MainCommandEntered("/secret remove bot token"),
        MainCommandEntered("/secret"),
    )
    assert "secret token of bot: removed" in chat
    assert any("secret_not_found" in line and "token" in line for line in chat)
    assert "secrets: 0" in chat
    assert files_with(main.root.parent, VALUE) == []


@pytest.mark.parametrize(
    "value", ["tab\tinside", "x" * 8193], ids=["control character", "too long"]
)
def test_a_value_with_control_characters_or_too_long_is_refused_without_showing_it(
    main: MainHarness, value: str
):
    chat = main.run(
        MainCommandEntered("/secret set bot token"), BoxAnswered(BoxId("b1"), None, value)
    )
    assert any("secret_invalid" in line for line in chat)
    assert all(value not in line for line in chat)
    assert files_with(main.root.parent, value) == []


@pytest.mark.parametrize(
    "values", [{"to/ken": VALUE}, {"token": f"{VALUE}\n"}], ids=["name", "value"]
)
def test_a_file_edited_by_hand_that_set_would_refuse_is_an_error_that_shows_no_value(
    main: MainHarness, values: dict[str, str]
):
    folder = main.profile.folder / "secrets"
    folder.mkdir()
    (folder / "bot.json").write_text(json.dumps({"values": values}))
    chat = main.run(MainCommandEntered("/secret"))
    assert any("profile_storage_failed" in line for line in chat)
    assert all(VALUE not in line for line in chat)


@pytest.mark.parametrize(
    "command",
    [
        "/secret set",
        "/secret set bot",
        "/secret set Bot token",
        "/secret set bot to/ken",
        "/secret x",
    ],
)
def test_a_wrong_name_or_action_shows_its_usage_and_asks_nothing(main: MainHarness, command: str):
    chat = main.run(MainCommandEntered(command))
    assert any("invalid_arguments" in line and "/secret set" in line for line in chat)
    assert secret_boxes(main) == []


@pytest.mark.parametrize(
    "command", ["/secret", "/secret set bot token", "/secret remove bot token"]
)
def test_from_a_session_nobody_sets_lists_or_removes_one(main: MainHarness, command: str):
    session = main.create_session()
    chat = main.run(MainCommandEntered(command, from_session(session)))
    assert any("human_only" in line for line in chat)
    assert secret_boxes(main) == []


def test_the_box_shows_in_mains_chat_alone_and_no_window_answers_it(main: MainHarness):
    session = main.create_session()
    chat = main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered("/secret set bot token"),
        BoxAnswered(BoxId("b1"), False, window=session.id),
        BoxAnswered(BoxId("b1"), None, VALUE),
        MainCommandEntered("/secret"),
    )
    [window] = main.windows(session.id)
    assert all("box" not in type(message).__name__.lower() for message in window.sent)
    assert "secrets: 1" in chat


def test_a_value_written_on_the_command_line_reaches_neither_the_log_nor_the_chat(
    main: MainHarness, tmp_path: Path
):
    chat = main.run(MainCommandEntered(f"/secret set bot token {VALUE}"))
    assert any("invalid_arguments" in line for line in chat)
    assert all(VALUE not in line for line in chat)
    assert files_with(tmp_path, VALUE) == []


# Typed as @secret in a Window (#155): /secret is Main's Chat's alone


def test_typed_as_at_secret_in_a_window_it_points_to_main_chat_and_keeps_four_words(
    main: MainHarness, tmp_path: Path
):
    session = main.create_session()
    chat = main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, f"  @secret set bot token {VALUE}"),
        MainCommandEntered("/exit"),  # Main keeps its Drafts on disk
        settle=True,
    )
    [window] = main.windows(session.id)
    errors = [m.text for m in window.sent if isinstance(m, ChatAppend) and m.role == "error"]
    [error] = errors
    assert error.startswith("@secret set bot token …: secret_in_main_chat_only")
    assert "/secret set <instance> <name>" in error
    assert all(VALUE not in str(message) for message in window.sent)
    assert not any(isinstance(message, InputRestore) for message in window.sent)
    assert main.state.load().drafts == {}
    assert all(VALUE not in line for line in chat)
    assert files_with(tmp_path, VALUE) == []
    assert secret_boxes(main) == []


def test_asked_as_at_secret_by_a_tool_it_is_refused_and_logs_four_words(
    main: MainHarness, tmp_path: Path
):
    session = main.create_session()
    answers: list[SessionCommandResult] = []

    def answer(result: SessionCommandResult) -> bool:
        answers.append(result)
        return True

    asked = f"@secret set bot token {VALUE}"
    main.run(SessionCommandAsked(session.id, asked, from_session(session), answer), settle=True)
    [result] = answers
    assert result.error is not None and result.error.startswith("secret_in_main_chat_only")
    [logged] = [e.fields for e in main.main_log() if e.event == "session_command"]
    assert logged["command"] == "@secret set bot token …"
    assert files_with(tmp_path, VALUE) == []


def test_a_draft_that_starts_with_at_secret_is_not_kept(main: MainHarness, tmp_path: Path):
    # Main writes the Drafts to disk after each event, while the human may still be typing.
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        DraftReceived(session.id, ("hola", f"@secret set bot token {VALUE}")),
        MainCommandEntered("/exit"),
        settle=True,
    )
    assert main.state.load().drafts == {session.id: ["hola"]}
    assert files_with(tmp_path, VALUE) == []


def test_at_secret_with_no_value_is_refused_too_and_kept_whole(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "@secret"),
        settle=True,
    )
    [window] = main.windows(session.id)
    [error] = [m.text for m in window.sent if isinstance(m, ChatAppend) and m.role == "error"]
    assert error.startswith("@secret: secret_in_main_chat_only")


# Written in Main's Chat as @secret, or sent to a Session with /send (#178)


def logged_commands(main: MainHarness) -> list[JsonValue]:
    return [entry.fields["command"] for entry in main.main_log() if entry.event == "command"]


@pytest.mark.parametrize("window_open", [False, True])
def test_sent_as_at_secret_from_mains_chat_it_reaches_no_draft_and_logs_four_words(
    main: MainHarness, tmp_path: Path, window_open: bool
):
    session = main.create_session()
    opened = [MainCommandEntered(f"/open {session.id}")] if window_open else []
    chat = main.run(
        *opened,
        MainCommandEntered(f"/send {session.id} @secret set bot token {VALUE}"),
        MainCommandEntered("/exit"),  # Main keeps its Drafts on disk
        settle=True,
    )
    [refused] = [line for line in chat if "secret_in_main_chat_only" in line]
    assert f"/send {session.id} @secret set bot token …: error" in refused
    assert "/secret set <instance> <name>" in refused
    assert f"/send {session.id} @secret set bot token …" in logged_commands(main)
    for window in main.windows(session.id):
        assert all(VALUE not in str(message) for message in window.sent)
        assert not any(isinstance(message, InputRestore) for message in window.sent)
    assert main.state.load().drafts == {}
    assert all(VALUE not in line for line in chat)
    assert files_with(tmp_path, VALUE) == []


def test_sent_as_at_secret_with_reply_to_it_logs_four_words_of_it_too(
    main: MainHarness, tmp_path: Path
):
    session, other = main.create_session(), main.create_session()
    sent = f"/send {session.id} --reply-to={other.id} @secret set bot token {VALUE}"
    main.run(MainCommandEntered(sent), MainCommandEntered("/exit"), settle=True)
    assert f"/send {session.id} --reply-to={other.id} @secret set bot token …" in logged_commands(
        main
    )
    assert main.state.load().drafts == {}
    assert files_with(tmp_path, VALUE) == []


def test_another_command_session_sent_from_mains_chat_still_goes_back_to_its_input(
    main: MainHarness,
):
    session = main.create_session()
    main.run(MainCommandEntered(f"/send {session.id} @rewind 1"), settle=True)
    assert main.state.load().drafts == {session.id: ["@rewind 1"]}
    assert f"/send {session.id} @rewind 1" in logged_commands(main)


def test_typed_as_at_secret_in_mains_chat_it_shows_the_usage_and_keeps_four_words(
    main: MainHarness, tmp_path: Path
):
    chat = main.run(
        MainCommandEntered(f"@secret set bot token {VALUE}"),
        MainCommandEntered("/exit"),
        settle=True,
    )
    [refused] = [line for line in chat if "secret_in_main_chat_only" in line]
    assert refused.startswith("@secret set bot token …: error")
    assert "/secret set <instance> <name>" in refused
    assert "@secret set bot token …" in logged_commands(main)
    assert all(VALUE not in line for line in chat)
    assert files_with(tmp_path, VALUE) == []
    assert secret_boxes(main) == []


def test_another_unknown_command_in_mains_chat_is_still_kept_whole(main: MainHarness):
    chat = main.run(MainCommandEntered("@rewind one two three four"))
    assert any(
        line.startswith("@rewind one two three four: error unknown_command") for line in chat
    )


def test_it_goes_to_the_profile_it_was_asked_in_though_main_switched_meanwhile(
    main: MainHarness,
):
    chat = main.run(
        MainCommandEntered("/profile new other"),
        MainCommandEntered("/secret set bot token"),
        MainCommandEntered("/profile switch other"),
        BoxAnswered(BoxId("b1"), None, VALUE),
        MainCommandEntered("/secret"),
    )
    assert chat[-2:] == ["secrets: 0", "/secret: ok"]
    profiles = main.profile.folder.parent
    assert files_with(profiles, VALUE) == [main.profile.folder / "secrets" / "bot.json"]


@pytest.fixture
def keeper(tmp_path: Path) -> Iterator[MainHarness]:
    """Main with real descriptions and the approved fproc ``keeper``."""
    harness = MainHarness(tmp_path)
    harness.describer = ToolLauncher(tmp_path, grace=1.0)
    harness.describer.start()
    folder = harness.profile.folder / "functions" / "keepers"
    folder.mkdir(parents=True)
    (folder / "keeper.py").write_text(KEEPER)
    descriptions = Descriptions(tmp_path, harness.describer, harness.confinement)
    pieces = Quarantine(harness.profile.folder, descriptions)
    ref = PieceRef("function", "keepers")
    pieces.approve(ref, pieces.piece(ref).hash)
    yield harness
    harness.describer.stop()


def calling(
    main: MainHarness,
    method: str,
    seen: list[JsonValue],
    arguments: dict[str, JsonValue] | None = None,
    instance: str = "keeper",
) -> Callable[[], None]:
    """A step: call ``method`` of the Proceso adherido ``instance`` and keep what it returned."""

    def step() -> None:
        process = main.dispatcher.attached.attached_fproc(instance).process
        seen.append(process.call(method, arguments or {}, timeout=10).value)

    return step


@needs_landlock
def test_set_once_it_reaches_the_fproc_each_time_it_is_attached_until_removed(
    keeper: MainHarness,
):
    seen: list[JsonValue] = []
    keeper.run(
        MainCommandEntered("/secret set keeper token"),
        BoxAnswered(BoxId("b1"), None, VALUE),
        MainCommandEntered("/attach keeper"),
        calling(keeper, "fingerprint", seen, TOKEN),
        calling(keeper, "fingerprint", seen, {"name": "other"}),
        MainCommandEntered("/detach keeper"),
        MainCommandEntered("/attach keeper"),
        calling(keeper, "fingerprint", seen, TOKEN),
        MainCommandEntered("/secret remove keeper token"),
        MainCommandEntered("/detach keeper"),
        MainCommandEntered("/attach keeper"),
        calling(keeper, "fingerprint", seen, TOKEN),
        settle=True,
    )
    assert seen == [FINGERPRINT, None, FINGERPRINT, None]


@needs_landlock
def test_it_is_the_instances_and_another_name_of_the_same_fproc_never_gets_it(
    keeper: MainHarness,
):
    seen: list[JsonValue] = []
    keeper.run(
        MainCommandEntered("/secret set keeper token"),
        BoxAnswered(BoxId("b1"), None, VALUE),
        MainCommandEntered("/attach keeper other"),
        calling(keeper, "fingerprint", seen, TOKEN, instance="other"),
        settle=True,
    )
    assert seen == [None]


@needs_landlock
def test_it_never_travels_by_the_environment_and_lands_in_no_file_but_its_own(
    keeper: MainHarness, tmp_path: Path
):
    seen: list[JsonValue] = []
    keeper.run(
        MainCommandEntered("/secret set keeper token"),
        BoxAnswered(BoxId("b1"), None, VALUE),
        MainCommandEntered("/attach keeper"),
        calling(keeper, "found_besides", seen),
        calling(keeper, "fingerprint", seen, TOKEN),
        MainCommandEntered("/detach keeper"),
        settle=True,
    )
    assert seen == [[], FINGERPRINT]
    assert files_with(tmp_path, VALUE) == [keeper.profile.folder / "secrets" / "keeper.json"]


def test_no_copy_of_the_profile_no_kit_and_no_new_profile_carries_it(
    main: MainHarness, tmp_path: Path
):
    chat = main.run(
        MainCommandEntered("/secret set bot token"),
        BoxAnswered(BoxId("b1"), None, VALUE),
        MainCommandEntered(f"/profile export default {tmp_path / 'copy'}"),
        MainCommandEntered(f"/profile import {tmp_path / 'copy'} imported"),
        MainCommandEntered(f"/kit export {tmp_path / 'kit'}"),
        MainCommandEntered("/profile new other"),
    )
    assert not any("error" in line for line in chat), chat
    assert all((tmp_path / folder).is_dir() for folder in ("copy", "kit"))
    assert files_with(tmp_path, VALUE) == [main.profile.folder / "secrets" / "bot.json"]


@needs_landlock
def test_its_secret_accessor_is_no_method_anyone_calls(keeper: MainHarness):
    chat = keeper.run(
        MainCommandEntered("/secret set keeper token"),
        BoxAnswered(BoxId("b1"), None, VALUE),
        MainCommandEntered("/attach keeper"),
        MainCommandEntered("/call keeper secret name=token"),
        MainCommandEntered("/call keeper"),
        MainCommandEntered("/detach keeper"),
    )
    assert any("attached_method_not_found" in line and "secret" in line for line in chat)
    methods = [line.strip() for line in chat if line.startswith("  ")]
    assert [line.partition("(")[0] for line in methods] == ["fingerprint", "found_besides"]
    assert all(VALUE not in line for line in chat)


@needs_landlock
def test_started_again_because_its_reach_changed_it_gets_them_again(keeper: MainHarness):
    seen: list[JsonValue] = []
    source = keeper.profile.folder / "functions" / "keepers" / "keeper.py"

    def widen() -> None:
        text = source.read_text().replace(
            "class Keeper(Fproc):", '@reach(read=["home"])\nclass Keeper(Fproc):'
        )
        source.write_text("from allin.decorators import reach\n" + text)

    keeper.run(
        MainCommandEntered("/secret set keeper token"),
        BoxAnswered(BoxId("b1"), None, VALUE),
        MainCommandEntered("/attach keeper"),
        widen,
        MainCommandEntered("/piece show function:keepers"),
        MainCommandEntered("/piece approve function:keepers"),
        calling(keeper, "fingerprint", seen, TOKEN),
        settle=True,
    )
    restarted = [e.fields["restarted"] for e in keeper.main_log() if e.event == "attached"]
    assert restarted == [False, True]
    assert seen == [FINGERPRINT]
