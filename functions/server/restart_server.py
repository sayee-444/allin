"""An fsource of AllIn's installation: restart this Session's Server (ADR-0009, ADR-0025)."""

from allin.channel import command
from allin.decorators import reach


@reach(commands=["server"])
def restart_server() -> object:
    """Restart the Server this Session runs on, and say how it came back: ready, or failed and
    why. While it restarts, no Step of any Session starts on it."""
    return command("@server restart")
