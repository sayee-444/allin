"""``/kit``: export a Kit from the active Profile and load one into it (ADR-0014)."""

from collections.abc import Iterator
from pathlib import Path

from pydantic import JsonValue

from allin.core.errors import AllInError, InvalidArguments
from allin.main.boxes import MainBoxes
from allin.main.kits import Choice, KitLoaded, Kits
from allin.main.notices import NoticeTypes
from allin.main.profile_switch import ProfileSwitch
from allin.main.reporting import Reporter
from allin.main.settings_commands import SettingsCommands
from allin.supervisor.ports import port_free
from allin.supervisor.settings import ServerSettings, load_server_settings, save_server_settings

USAGE = (
    "/kit export <folder> [piece…] [--version <v>]"
    " | /kit load <folder> [--take <piece>…] [--both <piece>…]"
)
_FLAGS: dict[str, Choice] = {"--take": "take", "--both": "both"}
_FIRST_PORT = 9940  # where the ports of Supervisors a Kit suggests are looked for
_LAST_PORT = 9999


class KitCommands:
    def __init__(
        self,
        reporter: Reporter,
        kits: Kits,
        switch: ProfileSwitch,
        settings: SettingsCommands,
        notices: NoticeTypes,
        boxes: MainBoxes,
        servers: Path,
    ) -> None:
        self._reporter = reporter
        self._kits = kits
        self._switch = switch
        self._settings = settings
        self._notices = notices
        self._boxes = boxes
        self._servers = servers

    def run(self, text: str = "") -> bool:
        match text.split():
            case ["export", folder, *rest]:
                self._export(Path(folder).expanduser(), rest)
            case ["load", folder, *rest]:
                self._load(Path(folder).expanduser(), _choices(rest))
            case _:
                raise InvalidArguments(usage=USAGE)
        return True

    def _export(self, folder: Path, rest: list[str]) -> None:
        version = "1"
        if "--version" in rest:
            at = rest.index("--version")
            if at + 1 >= len(rest):
                raise InvalidArguments(usage=USAGE)
            version = rest[at + 1]
            rest = rest[:at] + rest[at + 2 :]
        manifest = self._kits.export(
            self._switch.active,
            self._settings.current,
            self._notices.setting_keys(prefix="session."),
            folder,
            rest,
            version,
        )
        names: list[JsonValue] = [f"{p.kind}:{p.name}" for p in manifest.pieces]
        shown = f"kit {manifest.name} {manifest.version} exported: {', '.join(map(str, names))}"
        self._reporter.report(None, "kit_exported", shown, kit=manifest.name, pieces=names)

    def _load(self, folder: Path, choices: dict[str, Choice]) -> None:
        loaded = self._kits.load(
            self._switch.active,
            self._settings.current,
            self._notices.setting_keys(prefix="session."),
            folder,
            choices,
        )
        if loaded.settings is not None:
            self._settings.take(loaded.settings)
        self._tell(loaded)
        for name, values in loaded.servers.items():
            self._suggest(loaded.kit, name, values)

    def _tell(self, loaded: KitLoaded) -> None:
        written: list[JsonValue] = list(loaded.written)
        kept: list[JsonValue] = list(loaded.kept)
        shown = f"kit {loaded.kit} {loaded.version} loaded: {', '.join(loaded.written) or 'nothing new'}"
        if loaded.kept:
            shown += f"; kept the profile's own {', '.join(loaded.kept)}"
        self._reporter.report(
            None,
            "kit_loaded",
            shown,
            kit=loaded.kit,
            version=loaded.version,
            written=written,
            kept=kept,
        )

    def _suggest(self, kit: str, name: str, values: dict[str, JsonValue]) -> None:
        """A Supervisor the Kit suggests is made only if a human accepts: new and asleep,
        never changing one there is or any Enlace (ADR-0014). Its model, which the Kit names
        by its file's name, it gets by its path if it is next to another Supervisor's (#158)."""
        model = str(values["model"])
        found = self._find_model(model)

        def on_answer(answer: bool) -> None:
            if not answer or (self._servers / f"{name}.toml").exists():
                return
            given = str(found or model)
            settings = ServerSettings.model_validate(
                {**values, "model": given, "port": self._free_port()}
            )
            save_server_settings(self._servers / f"{name}.toml", settings)
            shown = f"Supervisor {name} created from kit {kit}, asleep"
            fields: dict[str, JsonValue] = {"server": name, "kit": kit, "model": given}
            self._reporter.report(None, "kit_server_created", shown, **fields)

        self._boxes.confirm(None, "Kit Supervisor", _offer(kit, name, model, found), on_answer)

    def _find_model(self, model: str) -> Path | None:
        """The file named ``model`` in the folder of some Supervisor's model, if there is one."""
        for settings in self._supervisors():
            candidate = settings.model_file(Path.cwd()).parent / model
            if candidate.is_file():
                return candidate
        return None

    def _supervisors(self) -> Iterator[ServerSettings]:
        """The Settings of each Supervisor on this machine, in the order of their names."""
        for path in sorted(self._servers.glob("*.toml")):
            try:
                yield load_server_settings(path)
            except AllInError:  # a broken toml names no model nor any port worth avoiding
                continue

    def _free_port(self) -> int:
        taken = {settings.port for settings in self._supervisors()}
        for port in range(_FIRST_PORT, _LAST_PORT + 1):
            if port not in taken and port_free(port):
                return port
        raise InvalidArguments(usage=f"no free port in {_FIRST_PORT}-{_LAST_PORT}")


def _offer(kit: str, name: str, model: str, found: Path | None) -> str:
    """What the box asks of a Supervisor the Kit suggests: the path of its model, or that it
    is not next to any Supervisor's (#158)."""
    if found is not None:
        return f"Create Supervisor {name} from kit {kit} with model {found}, asleep?"
    return (
        f"Create Supervisor {name} from kit {kit}, asleep? Its model {model} is not next to the"
        f" model of any Supervisor on this machine; give it one with /server set {name}"
        " model=<path>"
    )


def _choices(words: list[str]) -> dict[str, Choice]:
    """``--take <piece>`` and ``--both <piece>``, as many as given."""
    choices: dict[str, Choice] = {}
    pairs = iter(words)
    for flag in pairs:
        choice = _FLAGS.get(flag)
        piece = next(pairs, None)
        if choice is None or piece is None:
            raise InvalidArguments(usage=USAGE)
        choices[piece] = choice
    return choices
