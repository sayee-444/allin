"""Main's Settings on disk: the current ones and the defaults, each in its own JSON file.

They live in the ``settings/`` folder of each Profile (ADR-0010).
A file that cannot be read never leaves Main with half its Settings: the whole version falls
back (current to the defaults, defaults to the code's) and each failing key is told.
"""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from allin.core.errors import SettingsFileInvalid, SettingsStorageFailed
from allin.core.files import write_atomically
from allin.core.settings import MainSettings, failing_key

_CURRENT = "current.json"
_DEFAULT = "default.json"


@dataclass(frozen=True)
class LoadedSettings:
    current: MainSettings
    default: MainSettings
    # What was wrong with the files, one per failing key, for Main to tell on start.
    problems: list[SettingsFileInvalid] = field(default_factory=list[SettingsFileInvalid])


class MainSettingsFiles:
    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def load(self) -> LoadedSettings:
        problems: list[SettingsFileInvalid] = []
        default = self._read(_DEFAULT, problems) or MainSettings()
        current = self._read(_CURRENT, problems) or default
        return LoadedSettings(current, default, problems)

    def save_current(self, settings: MainSettings) -> None:
        self._write(_CURRENT, settings)

    def save_default(self, settings: MainSettings) -> None:
        self._write(_DEFAULT, settings)

    def _read(self, name: str, problems: list[SettingsFileInvalid]) -> MainSettings | None:
        """The file's Settings; ``None`` if it is missing or broken, in which case
        ``problems`` gets why."""
        try:
            return MainSettings.model_validate_json((self._folder / name).read_bytes())
        except FileNotFoundError:
            return None
        except OSError as error:
            problems.append(SettingsFileInvalid(file=name, key=None, reason=str(error)))
        except ValidationError as error:
            for failure in error.errors():
                key = failing_key(failure)
                problems.append(SettingsFileInvalid(file=name, key=key, reason=failure["msg"]))
        return None

    def _write(self, name: str, settings: MainSettings) -> None:
        path = self._folder / name
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            write_atomically(path, settings.model_dump_json(indent=2))
        except OSError as error:
            raise SettingsStorageFailed(file=name, reason=str(error)) from error
