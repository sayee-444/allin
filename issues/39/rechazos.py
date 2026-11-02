"""Load variants of the kit k39 into the Profile «otro» and print how each one ends."""
import json, shutil, sys
from pathlib import Path
from allin.core.errors import AllInError
from allin.main.kits import Kits
from allin.main.portable import MANIFEST, _hash
from allin.main.profiles import ProfileStore
from allin.main.settings_files import MainSettingsFiles

M = Path(sys.argv[1]); D = M / "d39b"
profiles = ProfileStore(D, Path("sysprompts"))
otro = profiles.find("otro")
current = MainSettingsFiles(otro.settings).load().current
from allin.main.notices import load_notice_types
EXTRA = load_notice_types(D / "notices").types.setting_keys(prefix="session.")

def variant(name, change):
    folder = M / "variantes39" / name
    shutil.rmtree(folder, ignore_errors=True)
    shutil.copytree(M / "k39", folder)
    change(folder)
    snapshot = {p: p.read_bytes() for p in otro.folder.rglob("*") if p.is_file()}
    try:
        Kits(D / "servers").load(otro, current, EXTRA, folder, {})
        result = "cargado"
    except AllInError as error:
        result = str(error).replace(str(M), "<scratch>")
    same = snapshot == {p: p.read_bytes() for p in otro.folder.rglob("*") if p.is_file()}
    print(f"{name}: {result}\n   Perfil «otro» sin cambios: {same}")

def add(kind, pname, files):
    def change(folder):
        for path, data in files.items():
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).write_bytes(data)
        m = json.loads((folder / MANIFEST).read_text())
        m["pieces"].append({"kind": kind, "name": pname, "files": sorted(files), "sha256": _hash(files)})
        (folder / MANIFEST).write_text(json.dumps(m))
    return change

def fmt(folder):
    m = json.loads((folder / MANIFEST).read_text()); m["format"] = 7
    (folder / MANIFEST).write_text(json.dumps(m))

def missing(folder):
    m = json.loads((folder / MANIFEST).read_text())
    m["pieces"] = [p for p in m["pieces"] if p["kind"] != "sysprompt"]
    (folder / MANIFEST).write_text(json.dumps(m)); (folder / "sysprompts/tutor.txt").unlink()
    s = json.loads((folder / "settings.json").read_text()); s["session.sysprompt"] = "no-existe.txt"
    (folder / "settings.json").write_text(json.dumps(s))
    m = json.loads((folder / MANIFEST).read_text())
    for p in m["pieces"]:
        if p["kind"] == "settings":
            p["sha256"] = _hash({"settings.json": (folder / "settings.json").read_bytes()})
    (folder / MANIFEST).write_text(json.dumps(m))

variant("pyc", add("sysprompt", "x", {"functions/t/__pycache__/t.cpython-314.pyc": b"\x00\x01"}))
variant("binario", add("sysprompt", "logo.png", {"sysprompts/logo.png": b"\x89PNG\x00"}))
variant("atajos", add("shortcut", "ctrl-x", {"shortcuts/ctrl-x.json": b"{}"}))
variant("servidor-puerto", add("server", "cpu", {"servers/cpu.toml": b'model = "m.gguf"\nport = 9200\n'}))
variant("servidor-mmproj", add("server", "cpu", {"servers/cpu.toml": b'model = "m.gguf"\nmmproj = "p.gguf"\n'}))
variant("servidor-ruta", add("server", "cpu", {"servers/cpu.toml": b'model = "/models/m.gguf"\n'}))
variant("referencia", missing)
variant("formato", fmt)
