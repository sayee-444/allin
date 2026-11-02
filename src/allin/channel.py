"""How a Function tool runs a Command session (ADR-0005, ADR-0025)::

    from allin.channel import command
    from allin.decorators import reach

    @reach(commands=["send"])
    def tell(sid: str, text: str) -> object:
        \"\"\"Send a Prompt to another Session.\"\"\"
        return command(f"@send {sid} {text}")

It is the only way a confined process reaches Main. Main runs only the Command sessions the
function declares in ``@reach(commands=[…])`` and that every Session of its chain has, and
gives back how it went; the time it waits for that is not counted in its timeout.

It runs inside the tool's own process, so it uses the standard library alone.
"""

import json
import os
import threading

type _Json = bool | int | float | str | list["_Json"] | dict[str, "_Json"] | None

_lock = threading.Lock()  # one Command session at a time on the channel
_pipes: tuple[int, int] | None = None  # (to the warden, from the warden), once connected


def connect(to_warden: int, from_warden: int) -> None:
    """Set by the warden in the runner before it imports the function."""
    global _pipes  # noqa: PLW0603 - the runner's one channel, set once
    _pipes = (to_warden, from_warden)


def command(text: str) -> _Json:
    """Run a Command session, ``@name …`` as a Window takes it, and give back its result:
    ``{"result": "ok"}``, or ``{"error": …}`` with why it was refused or failed. Raises
    ``RuntimeError`` outside a tool call, as in the encierro de descripción."""
    if _pipes is None:
        msg = "no channel to Main: a Command session runs only from a tool call"
        raise RuntimeError(msg)
    to_warden, from_warden = _pipes
    line = json.dumps({"type": "command", "text": text}).encode() + b"\n"
    with _lock:
        sent = 0
        while sent < len(line):  # a pipe may take a long line in parts
            sent += os.write(to_warden, line[sent:])
        answer = b""
        while not answer.endswith(b"\n"):
            chunk = os.read(from_warden, 1 << 16)
            if not chunk:
                msg = "the channel to Main closed"
                raise RuntimeError(msg)
            answer += chunk
    result: _Json = json.loads(answer)["result"]  # the warden passes on only Main's results
    return result
