# IPC Main ↔ Ventana por par de FIFOs con JSON por línea

Cada Sesión abierta tiene dos FIFOs (`<sid>.in`, `<sid>.out`) creadas por Main y pasadas a la Ventana por argv; los mensajes son JSON por línea validados con una unión discriminada de Pydantic, y cada extremo tiene un thread lector que vuelca a una `queue.Queue`. Se descartaron sockets (requisito explícito) y `select` (se prefiere threads bloqueantes); el formato textual facilita depurar con `cat` sobre la FIFO.
