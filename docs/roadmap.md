# Roadmap

**Meta:** un orquestador de terminal para conversar con un LLM local desde varias Ventanas, donde Main ordena los Turnos de todas las Sesiones, el Servidor lo lanza y vigila un Supervisor, y todo lo que se extiende (functions, Avisos, Sysprompts, Perfiles) es modular y se declara con nombres y decoradores legibles para una persona.

Cada fase es un milestone en GitHub (`sayee-444/allin`). Dentro de una fase, los issues van en el orden de la lista; las dependencias están marcadas en GitHub como "blocked by". Vocabulario: `GLOSSARY.md`; decisiones: `docs/adr/`.

## Dónde vamos

Lo actualiza el agente al terminar cada issue (`docs/agents/issue-workflow.md`).

- **Último hecho:** #174 Al reabrir la Ventana vuelven también los Textos intercalados de un Turno fallido y el Prompt humano que no cupo, y #178 (2026-11-03), en paralelo.
  - #174: con la Ventana cerrada, al reabrirla vuelven los Textos intercalados de un Turno fallido (`joined` de `turn_failed`), tras su Prompt si es humano y delante de los Prompts humanos de la cola, como con ella abierta. El Prompt que no cabe ni en el slot vacío deja `prompt_too_big` en el Log, con lo tecleado (`typed`) y su Origen; al reabrir la Ventana vuelve el humano, tal como se tecleó y una sola vez, y el de otro Origen no. Lo borra un Prompt humano posterior, no uno de otra Sesión.
  - **Probado a mano** con el Swift IQ3_XXS, la function `tell` de #171, una tool `nap` que espera y el proxy de #171, que corta solo el Paso con el Texto intercalado: en `cerrada174`, durante un Turno de `lider174`, el humano escribió «Ademas di un color en una palabra.», cerró la Ventana (el Turno siguió) y mandó desde Main otro Prompt, que quedó en cola; al fallar el Turno y reabrir la Ventana, el input trajo el Texto intercalado con «+1 waiting» (el de la cola, con su `@top_k=5`), y al reabrirla otra vez, lo mismo desde el Borrador. En `grande174`, con la Ventana cerrada, «Di hola. @top_k=5» no cupo, luego tampoco uno de `lider174`, y al reabrir volvió solo el humano, con su `@top_k=5`.
  - Métricas: 1799 tests (con #178 fusionado), cobertura 85 %, mutación 93 %, 41 escapes.
- #178 El valor de un `@secret` escrito en el Chat de Main no llega al Borrador ni al Log (2026-11-03), en paralelo con #174.
  - #178: `/send <sid> @secret …` desde el Chat de Main se rechaza con `secret_in_main_chat_only` y el uso de `/secret` (antes, `session_command_in_prompt`), y la línea no vuelve a la entrada de la Ventana ni al Borrador que se guarda en `state.json`. El Log y el Chat de Main guardan solo `/send <sid> @secret` y sus tres palabras siguientes con «…», también con `--reply-to=<sid>`. `@secret …` tecleado en el Chat de Main ya no es un comando desconocido: se rechaza igual y guarda solo sus cuatro primeras palabras. Otro `@…` enviado con `/send` sigue volviendo a la entrada.
  - **Probado a mano** sin Servidor: con la Ventana de `caja` cerrada y luego abierta, `/send caja @secret set bot token <valor>` dio el error con el uso de `/secret` en el Chat de Main y en la Ventana, cuya entrada quedó vacía, también al reabrirla; `@secret set bot token <valor>` en el Chat de Main, lo mismo. `state.json` siguió con `"drafts": {}` y ningún archivo de la carpeta de datos tenía el valor, tampoco tras `/exit`.
  - Métricas: 1776 tests, cobertura 85 %, mutación 93 %, 41 escapes.
- #171 Solo vuelve al input el Prompt humano de un Turno fallido, y el que no cabe vuelve como se tecleó, y #169 (2026-11-03), en paralelo.
  - #171: al fallar un Turno, su Prompt y los que caen con él de la cola vuelven al input solo si su Origen es humano, con la Ventana abierta y al reabrirla; los de otra Sesión o de una Suscripción ya fallan hacia quien los envió. Sus Textos intercalados, que son del humano, vuelven siempre. El Log dice el Origen en `turn_failed` (`origin`) y en `prompts_dropped` (`origins`); un Log de antes no lo dice y se lee como antes. El Prompt humano que no cabe ni en el slot vacío vuelve tal como se tecleó (con sus `@clave=valor`), y el de otro Origen no vuelve.
  - **Probado a mano** con el Swift IQ3_XXS, la function `tell` de #156 y el proxy de #166, que ahora solo corta los Pasos de los Prompts que mandó `lider171`: en `abierta171` (Ventana abierta) y en `cerrada171` (al reabrirla), de dos Prompts de `lider171` y uno humano en cola, solo volvió el humano, con su `@top_k=5` o su `@temperature=0.3`; en `grande171`, con un Output cap mayor que el slot, el humano volvió con su `@top_k=5` y el de `lider171` no entró en el input.
  - Métricas: 1770 tests (con #169 fusionado), cobertura 85 %, mutación 93 %, 41 escapes.
- #169 Tras compactar, el Organizador muestra el Contexto nuevo (2026-11-03), en paralelo con #166 y #171.
  - #169: la fila de una Sesión en el Organizador muestra el mismo Contexto que el borde de su Ventana (`OpenWindows.saved_context`): tras guardarse una Compactación deja el de antes, pasa a ninguno y luego al que mide el Servidor, y se actualiza al llegar la medida aunque no cambie ningún archivo. Sin medida en este Main (no supo medir, o tras reiniciar) queda sin Contexto hasta el Turno siguiente. La caché de filas guarda la Conversación solo cuando lleva `compacted_since_turn`, no la de cada Sesión del Perfil.
  - **Probado a mano** con el Swift IQ3_XXS y el proxy de #157: en `fila169`, de «context 7930» a «context 235» al compactar, igual que el borde y sin Turno nuevo; de «context 3199» a ninguno cuando el Servidor no supo medir, también tras reiniciar Main; y el Turno siguiente, «context 396» en la fila y en el borde.
  - Métricas: 1759 tests (con #166 fusionado), cobertura 85 %, mutación 93 %, 41 escapes.
- #166 El Prompt de un Turno fallido vuelve al input tal como se tecleó (2026-11-03), en paralelo con #169.
  - #166: al fallar un Turno, su Prompt y los que esperaban en cola y caen con él vuelven al input con sus Command prompts (como `@top_k=5`), con la Ventana abierta y al reabrirla, como ya volvían el cancelado (#156) y el que sale de la cola con ↑. El Log sigue guardando el texto que leyó el LLM; lo tecleado va aparte, en `typed` de `turn_failed` y de `prompts_dropped`.
  - **Probado a mano** con el Swift IQ3_XXS y un proxy delante que corta cada Paso y, a demanda, hace esperar la cola: en `abierta166`, con la Ventana abierta, y en `cerrada166`, al reabrirla, los dos Prompts volvieron con su `@top_k=5` y su `@temperature=0.3`; reenviado con Enter, el primero llevó otra vez su `top_k`.
  - Métricas: 1753 tests, cobertura 85 %, mutación 93 %, 41 escapes.
- #157 Tras compactar, el borde muestra el Contexto nuevo (2026-11-03), en paralelo con #158.
  - #157: en cuanto se guarda una Compactación, resumida o solo podada, el borde deja el Contexto de antes: pasa a «?» y luego al que mide el Servidor de la Conversación compactada tal como la lee su LLM, con su plantilla y su tokenizador. Si no sabe medir, se queda en «?» (y el Log dice `context_unmeasured`) hasta el Turno siguiente. La Ventana reabierta muestra lo medido; tras reiniciar Main, «?», porque la Conversación guardada lo marca (`compacted_since_turn`) hasta su Turno siguiente. Un cambio de Enlace mide contra el nuevo límite lo medido, no el Contexto de antes.
  - **Probado a mano** con el Swift IQ3_XXS y un proxy delante que, a demanda, no deja medir lo compactado: en `borde157`, de «context 18914» a «context 153» al compactar (lo mismo que mide aparte la Conversación guardada), también al reabrir la Ventana; y de «context 2972» a «context ? / 32k» cuando el Servidor no supo medir, también tras reiniciar Main.
  - Métricas: 1750 tests (con #158 fusionado), cobertura 85 %, mutación 93 %, 41 escapes.
- #158 Un Supervisor que propone un Kit avisa si su modelo no está (2026-11-03), en paralelo con #156.
  - #158: el Supervisor mira si existe el archivo de su modelo antes de lanzar `llama` (uno relativo parte de su `cwd` o, si no lo tiene, de la carpeta desde la que corre). Si no está, no lanza nada y queda `failed` con `model_missing`; unos Ajustes pendientes con un modelo que no está vuelven a los últimos que arrancaron. El Aviso de Main («Supervisor <nombre> has no model») nombra el archivo y `/server set <nombre> model=<path>`, y lo mismo dicen la espera de sus Sesiones y un cambio de Enlace que falla. El recuadro de `/kit load` busca el modelo sugerido junto a los modelos de los Supervisores de la carpeta de datos: si está, el Supervisor creado lo nombra con su ruta; si no, el recuadro lo dice con la misma orden.
  - **Probado a mano** sin Servidor (el criterio es justo que no se lance): un Kit con dos Supervisores, uno con el Swift IQ2_XXS (junto al IQ3_XXS de `base`) y otro con un modelo que no existe. El primero se creó con la ruta del modelo; el segundo, al enlazarle una Sesión, dio el Aviso y nunca escribió `server.log`.
  - Métricas: 1741 tests (con #156 fusionado), cobertura 85 %, mutación 93 %, 41 escapes.
- #156 El Prompt humano de un Turno cancelado al cerrar la Ventana vuelve al Borrador (2026-11-03), y en paralelo #159.
  - #156: al cancelarse un Turno de Origen humano (cerrar la Ventana, `/close`, `/profile switch --now`), su Prompt vuelve al Borrador tal como se tecleó, en un bloque propio, delante de sus Textos intercalados y de lo que esperaba en cola. El cambio de Perfil ya no lo devuelve por su cuenta: lo ponía detrás de los Textos intercalados y devolvía también los Prompts de otras Sesiones. Tras una caída de Main, solo vuelven al Borrador los Prompts humanos que corrían, también tal como se teclearon.
  - **Probado a mano** con el Swift IQ3_XXS: en `caja156`, con un Turno largo, uno en cola y un texto sin enviar, cerrar la Ventana dejó los tres bloques en ese orden («+2 waiting»), también al reabrirla y tras reiniciar Main; un Turno que mandó `lider156` con la function `tell` siguió hasta el final al cerrar la Ventana, y su Prompt no entró en el Borrador.
  - Métricas: 1733 tests, cobertura 85 %, mutación 93 %, 41 escapes.
- #159 Quitar las duplicaciones que dejó #123 (2026-11-03), y en paralelo #155.
  - #159: la Iniciativa de una fproc corre por el mismo camino que los Command sessions de una Sesión, con sus mismos `SessionCommandType` (su campo `initiative` dice qué es cada uno pedido por su cuenta; `@close` lleva el `<sid>`, porque la fproc no tiene Ventana); desaparece la tabla `_INITIATIVE`. `/new` crea en el Perfil y en un Subperfil con una sola secuencia (`_born`), `load_package_mold` usa `load_mold`, y `actors` es la única regla de quién actúa en una cadena de Origen. Sin cambio de comportamiento: la suite pasó sin tocar sus tests (1716). Al fusionar #155, el error de un `@secret …` que pide una fproc por su cuenta guarda solo sus primeras palabras, como el de una Sesión.
  - **Probado a mano** sin Servidor: `/new` y `/new --mold=… --bg` en un Main de la rama; la Sesión del Molde nació con sus Command sessions.
  - Métricas: 1728 tests, cobertura 85 %, mutación 93 %, 41 escapes.
- #155 `@secret` tecleado en una Ventana no deja su valor en el Log (2026-11-03).
  - #155: `@secret …` ya no es un Command session desconocido: tecleado en una Ventana, pedido por una herramienta o declarado por un módulo de `comsession/`, se rechaza con `secret_in_main_chat_only` y el uso de `/secret`. El Log, el Chat de Main y el error de la Ventana guardan solo sus cuatro primeras palabras y «…»; la línea no vuelve a la entrada, y un bloque `@secret …` no entra en el Borrador que el Estado de Main guarda en disco.
  - **Probado a mano** sin Servidor: en la Ventana de `caja`, «@secret set bot token <valor>» dejado sin Enter no llegó a `state.json`; con Enter dio el error con el uso de `/secret`, la entrada quedó vacía y ningún archivo de la carpeta de datos tenía el valor, tampoco tras `/exit`.
  - Métricas: 1716 tests, cobertura 85 %, mutación 93 %, 41 escapes. Antes: #154 La Ventana rotula cada Prompt por su Origen.
- #153 La identidad del Externo y la Iniciativa en la Admisión, como dice ADR-0026 (2026-11-03).
  - El LLM lee el Prompt de un Externo con la cabecera del proceso (`[from attached process bot]`); su identidad queda en el Turno guardado y en `turn_started`. El Prompt recuerda que lo trajo la Iniciativa (`initiative`, también en el Estado de Main y en `turn_started`), y solo ese va detrás de los humanos y de las Sesiones en la Lista de espera (la clase pasa de `fproc` a `initiative`), con la Compactación que lo precede y con la tarea del Molde de un `@new` por su cuenta. El que manda un método durante una llamada va por orden de llegada, aunque su cadena sea la misma.
  - **Probado a mano** con el Swift IQ3_XXS y una fproc `bot` que lee un buzón: el LLM de la Sesión de Juan no supo quién le escribía; con un Turno largo en marcha, el Prompt de la Iniciativa esperó detrás de uno humano posterior y el de `@call bot tell` no; y la marca siguió en la cola del Perfil tras `/profile switch` ida y vuelta.
  - Métricas: 1716 tests, cobertura 85 %, mutación 93 %, 41 escapes.
- **Siguiente:** lo que queda de «Pre-RAG · Pendientes de F2», en orden: #176 (lo que vuelve al Borrador al cancelarse o fallar un Turno, también con `/exit` y con un Turno suspendido), #179 (un `@new` de la Iniciativa con tarea no deja una Sesión huérfana con el Subperfil en su tope), #177 (el Contexto tras compactar sobrevive a Rewind, a un Turno que cedió y a `/session id`), #181 (cerrar la Ventana de un Turno de otro Origen no le da un intento más tras caer el Servidor) y #182 (al reabrir la Ventana vuelve lo de un Turno fallido aunque después llegara un Prompt de otro Origen); #176 a #179 salieron de la revisión de lo fusionado en Pre-RAG, y #181 y #182, de #174. Después, la fase del RAG, que aún no tiene issues y empieza con su prototipo (ADR-0019), o el grilling de F3. #18 sigue en `needs-triage`, para F3.
- **Preguntas abiertas para el usuario:** ¿le vale que Main apruebe solo los Sysprompts idénticos a los instalados (#104)? Los que ya editó en su Perfil `default` quedarán en cuarentena hasta `/piece approve --all`. ¿Le vale que las fsources vivan en `functions/` y las fagents en `agents/` del Perfil (#106)? ¿Le vale que, en un kernel que no encierra del todo, una tool que no escribió él ni se ofrezca al LLM, en vez de devolverle «este kernel no puede encerrar esta tool» como decía #106? ¿Le vale que `-y` para las tools se ponga solo con `/send -y` desde el Chat de Main (en la Ventana aún no hay forma), y que todas las Confirmaciones de un Paso se pregunten antes de que corra ninguna tool (#107)? ¿Le vale que lo que muestra un `@…` tecleado en la Ventana (como `@conversation_logs list`) salga en el Chat de Main, donde sale todo Command main, y no en la Ventana (#109)? ¿Le vale que la forma estructurada de `/send` (`--attach=`, `--temperature=`…) sea solo de las Sesiones y que un humano siga escribiendo `@./ruta` y `@clave=valor` en su texto (#110)? Ante el «no» a un archivo de fuera, el LLM lo volvió a pedir una vez, y saltó otro recuadro. ¿Le vale que `/grant` lo pueda escribir también un humano en el Chat de Main (pide Confirmación como si fuera de una Sesión), y que dar la fsource que declara Otorgar dé ya `@grant`, en vez de pedir las dos cosas por separado (#111)? Ahora toda Confirmación que no pidió un humano dice quién la pide (`asked by human > session:…`). ¿Le vale que `@send` y `@server` sean de Main (siempre están, y la lista por defecto los trae) en vez de módulos en `comsession/`, y que si se cancela el Turno de una tool que espera la Confirmación de su Command main, la Confirmación siga abierta y el comando corra si alguien dice sí (#108)? ¿Le vale que `/dev/shm` se declare en `@reach` en vez de ir en el mínimo, y que `HOME` sea la Carpeta de trabajo? Un `@reach` puesto en un método de una fproc ya hace fallar su descripción (#114). ¿Le vale que `@create` no venga en la lista por defecto de una Sesión (se da con `-cs=create` o con Otorgar), y que una Sesión que crea desde un Molde con tarea necesite `@send` (#112)? ¿Le vale que en el modo normal el «sí» al recuadro de una pieza creada por una Sesión la deje aprobada? El criterio del issue decía «deja la pieza en cuarentena»; el cuerpo y el ADR-0013 dicen «Confirmación y Aprobación a la vez». `-y` no aplica: un comando que pide una Sesión nunca lo trae. Sobre #113:
  - ¿Le vale que los pids de los Procesos adheridos vayan en `state/attached.json`, uno por carpeta de datos, y no en el Estado de Main, que es de cada Perfil?
  - ¿Que `@attach` no venga en la lista por defecto, como `@create`?
  - ¿Que `/detach` sea solo de un humano?
  - ¿Que el `--file` relativo de un humano parta del directorio desde el que lanzó Main?

  Sobre #114:
  - ¿Le vale que un método llamado con `/call` desde el Chat de Main no pueda ejecutar Command sessions, porque ahí no hay Sesión en la que correr?
  - ¿Que con un `@call` tecleado por un humano, los Command sessions del método no los limite la lista de la Sesión (como cuando él teclea `@…`), sino el Alcance de la fproc y la Confirmación?
  - ¿Que `@call` no venga en la lista por defecto, como `@attach`?
  - ¿Que un `@call` que ejecuta una tool reciba solo `ok`, y no lo que devolvió el método?

  Sobre #115:
  - ¿Le vale que solo se comprueben las escrituras que hace el LLM con sus tools? Las de `/call` y `@call` no se comprueban, tampoco cuando el `@call` lo lanza una fsource de una Sesión. El issue decía «un `@writes` de una Sesión».
  - ¿Que una Sesión que nunca leyó vea rechazado su primer `@writes`, aunque nada haya cambiado?
  - ¿Que una llamada cancelada que el método no deja también acabe matando el proceso, pasados su timeout y `attached.release`?
  - ¿Que `@concurrent()` con paréntesis sea «sin tope», y que `@concurrent` a secas dé error?
  - Mientras se crea la clase, adherir bloquea a Main hasta 20 s. Lo perdido aún no avisa a las Sesiones afectadas: llega con el Informe de recuperación.

  Sobre #116:
  - ¿Le vale que los eventos se agrupen en el tick (hasta un segundo de espera), y no al momento?
  - ¿Que `@subscribe` y `@unsubscribe` vengan en la lista por defecto? Solo despiertan a la propia Sesión y su cadena tiene tope.
  - ¿Que todas las respuestas de una Sesión a otra, y todos sus temporizadores, cuenten como la misma Suscripción para el tope de la cadena?
  - ¿Que un humano escriba `--reply-to=<sid>` justo tras el `<sid>` de `/send`, como primera palabra?
  - ¿Que un método solo pueda anunciar durante una llamada, y no desde un hilo propio (un bot que vigila)? Hoy da error fuera de una llamada.
  - ¿Que la Suscripción no tenga Exención propia, sino la de la Sesión a la que despierta?
  - ¿Que `subscription.repeats` sea 5 por defecto?
  - ¿Que solo una respuesta pendiente retenga la del Prompt, y no un temporizador abierto? El issue decía «si un Turno deja abierta una Suscripción».
  - Restablecer y `/reset --all` serán eventos cuando existan.

  Sobre #117:
  - ¿Le vale que `@errand` y `@batch` vengan en la lista por defecto, como `@subscribe`? El árbol y el tope de la cadena los acotan.
  - ¿Que el plazo de un Lote sea por tarea (cada una tiene el suyo, y esperar trabajadora libre también lo gasta) y no uno para todo el Lote?
  - ¿Que una tarea que falla salga otra vez en el tick siguiente (hasta un segundo después), y no al momento?
  - ¿Que el Informe de recuperación, por ahora, solo traiga los Encargos? Lo demás que nombra el GLOSSARY (Procesos adheridos perdidos, Turnos cortados) llegará con sus issues.
  - ¿Que «tener sitio» en el Lote signifique un slot del Servidor que no ocupa ya esta Main? Otros clientes del mismo Servidor no se ven.
  - ¿Que las tareas de un Lote se escriban como lista JSON o una por línea, y las de un Encargo con tarea propia por trabajadora como objeto JSON (`{"obra1": "…"}`)?
  - ¿Que una trabajadora cuyo Turno cortó un apagado limpio (`/exit`) cuente como sin respuesta en el siguiente arranque, igual que si Main muriera?

  Sobre #118:
  - ¿Le vale que el Command session se llame `@status` (un humano: `/status <sid>`), y que venga en la lista por defecto porque solo lee?
  - ¿Que muestre las Suscripciones y Encargos de todas las Sesiones del Perfil activo, y no solo los de la Sesión que pregunta?
  - ¿Que Servidores y Supervisores, que son de la máquina, salgan igual desde cualquier Perfil?
  - Un Supervisor dormido sale sin capacidad: sus slots solo se saben cuando su Servidor está listo.

  Sobre #119:
  - ¿Le vale que la poda no reescriba nada (las salidas siguen enteras en la Conversación y la Ventana; solo cambia lo que lee el LLM), y que pode solo salidas de más de 400 caracteres?
  - ¿Que la línea podada estime los tokens por caracteres (~4 por token), sin preguntar al Servidor?
  - ¿Que el Turno ceda según la medida de admisión, que cuenta en caracteres y sobra? Cede más de lo necesario: a veces sale el aviso y la medida exacta ve que cabe, y sigue sin compactar.
  - ¿Que `@compact` venga en la lista por defecto? Solo toca la propia Conversación, y un Rewind la devuelve.
  - ¿Que la Notificación de estado vivo no se deje cuando la Sesión no tiene nada abierto? El issue decía «Main añade una».
  - ¿Que traiga solo los Procesos adheridos cuyos métodos tiene la Sesión, y no todos los de Main?

  Sobre #120:
  - ¿Le vale que las líneas al LLM vayan en inglés, como las demás Notificaciones de Main?
  - ¿Que la línea de Contexto se deje al guardarse el Turno, y no si el Turno falla o se cancela?
  - ¿Que el corte por Shift+Enter siga con su fuente `interrupted`, aparte de `cut`?

  Sobre #121:
  - ¿Le vale que solo entre texto llano, y que lo que lleva Command prompts o `-y` espere su propio Turno, con lo escrito después detrás, para no cambiar el orden?
  - ¿Que lo que no cabe en lo que le queda al slot (contado a un token por carácter) espere su Turno en vez de entrar?
  - ¿Que lo escrito mientras el Prompt anterior aún esperaba slot cuente como el siguiente Prompt y no entre en su Turno?
  - ¿Que, al cortar con Shift+Enter unas tools que seguían corriendo, el LLM lea en cada llamada que se cortó y que pudo hacer parte de su trabajo?
  - Al cancelarse un Turno (cerrar la Ventana), lo intercalado vuelve al Borrador, detrás del Prompt de ese Turno si su Origen es humano (#156).

  Sobre #156:
  - ¿Le vale que, si Main se cierra (`/exit`) con un Turno humano en marcha, su Prompt no vuelva al Borrador al momento sino al arrancar de nuevo, como ya hacía tras una caída?
  - ¿Que un Prompt cancelado vuelva como se tecleó (con sus `@clave=valor`), y no con el texto que leyó el LLM? Desde #166 también el de un Turno que falla.

  Sobre #166:
  - El GLOSARIO dice que el Prompt de un Turno que falla vuelve al input solo si su Origen es humano, pero hoy vuelve sea cual sea; y el que no cabe en el slot vuelve con el texto que leyó el LLM. No entraba en #166: es #171 (hecho).

  Sobre #171:
  - ¿Le vale que, si falla un Turno de otra Sesión, los Textos intercalados que el humano escribió durante él sí vuelvan al input? Son suyos (ADR-0024), aunque el Prompt no lo sea.
  - Al reabrir la Ventana no vuelven los Textos intercalados de un Turno fallido, y el Prompt humano que no cabe se pierde si se envió con la Ventana cerrada: es #174 (hecho).

  Sobre #174:
  - ¿Le vale que el Prompt humano que no cupo, con la Ventana cerrada, deje de volver al reabrirla solo si después el humano manda otro Prompt (o ya se le devolvió), y no si llega uno de otra Sesión o termina otro Turno? Con la Ventana abierta se queda en el input pase lo que pase; lo de un Turno fallido aún se borra con cualquier Prompt o Turno posterior, y eso es #182 (la pregunta de la revisión de Pre-RAG sobre #171).
  - Si al reabrir vuelven a la vez lo de un Turno fallido y uno o más Prompts que no cupieron, el input los muestra en el orden en que los habría dejado con la Ventana abierta: lo último devuelto, primero. ¿Le vale?

  Sobre #122:
  - ¿Le vale que `@open` sea también un Command session? El issue nombraba `@bg`, `@close` y `@windows`, pero la fsource lo necesita para traer una Sesión.
  - ¿Que `@windows`, `@open` y `@bg` vengan en la lista por defecto? Las Sesiones de antes no los tienen: se les dan con `/func add <sid> @windows @open @bg`.
  - ¿Que `@close` cierre solo la Ventana en que se teclea? Las demás se cierran con `/close <sid>` en el Chat de Main.
  - ¿Que la marca de «pide atención» sea un Aviso, que se descarta como los demás y se pierde al reiniciar Main, como ellos?
  - ¿Que la lista que pide una tool no se escriba en el Chat de Main? La ve la Ventana de la Sesión, como salida de la tool; lo que teclea un humano sí sale.
  - ¿Que, con una cadena de varias Sesiones, cada una tenga que ser la destino o su creadora para que `@bg` la mande a segundo plano?
  - Un LLM aún no tiene cómo crear Sesiones, salvo con un módulo de `comsession/` que escriba un humano; las Sesiones que crea una fproc llegan con #123.

  Sobre #154:
  - ¿Le vale que el rótulo de un Prompt que trae una fproc sea solo `process:<nombre>`, sin la identidad del Externo? Esa identidad queda en el Turno y en el Log, y con #153 ya no va en la cabecera del texto, así que en la Ventana no se ve quién escribió.

  Sobre #155:
  - ¿Le vale que `@secret` se rechace siempre, también si lo pide una herramienta o si un módulo de `comsession/` declara uno con ese nombre?
  - ¿Que tras el error la línea no vuelva a la entrada, como sí vuelve la de otro `@…` que falla, y que un bloque `@secret …` no se guarde en el Borrador?
  - Mientras la Ventana sigue abierta, ↑ aún trae la línea entera, igual que el Chat de Main con `/secret`: está solo en la memoria de ese proceso y no se escribe en disco. ¿Le vale así?
  - `/send <sid> @secret …` escrito en el Chat de Main se rechazaba (`session_command_in_prompt`), pero el Log guardaba la línea entera, como todo `/send`: desde #178 se recorta (ver abajo).

  Sobre #153:
  - ¿Le vale que la tarea del Molde de una Sesión que la fproc crea por su cuenta (`@new` de la Iniciativa) también vaya detrás, como un Prompt de la Iniciativa, y la de un `@new` que pide un método durante una llamada vaya por orden de llegada?
  - La clase de espera se llama ahora `initiative` en `servers/<puerto>.waits.json`, que comparten los Mains de un Servidor. Un Main de antes de #153 que use el mismo Servidor a la vez leería esa lista como vacía hasta reiniciarse. ¿Le vale, o prefiere conservar el nombre `fproc`?

  Sobre #159:
  - ¿Le vale que `SessionCommandType`, que también importan los módulos de `comsession/`, lleve el campo `initiative`, aunque solo lo pongan los de Main? Así la Iniciativa usa los mismos tipos, en vez de una tabla aparte.

  Sobre #157:
  - ¿Le vale que el Contexto tras compactar sea la Conversación como la lee el LLM, sin los esquemas de sus tools ni sus imágenes (que van por su nombre, como en las medidas de la propia Compactación)? El Turno siguiente, que sí los cuenta, puede mostrar algo más.
  - ¿Que, tras reiniciar Main, el borde de una Conversación compactada sin Turno nuevo diga «?» en vez de volver a medirla? Lo medido vive solo en la memoria de Main.
  - ¿Que tras un Rewind a una Conversación compactada sin Turno nuevo el borde también diga «?»? El último Turno que queda se midió con la Conversación de antes de compactar.
  - El Organizador sigue mostrando el Contexto de antes hasta el Turno siguiente: es #169, ya hecho.

  Sobre #169:
  - Con el Contexto desconocido, la fila del Organizador no lo muestra (como antes con una Sesión sin Turnos), mientras el borde dice «context ? / 32k». ¿Le vale, o prefiere «?» también en la fila?
  - Cuando una Compactación resume todos los Turnos, una fila sin título deja de mostrar el inicio del primer Prompt (solo su id), porque lo toma del primer Turno que queda. ¿Le vale, o prefiere que lo busque en el archivo de lo resumido?

  Sobre #158:
  - ADR-0014 dice que, sin su modelo, el Supervisor «sigue dormido con un Aviso». Queda `failed` (`model_missing`), no dormido: uno dormido se despierta con el trabajo y volvería a fallar en bucle; fallido, lo intenta el siguiente Prompt, y eso no cuesta nada porque no lanza `llama`. ¿Le vale?
  - «Los Supervisores de la máquina» son los de `servers/` de la carpeta de datos; no se mira otra carpeta de datos de la misma máquina. ¿Le vale?
  - Si el modelo no está, el Supervisor creado guarda solo el nombre del archivo, que `llama` busca en la carpeta desde la que se lanzó el Main que lo despierta. ¿Le vale, o prefiere que el recuadro pida la ruta en ese momento?

  Sobre #178:
  - ¿Le vale que `/send <sid> @secret …` desde el Chat de Main se rechace con `secret_in_main_chat_only` y el uso de `/secret`, como en la Ventana, y que el Log lo recorte? Era la pregunta de #155: el issue pedía que el grep del valor en la carpeta de datos no diera nada, y el Log de Main está en ella.
  - El recorte del Log vale para todo `/send` cuyo texto empieza por `@secret`, también el de una Sesión, aunque ese texto sí llega a la otra Sesión como Prompt (Main nunca lee el texto de una Sesión). ¿Le vale así?

  Sobre la revisión de Pre-RAG (2026-11-03):
  - ¿Un Supervisor cuyo `extra` lleva `-hf`/`--model-url` debería poder arrancar aunque el archivo de `model` aún no exista? (#158)
  - Al reabrir la Ventana, un Prompt posterior de otro Origen hace que no vuelva el Prompt humano fallido: ¿que solo cuenten como posteriores los Prompts humanos? (#171) Decidido que sí, como #174 hizo con el que no cupo: es #182.
  - Un Prompt de la Iniciativa que estaba en cola en un Estado de Main guardado antes de #153 se lee sin su clase y adelanta a los humanos, una sola vez al actualizar: ¿vale así? (#153)
- **Queda pendiente, menor:** la imagen no tiene aún descripción (llega con el Sysprompt `describir`, ADR-0019); y en el Chat de Main, que corre en el terminal del humano, Ctrl+Enter y Shift+Enter solo se distinguen si ese terminal manda `modifyOtherKeys`. Lo demás que estaba aquí pasó a issues (#154–#159).
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
12. #176 Lo que vuelve al Borrador al cancelarse o fallar un Turno, también con `/exit` y con un Turno suspendido (de #156, #166, #171 y #174)
13. ✅ #178 El valor de un `@secret` escrito en el Chat de Main no llega al Borrador ni al Log (de #155)
14. #179 Un `@new` de la Iniciativa con tarea no deja una Sesión huérfana cuando el Subperfil está en su tope (de #153 y #159)
15. #177 El Contexto tras compactar sobrevive a Rewind, a un Turno que cedió y a `/session id` (de #157 y #169)
16. #181 Cerrar la Ventana de un Turno de otro Origen no le da un intento más tras caer el Servidor (de #174)
17. #182 Al reabrir la Ventana vuelve lo de un Turno fallido aunque después llegara un Prompt de otro Origen (de #171 y #174)

## RAG · Memoria de consulta

Sin issues ni milestone todavía. Va después de F2, porque necesita el encierro, los Command sessions, las fsources, las Notificaciones, los Procesos adheridos y la Aprobación, además de los Supervisores (#24–#27) y los Perfiles (#30); no necesita F3 (ADR-0019). Empieza con un prototipo desechable que mide lo que el diseño aún no sabe: los umbrales y el tope del reranker, cuánto tarda el reranker en CPU, si el modelo de embeddings cabe en la GPU junto al Q6, si el modo router de `llama serve` sirve con dos modelos, si la build en uso corre Qwen3-Reranker, y si el modelo usa `buscar` y hace caso a las Pistas. Después: la fproc `rag` adherida por el Ajuste `rag`; los Supervisores de embeddings y reranker, con `@embed` y `@rerank`; la búsqueda por palabras (FTS5), que funciona sin modelos; los Segmentos por Sesión; `buscar` y `leer` con sus Fichas; las Pistas; los Corpus, con `/rag add` y su Aprobación; el historial por Turno, con las marcas de deshecho y la puesta al día; el manual de AllIn y el catálogo de functions; y la descripción de imágenes y video con el Sysprompt `describir`. De la revisión del Supervisor: el Ajuste `rag` es de cada Sesión, copiado del de Main en `/new`, y Main adhiere el RAG siempre; sus Supervisores despiertan cuando el RAG los necesita, y `describir` va al Enlace por defecto (ADR-0019, ADR-0022). Indexar un Corpus, reindexar al adherirse y la puesta al día son llamadas largas, y el RAG contesta búsquedas mientras tanto (ADR-0019). Su Alcance declara solo `@embed` y `@rerank` (ADR-0025). Del grilling de las fprocs: el RAG de cada Subperfil, otra instancia de la fproc `rag`, al que la fproc añade lo que trae con su procedencia (ADR-0026).

## F3 · Submains y Traspaso

Submains, Traspaso de Sesiones dentro del árbol, y #18 (FIFOs huérfanas por Main). Antes, en su grilling: el protocolo entre Mains y las primitivas entre Mains del árbol (ADR-0011), y con él el Máximo de Ventanas compartido por el árbol y un Organizador de todo el árbol (ADR-0020). Un submain no hereda `--devastation`: sus Confirmaciones solo suben al Main de arriba si un humano le pone la Exención, al crearlo o después, o si la trae un Plano, y cada Main lo marca en sus bordes. `/tree` dibuja el árbol de Mains y `/tree --exempt`, el de Exención (ADR-0013). En su grilling, además: que un Traspaso o crear en otro Main no dejen cubierto lo que no lo estaba.

## Después

Lo que trae código ajeno (fagent del LLM y Kits, segunda parte) ya llega con el encierro de F2: sus functions corren con el Alcance aprobado, y en un kernel sin Landlock no corren (ADR-0016).

Sin fecha ni orden fijo: fagent y packages (con Crear de functions desde una Sesión), encadenar functions, Clon y Goal. Kits, segunda parte: functions con cuarentena, Moldes, Planos, historial inicial y entrada (ADR-0014). Restablecer (`/session reset`) y `/reset --all` (ADR-0015). La personalidad, con un LoRA entrenado con las conversaciones del usuario: es un feature aparte que se discutirá por su cuenta, y no va en el RAG (ADR-0019). Una fproc `discord`, genérica, con una instancia por servidor de Discord: el ejemplo guía de ADR-0026. Adherir un servidor MCP como fproc (sin `@writes`, una llamada a la vez), que depende de las functions cuya cara no es Python. Functions que traen su propio código compilado, o cuya cara (nombre, parámetros, decoradores y Alcance) no es Python (ADR-0016).
