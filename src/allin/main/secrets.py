"""Secretos (#124, ADR-0026): named values only a human gives, such as a bot's token, kept in
the active Profile under the name of the instance they are for, whether or not it has a
Subperfil.

Each instance's live in ``<profile>/secrets/<name>.json``, which only the user reads. Main hands
them to the Proceso adherido each time it is attached, over its channel. Nothing else reads
that folder: the Log, the Conversations, the Settings, Kits, Moldes and copies of the Profile
all name what they carry instead of copying the Profile's folder whole. No error ever holds a
value.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import NewType

from pydantic import BaseModel, Field, ValidationError, field_validator

from allin.core.errors import ProfileStorageFailed, SecretInvalid, SecretNotFound
from allin.core.files import write_atomically
from allin.core.ids import AttachedName, parse_attached_name

FOLDER = "secrets"
VALUE_MAX = 8192  # characters: a token or a webhook's URL, never a file

SecretName = NewType("SecretName", str)

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")


def parse_secret_name(text: str) -> SecretName | None:
    return SecretName(text) if _NAME.fullmatch(text) is not None else None


def _value_problem(value: str) -> str | None:
    """What is wrong with a value, never quoting it: empty, too long or with control
    characters; ``None`` for one Main keeps."""
    if not value:
        return "empty"
    if len(value) > VALUE_MAX:
        return f"longer than {VALUE_MAX} characters"
    if not value.isprintable():
        return "control characters"
    return None


def check_value(value: str) -> None:
    """Raises ``SecretInvalid`` for a value Main does not keep; its reason never holds it."""
    problem = _value_problem(value)
    if problem is not None:
        raise SecretInvalid(reason=problem)


class _Record(BaseModel, frozen=True):
    values: dict[SecretName, str] = Field(repr=False)

    @field_validator("values")
    @classmethod
    def _as_set_keeps_them(cls, values: dict[SecretName, str]) -> dict[SecretName, str]:
        """A file edited by hand holds only what ``/secret set`` would have kept."""
        for name, value in values.items():
            if parse_secret_name(name) is None or _value_problem(value) is not None:
                msg = "a Secreto /secret set would refuse"
                raise ValueError(msg)
        return values


class Secrets:
    """The Secretos of the active Profile; only Main's dispatcher changes them."""

    def __init__(self, profile_folder: Callable[[], Path]) -> None:
        self._profile_folder = profile_folder

    def pinned(self) -> "Secrets":
        """The Secretos of the Profile active now, which stay its after Main switches."""
        folder = self._profile_folder()
        return Secrets(lambda: folder)

    def names(self) -> dict[AttachedName, list[SecretName]]:
        """The names of each instance's Secretos, never their values; instances in name order."""
        folder = self._folder()
        if not folder.is_dir():
            return {}
        parsed = (parse_attached_name(path.stem) for path in folder.glob("*.json"))
        names = {name: sorted(self.of(name)) for name in sorted(n for n in parsed if n)}
        return {name: secrets for name, secrets in names.items() if secrets}

    def of(self, instance: AttachedName) -> dict[SecretName, str]:
        """The Secretos the instance gets when it is attached, by name."""
        path = self._path(instance)
        try:
            return dict(_Record.model_validate_json(path.read_bytes()).values)
        except FileNotFoundError:
            return {}
        except (OSError, ValidationError) as error:
            # Only its type: a ValidationError's text quotes the file, values and all.
            raise ProfileStorageFailed(path=str(path), reason=type(error).__name__) from None

    def set(self, instance: AttachedName, name: SecretName, value: str) -> None:
        check_value(value)
        self._save(instance, {**self.of(instance), name: value})

    def remove(self, instance: AttachedName, name: SecretName) -> None:
        values = self.of(instance)
        if name not in values:
            raise SecretNotFound(instance=instance, name=name)
        del values[name]
        self._save(instance, values)

    def _folder(self) -> Path:
        return self._profile_folder() / FOLDER

    def _path(self, instance: AttachedName) -> Path:
        return self._folder() / f"{instance}.json"

    def _save(self, instance: AttachedName, values: dict[SecretName, str]) -> None:
        path = self._path(instance)
        try:
            if not values:
                path.unlink(missing_ok=True)
                return
            path.parent.mkdir(mode=0o700, exist_ok=True)
            path.parent.chmod(0o700)
            write_atomically(path, _Record(values=values).model_dump_json(indent=2), mode=0o600)
        except OSError as error:
            raise ProfileStorageFailed(path=str(path), reason=str(error)) from error
