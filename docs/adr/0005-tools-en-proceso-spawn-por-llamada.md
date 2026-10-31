# Cada llamada a una tool corre en un proceso spawn propio

Las functions las puede escribir el LLM y se recargan en caliente; si corrieran dentro de Main, una tool colgada o que llame `os._exit()` congelaría o tumbaría todas las Sesiones. Cada llamada se ejecuta en un proceso `multiprocessing` en modo spawn (seguro con threads) con timeout de los Ajustes; cancelar el Turno hace `terminate()`, y las Command sessions que la tool dispare viajan al despachador por una `multiprocessing.Queue`. Se aceptan ~50–100 ms por llamada; se descartaron threads (no se pueden matar) y un pool persistente (estado global contaminado entre llamadas).

Cada proceso se encierra a sí mismo al arrancar, antes de importar la function, con lo que esta declara en `@reach` (ADR-0016).
