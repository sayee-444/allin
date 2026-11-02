import shutil
from pathlib import Path

import pytest

from allin.core.errors import PieceInQuarantine, SyspromptInvalid
from allin.core.settings import SessionSettings
from allin.main.quarantine import approve_installed
from allin.main.sysprompt import render_sysprompt
from tests.pieces import approve_sysprompt

SETTINGS = SessionSettings(assistant_name="AllIn", username="ana", sysprompt="t.txt")


@pytest.fixture
def sysprompts(tmp_path: Path) -> Path:
    """A Profile's ``sysprompts/``: its Approvals live beside it."""
    folder = tmp_path / "profile" / "sysprompts"
    folder.mkdir(parents=True)
    return folder


def test_the_template_is_filled_with_the_session_settings(sysprompts: Path):
    (sysprompts / "t.txt").write_text("I am $assistant_name, talking with $username.")
    approve_sysprompt(sysprompts, "t.txt")
    assert render_sysprompt(sysprompts, SETTINGS) == "I am AllIn, talking with ana."


@pytest.mark.parametrize("template", ["Hi $nobody", "Cost: 5$"])
def test_a_template_that_cannot_be_rendered_is_an_error(sysprompts: Path, template: str):
    (sysprompts / "t.txt").write_text(template)
    approve_sysprompt(sysprompts, "t.txt")
    with pytest.raises(SyspromptInvalid):
        render_sysprompt(sysprompts, SETTINGS)


def test_one_never_approved_or_changed_since_is_in_quarantine_and_not_used(sysprompts: Path):
    (sysprompts / "t.txt").write_text("New, $assistant_name.")
    with pytest.raises(PieceInQuarantine) as new:
        render_sysprompt(sysprompts, SETTINGS)
    assert new.value.fields == {"piece": "sysprompt:t.txt"}
    approve_sysprompt(sysprompts, "t.txt")
    assert render_sysprompt(sysprompts, SETTINGS) == "New, AllIn."
    (sysprompts / "t.txt").write_text("Changed, $assistant_name.")  # by anyone: an editor, a tool
    with pytest.raises(PieceInQuarantine):
        render_sysprompt(sysprompts, SETTINGS)


@pytest.mark.parametrize("name", ["missing.txt", "../t.txt"])
def test_a_sysprompt_outside_the_folder_or_missing_is_an_error(tmp_path: Path, name: str):
    (tmp_path / "t.txt").write_text("x")
    with pytest.raises(SyspromptInvalid):
        render_sysprompt(tmp_path / "sub", SETTINGS.model_copy(update={"sysprompt": name}))


@pytest.mark.parametrize("name", ["default.txt", "allin.txt"])
def test_the_shipped_sysprompts_render_once_a_profile_has_them_as_installed(
    sysprompts: Path, name: str
):
    installed = Path(__file__).parents[1] / "sysprompts"
    shutil.copy(installed / name, sysprompts / name)
    approve_installed(sysprompts.parent, installed)
    text = render_sysprompt(sysprompts, SETTINGS.model_copy(update={"sysprompt": name}))
    assert "AllIn" in text and "ana" in text
