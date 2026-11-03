"""Drive and capture AllIn's xterms for an issue's evidence (docs/agents/issue-workflow.md).

Run it without adding its libraries to the project:

    uv run --with python-xlib --with pillow python tools/xterm_drive.py <command> ...

Commands:
    titles                          the titles of AllIn's xterms
    shot <title> <out.png>          a pixel capture of the xterm with that title
    type <title> <text>             type ``text`` into it; ``\\n`` is Enter
    shiftenter <title>              Shift+Enter (interrupt the Session's Turn)
    key <title> <keysym>...         press keys by their X name: ``Down``, ``Up``, ``Return``
    wait <log.jsonl> <event> <n> [seconds]
                                    wait until the Log has ``n`` lines of ``event`` (default 60 s);
                                    a Session's Log is ``<data-dir>/sessions/<sid>/log.jsonl``

xterm only takes sent keys with ``XTerm*allowSendEvents: true``: start Main's xterm with
``-xrm 'XTerm*allowSendEvents: true'`` and ``XENVIRONMENT=<file holding that line>``, so the
Windows Main opens take it too. Only characters on the keyboard map can be typed.
"""

import json
import sys
import time
from pathlib import Path

from PIL import Image
from Xlib import X, XK, display
from Xlib.protocol import event
from Xlib.xobject.drawable import Window

_DISPLAY = display.Display()
_ROOT = _DISPLAY.screen().root


def _windows(window: Window = _ROOT) -> list[Window]:
    found = [window]
    try:
        for child in window.query_tree().children:
            found += _windows(child)
    except Exception:  # a window closed while walking the tree
        pass
    return found


def _titled(title: str) -> Window:
    for window in _windows():
        try:
            wm_class = window.get_wm_class()
            if window.get_wm_name() == title and wm_class and "XTerm" in wm_class:
                return window
        except Exception:
            continue
    raise SystemExit(f"no xterm titled {title!r}")


def _frame(window: Window) -> Window:
    while (parent := window.query_tree().parent) not in (_ROOT, None):
        window = parent
    return window


def titles() -> None:
    names = set[str]()
    for window in _windows():
        try:
            name = window.get_wm_name()
        except Exception:
            continue
        if isinstance(name, str) and "AllIn" in name:
            names.add(name)
    print("\n".join(sorted(names)))


def shot(title: str, out: str) -> None:
    frame = _frame(_titled(title))
    geometry = frame.get_geometry()
    raw = frame.get_image(0, 0, geometry.width, geometry.height, X.ZPixmap, 0xFFFFFFFF)
    size = (geometry.width, geometry.height)
    Image.frombytes("RGB", size, raw.data, "raw", "BGRX").save(out)


def _press(widget: Window, keysym: int, shift: bool) -> None:
    codes = list(_DISPLAY.keysym_to_keycodes(keysym))
    if not codes:
        raise SystemExit(f"keysym {keysym:#x} is not on the keyboard map")
    code, index = codes[0]
    state = X.ShiftMask if shift or index == 1 else 0
    for kind in (event.KeyPress, event.KeyRelease):
        sent = kind(
            time=X.CurrentTime, root=_ROOT, window=widget, same_screen=1, child=X.NONE,
            root_x=0, root_y=0, event_x=0, event_y=0, state=state, detail=code,
        )  # fmt: skip
        widget.send_event(sent, event_mask=X.KeyPressMask | X.KeyReleaseMask, propagate=True)
    _DISPLAY.flush()
    time.sleep(0.03)


def _vt100(title: str) -> Window:
    """The widget that takes keys: the child of the xterm's window."""
    return _titled(title).query_tree().children[0]


def type_text(title: str, text: str) -> None:
    widget = _vt100(title)
    for char in text.replace("\\n", "\n"):
        # A Latin-1 character's keysym is its code point.
        keysym = XK.string_to_keysym("Return") if char == "\n" else ord(char)
        _press(widget, keysym, shift=False)


def shift_enter(title: str) -> None:
    _press(_vt100(title), XK.string_to_keysym("Return"), shift=True)


def keys(title: str, names: list[str]) -> None:
    widget = _vt100(title)
    for name in names:
        keysym = XK.string_to_keysym(name)
        if keysym == X.NoSymbol:
            raise SystemExit(f"no keysym named {name!r}")
        _press(widget, keysym, shift=False)


def wait(log: str, name: str, count: int, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    path = Path(log)
    while time.monotonic() < deadline:
        lines = path.read_text().splitlines() if path.exists() else []
        if sum(json.loads(line).get("event") == name for line in lines) >= count:
            return
        time.sleep(0.25)
    raise SystemExit(f"{name!r} did not reach {count} in {log} within {seconds:g} s")


def main(argv: list[str]) -> None:
    match argv:
        case ["titles"]:
            titles()
        case ["shot", title, out]:
            shot(title, out)
        case ["type", title, text]:
            type_text(title, text)
        case ["shiftenter", title]:
            shift_enter(title)
        case ["key", title, *names] if names:
            keys(title, names)
        case ["wait", log, name, count]:
            wait(log, name, int(count))
        case ["wait", log, name, count, seconds]:
            wait(log, name, int(count), float(seconds))
        case _:
            raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
