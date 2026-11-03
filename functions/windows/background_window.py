"""An fsource of AllIn's installation: send a Session to the background (ADR-0020)."""

from allin.channel import command
from allin.decorators import reach


@reach(commands=["bg"])
def background_window(sid: str = "") -> object:
    """Send your own Session, or one you created, to the background: its Window closes, and
    its Turns and tools go on. Leave ``sid`` empty for your own. You can never send away
    another Session, nor close a Window."""
    if not sid:
        return command("@bg")
    return command(f"@bg {sid}")
