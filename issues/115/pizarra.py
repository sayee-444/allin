"""Una pizarra que comparten varias Sesiones."""

import time

from allin.decorators import commutative, concurrent, human, writes
from allin.fproc import Fproc


@concurrent(2)
class Pizarra(Fproc):
    """Una pizarra de texto compartida."""

    def __init__(self):
        self.texto = ""
        self.notas = []
        self.dejadas = []

    def leer(self) -> str:
        """Lo que pone la pizarra."""
        return self.texto

    @writes
    def escribir(self, texto: str) -> str:
        """Borra la pizarra y escribe texto."""
        self.texto = texto
        return texto

    @commutative
    def anotar(self, nota: str) -> int:
        """Añade una nota al final; da cuántas hay."""
        self.notas.append(nota)
        return len(self.notas)

    @human
    def a_mano(self, texto: str) -> None:
        """Escribe como lo haría el humano en la ventana de la pizarra."""
        self.texto = texto
        self.changed()

    def esperar(self, segundos: float) -> str:
        """Espera unos segundos y dice cuándo empezó y acabó."""
        empezo = time.strftime("%H:%M:%S")
        time.sleep(segundos)
        return f"{empezo} -> {time.strftime('%H:%M:%S')}"

    def larga(self, pasos: int) -> int:
        """Una tarea larga, de un segundo por paso, que avisa de cómo va."""
        for paso in range(pasos):
            time.sleep(1)
            self.progress(f"paso {paso + 1} de {pasos}")
        return pasos

    def paciente(self, nombre: str) -> str:
        """Espera hasta que la cancelen."""
        while not self.cancelled():
            time.sleep(0.1)
        self.dejadas.append(nombre)
        return "dejada"

    def dejadas_hasta_ahora(self) -> list:
        """Las llamadas a paciente que se cancelaron."""
        return self.dejadas

    def terca(self) -> None:
        """No acaba nunca."""
        time.sleep(600)
