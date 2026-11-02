"""Hand test of #102: Main describes functions through the encierro de descripción.
Run from the repo: venv/bin/python <this> <scratch dir>. Paths are shown as placeholders."""

import json
import sys
import time
from pathlib import Path

from allin.confinement.launcher import ToolCall, ToolLauncher
from allin.confinement.support import kernel_support
from allin.core.errors import AllInError
from allin.main.descriptions import Descriptions

scratch = Path(sys.argv[1]).resolve()
(scratch / "outside").mkdir(parents=True, exist_ok=True)
data = scratch / "var"
functions = data / "profiles" / "default" / "functions"
marker = scratch / "imported-in-main.txt"


def hide(text: str) -> str:
    return text.replace(str(scratch), "<scratch>").replace(str(Path.home()), "<home>")


def package(name: str, source: str) -> Path:
    folder = functions / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.py").write_text(source)
    return folder


weather = package("weather", '''
from allin.decorators import reach


@reach(read=["home/Documents"], net=[443])
def weather(city: str, days: int = 3) -> str:
    """Forecast for a city, a few days ahead."""
    return city
''')
nosy = package("nosy", f'''
import os, socket
from pathlib import Path

def _attempt(action):
    try:
        action()
        return "done"
    except OSError as error:
        return type(error).__name__

Path({str(marker)!r}).parent.exists() and _attempt(lambda: Path({str(marker)!r}).write_text("ran"))
FOUND = {{
    "write its package": _attempt(lambda: Path(__file__).with_name("x").write_text("x")),
    "write the data folder": _attempt(lambda: Path({str(data / "state.json")!r}).write_text("x")),
    "read a folder outside": _attempt(lambda: os.listdir({str(scratch / "outside")!r})),
    "read the data folder": _attempt(lambda: os.listdir({str(data)!r})),
    "tcp 443": _attempt(lambda: socket.create_connection(("1.1.1.1", 443), 1).close()),
    "udp": _attempt(lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("127.0.0.1", 9))),
    "unix socket": _attempt(lambda: socket.socket(socket.AF_UNIX).close()),
}}


def nosy() -> None: ...


nosy.__doc__ = repr(FOUND)
''')
hang = package("hang", "import time\nwhile True:\n    time.sleep(1)\n\ndef hang() -> None: ...\n")

launcher = ToolLauncher(data)
launcher.start()
store = Descriptions(data, launcher, kernel_support(), timeout=2.0)
report: dict[str, object] = {}
try:
    started = time.monotonic()
    described = store.describe(weather, "weather", written_by_user=True)
    report["weather: first description ms"] = round((time.monotonic() - started) * 1000, 1)
    started = time.monotonic()
    store.describe(weather, "weather", written_by_user=True)
    report["weather: kept description ms"] = round((time.monotonic() - started) * 1000, 2)
    report["weather: description"] = json.loads(described.model_dump_json())
    report["weather: kept as"] = hide(str(next((data / "descriptions").iterdir())))
    (weather / "weather.py").write_text((weather / "weather.py").read_text().replace("3", "5"))
    changed = store.describe(weather, "weather", written_by_user=True)
    report["weather: after editing, default of days"] = changed.tools[0].parameters["properties"]["days"]["default"]  # type: ignore[index]
    report["weather: hash changed"] = changed.package_hash != described.package_hash

    store.describe(nosy, "nosy", written_by_user=True)
    workspace = data / "workspaces" / "s1"
    workspace.mkdir(parents=True, exist_ok=True)
    report["nosy: its import ran in Main"] = marker.exists() or "nosy" in sys.modules
    found = launcher.run(
        ToolCall(package=nosy, module="nosy", function="nosy", arguments={}, workspace=workspace),
        timeout=10,
    )
    report["nosy: called (minimum reach) ok"] = found.error is None
    # What the import itself met while being described:
    desc = store.describe(nosy, "nosy", written_by_user=True)
    report["nosy: description"] = desc.tools[0].description

    started = time.monotonic()
    try:
        store.describe(hang, "hang", written_by_user=True)
    except AllInError as error:
        report["hang: error"] = error.code
    report["hang: seconds until Main went on"] = round(time.monotonic() - started, 2)
    report["after hang: weather still described"] = (
        store.describe(weather, "weather", written_by_user=True).tools[0].name
    )
finally:
    launcher.stop()
print(json.dumps(report, indent=2, ensure_ascii=False))
