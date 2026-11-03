"""Tool de prueba de #116: suscribirse."""

from allin.channel import command
from allin.decorators import reach


@reach(commands=["subscribe"])
def suscribir(evento: str) -> object:
    """Pide que un evento te despierte con un Prompt: process:<nombre>, timer:<segundos>,
    rewind:<sid> o compact:<sid>."""
    return command(f"@subscribe {evento}")
