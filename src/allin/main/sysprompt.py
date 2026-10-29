"""Rendering a Sysprompt template from ``sysprompts/`` with a Session's Settings."""

from pathlib import Path
from string import Template

from allin.core.errors import SyspromptInvalid
from allin.core.settings import SessionSettings


def render_sysprompt(folder: Path, settings: SessionSettings) -> str:
    path = (folder / settings.sysprompt).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise SyspromptInvalid(name=settings.sysprompt, reason="outside the sysprompts folder")
    try:
        template = Template(path.read_text(encoding="utf-8"))
        return template.substitute(
            assistant_name=settings.assistant_name, username=settings.username
        )
    except OSError as error:
        raise SyspromptInvalid(name=settings.sysprompt, reason=str(error)) from error
    except (KeyError, ValueError) as error:  # unknown $name, or a stray "$"
        raise SyspromptInvalid(
            name=settings.sysprompt, reason=f"bad placeholder: {error}"
        ) from error
