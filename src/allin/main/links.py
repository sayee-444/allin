"""Where each Session's Steps go: its Enlace (ADR-0022).

A Session linked to a Supervisor sends its Steps to the port in that Supervisor's Server
Settings; one with no Enlace, to Main's ``base_url``, a Server Main only observes.
"""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from allin.core.errors import AllInError
from allin.core.ids import ServerKey, SupervisorName
from allin.core.settings import SessionSettings
from allin.main.slots import server_name
from allin.supervisor.settings import load_server_settings


@dataclass(frozen=True)
class Link:
    key: ServerKey  # the Server's name in its slot locks and in Main's queue
    base_url: str
    supervisor: SupervisorName | None  # ``None``: a Server Main only observes

    def describe(self) -> str:
        """How Main's Chat and the Windows name it."""
        return f"Supervisor {self.supervisor}" if self.supervisor else "the Server"


class LinkResolver:
    def __init__(self, servers: Path, base_url: str) -> None:
        self._servers = servers
        self._base_url = base_url

    @property
    def servers(self) -> Path:
        """The folder of every Supervisor's Server Settings and state."""
        return self._servers

    def observed(self) -> Link:
        """The Server at Main's ``base_url``, which Main only observes."""
        return Link(ServerKey(server_name(self._base_url)), self._base_url, None)

    def server_ports(self) -> frozenset[int]:
        """The port of every Supervisor's Server and of the observed one: no tool's Alcance
        reaches them, not even with ``net=True`` (ADR-0016). Read at every call, so a
        Supervisor created since counts."""
        observed = urlsplit(self._base_url)
        ports = {observed.port or (443 if observed.scheme == "https" else 80)}
        for path in self._servers.glob("*.toml"):
            try:
                ports.add(load_server_settings(path).port)
            except AllInError:  # a broken toml holds no Server to protect
                continue
        return frozenset(ports)

    def resolve(self, settings: SessionSettings) -> Link:
        """Raises ``ServerSettingsInvalid`` if the Supervisor has no usable Server Settings."""
        if settings.server is None:
            return self.observed()
        return self.resolve_name(SupervisorName(settings.server))

    def resolve_name(self, name: SupervisorName) -> Link:
        server = load_server_settings(self._servers / f"{name}.toml")
        return Link(ServerKey(name), server.base_url(), name)
