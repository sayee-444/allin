# Roadmap

**Meta:** un orquestador de terminal para conversar con un LLM local desde varias Ventanas, donde Main ordena los Turnos de todas las Sesiones, el Servidor lo lanza y vigila un Supervisor, y todo lo que se extiende (functions, Avisos, Sysprompts, Perfiles) es modular y se declara con nombres y decoradores legibles para una persona.

Cada fase es un milestone en GitHub (`sayee-444/allin`). Dentro de una fase, los issues van en el orden de la lista; las dependencias están marcadas en GitHub como "blocked by". Vocabulario: `GLOSSARY.md`; decisiones: `docs/adr/`.

## Dónde vamos

Lo actualiza el agente al terminar cada issue (`docs/agents/issue-workflow.md`).

- **Último hecho:** los pendientes de Pre-RAG, de #153 a #197 (fusionados hasta el PR #200, 2026-11-03): la Iniciativa y el Externo (#153, #154, #159, #179), los Secretos fuera del Log y del Borrador (#155, #178, #195), lo que vuelve al input o al Borrador (#156, #166, #171, #174, #176, #182, #196), el Contexto tras compactar (#157, #169, #177, #197), el Supervisor sin modelo (#158) y las caídas y la cola (#181, #185, #188, #190, #197). Métricas al cerrar #197: 1914 tests, cobertura 85 %, mutación 94 %, 41 escapes. Qué cambió en cada uno, cómo se probó a mano y sus métricas está en el comentario de cierre de su issue. Después, las preguntas abiertas de #104 a #197 pasaron a decisiones según los ADR (abajo), y lo que el código no cumplía, a #201–#206. Luego, #209 (2026-11-03): una sola lectura del `@secret` de una línea (`read_secret`) para el recorte del Log, el rechazo y la vuelta a la entrada, que no engañan opciones (también entre comillas), tabs, saltos de línea ni escapes de JSON, y que vale también para la Iniciativa; lo que encontró fuera de sus formas, a #210. Métricas al cerrar #209: 1951 tests, cobertura 85 %, mutación 94 %, 41 escapes. Y #208 (2026-11-03), en paralelo: Shift+Enter con el Turno suspendido, o cedido para compactar, lo guarda cortado con los Pasos que terminó y lo escrito va detrás, sin heredar su cuenta de caídas; los caminos que salían de la cola sin la salida única (la Compactación que falla al medirse o se guarda, la automática que no puede empezar, que ya deja seguir a su Prompt, y el Prompt cuya Conversación no se lee al arrancar) pasan por `_leave`, que tiene el final `saved`; y una Continuación que reserva el pool entero porque su Servidor, aún listo, no la midió se mide otra vez si el Servidor cambia; lo que encontró fuera, a #212. Métricas al cerrar #208 (con #209 fusionado): 1963 tests, cobertura 86 %, mutación 94 %, 41 escapes.
- **Siguiente:** #201 a #206, #210, #212, #214 y #217, en «Pre-RAG · Pendientes de F2». Después, la fase del RAG, que aún no tiene issues: primero se actualiza `llama`, que el usuario autorizó el 2026-11-03, y luego viene su prototipo (ADR-0019). Luego el Personaje (ADR-0027 a ADR-0029), cuyos issues se abren cuando empiece el RAG, y después F3, con su grilling. #18 sigue en `needs-triage`, para F3.
- **Queda pendiente, menor:** la imagen no tiene aún descripción (llega con el Sysprompt `describir`, ADR-0019); y en el Chat de Main, que corre en el terminal del humano, Ctrl+Enter y Shift+Enter solo se distinguen si ese terminal manda `modifyOtherKeys`. Lo demás que estaba aquí pasó a issues (#154–#159).

### Preguntas que sí necesitan al usuario

Ninguna abierta.

- #109 — Respondida por el usuario (2026-11-03): todo `@…` tecleado en una Ventana muestra su salida en esa Ventana, como `@call` (ADR-0021, enmienda del 2026-11-03). Va a #214.

### Decisiones tomadas según los ADR

Lo que el código hace y se apega a la letra de los ADR y del GLOSSARY. Lo que no se apegaba va a su issue.

**Piezas, Cuarentena y Encierro**
- #104 — Decidido según ADR-0013 (enmienda #104, «Lo que instala AllIn»): Main aprueba solo los Sysprompts idénticos byte a byte a los instalados; los que tocó el usuario esperan a `/piece approve`.
- #106 — Decidido según ADR-0005 (enmienda #106) y el GLOSSARY (fsource, fagent): las fsources viven en `functions/` y las fagents en `agents/` del Perfil.
- #106 — Decidido según ADR-0005 (enmienda #106) y ADR-0016: en un kernel que no encierra del todo, una pieza que no escribió el usuario no se describe ni se ofrece, y si el LLM la llama recibe un error que lo dice.
- #114 — Decidido según ADR-0016 (enmienda #101) y ADR-0005 (enmienda #99 y #100): `/dev/shm` se declara en `@reach` y no va en el mínimo, y `HOME` es la Carpeta de trabajo; un `@reach` en un método hace fallar la descripción (ADR-0021, enmienda #114).
- #112 — Decidido según ADR-0013 (texto y enmienda #112) y el GLOSSARY (Crear): en modo normal, el «sí» al recuadro de una pieza creada por una Sesión es Confirmación y Aprobación a la vez y la deja aprobada; con `--devastation`, queda en cuarentena.
- #158 — Decidido según ADR-0014 y ADR-0022: un Supervisor sin su modelo no lanza nada, su trabajo espera y sale un Aviso, como el que no cabe en VRAM; que su estado se llame `failed` (`model_missing`) y no «dormido» es interno, y así no despierta en bucle.
- #158 — Decidido según ADR-0008: «los Supervisores de la máquina» son los de `servers/` de la carpeta de datos.
- #158 — Decidido según ADR-0014 y ADR-0008: si el modelo no está, el Supervisor creado guarda solo el nombre del archivo, y su ruta la fija un humano con `/server set <nombre> model=<path>`, como dice el Aviso.
- Revisión de Pre-RAG (#158) — Decidido según ADR-0008 y ADR-0014: el modelo de un Supervisor es el archivo de `model` (`-m`), que nombra un humano; un `-hf` o `--model-url` en `extra` no lo sustituye, así que sin ese archivo no arranca.

**Confirmación, Origen y Otorgar**
- #107 — Decidido según ADR-0009 (enmienda #107) y el GLOSSARY (Confirmación): `-y` para las tools va en `/send -y` desde el Chat de Main; en la Ventana aún no hay forma, como anota el ADR.
- #107 — Decidido según ADR-0009 (enmienda #107, «Cuándo»): todas las Confirmaciones de un Paso se preguntan antes de que corra ninguna tool.
- #110 — Decidido según ADR-0013 (enmienda #110) y el GLOSSARY (Command prompt): la forma estructurada de `/send` es solo de las Sesiones, y un humano sigue escribiendo `@./ruta` y `@clave=valor`; un «no» a un archivo de fuera es dato para el LLM, y si lo vuelve a pedir, vuelve a preguntar a un humano.
- #111 — Decidido según ADR-0025 (enmienda #111): `/grant` lo escribe también un humano y pide Confirmación con quién la pide; dar la fsource que declara Otorgar da ya `@grant`.
- #108 — Decidido según ADR-0025 (enmienda #108): `@send` y `@server` son de Main, siempre están y van en la lista por defecto.
- #108 — Va a #202: si se cancela el Turno de una tool que espera la Confirmación de su Command session, el recuadro sigue y el comando corre con un «sí»; lo que abre un Turno cancelado se cancela (ADR-0011, enmienda #116; ADR-0012, enmienda #115).

**Command sessions y la lista por defecto**
- #112 — Decidido según ADR-0013 (enmienda #112) y el GLOSSARY (Crear): `@create` no viene en la lista por defecto; se da con `-cs=create` o con Otorgar.
- #112 — Decidido según ADR-0011 (enmienda #112): una Sesión que crea desde un Molde con tarea necesita `@send` en toda su cadena.
- #122 — Decidido según ADR-0020 y el GLOSSARY (Command session): `@open` es un Command session, porque la fsource `windows` trae Sesiones a la vista.
- #122 — Decidido según ADR-0020 y ADR-0025: `@windows`, `@open` y `@bg` van en la lista por defecto, porque la fsource `windows` se hereda; a las Sesiones de antes se les dan con `/func add <sid> @windows @open @bg`.
- #122 — Decidido según ADR-0020 («nunca cierra una Ventana»): `@close` no va en la lista por defecto y, tecleado, cierra solo la Ventana en que se teclea; las demás, `/close <sid>` en el Chat de Main.
- #122 — Decidido según ADR-0025 (canal de vuelta): la lista que pide una tool le vuelve por el canal y la ve la Ventana como salida de la tool; no se escribe en el Chat de Main.
- #122 — Decidido según ADR-0025 y ADR-0026: un LLM crea Sesiones con `@new`, que no viene en la lista por defecto, y una fproc con su Iniciativa (#123).
- #159 — Decidido según ADR-0026 (la Iniciativa usa los Command sessions de su Alcance): `SessionCommandType` lleva el campo `initiative`, y no hay tabla aparte.

**Procesos adheridos y fprocs**
- #113 — Decidido según ADR-0012 (enmienda #113): los pids van en `state/attached.json`, uno por carpeta de datos; `@attach` no viene en la lista por defecto; `/detach` es solo de un humano.
- #113 — Decidido según el GLOSSARY (Referencia a archivo): una ruta relativa de una Sesión parte de su Carpeta de trabajo, y la de un humano (`--file`, como `@./ruta`), del directorio desde el que lanzó Main.
- #114 — Decidido según ADR-0021 (enmienda #114): un método llamado con `/call` desde el Chat de Main no ejecuta Command sessions (`command_session_without_session`); con `@call` tecleado por un humano los limitan el Alcance de la fproc y la Confirmación, no la lista; `@call` no viene en la lista por defecto; un `@call` que ejecuta una tool recibe solo `ok`.
- #115 — Decidido según ADR-0012 (enmienda #115): solo se comprueban las escrituras del LLM; una Sesión que nunca leyó ve rechazado su primer `@writes`; la llamada cancelada que el método no deja muere pasados su timeout y `attached.release`; `@concurrent()` es sin tope.
- #115 — Decidido según ADR-0012 (enmienda #113): adherir bloquea a Main hasta que la clase está hecha, con un tope de 20 s.
- #115 y #117 — Va a #203: el Informe de recuperación solo trae los Encargos, y los Procesos adheridos perdidos y los Turnos cortados no avisan a las Sesiones afectadas, como pide el GLOSSARY.
- #116 — Va a #201: un método solo puede anunciar durante una llamada, y ADR-0021 dice que entre llamadas lo que pasa se avisa como evento.
- #153 — Decidido según ADR-0026 y el GLOSSARY (Admisión): la tarea del Molde de un `@new` de la Iniciativa va detrás, como un Prompt de la Iniciativa, y la de un `@new` que pide un método durante una llamada, por orden de llegada.
- #153 — Decidido según ADR-0026 y el GLOSSARY (Admisión): la clase de espera se llama `initiative` en `servers/<puerto>.waits.json`; un Main de antes de #153 que comparta el Servidor la lee vacía hasta reiniciarse, una sola vez.
- Revisión de Pre-RAG (#153) — Decidido según ADR-0026: un Prompt de la Iniciativa en cola en un Estado de Main de antes de #153 se lee sin su clase y adelanta a los humanos una sola vez, al actualizar; no hace falta migrarlo.
- #154 — Decidido según el GLOSSARY (Origen, Externo): el rótulo de un Prompt que trae una fproc es `process:<nombre>`; la identidad del Externo queda solo en el Turno y en el Log.

**Suscripción, Encargo, Lote y Consultar**
- #116 — Decidido según ADR-0011 (enmienda #116): los eventos se agrupan en el tick; `@subscribe` y `@unsubscribe` van en la lista por defecto; las respuestas y los temporizadores de una Sesión a otra cuentan como la misma Suscripción; un humano escribe `--reply-to=<sid>` como primera palabra tras el `<sid>` de `/send`; `subscription.repeats` es 5.
- #116 — Decidido según ADR-0011 (enmienda #116) y ADR-0013 (enmienda #105): la Suscripción no tiene Exención propia, sino la de la Sesión a la que despierta.
- #116 — Decidido según ADR-0011 (enmienda #116) y el GLOSSARY (Prompt): solo una respuesta pendiente (un Encargo, un `--reply-to`) retiene la del Prompt, no un temporizador.
- #116 — Decidido según ADR-0011 y ADR-0015: Restablecer y `/reset --all` serán eventos cuando existan, en «Después».
- #117 — Decidido según ADR-0011 (enmienda #117): `@errand` y `@batch` van en la lista por defecto; el plazo es de cada tarea; una tarea que falla sale en el tick siguiente; «tener sitio» es un slot que no ocupa esta Main; las tareas van como lista JSON, una por línea u objeto JSON; una trabajadora cortada por `/exit` cuenta como sin respuesta.
- #118 — Decidido según ADR-0011 (enmienda #118) y el GLOSSARY (Consultar): `@status` (un humano, `/status <sid>`) va en la lista por defecto porque solo lee; muestra las Sesiones del Perfil activo; Servidores y Supervisores son de la máquina; uno dormido sale sin capacidad.

**Compactación y Contexto**
- #119 — Decidido según ADR-0017 (nota #119): la poda no reescribe nada y solo poda salidas de más de 400 caracteres, con los tokens estimados por los caracteres; el Turno cede según la medida de admisión; `@compact` va en la lista por defecto; la Notificación de estado vivo no se deja si no hay nada abierto, y trae solo los Procesos adheridos cuyos métodos tiene la Sesión.
- #120 — Decidido según ADR-0004 (nota #120): las líneas al LLM van en inglés; la de Contexto se deja al guardarse el Turno, no si falla o se cancela; el corte por Shift+Enter conserva su fuente `interrupted`.
- #157 — Decidido según ADR-0017 (nota #157): el Contexto tras compactar es la Conversación como la lee el LLM, sin esquemas de tools ni imágenes; tras reiniciar Main, o tras un Rewind en una Conversación compactada, el borde dice «?».
- #157 — Resuelto en #169: el Organizador ya muestra el Contexto nuevo.
- #169 — Decidido según ADR-0020 y ADR-0017: con el Contexto desconocido, la fila del Organizador no lo muestra, como la de una Sesión sin Turnos; y una fila sin título, tras resumirse todos los Turnos, muestra solo su id, porque lo resumido está archivado y la fila lee la Conversación.
- #177 — Decidido según ADR-0017 (nota #157): tras un Rewind en una Conversación compactada alguna vez, el borde dice «?» hasta el Turno siguiente.
- #188 — Decidido según ADR-0004 (nota #188): una Continuación que el Servidor no sabe medir reserva el pool entero; solo se mide la que pasa del pool; la que no cabe falla con `prompt_too_big`.
- #197 — Decidido según ADR-0023: la Continuación cuya medida se pierde no se suspende (`turn_suspended`) porque no tiene slot ni Paso a medias; espera al Servidor como cualquier Continuación.
- #197 — Decidido según ADR-0010 y ADR-0024: mientras el cambio de Perfil espera, el Turno entre Pasos toma en su Paso siguiente los Textos intercalados de su Ventana, porque sigue en curso.

**Escribir durante un Turno y lo que vuelve al input**
- #121 — Decidido según ADR-0024 (cómo quedó en F2) y el GLOSSARY (Texto intercalado): solo entra texto llano, y lo que lleva Command prompts o `-y` espera su Turno con lo de detrás; lo que no cabe en lo que le queda al slot espera; lo escrito antes de que el Turno empezara es el Prompt siguiente; al cortar con Shift+Enter unas tools que corrían, cada llamada lee que se cortó y que pudo hacer parte de su trabajo; al cancelarse, lo intercalado vuelve al Borrador tras el Prompt humano (#156).
- #156 — Decidido según el GLOSSARY (Estado de Main, Texto intercalado): con `/exit` a mitad de un Turno humano, su Prompt vuelve al arrancar, porque queda en los Turnos en curso del Estado de Main.
- #156 y #166 — Decidido según el GLOSSARY (Borrador, Rewind: «para reeditarlo»): un Prompt cancelado o fallido vuelve como se tecleó, con sus `@clave=valor`.
- #166 — Resuelto en #171: solo vuelve al input el Prompt humano, y el que no cabe vuelve como se tecleó.
- #171 — Decidido según el GLOSSARY (Texto intercalado) y ADR-0024: los Textos intercalados de un Turno fallido de otra Sesión vuelven al input, porque son del humano.
- #171 — Resuelto en #174: al reabrir vuelven los Textos intercalados del Turno fallido y el Prompt humano que no cupo.
- #174 — Decidido según ADR-0013 (solo el Origen humano vuelve al input): lo que vuelve al reabrir solo lo borra un Prompt humano posterior o que ya se devolviera, no uno de otra Sesión; vale también para un Turno fallido (#182).
- #174 — Decidido según el GLOSSARY (Borrador): si vuelven a la vez lo de un Turno fallido y Prompts que no cupieron, lo último devuelto va delante.
- #176 — Resuelto en #196: tras un `kill` de Main, los Textos intercalados del Turno en curso también vuelven (`interjected` en el Estado de Main).
- #176 — Decidido según el GLOSSARY (Estado de Main): un Turno interrumpido con Shift+Enter cuyo fin no llega antes de que Main se pare vuelve al Borrador al arrancar, como cualquier Turno en curso, sin guardarse cortado.
- #176 y #196 — Va a #204: el Chat reconstruido no muestra los Textos intercalados ni los Pasos de un Turno suspendido que se cancela al cerrar la Ventana, ni los de un Turno perdido con un `kill -9` (ADR-0024: «en vivo y al reconstruirse»).
- #182 — Decidido según el GLOSSARY (Rewind): un Rewind no borra lo de un Turno fallido que espera a reabrir la Ventana, porque no está en ninguna Conversación.
- Revisión de Pre-RAG (#171) — Resuelto en #182: al reabrir, solo cuentan como posteriores los Prompts humanos.
- #182 — Va a #205: con la Ventana cerrada, un Turno fallido y luego un Rewind vuelven en el orden contrario al de la Ventana abierta (GLOSSARY, Borrador).
- #196 — Decidido según el GLOSSARY (Prompt): un Prompt humano que no puede empezar por otra razón que no caber vuelve al input (`prompt_not_started`).
- #196 — Decidido según ADR-0024 y ADR-0013: ↑ solo toma un Prompt humano; uno de otra Sesión o de una Suscripción se queda en la cola.
- #196 — Decidido según el GLOSSARY (Turno suspendido): `/profile switch --now` cancela también el Turno que un `/server restart --now` ya había parado, que acaba cancelado y con su Prompt en el Borrador.
- #196 — Va a #206: un Prompt que no llega a empezar y no cabe no se lleva su cola, y #16 hace caer la cola de un Prompt que falla.
- #208 — Decidido según ADR-0024 («lo guarda con lo que llevaba, como un Turno cortado») y ADR-0023 (lo que generó el Paso a medias se descarta): Shift+Enter sobre un Turno suspendido lo guarda cortado con los Pasos que terminó, sin necesitar el Servidor; si no terminó ninguno, con un Paso vacío, como el Turno en marcha que se corta antes de generar nada.
- #208 — Decidido según ADR-0017 (la Compactación es todo o nada y falla si la Conversación cambia mientras corre): Shift+Enter sobre un Turno cedido no para la Compactación a la que cedió; el Turno se guarda cortado en cuanto ella acaba, y lo escrito va detrás.
- #208 — Decidido según la regla de #196 (una sola salida): un Turno o una Compactación que se guardan salen también por `_leave`, con el final `saved`, que solo olvida su cuenta de caídas.
- #208 — Va a #212: la Compactación de un Turno suspendido hereda su cuenta de caídas, y su Paso de resumen falla a la primera caída en vez de repetirse (ADR-0023; ADR-0017, enmienda del 2026-11-01).

**Secretos**
- #155 — Decidido según ADR-0026 (#124, #155): `@secret` se rechaza siempre, también si lo pide una herramienta o lo declara un módulo de `comsession/`; tras el error la línea no vuelve a la entrada ni entra en el Borrador.
- #155 — Decidido según ADR-0026: ↑ en la Ventana abierta trae la línea entera, porque está solo en la memoria de ese proceso y nunca llega al Log, la Conversación ni el disco.
- #155 — Resuelto en #178: `/send <sid> @secret …` desde el Chat de Main se rechaza y se recorta en el Log.
- #178 — Decidido según ADR-0026 (#178, #195): `/send <sid> @secret …` se rechaza con `secret_in_main_chat_only` y el uso de `/secret`, y el recorte del Log vale para todo `/send`, también el de una Sesión.
- #195 — Decidido según ADR-0026 (#195): cualquier Log corta una línea de comando tres palabras después de su primer `@secret` o `/secret`, aunque solo lo nombre; un Encargo o un Lote cuya tarea manda un `@secret` se rechaza venga de quien venga; un Command session que lo nombra donde el Log lo cortaría no vuelve a la entrada ni al Borrador.
- #209 — Decidido según ADR-0026 («el `@secret` que manda un `/send` … se rechaza») y el GLOSSARY (Command prompt): un `/send` humano con una palabra que empieza por `--` delante del `@secret` (`--attach=…`, `--temperature=…`, `--`) se rechaza. Su texto iría tal cual, pero empieza como la forma estructurada de una Sesión, y leerlo igual para todos deja una sola lectura de la línea; un Prompt que solo nombra `@secret` sigue yendo.
- #209 — Decidido según ADR-0026 («no vuelve a ninguna entrada ni Borrador»): `OpenWindows.put_back` nunca devuelve una línea que lleva un `@secret`, la devuelva quien la devuelva, y el Log guarda el `\u0040secret` de un JSON ya leído («@secret set bot token …»).
- #209 — Va a #210: `/secret set … <valor>` tecleado como Prompt en una Ventana, o mandado con `/send`, va entero al LLM y al Log.

**Caídas y Supervisor**
- #190 — Decidido según ADR-0023: la cuenta de caídas es de un Main vivo, y el Turno suspendido que se lleva el Perfil que se deja vuelve con dos intentos, como tras reiniciar Main.
- #190 — Resuelto en #196: la salida única limpia las Notificaciones contadas y la marca de Turno en curso por cualquier camino menos `take_queue`, cuyo Turno sigue en la cola del otro Perfil.

### Decisiones anteriores

- **Respuestas sobre #123 y #124 (2026-11-03):** ante cada pregunta, lo que más se apega a los ADR y a la lógica del proyecto.
  - Quedan como estaban, de #123:
    - sin `@subprofile` no hay Iniciativa;
    - `turns` es 1 por defecto;
    - «no sale del Subperfil» es una tool que solo toca su Carpeta de trabajo, sin red, pantalla ni Command sessions, y nunca un método de fproc;
    - las Sesiones del Subperfil no crean Sesiones, no adhieren procesos ni usan `/status`, `/errand` o `/batch`;
    - `/subprofile` es solo humano, y el Subperfil queda ligado a su fproc;
    - `@new` se declara por nombre en el Alcance;
    - ningún comando borra Sesiones.
  - Queda todo #124 como estaba:
    - el valor se pide en un recuadro con puntos, y Enter vacío cancela;
    - un cambio llega en la siguiente adhesión;
    - el valor es una línea de hasta 8192 caracteres, en claro, con permisos 600;
    - la fproc lo lee en claro;
    - hay `/secret list <instancia>`, y se puede poner antes de adherir;
    - un recuadro solo lo contesta el Chat de Main o la Ventana de su Sesión.
  - Cambian dos decisiones, que se apartaban de ADR-0026 (#153): la identidad del Externo ya no va en la cabecera que lee el LLM, solo en el Log y en el Turno guardado; y solo el Prompt de la Iniciativa va detrás en la Admisión, no el que manda un método durante una llamada.
  - Los pendientes menores pasan a issues, para resolverlos cuanto antes (#154–#159).
- **Decidido en el grilling del tejido (ronda 1):** autoridad por Origen, `-y` y `--devastation` solo humanos, inyección entre Sesiones (ADR-0013); vida del Encargo si falla la coordinadora (ADR-0011); revisión optimista en Procesos adheridos (ADR-0012); Kit compartible y cargado por copia (ADR-0014).
- **Decidido en el grilling del tejido (rondas 2–4):** cuarentena de functions ajenas, fusión de Kits, Servidor sugerido, entrada, Plano e historial inicial (ADR-0014); Restablecer, `/reset --all` y archivos como composición (ADR-0015).
- **Decidido en el grilling del aislamiento:** cada proceso de tool y de Proceso adherido se encierra con Landlock y seccomp según su Alcance (`@reach`), con Carpeta de trabajo por Sesión y la carpeta de datos siempre fuera; llega al comienzo de F2 y F4 desaparece (ADR-0016).
- **Revisión de coherencia (2026-10-30):** corregidas 14 contradicciones entre ADR-0016 y el resto del tejido: descripción de functions sin importarlas en Main, recorte también de la instalación, procedencia frente a Origen, Referencia a archivo, canal de vuelta, muerte de tools con Main, Suscripción no bloqueante, Rewind por Origen, cambio de Perfil con Encargos vivos, Clon y Carpeta de trabajo.
- **Segunda revisión de coherencia (2026-10-30):** encierro de descripción, timeout sin las esperas a Main, `display` como actuar por el usuario, puertos del Servidor fuera de todo Alcance, UDP con lista de puertos, Referencia a archivo como instantánea, ruta de la Carpeta de trabajo fuera de los Ajustes, procedencia, Avisos y Command sessions como parte de Main, plazo vencido que cancela, Ventana según el Origen del Prompt y Encargos del Perfil que los abrió.
- **Tercera revisión (2026-10-30), con el diagrama del tejido:** un proceso encerrado salía por los sockets unix con ruta (D-Bus → `systemd --user`, X11, agente SSH), comprobado en este kernel; lo cierra el filtro seccomp, junto con io_uring y `TIOCSTI`, y el proceso de una tool nace sin la terminal, el entorno ni las rutas de importación de Main. Además: tres puertas (Confirmación, Aprobación, Encierro); Main y el Supervisor nunca tocan rutas ni red por una Sesión más allá de sus tools; package entero y sin `.pyc`; recorte de toda ruta concedida; escribir donde el usuario ejecuta como `display`; caracteres de control y de dirección neutralizados en pantalla; la respuesta de un Prompt al final de su cadena; ADR-0004 con el modelo de Pasos; Carpeta de trabajo como directorio actual, y una propia para cada Proceso adherido; Rewind que cancela lo abierto después; solo el Origen humano vuelve al input; el Perfil como frontera de `/send`; piezas por Perfil; y una batería de fugas antes de la primera tool.
- **Decidido en el grilling de la Compactación (2026-10-30):** una Conversación larga se compacta a mano (`/session compact`) o sola al 85 % del límite del slot, archivando lo resumido y con el resumen como dato; nunca se trunca en silencio, y `--context-shift` no se admite (ADR-0017). La primera parte es #46, tras #29; la poda y lo que depende de las tools, en F2.
- **Decidido en el grilling de los Temas (2026-10-31):** cada Ventana y el Chat de Main se ven según un Tema (letra, colores, borde, tamaño y caracteres especiales), que la Sesión guarda por nombre y que se aplica en vivo, sin cerrar la Ventana; el borde toma por defecto un color distinto por Sesión, y un Tema solo admite valores con forma fija (ADR-0018). Es #47, en los pendientes de F1. Al verificarlo contra el código salió que, desde F1-01, el xterm de las Ventanas acepta preguntas (`allowWindowOps`) con las que un texto puede hacer pasar el título o el portapapeles por algo tecleado; lo apaga #28.
- **Decidido en el grilling del RAG (2026-10-31):** cada Perfil tiene un RAG, la memoria de consulta del sistema. Es un Proceso adherido encerrado que guarda documentos, el historial de las Sesiones y la documentación de AllIn con su catálogo de functions. Devuelve como dato pocas Fichas, buscadas por palabras y embeddings y reordenadas por un reranker, y estos dos modelos tienen cada uno su Supervisor (ADR-0019). Va en su propia fase, después de F2. Del mismo grilling salieron el Tope de salida por defecto y el Turno cortado, que la Responses API de llama no señala (ADR-0004), y el quórum `k` del Encargo (ADR-0011).
- **Decidido en el grilling de las Ventanas (2026-10-31):** las Sesiones abiertas que pasan del Máximo de Ventanas (`windows.max`) quedan en segundo plano: sin xterm, pero siguen trabajando y no se cancela nada. El Organizador (`/windows`) es parte de Main y las trae, las manda a segundo plano o las cierra con un clic, y el LLM tiene la fsource `windows` (ADR-0020). Además, a los métodos de un Proceso adherido también los llama un humano con `/call`, los recibe la Sesión que lo adhiere y pueden ejecutar Command sessions, con el proceso como un eslabón que nunca cuenta como humano (ADR-0021). Son #52 y #53, al final de Pendientes de F1; lo que necesita fsources va a F2, y lo del árbol a F3. Del grilling del RAG salió también #51, el Turno cortado, antes de #29.
- **Revisión del Supervisor y del tejido (2026-11-01):** el Enlace pasa a ser de cada Sesión, con un Enlace por defecto en Main. Un Supervisor despierta cuando le llega trabajo, y lo que duerme un humano solo lo despierta un humano. Cada Supervisor ordena su espera con una lista compartida por todos los Mains, que lleva también las reservas del pool (ADR-0022). Una caída repite el Paso que dejó a medias y `@zombie` desaparece; un Paso colgado falla tras `step.stall` (ADR-0023). Enter ya no se bloquea durante un Turno: lo escrito espera en cola, entra en el Turno desde F2 y se retira con ↑, y Shift+Enter interrumpe (ADR-0024). Además: el Lote y el plazo de cada trabajadora (ADR-0011), compactar por partes (ADR-0017), el RAG encendido por Sesión (ADR-0019) y los Atajos, que solo cambia un humano (ADR-0013). Issues nuevos: #55 (Ajustes), #56 (escribir durante un Turno), #57 (Atajos), #58 (Rewind) y #59 (id de Sesión). Y en los Procesos adheridos: varias llamadas a la vez si su fproc lo declara, la revisión dentro del proceso y las llamadas largas (ADR-0012). Y en los Command sessions: cada Sesión tiene su lista, heredada como sus functions, cada function declara los que ejecuta, y solo un humano da Otorgar (ADR-0025). Y `--devastation`: lo enciende un humano en un Main y dura ese arranque, y las Sesiones y los submains con Exención le suben sus Confirmaciones, que contesta él; lo que no puede hacer queda escrito en ADR-0013, y `/tree` y `/tree --exempt` llegan con F3.
- **Decidido en el grilling de las fprocs (2026-11-02):** «fscreen» pasa a llamarse **fproc** (*function process*). Con el ejemplo guía de un bot de Discord: entre llamadas, una fproc actúa por su cuenta sobre las Sesiones que creó, con los Command sessions de su Alcance; lo que dice un **Externo** entra con el Origen de la fproc, que nunca cuenta como humano; la fproc que lo declara tiene un **Subperfil** por instancia, con sus Sesiones (creadas solo desde Moldes de su package, con el techo de la Sesión que la adhirió), su propio RAG y su carpeta, como frontera de `/send`; un humano puede poner la Exención a un Subperfil solo para lo que no sale de él; sus Prompts van detrás de los humanos en la Admisión, con un tope por Subperfil; y un **Secreto** se guarda una vez en el Perfil, a nombre de la instancia (ADR-0026). Se retiran la fproc `screen` de `spec.md` y el motor gráfico ligero: lo que una fproc puede hacer se mostrará de otra manera. La investigación de sistemas parecidos está en `docs/research/sistemas-parecidos.md`.

## F1 · Cierre

Ya tienen código; verificados y comentados.

1. ✅ #8 Contexto y velocidad en el borde
2. ✅ #9 Cerrar una Ventana cancela su Turno
3. ✅ #10 Caída de llama.cpp sin perder el Prompt
4. ✅ #11 `/exit`, señales y caída de Main
5. ✅ #12 Restaurar MainState

## Pre-F2 · Servidor

1. ✅ #21 Observar el Servidor en caliente, junto con #17 (503 al cargar el modelo)
2. ✅ #20 Partir el Dispatcher
3. ✅ #49 Fijar el estilo de Python con ruff y basedpyright
4. ✅ #55 Ajustes: archivo, `/set`, `/set --default`, `/reset`, `/session set` y `/session`
5. ✅ #28 Recuadro de Aviso y Confirmación
6. ✅ #24 Supervisor 1/4: Supervisor con nombre, Enlace de cada Sesión y despertar con trabajo (ADR-0022)
7. ✅ #25 Supervisor 2/4: reinicio ordenado, drenado y una sola regla para las caídas (ADR-0023)
8. ✅ #26 Supervisor 3/4: `/server …`, con `wake`, e investigación de Ajustes del Servidor
9. ✅ #27 Supervisor 4/4: cambiar el Enlace de una Sesión en caliente
10. ✅ #51 Turno cortado: detectarlo por los tokens, guardarlo marcado y avisar (ADR-0004)
11. ✅ #29 Admisión al pool y turnos entre Mains, con el Tope de salida por defecto como reserva (ADR-0004, ADR-0022)
12. ✅ #46 Compactación del Contexto: `/session compact`, automática entre Turnos y por partes

## Pre-F2 · Perfiles

1. ✅ #30 Perfiles 1/3: Perfil con nombre, `--profile`, `/profile new`, piezas por Perfil, migración a `default`
2. ✅ #31 Perfiles 2/3: `/profile switch`
3. ✅ #32 Perfiles 3/3: compartir Sesiones
4. ✅ #39 Perfiles 4/4: exportar un Perfil como copia, y Kits (primera parte)

## Pre-F2 · Pendientes de F1

1. ✅ #15 El texto de un Turno fallido con la Ventana cerrada
2. ✅ #16 Un Prompt en cola sin el anterior de su Sesión que falló
3. ✅ #13 Selección con el ratón, rueda y portapapeles
4. ✅ #14 Command prompts: Referencia a archivo y Sobrescritura de parámetro
5. ✅ #22 Cursor en el input e historial de Prompts
6. ✅ #56 Escribir durante un Turno: Enter sin bloqueo, la cola, ↑ para retirar y Shift+Enter para interrumpir (ADR-0024)
7. ✅ #57 Atajos de teclado con `/key`
8. ✅ #23 `/help` generado del registro de Command main
9. ✅ #19 Lo que quede de la señal durante el arranque
10. ✅ #33 Una Ventana desconectada sigue mandando Borradores (Broken pipe)
11. ✅ #34 Los textos que vuelven al input se mezclan con lo escrito
12. ✅ #58 Rewind de una Sesión
13. ✅ #59 Elegir y cambiar el id de una Sesión
14. ✅ #47 Temas en las Ventanas y el Chat de Main, por nombre y en vivo
15. ✅ #52 Segundo plano y Máximo de Ventanas
16. ✅ #53 El Organizador de Ventanas

## F2 · Command sessions y fsource

Creados el 2026-11-02 con todo lo decidido hasta ADR-0026; cada issue nombra sus ADRs y sus bloqueos, que también están en GitHub. Lo primero es el encierro (ADR-0016): ninguna tool corre antes de que la batería de fugas (#103) falle entera. Quedan para «Después» Goal, Clon, Kits (segunda parte), Restablecer y las fagents que crea el LLM.

1. ✅ #99 Prototipo desechable del encierro
2. ✅ #100 Lanzador de tools encerrado
3. ✅ #101 `@reach` y el Alcance
4. ✅ #102 Encierro de descripción
5. ✅ #103 Batería de fugas
6. ✅ #104 Cuarentena y Aprobación
7. ✅ #105 Origen con cadena y eslabón más débil
8. ✅ #106 Function tools en el Turno
9. ✅ #107 `@confirm` en las tools
10. ✅ #108 Command sessions
11. ✅ #109 Notificaciones y Conversation logs
12. ✅ #110 `/send` entre Sesiones
13. ✅ #111 Otorgar
14. ✅ #112 Moldes y Crear
15. ✅ #113 Adherir y soltar Procesos adheridos
16. ✅ #114 Métodos de una fproc
17. ✅ #115 Revisión, concurrencia y llamadas largas en los Procesos adheridos
18. ✅ #116 Suscripción
19. ✅ #117 Encargo y Lote
20. ✅ #118 Consultar
21. ✅ #119 Compactación en F2
22. ✅ #120 Notificaciones de Turno cortado y de Contexto restante
23. ✅ #121 Lo escrito durante un Turno entra antes del siguiente Paso
24. ✅ #122 La fsource `windows`
25. ✅ #123 Iniciativa de una fproc, Externos y Subperfil
26. ✅ #124 Secretos

## Pre-RAG · Pendientes de F2

Lo que quedó de F2, antes de la fase del RAG: dos decisiones de #123 que vuelven a ceñirse a ADR-0026 y los pendientes menores.

1. ✅ #153 La identidad del Externo y la Iniciativa en la Admisión, como dice ADR-0026
2. ✅ #154 La Ventana rotula cada Prompt por su Origen
3. ✅ #155 `@secret` tecleado en una Ventana no deja su valor en el Log
4. ✅ #156 El Prompt humano de un Turno cancelado al cerrar la Ventana vuelve al Borrador
5. ✅ #157 Tras compactar, el borde muestra el Contexto nuevo
6. ✅ #158 Un Supervisor que propone un Kit avisa si su modelo no está
7. ✅ #159 Quitar las duplicaciones que dejó #123 (tras #153)
8. ✅ #166 El Prompt de un Turno fallido vuelve al input tal como se tecleó (de #156)
9. ✅ #169 Tras compactar, el Organizador muestra el Contexto nuevo (de #157)
10. ✅ #171 Solo vuelve al input el Prompt humano de un Turno fallido, y el que no cabe vuelve como se tecleó (de #166)
11. ✅ #174 Al reabrir la Ventana vuelven también los Textos intercalados de un Turno fallido y el Prompt humano que no cupo (de #171)
12. ✅ #176 Lo que vuelve al Borrador al cancelarse o fallar un Turno, también con `/exit` y con un Turno suspendido (de #156, #166, #171 y #174)
13. ✅ #178 El valor de un `@secret` escrito en el Chat de Main no llega al Borrador ni al Log (de #155)
14. ✅ #179 Un `@new` de la Iniciativa con tarea no deja una Sesión huérfana cuando el Subperfil está en su tope (de #153 y #159)
15. ✅ #177 El Contexto tras compactar sobrevive a Rewind, a un Turno que cedió y a `/session id` (de #157 y #169)
16. ✅ #181 Cerrar la Ventana de un Turno de otro Origen no le da un intento más tras caer el Servidor (de #174)
17. ✅ #182 Al reabrir la Ventana vuelve lo de un Turno fallido aunque después llegara un Prompt de otro Origen (de #171 y #174)
18. ✅ #185 Quien envió un Prompt no se entera si su Turno falla o se cancela cuando Main ya se para (de #176)
19. ✅ #188 Una Continuación que se estima mayor que el slot espera sitio en el pool para siempre (de #177)
20. ✅ #190 Cancelar el Prompt de un Turno suspendido deja viva su cuenta de caídas (de #181)
21. ✅ #195 `@send <sid> @secret` tecleado en una Ventana, y un `/send` que no encaja, no dejan el valor en el Log (de #155 y #178)
22. ✅ #196 Una sola salida para un Turno, que devuelve lo del humano, olvida sus caídas y cierra lo que abrió (de #156, #166, #171, #174, #176, #181, #182, #185 y #190)
23. ✅ #197 Una Continuación cuya medida se pierde, `/profile switch` con un Turno entre Pasos y el aviso de límite de contexto tras compactar
24. #201 Un Proceso adherido anuncia eventos también entre llamadas, como dice ADR-0021 (de #116)
25. #202 La Confirmación de un Command session que pidió una tool se retira si su Turno se cancela (de #108)
26. #203 El Informe de recuperación trae los Turnos, Ventanas y Procesos adheridos que quedaron a medias, y avisa a cada Sesión afectada (de #115 y #117)
27. #204 El Chat reconstruido muestra los Textos intercalados de un Turno perdido con un `kill -9` o cancelado mientras estaba suspendido (de #176 y #196)
28. #205 Al reabrir la Ventana, lo de un Turno fallido y lo que devolvió un Rewind salen en el orden de la Ventana abierta (de #182)
29. #206 Un Prompt que no cabe o no puede empezar se lleva su cola, como un Turno fallido (de #16 y #196)
30. ✅ #209 Los caminos que aún llevaban el valor de un `@secret` a la entrada o al Log: opciones de `/send`, `@` escapado e Iniciativa (de #195)
31. #210 `/secret set … <valor>` tecleado como Prompt en una Ventana, o mandado con `/send`, llega al LLM y al Log (de #209)
32. ✅ #208 Los caminos que quedaron fuera de la salida única del Turno: Shift+Enter con el Turno suspendido, Compactación y arranque (de #196 y #197)
33. #212 El Paso de resumen de la Compactación de un Turno suspendido tiene sus dos intentos (de #208)
34. #214 La salida de todo `@…` tecleado en una Ventana sale en esa Ventana (respuesta del usuario a #109)
35. #217 `/profile export` copia también los Subperfiles y las Carpetas de trabajo (del grilling del Personaje, ADR-0027)

## RAG · Memoria de consulta

Sin issues ni milestone todavía. Va después de F2, porque necesita el encierro, los Command sessions, las fsources, las Notificaciones, los Procesos adheridos y la Aprobación, además de los Supervisores (#24–#27) y los Perfiles (#30); no necesita F3 (ADR-0019). Antes de su prototipo se actualiza `llama` (autorizado por el usuario el 2026-11-03; hoy es la 0.4.0-dev, build 10909, un solo binario en `~/.local/bin/llama`). Se guarda el binario viejo para volver atrás, se comprueba el Supervisor con la build nueva y se repiten las mediciones del Personaje (ADR-0027). Así el prototipo ya mide con ella. Empieza con un prototipo desechable que mide lo que el diseño aún no sabe: los umbrales y el tope del reranker, cuánto tarda el reranker en CPU, si el modelo de embeddings cabe en la GPU junto al Q6, si el modo router de `llama serve` sirve con dos modelos, si la build en uso corre Qwen3-Reranker, y si el modelo usa `buscar` y hace caso a las Pistas. Después: la fproc `rag` adherida por el Ajuste `rag`; los Supervisores de embeddings y reranker, con `@embed` y `@rerank`; la búsqueda por palabras (FTS5), que funciona sin modelos; los Segmentos por Sesión; `buscar` y `leer` con sus Fichas; las Pistas; los Corpus, con `/rag add` y su Aprobación; el historial por Turno, con las marcas de deshecho y la puesta al día; el manual de AllIn y el catálogo de functions; y la descripción de imágenes y video con el Sysprompt `describir`. De la revisión del Supervisor: el Ajuste `rag` es de cada Sesión, copiado del de Main en `/new`, y Main adhiere el RAG siempre; sus Supervisores despiertan cuando el RAG los necesita, y `describir` va al Enlace por defecto (ADR-0019, ADR-0022). Indexar un Corpus, reindexar al adherirse y la puesta al día son llamadas largas, y el RAG contesta búsquedas mientras tanto (ADR-0019). Su Alcance declara solo `@embed` y `@rerank` (ADR-0025). Del grilling de las fprocs: el RAG de cada Subperfil, otra instancia de la fproc `rag`, al que la fproc añade lo que trae con su procedencia (ADR-0026).

## Personaje

Va después del RAG, que es su memoria, y antes de F3, que no necesita (ADR-0027 a ADR-0029). Sus issues se abren cuando empiece la fase del RAG, con sus bloqueos. Hay un milestone por tramo:

1. «Personaje · Texto»: su Ventana, su memoria, su Reposo y la curación de Charlas.
2. «Personaje · Discord»: en texto, con otras personas y sus Confidencias.
3. «Personaje · Voz».
4. «Personaje · Vista».

«Personaje · Texto» tiene doce issues, que se abren con sus bloqueos cuando empiece la fase del RAG.

Primitivas de Main, para cualquier fproc:

1. Un Ajuste del Perfil adhiere fprocs y despierta y duerme sus Supervisores al entrar y al salir (enmienda de ADR-0022).
2. Una fproc declara sus Ajustes, y las Sesiones de su Subperfil pueden cambiarlos.
3. Main le avisa a una fproc cuando termina cada Turno de su Subperfil, y antes de soltarla, con un tope.
4. Una fproc muestra un Aviso.
5. La tool de Main que marca una Confidencia: la marca queda en el Turno, y la respetan el Reposo y la exportación. El filtro por presencia va en «Personaje · Discord».
6. Marcar Sesiones para entrenar y exportarlas en JSONL, nunca las que tengan una Confidencia (tras 5).

El Personaje, en su package:

7. Su fproc, su Subperfil y sus Charlas: abrirlas y cerrarlas por la pausa o al despedirse, dejar lista la siguiente, mandar el Autorretrato y la Relación al abrir, y la voz interior marcada (tras 1, 2, 3 y las Pistas del RAG).
8. Su Reposo: reflexiona en el 27B al cerrarse cada Charla, con citas, versiones y la vuelta atrás, sin ver Confidencias (tras 5 y 7).
9. Su iniciativa por texto: los silencios, escribir primero con el Aviso, la despedida y la vuelta (tras 3, 4 y 8).
10. Las pruebas con la GPU: el 27B hace del usuario, un juez puntúa contra el Núcleo, y se comprueban las citas y la latencia (tras 9).

Del usuario (`ready-for-human`):

11. Escribir su Núcleo y crear su Perfil y sus Supervisores, con una guía; el 9B lleva `-b 256 -ub 256 --reasoning off` en `extra` (tras 1 y 7).
12. Las Charlas largas y naturales (tras 10 y 11).

Cada milestone se trabaja con un `/goal`, y el usuario lo revisa antes de pasar al siguiente. Lo que necesita su voz o su cara, como registrarlo o las Charlas largas, va en issues `ready-for-human`.

«Personaje · Texto» se prueba en dos capas. Sin modelo, en cada `pytest`, su fproc corre con un `llama` falso y un reloj falso. Se comprueba que abra y cierre las Charlas por la pausa, que deje lista la siguiente y la cambie al terminar la reflexión, que mande el Autorretrato y la Relación al abrir, las versiones y la vuelta atrás, la despedida y el tope para escribir primero. Con la GPU, aparte, el 27B hace del usuario con un guion de carácter y conversa con ella varias Charlas, con Reposos entre medio. Se comprueba que cada frase del Autorretrato cite Turnos que existen y que no se aleje de su Núcleo, que un LLM juez puntúa en cada Charla, y se mide cuánto tarda su primer token. El usuario solo entra en las Charlas largas y naturales.

Antes va #217, en los pendientes de F2. Los otros tramos tienen su propio grilling antes de abrir sus issues:

- **Discord:** cómo la presenta el usuario en un servidor, el aviso de las reglas (+18), cómo elige canal en su tiempo libre, cómo se enlazan las cuentas con los Conocidos y el filtro por presencia.
- **Voz:**
  - el STT y el TTS: Voxtral Realtime necesita la `llama` nueva;
  - la VRAM: con el STT y el TTS queda al filo de los 32 GB, y quizá el TTS vaya en la CPU;
  - la latencia, el eco, el registro del usuario y las Huellas, y los detalles de tomar la palabra.
- **Vista:**
  - las capturas de pantalla, que necesitan un intermediario porque D-Bus está cerrado (ADR-0016);
  - la webcam, por ruta y con su Alcance;
  - el mmproj del 9B y las Huellas de cara.
- **El LoRA:** se entrena fuera de AllIn y entra como `--lora` en el `extra` de su Supervisor.

Pendiente del usuario: mover el SN850X a la M.2_1 y reasentar la GPU, que hoy va a PCIe x1; después se vuelve a medir. Los informes de medición y la investigación están en `docs/research/personaje/`, sin seguimiento.

## F3 · Submains y Traspaso

Submains, Traspaso de Sesiones dentro del árbol, y #18 (FIFOs huérfanas por Main). Antes, en su grilling: el protocolo entre Mains y las primitivas entre Mains del árbol (ADR-0011), y con él el Máximo de Ventanas compartido por el árbol y un Organizador de todo el árbol (ADR-0020). Un submain no hereda `--devastation`: sus Confirmaciones solo suben al Main de arriba si un humano le pone la Exención, al crearlo o después, o si la trae un Plano, y cada Main lo marca en sus bordes. `/tree` dibuja el árbol de Mains y `/tree --exempt`, el de Exención (ADR-0013). En su grilling, además: que un Traspaso o crear en otro Main no dejen cubierto lo que no lo estaba.

## Después

Lo que trae código ajeno (fagent del LLM y Kits, segunda parte) ya llega con el encierro de F2: sus functions corren con el Alcance aprobado, y en un kernel sin Landlock no corren (ADR-0016).

Sin fecha ni orden fijo: fagent y packages (con Crear de functions desde una Sesión), encadenar functions, Clon y Goal. Kits, segunda parte: functions con cuarentena, Moldes, Planos, historial inicial y entrada (ADR-0014). Restablecer (`/session reset`) y `/reset --all` (ADR-0015). El Personaje salió de aquí: tiene su propia sección, antes de F3. Una fproc `discord`, genérica, con una instancia por servidor de Discord: el ejemplo guía de ADR-0026. Adherir un servidor MCP como fproc (sin `@writes`, una llamada a la vez), que depende de las functions cuya cara no es Python. Functions que traen su propio código compilado, o cuya cara (nombre, parámetros, decoradores y Alcance) no es Python (ADR-0016).
