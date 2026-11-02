"""``/help`` (#23): built from the registry of Command mains, so a new one shows by itself."""

from pathlib import Path

import pytest

from allin.main.events import MainCommandEntered
from tests.test_dispatcher import MainHarness


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def test_every_command_main_declares_a_one_line_description(main: MainHarness):
    main.run()
    entries = main.dispatcher.commands.help_entries()
    assert entries, "the registry is empty"
    for name, command in entries.items():
        assert command.summary.strip(), f"{name} declares no description"
        assert "\n" not in command.summary, f"{name}'s description is more than one line"
        for param in command.args:
            assert param in (*command.params, *command.optional), f"{name}: {param} is no param"


def test_help_lists_every_registered_command_main(main: MainHarness):
    chat = main.run(MainCommandEntered("/help"))
    names = list(main.dispatcher.commands.help_entries())
    for name in names:
        assert any(line.startswith(f"{name} ") for line in chat), f"{name} is not listed"
    assert "/help" in names and "/key" in names


@pytest.mark.parametrize("asked", ["/send", "send"])
def test_help_about_a_command_shows_its_usage_and_arguments(main: MainHarness, asked: str):
    chat = main.run(MainCommandEntered(f"/help {asked}"))
    assert "usage: /send [-y] <sid> [--attach=<path>]… [--<param>=<value>]… [--] <text>" in chat
    assert any(line.startswith("  <sid>: ") for line in chat)
    assert any(line.startswith("  <text>: ") for line in chat)


def test_help_about_an_unknown_command_is_an_error(main: MainHarness):
    chat = main.run(MainCommandEntered("/help /nope"))
    assert any("unknown_command" in line and "/nope" in line for line in chat)
