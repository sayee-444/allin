"""Rendering a Sysprompt template from the active Profile's ``sysprompts/`` with a Session's
Settings."""

from pathlib import Path
from string import Template

from allin.core.errors import SyspromptInvalid
from allin.core.settings import SessionSettings
from allin.main.quarantine import check_sysprompt


def render_sysprompt(
    folder: Path, settings: SessionSettings, name: str | None = None, default: str | None = None
) -> str:
    """The Session's Sysprompt, or the one called ``name`` (such as ``compact.sysprompt``);
    ``default`` is the template used when that file does not exist. Raises
    ``PieceInQuarantine`` for one that is not approved as it is now."""
    name = settings.sysprompt if name is None else name
    path = (folder / name).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise SyspromptInvalid(name=name, reason="outside the sysprompts folder")
    try:
        if default is not None and not path.exists():
            text = default  # in AllIn's code: nothing to approve
        else:
            data = path.read_bytes()
            check_sysprompt(folder, name, data)  # one in quarantine is never used (ADR-0013)
            text = data.decode("utf-8")
        return Template(text).substitute(
            assistant_name=settings.assistant_name, username=settings.username
        )
    except OSError as error:
        raise SyspromptInvalid(name=name, reason=str(error)) from error
    except UnicodeDecodeError as error:
        raise SyspromptInvalid(name=name, reason="not UTF-8 text") from error
    except (KeyError, ValueError) as error:  # unknown $name, or a stray "$"
        raise SyspromptInvalid(name=name, reason=f"bad placeholder: {error}") from error
