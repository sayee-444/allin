from allin.channel import command
from allin.decorators import reach


@reach(commands=["compact"])
def compactar(instrucciones: str = "") -> object:
    """Compacta tu propia Conversacion ahora: los Turnos viejos pasan a un resumen guiado por
    las instrucciones."""
    return command(f"@compact {instrucciones}".strip())
