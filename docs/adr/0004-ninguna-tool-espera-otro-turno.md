# Ninguna tool puede esperar a otro Turno

Main atiende tantos Turnos a la vez como slots tenga el servidor (`-np`), a menudo solo uno, así que si la Sesión A, dentro de su Turno, hiciera `/send` a B y esperara su respuesta, habría deadlock garantizado. Por eso `/send` desde una fsource solo encola el Prompt de B y devuelve "encolado"; con `--reply-to=A` la respuesta de B entra como un Prompt nuevo de A en un Turno posterior (modelo de actores). Se descartaron los Turnos anidados porque complican cola, cancelación y rewind, y una cadena A→B→A vuelve a bloquear.

La regla vale igual con N slots: como ningún ejecutor espera a otro Turno, los ejecutores nunca se bloquean entre sí, y el orden A → B → A lo imponen los mensajes, no la cantidad de slots.
Para que una Sesión ocupada no bloquee a las demás, un ejecutor libre toma de la cola el primer Prompt cuya Sesión no tenga un Turno activo; si hay más Prompts que slots, los sobrantes esperan en la cola de Main, no en el servidor.
