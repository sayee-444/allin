"""An fsource of AllIn's installation: the Sessions of this Profile and their Windows (ADR-0020)."""

from allin.channel import command
from allin.decorators import reach


@reach(commands=["windows"])
def list_windows() -> object:
    """List the Sessions of this Profile: which are in view, in the background or closed, and
    for each its title, whether a Turn runs, its queue, its Confirmations and Notices, whether it
    asks the human for attention, its Context and its Supervisor."""
    return command("@windows")
