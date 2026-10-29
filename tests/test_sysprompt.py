from pathlib import Path

import pytest

from allin.core.errors import SyspromptInvalid
from allin.core.settings import SessionSettings
from allin.main.sysprompt import render_sysprompt

SETTINGS = SessionSettings(assistant_name="AllIn", username="ana", sysprompt="t.txt")


def test_the_template_is_filled_with_the_session_settings(tmp_path: Path):
    (tmp_path / "t.txt").write_text("I am $assistant_name, talking with $username.")
    assert render_sysprompt(tmp_path, SETTINGS) == "I am AllIn, talking with ana."


@pytest.mark.parametrize("template", ["Hi $nobody", "Cost: 5$"])
def test_a_template_that_cannot_be_rendered_is_an_error(tmp_path: Path, template: str):
    (tmp_path / "t.txt").write_text(template)
    with pytest.raises(SyspromptInvalid):
        render_sysprompt(tmp_path, SETTINGS)


@pytest.mark.parametrize("name", ["missing.txt", "../t.txt"])
def test_a_sysprompt_outside_the_folder_or_missing_is_an_error(tmp_path: Path, name: str):
    (tmp_path / "t.txt").write_text("x")
    with pytest.raises(SyspromptInvalid):
        render_sysprompt(tmp_path / "sub", SETTINGS.model_copy(update={"sysprompt": name}))


@pytest.mark.parametrize("name", ["default.txt", "allin.txt"])
def test_the_shipped_sysprompts_render(name: str):
    folder = Path(__file__).parents[1] / "sysprompts"
    text = render_sysprompt(folder, SETTINGS.model_copy(update={"sysprompt": name}))
    assert "AllIn" in text and "ana" in text
