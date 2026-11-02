"""#101 by hand, in the real layout: AllIn in ~/Pictures/Neva, a test data folder inside home."""
import json, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from tests.test_tool_launcher import TOOLS
from allin.confinement.launcher import ToolCall, ToolLauncher
from allin.decorators import MINIMUM, Reach
from allin.main.links import LinkResolver
from allin.supervisor.settings import ServerSettings, save_server_settings

EXTRA = '''

def show(text):
    done = subprocess.run(["xmessage", "-title", "allin-ev101-display", "-timeout", "5", text],
                          capture_output=True, text=True)
    return {"exit": done.returncode, "stderr": done.stderr.strip()[:120]}


def https(host):
    try:
        socket.create_connection((host, 443), timeout=5).close()
        return "connected"
    except OSError as error:
        return type(error).__name__
'''
scratch = Path(tempfile.mkdtemp(dir=sys.argv[1]))
install = Path(sys.argv[2])
data = scratch / "var"
(data / "state").mkdir(parents=True); (data / "state" / "main.json").write_text("{}")
pkg = data / "profiles" / "default" / "functions" / "probe"; pkg.mkdir(parents=True)
(pkg / "probe.py").write_text(TOOLS + EXTRA)
ws = data / "workspaces" / "s1"; ws.mkdir(parents=True)
outside = scratch / "outside.txt"; outside.write_text("mine")
(scratch / "notes").mkdir()
servers = scratch / "servers"; servers.mkdir()
save_server_settings(servers / "fast.toml", ServerSettings(model="m.gguf", port=9935))
links = LinkResolver(servers, "http://127.0.0.1:9931")
home = str(Path.home())

def hide(v):
    s = json.dumps(v)
    for real, ph in ((str(scratch), "<scratch>"), (str(install), "<repo>"), (home, "<home>")):
        s = s.replace(real, ph)
    return json.loads(s)

L = ToolLauncher(data, server_ports=links.server_ports); L.start()
def run(fn, reach=MINIMUM, **a):
    return L.run(ToolCall(package=pkg, module="probe", function=fn, arguments=a, workspace=ws, reach=reach), 30).value

probe_in_install = install / ".probe-ev101"
targets = {"<scratch>/outside.txt (in home, outside AllIn)": str(outside),
           "<repo>/README.md (installation)": str(install / "README.md"),
           "<scratch>/var/state/main.json (data folder)": str(data / "state" / "main.json")}
out = {"server_ports (servers/*.toml + base_url)": sorted(links.server_ports())}
out["1_minimum_read"] = run("attempt", paths=targets)
out["2_read_home"] = run("attempt", Reach(read=("home",)), paths=targets)
out["2_write_home"] = {label: run("write", Reach(write=("home",)), path=path) for label, path in (
    ("<scratch>/notes/new.txt (in home, outside AllIn)", str(scratch / "notes" / "new.txt")),
    ("<scratch>/new.txt (beside the data folder: a trimmed level, no new entries)", str(scratch / "new.txt")),
    ("<repo>/.probe-ev101 (installation)", str(probe_in_install)),
    ("<repo>/.githooks/ev101-probe (installation, an existing folder)", str(install / ".githooks" / "ev101-probe")),
    ("<scratch>/var/state/x (data folder)", str(data / "state" / "x")))}
out["2_probe_left_in_installation"] = probe_in_install.exists() or (install / ".githooks" / "ev101-probe").exists()
p443 = Reach(net=(443,))
out["3_net_443"] = {"tcp 127.0.0.1:443": run("connect", p443, port=443), "tcp 127.0.0.1:80": run("connect", p443, port=80),
                    "udp 127.0.0.1:9": run("udp", p443, port=9), "dns + tcp example.com:443": run("https", p443, host="example.com")}
out["3_net_false"] = {"udp 127.0.0.1:9": run("udp", port=9), "tcp 127.0.0.1:443": run("connect", port=443)}
every = Reach(net=True)
out["4_net_true"] = {f"tcp 127.0.0.1:{p}": run("connect", every, port=p) for p in (9931, 9935, 9936, 443)}
out["6_terminals_read_slash_write_dev"] = run("terminals", Reach(read=("/",), write=("/dev",)))
out["5_display_off"] = run("show", text="no display")
result = {}
t = threading.Thread(target=lambda: result.update(on=run("show", Reach(display=True), text="AllIn #101: a tool with display=True")))
t.start(); time.sleep(2.5)
subprocess.run(["uv", "run", "-q", "--with", "python-xlib", "--with", "pillow", "python", sys.argv[3], "shot", "allin-ev101-display", sys.argv[4]], check=False)
t.join()
out["5_display_on"] = result["on"]
L.stop()
print(json.dumps(hide(out), indent=2, ensure_ascii=False))
