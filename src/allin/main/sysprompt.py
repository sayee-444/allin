"""Rendering a Sysprompt template from the active Profile's ``sysprompts/`` with a Session's
Settings."""

from pathlib import Path
from string import Template

from allin.core.errors import SyspromptInvalid
from allin.core.settings import SessionSettings


def render_sysprompt(
    folder: Path, settings: SessionSettings, name: str | None = None, default: str | None = None
) -> str:
    """The Session's Sysprompt, or the one called ``name`` (such as ``compact.sysprompt``);
    ``default`` is the template used when that file does not exist."""
    name = settings.sysprompt if name is None else name
    path = (folder / name).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise SyspromptInvalid(name=name, reason="outside the sysprompts folder")
    try:
        text = default if default is not None and not path.exists() else path.read_text("utf-8")
        return Template(text).substitute(
            assistant_name=settings.assistant_name, username=settings.username
        )
    except OSError as error:
        raise SyspromptInvalid(name=name, reason=str(error)) from error
    except (KeyError, ValueError) as error:  # unknown $name, or a stray "$"
        raise SyspromptInvalid(name=name, reason=f"bad placeholder: {error}") from error
