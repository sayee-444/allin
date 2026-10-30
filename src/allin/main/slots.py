"""What Main knows of its Server from llama-server's ``GET /props``, and how many Turns it runs
at once.

Main never queues more Turns on the server than it has slots; extra Prompts wait in Main's
queue (ADR-0004). The slots are ``total_slots`` in ``/props``, and the ``parallel`` Setting can
only lower that. Both change while Main runs: the Server watch reads them again (ADR-0004).
"""

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ServerProps:
    total_slots: int
    # n_ctx of each slot: the context limit Windows show (with --kv-unified, the whole pool).
    slot_context: int
    model_path: str


def parse_props(props: object) -> ServerProps | None:
    """What Main uses of ``/props``; ``None`` if it says nothing usable."""
    if not isinstance(props, dict):
        return None
    settings = props.get("default_generation_settings")
    slots = _positive(props.get("total_slots"))
    n_ctx = _positive(settings.get("n_ctx")) if isinstance(settings, dict) else None
    if slots is None or n_ctx is None:
        return None
    model_path = props.get("model_path")
    return ServerProps(
        total_slots=slots,
        slot_context=n_ctx,
        model_path=model_path if isinstance(model_path, str) else "",
    )


def _positive(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def capacity(props: ServerProps, parallel: int | None) -> int:
    """Turns at once: the server's slots, lowered by ``parallel`` if it is set."""
    if parallel is None:
        return props.total_slots
    return min(parallel, props.total_slots)


def server_root(base_url: str) -> str:
    """Where llama-server's own endpoints (``/props``, ``/health``) are: outside the
    OpenAI-compatible ``/v1``."""
    root = base_url.rstrip("/")
    return root.removesuffix("/v1")


def server_name(base_url: str) -> str:
    """The Server's name in its slot locks: its port, until Supervisors name it (ADR-0008)."""
    url = urlsplit(base_url)
    port = url.port or (443 if url.scheme == "https" else 80)
    return str(port)
