from allin.channel import command
from allin.decorators import reach


@reach(commands=["status"])
def consultar(parte: str = "") -> object:
    """Consulta lo que tienes a tu alcance: la cola de Turnos, las Suscripciones y Encargos,
    los Procesos adheridos, los Servidores (slots y modelo) y los Supervisores. parte puede ser
    queue, subscriptions, attached o servers; vacía, todo."""
    return command(f"@status {parte}".strip())
