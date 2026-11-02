"""Moldes (#112, ADR-0011, ADR-0015): named recipes for a specialized Session.

A Molde is a piece of the Profile, ``molds/<name>.toml``, and is used only as approved
(ADR-0013). It gives the new Session its Sysprompt, its Ajustes over the Session template, its
functions and its list of Command sessions, and, if it wants, an initial history (messages that
open the Conversation and are no Turns) and an initial task, sent as a ``/send`` with the
Origin of whoever creates the Session. What it leaves out the new Session takes as ``/new``
would give it::

    sysprompt = "coder.txt"
    functions = ["function:files"]
    commands = ["send"]
    task = "Lee el README y resume el proyecto."

    [settings]
    title = "coder"
    sampling = { temperature = 0.2 }

    [[history]]
    role = "user"
    content = "Contesta siempre en español."
"""

import tomllib
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from allin.core.errors import MoldInvalid, PieceInQuarantine
from allin.core.hashing import content_hash
from allin.core.ids import CommandName, PieceId
from allin.core.settings import SessionSettings
from allin.main.conversation import Item
from allin.main.quarantine import PieceRef, Quarantine
from allin.main.sessions import MoldVersion


class HistoryMessage(BaseModel, frozen=True):
    """A message of an initial history: it opens the Conversation, as no Turn does."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)

    def item(self) -> Item:
        return {"role": self.role, "content": self.content}


class Mold(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    sysprompt: str | None = None  # a Sysprompt of the Profile; none: the template's
    # Ajustes over the Session template, nested as ``/set session.*`` names them.
    settings: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    functions: tuple[PieceId, ...] | None = None  # none: as ``/new`` gives them
    commands: tuple[CommandName, ...] | None = None  # none: the root's
    history: tuple[HistoryMessage, ...] = ()
    task: str | None = Field(default=None, min_length=1)

    def settings_over(self, template: SessionSettings) -> SessionSettings:
        """The template with this Molde's Ajustes and Sysprompt on it."""
        merged = _merged(template.model_dump(mode="json"), dict(self.settings))
        if self.sysprompt is not None:
            merged["sysprompt"] = self.sysprompt
        return SessionSettings.model_validate(merged)


@dataclass(frozen=True)
class LoadedMold:
    """An approved Molde as read."""

    ref: PieceRef
    recipe: Mold
    hash: str

    @property
    def version(self) -> MoldVersion:
        return MoldVersion(piece=self.ref.id, hash=self.hash)

    def settings(self, template: SessionSettings) -> SessionSettings:
        """The new Session's Ajustes: the template with the Molde's on it."""
        try:
            return self.recipe.settings_over(template)
        except ValidationError as error:
            raise MoldInvalid(mold=self.ref.name, reason=_problem(error)) from error


def parse_mold(name: str, text: str) -> Mold:
    """The Molde ``text`` describes; raises ``MoldInvalid`` saying why it is not one."""
    try:
        return Mold.model_validate(tomllib.loads(text))
    except tomllib.TOMLDecodeError as error:
        raise MoldInvalid(mold=name, reason=str(error)) from error
    except ValidationError as error:
        raise MoldInvalid(mold=name, reason=_problem(error)) from error


def load_mold(quarantine: Quarantine, ref: PieceRef) -> LoadedMold:
    """The approved Molde ``ref`` as its approved version says; one in quarantine is never
    used (ADR-0013)."""
    name = ref.name
    piece = quarantine.piece(ref)
    if piece.state != "approved":
        raise PieceInQuarantine(piece=ref.id)
    try:
        data = piece.path.read_bytes()
    except OSError as error:
        raise MoldInvalid(mold=name, reason=str(error)) from error
    if content_hash(data) != piece.hash:  # changed since it was approved: not this one
        raise PieceInQuarantine(piece=ref.id)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MoldInvalid(mold=name, reason="not UTF-8 text") from error
    return LoadedMold(ref, parse_mold(name, text), piece.hash)


def _merged(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in over.items():
        kept = merged.get(key)
        if isinstance(kept, dict) and isinstance(value, dict):
            merged[key] = _merged(cast(dict[str, Any], kept), cast(dict[str, Any], value))
        else:
            merged[key] = value
    return merged


def _problem(error: ValidationError) -> str:
    first = error.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    return f"{where}: {first['msg']}" if where else first["msg"]
