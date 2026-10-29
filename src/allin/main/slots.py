"""How many Turns Main runs at once: the server's slots (llama-server ``-np``).

Main never queues more Turns on the server than it has slots; extra Prompts wait in Main's
queue (ADR-0004). The count comes from ``total_slots`` in llama-server's ``GET /props``, unless
the ``parallel`` Setting fixes it by hand; with neither, one Turn runs at a time.
"""

import json
import urllib.request
from dataclasses import dataclass
from typing import Literal

_PROPS_TIMEOUT = 2.0


@dataclass(frozen=True)
class Slots:
    count: int  # Turns at once, so executor threads
    source: Literal["server", "setting", "default"]
    server_slots: int | None  # total_slots the server reported, if it answered

    def describe(self) -> str:
        return f"{self.count} turn(s) at once (from {self.source})"

    def warning(self) -> str | None:
        """Set by hand above the server's slots, the extra Turns wait in the server's queue."""
        if self.server_slots is not None and self.count > self.server_slots:
            return (
                f"parallel={self.count} is above the server's {self.server_slots} slots: "
                "the server will queue the extra Turns"
            )
        return None


def resolve_slots(server_slots: int | None, parallel: int | None) -> Slots:
    if parallel is not None:
        return Slots(count=parallel, source="setting", server_slots=server_slots)
    if server_slots is not None:
        return Slots(count=server_slots, source="server", server_slots=server_slots)
    return Slots(count=1, source="default", server_slots=None)


def fetch_total_slots(base_url: str, timeout: float = _PROPS_TIMEOUT) -> int | None:
    """``total_slots`` from the server's ``/props``; ``None`` if it gives no usable answer."""
    try:
        with urllib.request.urlopen(f"{server_root(base_url)}/props", timeout=timeout) as reply:
            props = json.load(reply)
    except (OSError, ValueError):  # no server, an HTTP error status, or not JSON
        return None
    slots = props.get("total_slots") if isinstance(props, dict) else None
    if isinstance(slots, int) and not isinstance(slots, bool) and slots > 0:
        return slots
    return None


def server_root(base_url: str) -> str:
    """Where llama-server's own endpoints (``/props``, ``/health``) are: outside the
    OpenAI-compatible ``/v1``."""
    root = base_url.rstrip("/")
    return root.removesuffix("/v1")
