"""An fsource of AllIn's installation: bring a Session into view (ADR-0020)."""

from allin.channel import command
from allin.decorators import reach


@reach(commands=["open"])
def bring_window(sid: str) -> object:
    """Open a Session's Window, or bring it from the background, without sending any other
    away. If no more Windows fit, it stays in the background, marked as asking the human for
    attention until they bring it or dismiss the mark. Gives where it ended: ``view`` or
    ``background``."""
    return command(f"@open {sid}")
