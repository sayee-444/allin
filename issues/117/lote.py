"""Tool de prueba de #117: repartir una lista de tareas con un Lote."""

import json

from allin.channel import command
from allin.decorators import reach


@reach(commands=["batch"])
def lote(trabajadoras: str, tareas: list[str]) -> object:
    """Reparte una lista de tareas entre varias Sesiones (ids separados por comas): cada una
    toma la siguiente cuando queda libre. Despertarás con todos los resultados juntos."""
    return command(f"@batch --to={trabajadoras} {json.dumps(tareas, ensure_ascii=False)}")
