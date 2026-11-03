"""Tool de prueba de #117: repartir una tarea con un Encargo."""

from allin.channel import command
from allin.decorators import reach


@reach(commands=["errand"])
def encargar(trabajadoras: str, tarea: str) -> object:
    """Da la misma tarea a varias Sesiones a la vez (trabajadoras: ids separados por comas).
    Sus resultados no llegan aquí: despertarás con todos juntos en un Prompt nuevo cuando
    terminen."""
    return command(f"@errand --to={trabajadoras} {tarea}")
