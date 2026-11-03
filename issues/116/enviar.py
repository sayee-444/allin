"""Tool de prueba de #116: enviar con respuesta."""

from allin.channel import command
from allin.decorators import reach


@reach(commands=["send"])
def enviar(sid: str, texto: str) -> object:
    """Envía un Prompt a la Sesión sid. Su respuesta no llega aquí: te llegará después, como
    un Prompt nuevo, cuando termine tu Turno."""
    return command(f"@send {sid} --reply-to=me {texto}")
