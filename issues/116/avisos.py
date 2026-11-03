"""Proceso adherido de prueba de #116: anuncia cosas a quien se suscriba."""

from allin.fproc import Fproc


class Avisos(Fproc):
    """Un tablón que anuncia lo que se le pide."""

    def avisa(self, texto: str, veces: int = 1) -> str:
        """Anuncia texto, las veces dichas, seguidas."""
        for n in range(veces):
            self.announce(f"{texto} ({n + 1} de {veces})")
        return "anunciado"
