# AllIn

Se pronuncia "Alyn". Antes se llamaba Neva; ese nombre ya no se usa en ningún lado.

Los términos están en español; entre paréntesis va el nombre canónico en código (todo el código, comentarios y Log van en inglés).

Orquestador de terminal para conversar con un LLM local (vía OpenAI SDK) desde varias ventanas, donde un proceso central ordena y atiende las peticiones de cada una.

## Procesos

**Main**:
El proceso orquestador (`python -m allin`, paquete `allin.main`); dueño único del estado de las Sesiones que orquesta y de su cola de Turnos. Por carpeta de datos corre un solo Main raíz, con un Perfil a la vez; sus submains corren aparte, cada uno con su id (ADR-0007).
_Avoid_: abuelo, padre, proceso principal, servidor (el Servidor es `llama serve`)

**Perfil** (`Profile`):
Identidad con nombre de un Main raíz, guardada como archivos modulares en `profiles/<nombre>/`: sus Ajustes de Main (con el Enlace por defecto), las Sesiones que le pertenecen, sus submains, su Estado de Main y sus piezas (functions, Sysprompts, Moldes, Planos y Temas). Su nombre lo elige el usuario y solo `/profile new` lo crea: nombrar uno que no existe es un error, nunca lo crea. Arrancar un Perfil es arrancar el Main raíz con él (`--profile`); su id sigue siendo `root`, así que el dueño de sus Sesiones no cambia. Trabajar en otro proyecto es cambiar de Perfil, sin perder nada del anterior. `/profile export` lo copia entero en una carpeta, para respaldarlo o mudarlo, y `/profile import` crea uno nuevo desde esa copia. Una Sesión puede pertenecer a varios Perfiles a la vez (compartida): como solo corre un Main raíz, eso no cambia quién la orquesta. Se comparte con `/profile share <sid> <perfil>` o abriéndola con `/open` desde otro Perfil, que lo avisa; `/profile unshare` la quita de una lista, y una Sesión sin Perfil sigue en `sessions/`. Es privado: lo que se comparte con otros usuarios es un Kit, y desde una Sesión, `/send` y Consultar solo alcanzan las del Perfil activo.
_Avoid_: perfil de servidor, perfil de main, perfil de sesión, workspace

**Subperfil** (`Subprofile`):
Espacio propio de una instancia de Proceso adherido dentro del Perfil en que se adhirió: las Sesiones que creó, su RAG y su Carpeta de trabajo, aparte de los del Perfil. Es una frontera como la del Perfil: `/send` no la cruza en ningún sentido, y solo un humano pasa de un lado a otro. Lo tiene solo la fproc que lo declara (`@subprofile`), y existe mientras su Perfil está activo. Sus Sesiones nacen en segundo plano, fuera de la lista del Perfil, y salen plegadas bajo la instancia en el Organizador. Un humano le pone la Exención o le cambia el tope de Turnos con `/subprofile <nombre> [exempt=on|off] [turns=<n>]`: cuántas de sus Sesiones tienen a la vez un Turno en marcha o en cola, una por defecto; pasado el tope, la fproc recibe `subprofile_busy`.
_Avoid_: perfil de fproc, submain

**Kit** (`Kit`):
Receta portable y versionada de un entorno de trabajo (Sysprompts, Moldes, functions, Goals y composiciones, Temas, parte de los Ajustes de Main y Ajustes del Servidor sugeridos) que se exporta desde un Perfil y se carga en otro, de esta máquina o de otro usuario. Nunca lleva Sesiones ni nada de ejecución, y solo fuentes que se puedan leer: uno con `.pyc` o binarios se rechaza. Se exporta con `/kit export` y se carga con `/kit load`, que copia sus piezas en la carpeta del Perfil; cada una recuerda de qué Kit y versión vino (`kits.json`), y un Perfil puede cargar varios. Cargarlo no ejecuta nada con efectos, y sus piezas (functions, Sysprompts, Moldes, Planos e historiales iniciales) esperan en cuarentena hasta que un humano las aprueba.
_Avoid_: plantilla, paquete, perfil (para lo que se comparte)

**Traspaso** (`Handover`):
Pasar una Sesión de un Main a otro dentro del mismo árbol (main → submain, submain → main, submain → submain): desde ese momento sus Prompts entran en la cola del nuevo Main y él ordena sus Turnos. No se prohíbe: se avisa. Compartir una Sesión entre Perfiles no es un Traspaso.
_Avoid_: mover, migrar

**Lanzador** (`Launcher`):
El componente de Main que abre una Ventana: lanza xterm con `Popen` en una sesión nueva (`setsid`) y un thread vigía espera su terminación (ADR-0003). No hay proceso intermedio.
_Avoid_: hijo, worker

**Ventana** (`Window`):
El proceso xterm/curses (`python -m allin.window`) que muestra una Sesión y recibe lo que escribe el usuario. A lo sumo una Ventana por Sesión; una Sesión trabaja igual sin ella. Cerrarla cancela el Turno de su Sesión solo si el Prompt de ese Turno tiene Origen humano; para cualquier otro Turno es una mirilla, y cerrarla no detiene el trabajo. Su Enter nunca se bloquea, y lo que se envió y sigue en cola vuelve al Borrador al cerrarla. Mandar su Sesión a segundo plano no es cerrarla.
_Avoid_: nieto, terminal, xterm (como nombre del concepto)

**Segundo plano** (`background`):
Estado de una Sesión abierta que no tiene su Ventana a la vista. Sigue exactamente igual que con ella: recibe Prompts, corre sus Turnos y sus tools, y la alcanzan otras Sesiones y submains; solo le falta la pantalla. No está dormida ni se cancela nada al pasar a segundo plano, y al traerla de vuelta su Ventana se abre donde va. Una Sesión abierta está a la vista (con su Ventana) o en segundo plano; cerrar su Ventana es otra cosa.
_Avoid_: minimizada, oculta, dormida, suspendida

**Máximo de Ventanas** (`windows.max`):
Ajuste de Main: cuántas Sesiones pueden estar a la vista a la vez en ese Main. Las demás abiertas quedan en segundo plano: lo que abre un humano desplaza a la que lleva más tiempo sin usar, y lo que abre una Sesión nace en segundo plano. El tope compartido por un árbol de submains se decide en F3.

**Organizador** (`Organizer`):
Pantalla temporal de Main que lista las Sesiones abiertas del Perfil, a la vista o en segundo plano, para traer una, mandarla a segundo plano o cerrar su Ventana con un clic. Es parte de Main, no una pieza, así que sus clics tienen Origen humano. No es la Ventana de ninguna Sesión, no cuenta para el Máximo de Ventanas y no vuelve al reiniciar Main.
_Avoid_: fsource organizadora, barra de tareas, Lanzador (es el que abre las Ventanas)

**Pide atención** (`attention`):
Marca de una Sesión que un LLM, con la fsource `windows`, quiso traer a la vista cuando no cabían más Ventanas: no desplaza a nadie, así que sigue en segundo plano, y la marca sale como Aviso en el Chat de Main y en su fila del Organizador hasta que la Sesión está a la vista o el humano descarta el Aviso (ADR-0020). Se va también al cerrarla o al dejar su Perfil, y, como los demás Avisos, con un reinicio de Main.
_Avoid_: urgente, notificación (es otra cosa)

**Servidor** (`Server`):
El proceso `llama serve` que atiende los Pasos de las Sesiones enlazadas a su Supervisor. Lo lanza un Supervisor, o el usuario a mano; en ese caso Main solo lo observa. Los de los modelos del RAG no atienden Pasos: calculan embeddings o reordenan para él.
_Avoid_: deploy, LLM (como nombre del proceso)

**Supervisor** (`Supervisor`):
Entidad con nombre y sus propios Ajustes del Servidor que, cuando está **activo**, es un proceso de AllIn que lanza su Servidor, lo relanza si cae y lo reinicia con otros Ajustes cuando se le pide; **dormido**, solo existen sus Ajustes. Despierta cuando le llega trabajo de una Sesión enlazada a él, si cabe en VRAM y sin dormir a otro; al que durmió un humano solo lo despierta un humano. No muere con Main ni se duerme solo. Lo comparten todas las Sesiones enlazadas a él, sean del Main que sean, y se reparten los slots de su Servidor.
_Avoid_: singleton, el script que ejecuta llama, servidor por defecto

**Adoptar** (`/server adopt`):
Poner bajo un Supervisor nuevo el `llama serve` que un humano lanzó a mano y que escucha en el puerto del `base_url` de Main. Sus Ajustes del Servidor salen de la línea del proceso (`/proc/<pid>/cmdline`) y de su carpeta de trabajo; con Confirmación, ese proceso se cierra y el Supervisor lo relanza. No se adopta uno que ya corre bajo un Supervisor, ni uno que lleva `--context-shift`.
_Avoid_: importar, capturar el servidor

**Enlace** (`server` link):
Ajuste de Sesión que nombra el Supervisor al que van sus Pasos. Main guarda uno en su plantilla de Sesión, el Enlace por defecto, que se copia en `/new` y que heredan sus submains; cambiarlo no mueve a las Sesiones ya creadas. Sesiones del mismo Main pueden usar Supervisores distintos, y los Pasos de Main que no son de ninguna Sesión van al Enlace por defecto. Una Sesión nunca cambia sola de Supervisor, aunque otro esté libre, y lo conserva en un Traspaso.
_Avoid_: adherir (es lo que se hace con un Proceso adherido), url, servidor de Main

## Sesiones y registros

**Sesión** (`Session`):
Entidad persistente con id corto y carpeta propia (Conversación, Log, ajustes, functions); sobrevive al cierre de su Ventana. Un humano puede cambiarle el id, y el viejo sigue valiendo como alias. Guarda qué Sesión la creó, si la creó el LLM de una (`creator`): esa puede mandarla a segundo plano, si todas las Sesiones de la cadena pueden (ADR-0013, ADR-0020).
_Avoid_: ventana, chat

**Carpeta de trabajo** (`workspace`):
La carpeta donde las tools de una Sesión pueden escribir sin declarar nada más, y su directorio actual: `<carpeta de datos>/workspaces/<id>/`, aparte de la carpeta de la Sesión, que guarda su estado. La Conversación nombra sus archivos en relativo. Solo un humano la puede apuntar a otra ruta (`/session workspace <sid> <carpeta>|default`), nunca dentro de AllIn; si la contiene, el Encierro deja fuera lo de AllIn. Esa ruta no es un Ajuste y nunca viaja en plantillas, Moldes ni Kits. Entre Sesiones no se comparte por defecto: los archivos viajan como adjuntos de `/send`, que Main copia a `.allin/adjuntos/<prompt>/` (ADR-0016). Un Proceso adherido tiene la suya propia.
_Avoid_: carpeta de la Sesión, workdir, sandbox

**Chat** (`Chat`):
Lo que se dibuja en el pad de chat de una Ventana o de Main: mensajes, etiquetas, errores y salidas de comandos. Se reconstruye a partir de Conversación + Log.
_Avoid_: historial, conversación

**Conversación** (`Conversation`):
La lista de mensajes/ítems que el LLM recibe como `input`; es lo único que el modelo "ve".
_Avoid_: chat, historial

**Log** (`Log`):
Registro con fecha y hora de los eventos de una Sesión o de Main; siempre activo, nunca se apaga. El Log de Main y el de cada Sesión son independientes.
_Avoid_: historial

**Estado de Main** (`MainState`):
Lo que Main guarda de sí mismo tras cada cambio, en `state/<main_id>.json` (el del Main raíz, en `state.json` de la carpeta de su Perfil), para que al reiniciarse (tras `/exit`, una señal o un `kill -9`) el usuario vuelva a estar donde estaba: Ventanas abiertas (con el pid de su xterm, para cerrarlas si Main muere y quedan huérfanas, ADR-0007), Sesiones en segundo plano, Borradores, cola de Prompts y Prompts de los Turnos en curso, con sus Textos intercalados. Lo de cada Sesión (Conversación, Log, Ajustes) no va aquí.
_Avoid_: snapshot, sesión de Main

**Borrador** (`draft`):
Lo que hay en el input de una Ventana sin enviar; la Ventana se lo manda a Main cuando el usuario deja de teclear un momento, y vuelve al input al reabrirla o al reiniciar Main. Es una lista de bloques: el que se ve en el input y los que esperan detrás (el borde dice `+N waiting`). Un Prompt de Origen humano que vuelve al input (Turno fallido o interrumpido) se muestra como bloque propio y lo que había pasa a esperar; nunca se mezclan ni se repiten. Enter envía solo el bloque que se ve y muestra el siguiente.

**Sysprompt** (`Sysprompt`):
Archivo de texto plantilla en `sysprompts/`, dentro de la carpeta de su Perfil (los de la carpeta de instalación son la semilla con la que nace `default`), que, rellenado con los Ajustes de la Sesión, se vuelve el mensaje `developer` inicial de la Conversación. Es dato: Main lo rellena y nunca lo ejecuta. La Sesión guarda el texto renderizado, no la ruta: editar el archivo después no afecta a Sesiones existentes. Solo se puede fijar o cambiar mientras la Conversación no tiene Turnos (al crearla, o tras un Rewind hasta el inicio).
_Avoid_: system prompt (como nombre del archivo)

**Sesión coordinadora** (`coordinator`):
Papel de una Sesión que reparte trabajo a otras con Encargos y decide con sus resultados; se lo dan su Sysprompt o su Molde, no es un tipo de Sesión aparte.
_Avoid_: orquestador (es Main), maestro

**Sesión trabajadora** (`worker`):
Papel de una Sesión que recibe trabajo de una Sesión coordinadora; suele nacer de un Molde, sin Ventana.
_Avoid_: esclava, subventana

**Molde** (`Mold`):
Receta con nombre para crear una Sesión especializada: su Sysprompt, sus Ajustes, sus functions y, si se quiere, un historial inicial (mensajes que cuentan como inicio de la Conversación, no como Turnos) y una tarea inicial. La usan el usuario, las fsources y los submains para crear Sesiones que a su vez pueden crear otras. No es un Perfil. Es una pieza del Perfil (`molds/<nombre>.toml`, `mold:<nombre>`) y solo se usa aprobada: `/new --mold=<nombre>`. La tarea inicial sale como un `/send` con el Origen de quien crea, y una Sesión que crea solo pasa lo que tiene (#112).
_Avoid_: perfil, plantilla, preset

**Crear** (`Create`):
Primitiva que hace algo nuevo: una Sesión, desde la plantilla o un Molde, o una pieza. Una pieza se crea con `/create <sysprompt:archivo|mold:nombre> <contenido>`, o con `@create` desde una Sesión. La de un humano queda aprobada. La de una Sesión pide un recuadro que la muestra entera y es Confirmación y Aprobación a la vez; si `--devastation` contesta por él, queda en cuarentena hasta que un humano la apruebe. `@create` no está en la lista por defecto (ADR-0013).

**Plano** (`Blueprint`):
Receta de un árbol de Mains: qué submains, qué Sesiones crea cada uno desde qué Moldes, cuáles abren Ventana o nacen en segundo plano y cuál coordina. Viaja en un Kit; crear desde un Plano solo añade, nunca toca lo que ya existe.
_Avoid_: topología, red, estructura (como nombre del concepto)

**Informe de recuperación** (`RecoveryReport`):
Lo que Main reúne al arrancar cuando su apagado anterior no fue limpio o cortó Turnos: lo que quedó a medias (Turnos, Ventanas, Procesos adheridos, Encargos). Queda en el Log, se muestra al usuario como Aviso y llega a cada Sesión afectada como Notificación; dice qué quedó a medias, no por qué murió Main.
_Avoid_: crash report, volcado

**Ajustes** (`Settings`):
Parámetros que gobiernan a Main, a una Sesión o a un Proceso adherido, cuyos Ajustes declara su fproc. Se heredan por copia: lo ya creado no cambia cuando cambia el origen. La excepción es el Tema, del que se copia el nombre y que se lee en vivo (ADR-0018).
_Avoid_: settings, config

**Clave** (`SettingName`, `SettingKey`):
Nombre con puntos de un Ajuste suelto, tal como lo toman `/set` y `/session set` (`session.sampling.temperature`, o `sampling.temperature` en una Sesión). Cada Clave declara en el registro de Claves su tipo, su valor por defecto, su validación, si es de Main o de Sesión y para qué sirve; de ahí saldrá la ayuda (#23). Una Clave desconocida o un valor que su tipo no admite son un error y no cambian nada. Un valor vacío deja sin fijar una Clave que lo admite (el muestreo, `parallel`).
_Avoid_: opción, campo

**Ajustes de Main** (`MainSettings`):
Los que Main usa para sí mismo (su idioma, su Tema, `parallel`…); no se comparten con las Sesiones.

**Ajustes de Sesión** (`SessionSettings`):
Los que usa una Sesión y su Ventana: parámetros del LLM, de sus tools (`tools.timeout`) y de interfaz (idioma, Tema, fuente…). Main guarda unos de plantilla que se copian a la Sesión en `/new` y que no usa, salvo el Enlace por defecto para sus propios Pasos; después se cambian por Sesión con `/session set`, con efecto en su Ventana aunque esté abierta.

**Ajustes actuales** (`current settings`):
La versión de los Ajustes de Main (incluida la plantilla de Sesión) que rige ahora; se cambian con `/set` y son los que copian las Sesiones nuevas y los submains. Se guardan en `settings/current.json` de la carpeta del Perfil; si ese archivo no es válido, Main dice qué clave falla y arranca con los por defecto. `/set` a secas los muestra.

**Ajustes por defecto** (`default settings`):
La versión con la que Main arranca tras un `/reset`; se cambia con `/set --default` y se guarda en `settings/default.json`.

**Ajustes del Servidor** (`ServerSettings`):
Los parámetros de carga con los que arranca el Servidor (modelo, contexto, slots, caché KV, GPU…); solo cambian reiniciándolo. No son los parámetros de muestreo, que viajan en cada Paso desde los Ajustes de Sesión.
Los **en curso** son con los que corre el Servidor ahora (los informa el Supervisor); los **pendientes** son los que se aplicarán en su próximo arranque, sea por reinicio pedido o por caída. Si los pendientes no arrancan, el Supervisor vuelve solo a los **últimos que arrancaron** (`fallback`), deja el motivo en su estado y Main lo cuenta en su Chat y su Log.

**Tema** (`Theme`):
Conjunto con nombre de Ajustes visuales (letra, colores por tipo de línea, borde, tamaño de la Ventana y caracteres especiales) en `themes/<nombre>.toml`, dentro de la carpeta de su Perfil. Lo usan las Ventanas y el Chat de Main. Una Sesión guarda su nombre, no una copia, así que editarlo cambia al momento todas las Ventanas que lo usan; las claves sueltas sobrescritas tienen prioridad sobre él. Es dato y no pasa por cuarentena, pero solo admite valores con forma fija (ADR-0018).
_Avoid_: skin, estilo

**Atajo** (`key`):
Tecla asignada a una acción de la Ventana o del Chat de Main (enviar, saltar de línea, interrumpir…) o a un comando: un Command session en la Ventana, un Command main en el Chat de Main. Están en los Ajustes de cada Sesión, para su Ventana, y en los de Main, para su Chat. Pulsarla ejecuta con Origen humano, así que solo un humano los cambia (`/key`) y ningún Kit los trae. Cada tecla tiene a lo sumo un Atajo, y siempre queda una para enviar.
_Avoid_: keycap, keybinding, tecla rápida

## Ejecución

**Prompt**:
Una petición al LLM para una Sesión, en espera en la cola de Main, siempre con su Origen. Nace del Command main `/send` (lo que el usuario envía con Enter es `/send` a su propia Sesión) o de una Suscripción que despierta. Si su Turno falla o se cancela, vuelve al input solo si su Origen es humano; si no, falla hacia quien lo envió y queda en el Log. Su respuesta es la del último Turno de su cadena: un Turno que deja abierto un Encargo, un `--reply-to` o un Goal todavía no responde (ADR-0011).
_Avoid_: /run

**Turno** (`Turn`):
La atención completa de un Prompt: empieza cuando Main lo saca de la cola y termina cuando un paso concluye sin llamadas a tools pendientes, o al cancelarse o fallar. Una Sesión tiene a lo sumo un Turno activo: lo que el humano envía desde su Ventana mientras dura entra en él en el siguiente Paso, y los Prompts de otras Sesiones esperan su propio Turno. Sesiones distintas pueden tener Turnos activos en paralelo, y sus Pasos corren a la vez hasta el número de slots de su Servidor, que se reparten todas las Sesiones enlazadas a él; un Turno cuyas tools corren no ocupa slot.
_Avoid_: request, petición (para el ciclo completo)

**Turno suspendido** (`suspended turn`):
Turno que espera porque una caída o un reinicio de su Servidor le dejó un Paso a medias: conserva su Conversación hasta antes de ese Paso y lo repite, como Continuación, cuando el Servidor vuelve a estar listo. Si el mismo Paso queda a medias en dos caídas seguidas, el Turno falla. Un Turno cancelado (cerrar su Ventana, `/close`, Shift+Enter, un Encargo que ya no lo espera) nunca se suspende, aunque su caída o el reinicio llegue antes que la cancelación; y uno suspendido que sale de la cola sale como uno fallido o cancelado: lo que abrió se cancela y lo que tecleó un humano vuelve al input (#196). No es un Turno cortado, que se guarda con lo que generó, pero Shift+Enter lo vuelve uno: el suspendido, o el cedido para compactar, se guarda cortado con los Pasos que terminó, en cuanto acaba la Compactación a la que cedió, si la hay, y lo escrito va detrás, como un Turno nuevo (#208).

**Texto intercalado** (`interjected`):
Texto llano que el humano envía desde la Ventana de una Sesión mientras corre un Turno de esa Sesión y que entra en ese Turno: lo lee el siguiente Paso, después de las salidas de las tools, como un mensaje del usuario. No es un Prompt nuevo ni cambia el Origen del Turno. Lo que lleva Command prompts o `-y`, lo que no cabe en el slot, lo que llega por `/send` y lo escrito antes de que el Turno empezara esperan su propio Turno. Se guarda en el Paso tras cuyas tools entró; si el Turno falla, se cancela, o Main se para (`/exit`, una señal o un `kill -9`) antes de que acabe, vuelve al input, tras su Prompt si es humano (ADR-0024).
_Avoid_: interrupción (es Shift+Enter), mensaje en cola (es lo que aún espera)

**Turno cortado** (`cut turn`):
Turno cuyo último Paso terminó porque llegó a su Tope de salida o al límite del slot, o porque un humano lo interrumpió, no porque el LLM acabara. Se guarda con lo generado, marcado, y se lo dicen al humano un Aviso y al LLM una Notificación; no es un Turno fallido (ADR-0004).
_Avoid_: truncado (es lo que hace `--context-shift`), incompleto

**Continuación** (`Continuation`):
Trabajo que retoma algo ya empezado: un Turno suspendido, el Paso siguiente de un Turno cuyas tools terminaron, o una Sesión que despierta de una Suscripción. Dentro de su Main va antes que los Prompts nuevos, salvo cuando ya pasaron varias seguidas y toca dejar pasar uno nuevo.

**Suscripción** (`Subscription`):
Petición de una Sesión de que un evento la despierte: cuando ocurre (responden otras Sesiones, un Proceso adherido dice algo que le interesa, pasa un tiempo…), Main le encola un Prompt con lo ocurrido. Mientras espera no tiene Turno activo ni ocupa slot, y no bloquea a su Sesión: esta atiende sus Prompts con normalidad, y el del evento entra como Continuación en cuanto la Sesión no tenga Turno activo. Se abre con `@subscribe` (un humano, `/subscribe`) para `process:<nombre>`, `timer:<segundos>`, `rewind:<sid>` o `compact:<sid>`, o con `--reply-to` al enviar; los eventos que llegan seguidos van en un solo Prompt.
_Avoid_: listener, hook, trigger

**Encargo** (`Errand`):
Suscripción a las respuestas de los Prompts que una Sesión repartió a otras Sesiones de su árbol de Mains: la despierta con la lista de resultados cuando todas terminan, sea bien, fallando o sin respuesta al vencer su plazo. Con un quórum `k`, la despierta cuando `k` terminan bien y cancela las demás; una carrera es `k=1`. Los Encargos forman un árbol: ninguno puede esperar a la Sesión que lo abrió ni a sus ancestros. Pertenece a la Sesión, no a su Turno: cerrar su Ventana no lo cancela; solo lo cancelan un Rewind a antes de abrirlo o una cancelación explícita, que alcanza a lo que sus trabajadoras hacen para él. Vencer su plazo cancela igual la parte vencida. El plazo solo corre mientras su Perfil está activo en un Main vivo, y a cada trabajadora no le cuenta el tiempo en que su Servidor no está listo. Es del Perfil en que se abrió. Se abre con `@errand` (un humano, `/errand`), y una tarea que falla se reintenta hasta `--retries` sin despertarla.
_Avoid_: job, barrera, tarea

**Lote** (`Batch`):
Encargo cuyas tareas no van asignadas de antemano. Main le da la siguiente a una trabajadora de su lista cuando esta no tiene Turno ni cola y su Servidor tiene sitio para empezarla, así que los Supervisores libres se turnan el lote. La Sesión que lo abrió despierta una sola vez, con todos los resultados y el Supervisor y el modelo que respondió cada uno. Plazo, quórum y cancelación funcionan como en el Encargo. Se abre con `@batch` (un humano, `/batch`).
_Avoid_: pool, cola de trabajo, job

**Consultar** (`Status`):
Leer, sin cambiar nada, lo que una Sesión tiene a su alcance en el Perfil activo: la cola (qué Sesiones tienen Turno o Prompts esperando), las Suscripciones y Encargos abiertos con su avance, los Procesos adheridos y sus métodos, los Servidores que vigilan sus Enlaces con sus slots y su modelo, y los Supervisores con su capacidad. Una Sesión escribe `@status [parte]` y lo recibe por el canal de vuelta; un humano, `/status <sid> [parte]` en el Chat de Main. El árbol de Mains se añadirá en F3.
_Avoid_: estado, info, dashboard

**Paso** (`Step`):
Una sola llamada a `responses.create` dentro de un Turno; un Turno con tools tiene varios Pasos.
_Avoid_: ronda, iteración

**Paso colgado** (`stalled step`):
Paso que no avanza en `step.stall` segundos (Ajuste de Main): ni llegan eventos del stream ni, mientras el Servidor lee la entrada, crece lo que `/slots` dice que lleva leído. Main lo cancela, suelta su slot y saca un Aviso; el Paso falla sin repetirse, porque en el mismo Servidor volvería a colgarse. No es una caída (ADR-0023).
_Avoid_: Paso congelado, timeout del Paso

**Contexto** (`Context`):
Tokens totales que ocupa la Conversación en el último Paso (entrada + salida), comparados contra el límite que el Servidor da a cada slot (no un Ajuste). Con caché KV unificada, ese límite lo comparten los Pasos en curso de todas las Sesiones enlazadas al mismo Supervisor. Cuando una Sesión se acerca a su límite, se compacta.
_Avoid_: tokens gastados (ambiguo)

**Admisión** (`Admission`):
La decisión de si un Paso empieza ya en su Servidor. Lo que necesita es su entrada, medida con la plantilla y el tokenizador del propio Servidor, más su Tope de salida. Necesita un slot libre y su turno en la Lista de espera. Con caché KV unificada, además, tiene que caber en el pool menos lo que reservan los Pasos en curso de todos los Mains. Uno que no cabe puede ser adelantado por otro que sí, hasta que lleva esperando `admission.patience` segundos (Ajuste de Main): desde entonces nadie lo adelanta. Un Paso que no cabría ni en el slot vacío falla enseguida, sin hacer cola. En la Lista de espera van primero las Continuaciones, luego los Prompts nuevos, luego los que trae un Proceso adherido por su Iniciativa, y al final los de `describir` (ADR-0004, ADR-0022, ADR-0026). El que manda uno de sus métodos durante una llamada es un Prompt nuevo, por orden de llegada, como el de una Sesión; el Prompt recuerda que vino de la Iniciativa, porque la cadena de Origen de los dos puede ser la misma.
_Avoid_: entrada al pool, cupo

**Lista de espera** (`Wait list`):
Lista de cada Servidor, compartida por todos los Mains que lo usan, junto a sus candados de slot: quién espera para empezar un Paso y lo que reserva del pool cada Paso en curso. Se ordena por clase (Continuaciones, Prompts nuevos y, al final, `describir`) y, dentro de cada clase, por orden de llegada. El Main que suelta un slot no lo vuelve a tomar si otro espera desde antes (ADR-0022).
_Avoid_: cola del Servidor (la cola es la de cada Main)

**Tope de salida** (`max_output_tokens`):
Ajuste de Sesión: lo más que el LLM puede generar en un Paso. Es también la reserva con que se admite un Paso y se decide compactar; por defecto 16 384, o un cuarto del límite del slot si es menor (ADR-0004).
_Avoid_: max tokens, límite de salida

**Compactación** (`Compaction`):
Reemplazar la parte vieja de la Conversación de una Sesión por un resumen que escribe el LLM, archivando lo reemplazado, para que siga cabiendo en su Contexto. Se hace a mano (`/session compact`, `@compact`) o sola, al pasar un umbral del límite del slot (`compact.auto`, encendida por defecto). Quedan literales el Sysprompt, el historial inicial, el Prompt actual y los Turnos recientes. El resumen entra como `user`, nunca como `developer`, y un Rewind a antes del corte recupera lo archivado. Antes de resumir se **poda**: las salidas largas de tools de los Turnos viejos se leen como una línea, sin LLM, y siguen enteras en la Conversación; si con eso la automática ya cabe, no se resume. Con tools, también se compacta entre los Pasos de un Turno: el Turno **cede** tras sus tools, se compactan los Turnos guardados y sigue desde sus Pasos. Nunca se trunca en silencio (ADR-0017).
_Avoid_: truncar, context shift, resumen (es lo que produce)

**Paso de resumen** (`summary step`):
Paso aparte con que una Compactación pide al modelo del Enlace de la Sesión el resumen de una parte de su Conversación, guiado por el **Sysprompt de compactación** (el archivo que nombra `compact.sysprompt` en el Perfil, o el que trae Main si el Perfil no lo tiene). Pasa por la admisión y toma un candado de slot como cualquier Paso, y se repite tras una caída; no es un Turno, así que no se guarda en la Conversación. Si lo que se resume no cabe en un solo Paso, hay uno por parte, cada uno con el resumen del anterior (ADR-0017).

## Comandos

**Command main** (`MainCommand`):
Comando de Main, escrito con `/`. Es la única vía para modificar el estado; se carga al arrancar Main y no se recarga en caliente. Una pieza no puede añadir los suyos: a los métodos de un Proceso adherido, un humano los llama con `/call`.

**Command session** (`SessionCommand`):
Comando de una Ventana, escrito con `@` al inicio de un input que no contiene nada más; no lanza Turno y se traduce a exactamente un Command main. Sus módulos son parte de Main, comunes a todos los Perfiles, como los de Avisos (ADR-0016). Main trae los suyos (`@send`, `@server`, `@windows`, `@open`, `@bg`, `@close`…); los demás, los módulos de `comsession/`. Cada Sesión tiene su lista, que se hereda como sus functions (por defecto, los de la raíz de su carpeta; `/new -cs=` nombra otros) y que limita a su LLM, no al humano; una function solo ejecuta los que declara en su Alcance, por su canal de vuelta (`allin.channel.command`), y su resultado le vuelve por el mismo canal (ADR-0025).
_Avoid_: comsession (salvo como nombre de carpeta)

**Command prompt** (`PromptCommand`):
Directiva escrita con `@` en cualquier posición de un input que lanza un Turno; solo afecta a ese Turno y nunca cambia los Ajustes. Solo se reconoce en lo que teclea un humano (ADR-0013). Hoy existen dos: Referencia a archivo y Sobrescritura de parámetro. Una Sesión los pone en su **forma estructurada** (`SendOptions`): opciones delante del texto de `@send` (`--attach=<ruta>`, `--<parámetro>=<valor>`, y `--` antes de un texto que empiece por `--`), y Main nunca lee el texto (#110). Lo que teclea un humano va siempre tal cual.

**Referencia a archivo** (`FileReference`):
Command prompt `@<ruta>` (empieza con `/`, `~` o `./`, precedido de espacio o inicio) que adjunta un archivo al Prompt; Main lo copia, como instantánea, a `.allin/adjuntos/<prompt>/` en la Carpeta de trabajo de la Sesión, y la Conversación lo nombra en relativo para que sus tools lo lean; si ya está dentro, no se copia (ADR-0016). El que adjunta una Sesión sale de su propia Carpeta de trabajo (una ruta relativa parte de ella), y uno de fuera, también por un enlace, necesita la Aprobación de un humano (ADR-0013).
_Avoid_: @path (como nombre del concepto)

**Sobrescritura de parámetro** (`ParamOverride`):
Command prompt `@clave=valor` (p. ej. `@effort=medium`) que cambia un parámetro del LLM solo para ese Turno. Solo admite la lista cerrada de parámetros de muestreo que viajan en cada Paso; nunca functions, Sysprompt ni Alcance.

**Origen** (`origin`):
De dónde viene un Prompt o un comando: un humano (lo tecleado en una Ventana o en el Chat de Main, o lo que hace en el Organizador) o una Sesión, con la cadena por la que llegó; lo que Main pide por su cuenta a un Proceso adherido lleva el Origen de Main, que nunca cuenta como humano. Una acción nunca tiene más autoridad que el eslabón más débil de su Origen, ni usa un Command session que no esté en la lista de todas las Sesiones de su cadena. Lo que llega de otra Sesión es dato: Main nunca lo trata como comando ni le da más autoridad, y el LLM puede seguirlo, pero con la autoridad de su cadena. Una pieza (function, Sysprompt, Molde, Plano) tiene Origen humano solo cuando un humano aprobó su versión; hasta entonces está en cuarentena. Cada Prompt y cada comando guardan su cadena entera (en su Turno y en el Log, como `["human", "session:ab12"]`), y un Prompt que no tecleó un humano llega al LLM con una cabecera que dice de quién viene (`[from Session ab12]`). En el Chat de su Ventana, también al reconstruirlo, ese Prompt sale rotulado con el último eslabón de su cadena (`session:ab12`, `process:bot`, `subscription:…`, `main`), y solo el de Origen humano con el nombre del usuario.

**Iniciativa** (`initiative`):
Lo que un Proceso adherido hace por su cuenta, entre llamadas: desde un hilo propio pide Command sessions por su canal (`allin.channel.command`), y Main los corre con el Origen de quien lo adhirió seguido del Proceso adherido. Solo la tiene la fproc que declara su Subperfil, y solo sobre las Sesiones de este: `@new` desde los Moldes de su package que nombra su Alcance, `@send`, `@open` (siempre en segundo plano) y `@close`. Lo que crea queda dentro: las Sesiones de su Subperfil no crean Sesiones ni adhieren procesos (ADR-0026).
_Avoid_: autonomía, acción espontánea

**Externo** (`outsider`):
Persona de fuera de AllIn cuyo texto trae un Proceso adherido, como quien escribe en un canal de Discord. No es el humano, que siempre es el usuario de AllIn: lo que dice entra con el Origen del Proceso adherido, que nunca cuenta como humano, y su identidad (`@send <sid> --outsider=<quién> -- <texto>`) solo se anota para el Log y la memoria: en su Turno y en el Log, nunca en la cabecera que lee el LLM, que dice solo el proceso (`[from attached process bot]`). Si el código de la fproc quiere que su LLM sepa quién habla, lo escribe en el texto, que siempre es dato (ADR-0026).
_Avoid_: usuario, humano, cliente

**Otorgar** (`Grant`):
Command session que da Command sessions y functions a cualquier Sesión del Perfil, aunque quien lo usa no los tenga. La fsource que lo declara no se hereda: solo un humano se la da a una Sesión concreta, y Otorgar no da Otorgar. Su aprobación nunca va en bloque, el borde de la Sesión que lo tiene lo marca y cada uso pide Confirmación (ADR-0025). Se escribe `@grant <sid> <pieza|@comando>…` y corre como el Command main `/grant`; una Sesión lo tiene cuando `@grant` está en su lista, y dar la fsource que lo declara también lo da (#111).
_Avoid_: function session

**Notificación** (`Notification`):
Línea que Main deja pendiente, por un comando, una fsource o un evento suyo (el avance de un Encargo, el Informe de recuperación, un ciclo de Suscripciones cortado, que su respuesta anterior se cortó, cuánto Contexto le queda al salir el Aviso de Contexto), para entrar en la Conversación al inicio del siguiente Prompt, bajo la cabecera `[LOGS @COMMANDS]`, como mensaje `user` y nunca `developer`: lo que cuenta de otras Sesiones es dato (ADR-0013). Las Notificaciones de acciones que se anulan entre sí antes de enviarse se cancelan (dar y quitar la misma function); una que ya contó un Turno en curso no se cancela, porque el LLM ya la leyó. Salen del pendiente cuando se guarda el Turno que las contó; si ese Turno falla, las cuenta el siguiente. Además entran en el Log y el Chat de su Sesión.
_Avoid_: log (una Notificación se deriva del Log, pero no es el Log)

**Conversation logs**:
Ajuste de la Sesión que decide, por cada comando o fsource, si sus Notificaciones entran en la Conversación; se controla con `@conversation_logs on|off|list [fuente]` desde su Ventana, o con `/conversation_logs <sid> …` desde el Chat de Main; sin fuente, vale para todas. Nunca afecta al Log ni al Chat.
_Avoid_: @logs

**Rewind** (`Rewind`):
Retroceder una Sesión N Prompts, de cualquier Origen: trunca la Conversación hasta antes de ese Prompt y, si su Origen es humano, devuelve su texto al input para reeditarlo (si no, queda en el Log); `n=0` no hace nada. Nunca va más atrás del inicio de la Sesión (Sysprompt e historial inicial), pero sí a antes de una Compactación: recupera lo archivado y quita el resumen. Cancela los Encargos y Suscripciones abiertos después de ese punto y olvida las revisiones de Procesos adheridos vistas desde entonces (ADR-0015). Solo existe como Command main.

**Restablecer** (`SessionReset`):
Devolver Sesiones existentes a su receta de nacimiento (el Molde y versión, o la plantilla de Sesión copiada al crearla: Sysprompt, historial inicial, Ajustes, functions), sin cerrar nada: es un Rewind hasta el nacimiento que además repone los Ajustes. Con `/session reset`, a una, a todas las de un Main o a las del árbol. Cerrar todo además de restablecer es `/reset --all`. Un Clon hereda la receta de su original. La Conversación anterior se archiva, no se borra.
_Avoid_: clear, reset (a secas: `/reset` es de los Ajustes de Main), fábrica

**Clon** (`Clone`):
Sesión nueva creada como copia de otra en su último estado consistente (Conversación, Ajustes, functions y Carpeta de trabajo, que comparte si apunta a una ruta del usuario); tiene id y Log propios. Se crea con `/session clone` o `@clone`.
_Avoid_: fork, copia

**Goal** _(experimental)_:
fsource que repite Turnos sobre una meta descrita en un `.md` hasta que el LLM la declara cumplida, se rinde, o se agota `max_rounds`; cada ronda es un Turno separado que vuelve a la cola.
_Avoid_: loop

## Functions

**Function tool** (`FunctionTool`):
Función que invoca el LLM (va en `tools=[...]`); un módulo = una tool, salvo una fproc, que expone una por método. El usuario nunca llama directamente a una fsource ni a un fagent; a los métodos de un Proceso adherido sí, con `/call`.
_Avoid_: skill, comando

**fsource**:
Function tool que se hereda automáticamente en `/new` (salvo `-sf=off`, y salvo la que declara Otorgar, que solo da un humano) y que puede ejecutar los Command sessions que declara; es, con los métodos de una fproc, la vía por la que el LLM actúa sobre AllIn. Sus packages viven en `functions/` del Perfil.
_Avoid_: source, function source

**fagent**:
Function tool escrita por el usuario o por el LLM; se pasa explícitamente en `/new` (`-fa=<package>`) o se añade después con `/func add`. Sus packages viven en `agents/` del Perfil, fuera de las Sesiones. No es un agente.
_Avoid_: agent, function agent, agente

**fproc**:
Clase cuyos métodos públicos se exponen como Function tools para controlar un Proceso adherido, que el usuario también puede manipular directamente. Sus métodos los llaman el LLM y también un humano, con `/call`; los marcados con `@human` solo los llama un humano y nunca llegan al LLM. En `tools=[...]` cada método se llama `<nombre>_<método>`, con el nombre del Proceso adherido. Mientras atiende una llamada, un método puede ejecutar Command sessions, como una fsource. `@concurrent(n)` en la clase dice cuántas llamadas atiende a la vez, cada una en su hilo (`@concurrent()`: sin tope; sin él, una). En una llamada larga, `self.progress(…)` avisa de cómo va y `self.cancelled()` dice si Main la dejó. El nombre viene de *function process*. Su comportamiento lo definen sus métodos, no los Ajustes.
_Avoid_: fscreen, function screen, screen

**Proceso adherido** (`AttachedProcess`):
Proceso vivo y con estado que Main lanza a partir de una fproc y que sus Sesiones controlan llamando a los métodos de esta (un archivo compartido con su cursor, un bot de Discord…). Main solo lo adhiere (`/attach <fproc> [nombre] [--to=<sid,…>] [--file=<ruta>]`, o `@attach` desde una Sesión), lo vigila y lo suelta (`/detach <nombre>`); lo que hace lo define su fproc. Muere con Main, y al cambiar de Perfil se cierran los del saliente. Al adherirlo recibe un nombre, por defecto el de su fproc, y una Carpeta de trabajo propia que lo sobrevive (`<carpeta de datos>/attached/<Perfil>/<nombre>/`); Main nunca lo relanza solo: si se cae, sale un Aviso y sus métodos dan error, y adherirlo de nuevo con el mismo nombre le devuelve esa carpeta. Atiende una llamada a la vez, o las que declare su fproc, que las organiza por dentro. Se rechaza la escritura de una Sesión que no ha visto su último cambio, para que nadie escriba sobre una lectura obsoleta. Sus métodos los recibe como tools la Sesión que lo adhiere (todas las nuevas, si lo adhiere Main), que puede pasarlos a las que ella crea; un humano se los da o se los quita a cualquiera, y soltarlo se los quita a todas. En la cadena de Origen de lo que manda a Main es un eslabón que nunca cuenta como humano, porque su estado mezcla lo que escribieron varias Sesiones. Entre llamadas también actúa por su cuenta, pero solo sobre las Sesiones que él creó y con los Command sessions de su Alcance; así trae a AllIn lo que dicen los Externos.
_Avoid_: screen, plugin, servicio

**Revisión** (`revision`):
Contador de los cambios de un Proceso adherido, que lleva el propio proceso. Sube con cada método `@writes` o `@commutative` de su fproc, y con cada `self.changed()`, con el que la fproc marca un cambio que el humano hizo en su ventana. Main apunta la que vio cada Sesión en cada llamada de su LLM, y un `@writes` de una Sesión que no vio la actual no se ejecuta: «cambió desde tu última lectura; vuelve a leer». Un `@commutative` (añadir al final, por ejemplo) nunca se rechaza. Un Rewind olvida las que la Sesión vio desde entonces (ADR-0012).
_Avoid_: versión (la de una pieza), ETag

**Archivo compartido** (`file`):
El archivo que un Proceso adherido lee y escribe además de su Alcance, elegido antes de lanzarlo (`--file=`) y solo para esa instancia. Nunca es de AllIn, salvo dentro de la Carpeta de trabajo de una Sesión. El que pide una Sesión desde fuera de su Carpeta de trabajo necesita la Aprobación de un humano, que no dan ni `-y` ni `--devastation`. El Encierro lo fija en el archivo que había al lanzarlo: un editor que lo guarda reemplazándolo deja uno nuevo que el proceso no alcanza, y hay que volver a adherirlo (ADR-0012, ADR-0016).
_Avoid_: archivo abierto, documento

**Alcance** (`reach`):
Lo que el proceso de una Function tool o de un Proceso adherido puede tocar, declarado con el decorador `@reach` (qué carpetas lee y escribe, qué puertos de red, si abre una ventana gráfica y qué Command sessions ejecuta) y forzado por el Encierro, salvo los Command sessions, que los comprueba Main. Sin decorador es el mínimo: su Carpeta de trabajo, leer el sistema, sin red y sin Command sessions. Nunca incluye nada de AllIn, ni dentro de un Alcance amplio como `home`: ni la carpeta de datos (salvo la Carpeta de trabajo, el `TMPDIR` de la llamada y su propio package), ni la instalación (solo lectura), ni los puertos de sus Servidores; tampoco las terminales. `display`, o escribir donde el usuario ejecuta, equivale a actuar como él. Es parte del código aprobado: ampliarlo devuelve la function a cuarentena, y al aprobarla se puede reducir (ADR-0016).
_Avoid_: permiso, sandbox (como nombre del concepto), capacidades

**Encierro** (`confinement`):
Lo que el kernel impone al proceso de una Function tool o de un Proceso adherido según su Alcance: Landlock para archivos y TCP, y un filtro seccomp para lo que Landlock no ve (sockets unix, UDP sin red, io_uring, meter teclas en una terminal). El proceso se lo pone a sí mismo antes de importar su function, y no hereda de Main ni su terminal, ni su entorno, ni sus rutas de importación. De su package solo importa fuentes `.py`, compiladas en cada llamada, y Main no lanza una function cuyo package tenga un `.pyc` o código compilado. Nada lo salta, ni `--devastation` (ADR-0016).
_Avoid_: sandbox, jaula

**Batería de fugas** (`tests/test_leak_battery.py`):
Los tests en los que una tool con el Alcance mínimo intenta cada salida conocida del Encierro (D-Bus, X11, io_uring, señales y `ptrace` a Main, la terminal, el agente SSH, un `.pyc` preparado y los puertos de los Servidores) y todos los intentos tienen que fallar. Corren en la suite y en el pre-commit, para que un cambio que reabra una salida no pase. Una salida nueva que se descubra entra en ella (ADR-0016).
_Avoid_: tests de seguridad (es más amplio), pentest

**Descripción** (`Description`):
Lo que Main sabe de una function sin importarla: el schema de su tool para `tools=[...]` (uno por método público en una fproc), las marcas de sus decoradores y su Alcance. La saca un proceso aparte con el Encierro de descripción, que solo lee el mínimo, sin Carpeta de trabajo, sin escribir, sin red ni sockets unix y con timeout. Main la guarda con el hash de su package; cada llamada se encierra con el Alcance guardado, aunque el módulo diga otra cosa al importarse después (ADR-0016).
_Avoid_: schema (es solo una parte), firma

**Lanzador de tools** (`ToolLauncher`):
El thread de larga vida de Main que lanza el proceso de cada llamada a una Function tool, y lanzará también los Procesos adheridos: la señal que mata a esos procesos cuando muere Main está atada al thread que los lanzó. Cada proceso nace con `subprocess` y un `socketpair` como único canal (ADR-0005). No es el Lanzador, que abre Ventanas.
_Avoid_: worker, pool

**Vigilante** (`warden`):
El primer proceso de una llamada a una Function tool. Se encierra, crea el proceso que importa y corre la function y, cuando esta contesta, cuando Main cancela o cuando Main muere, mata a todos los procesos de la llamada con `kill(-1)`, que el scope de señales de Landlock limita a ese Encierro (ADR-0005).
_Avoid_: supervisor (es otra cosa), padre

**Confirmación** (`Confirmation`):
Pregunta s/n que Main hace al usuario humano antes de una acción marcada como delicada (una Function tool con el decorador `@confirm`, o un comando), en un recuadro superpuesto en la Ventana de la Sesión y en el Chat de Main; si la rechaza, el LLM recibe el rechazo como salida de la tool. Mientras espera, el Turno queda pausado sin ocupar slot y conserva su lugar: al responderse sigue como Continuación, antes que los Prompts nuevos de su Main. `-y` la salta para esa invocación, solo con Origen humano: en un comando delicado va tras su nombre, y en un Prompt con `/send -y`, que contesta las de las tools de ese Turno. `--devastation`, que un humano enciende en un Main y dura ese arranque, contesta sin preguntar las de ese Main y las que le suben por la Exención, si cubre todo lo que la acción toca. Ningún Ajuste la apaga por sí solo. Crear o modificar una pieza desde una Sesión siempre la pide, y además deja la pieza en cuarentena.
_Avoid_: aprobación (es otra puerta), permiso

**Exención** (`exemption`):
Marca de una Sesión o de un submain por la que sus Confirmaciones suben a su Main en lugar de preguntar al humano. Suben de Exención en Exención hasta el primer Main con `--devastation`, que las contesta y las deja en el Log; si la subida se corta antes, se pregunta al humano. Sola no hace nada, así que se guarda y viaja en Moldes, Planos y Kits. Las Sesiones de un Main nacen con ella, y un submain solo la tiene si se la pone un humano o la trae un Plano. Solo un humano la pone o la quita. Un Proceso adherido nunca la tiene, pero un humano se la puede poner a su Subperfil, y entonces solo cubre lo que no sale de él (ADR-0013, ADR-0026).
_Avoid_: link, vínculo

**Aprobación** (`approval`):
Lo que un humano da, a propósito, a todo lo que amplía un Encierro: cada versión de una pieza con su Alcance, la ruta de una Carpeta de trabajo, un archivo de fuera que pida una Sesión y los Ajustes del Servidor que nombran un archivo o abren la red. Ni `-y` ni `--devastation` la saltan. Da Origen humano a la pieza, pero no cambia su procedencia, y lo que se ajusta al aprobar (`@confirm`, un Alcance menor) se guarda con ella, sin tocar el código (ADR-0013).
_Avoid_: Confirmación, permiso

**Secreto** (`secret`):
Valor con nombre que solo da un humano, como el token de un bot, y que Main entrega a un Proceso adherido al lanzarlo, por su canal y nunca por su entorno. Se guarda una vez en el Perfil, a nombre de la instancia, y vale en cada adhesión hasta que un humano lo quita. `/secret set <instancia> <nombre>` pide el valor en un recuadro del Chat de Main que solo muestra puntos; `/secret` lista los nombres, nunca el valor, y `/secret remove` lo quita. La fproc lo lee con `self.secret("<nombre>")`. Nunca lo ven el LLM, la Conversación, el Log, el RAG ni los Ajustes, y nunca viaja en Moldes, Kits ni copias de un Perfil (ADR-0026).
_Avoid_: token, credencial, contraseña

**Cuarentena** (`quarantine`):
Estado de una pieza nueva o cambiada (function, Sysprompt, Molde, Plano, historial inicial) hasta su Aprobación: Main la muestra, pero no la ofrece al LLM ni la usa. De una function cuenta su package entero: cambiar cualquier archivo devuelve a cuarentena todas sus functions. Un package con código compilado o binarios está **rechazado**: no se aprueba nunca. `/piece` lista las piezas, `/piece show` muestra la pantalla de aprobación y `/piece approve` aprueba; Main dice al arrancar cuántas esperan (ADR-0013).
_Avoid_: pendiente, bloqueo

**Pantalla de aprobación** (`/piece show`):
Lo que un humano lee antes de aprobar una pieza: primero el Alcance de cada function, con `!` en lo que excede el mínimo («TCP 443 · UDP open»; `display` y escribir donde el usuario ejecuta dicen «acts as you»), y después su código entero, con los caracteres de control, de dirección e invisibles marcados (`<U+202E>`). Se aprueba la versión que mostró: si la pieza cambió después, hay que volver a verla (ADR-0013).
_Avoid_: diff, revisión

**Procedencia** (`Provenance`):
Quién escribió la versión actual de una pieza: el usuario, el autor de un Kit (mientras la pieza siga como la escribió el Kit) o la Sesión que la creó con Crear. Un cambio que aparece en disco por fuera de AllIn es del usuario. La Aprobación no la cambia. En un kernel sin soporte, las que no escribió el usuario no corren, y solo las suyas entran en la aprobación en bloque (ADR-0016).
_Avoid_: Origen (es de cada acción, no de la pieza), autor

**Recuadro** (`Box`):
Lo que Main superpone en el Chat de Main y en la Ventana de la Sesión a la que concierne para hablar con el humano sin pasar por el Chat: un Aviso o una Confirmación. Se contesta desde cualquiera de los dos y desaparece de ambos; mientras está, se lleva las teclas, salvo las que solo mueven la vista. Uno que llega con la Ventana cerrada sale al reabrirla, si sigue vigente. Su texto se dibuja saneado, como toda pantalla de AllIn (ADR-0013).
_Avoid_: popup, diálogo

**Aviso** (`Notice`):
Recuadro superpuesto en una Ventana (y en el Chat de Main) que informa algo sin pedir decisión: se cierra con una tecla y nunca bloquea un Turno. Cada tipo de Aviso es un módulo de `notices/`, parte de Main y común a todos los Perfiles, que se declara con un decorador y tiene sus propios Ajustes; Main trae los suyos (el de Turno cortado, `cut_turn`) con los mismos Ajustes, y un módulo con su nombre los reemplaza: encendido, título y texto en todos, y los que su tipo necesite (el de Contexto, un umbral), así que su lectura no tiene que ser de alarma; el de Contexto sale al cruzar un umbral del límite. No es una Notificación: no entra en la Conversación.
_Avoid_: advertencia (como nombre del concepto), alerta, popup

## Memoria

**RAG** (`Rag`):
La memoria de consulta de un Perfil: un Proceso adherido que guarda, con fecha y procedencia, documentos, el historial de las Sesiones y lo que AllIn sabe de sí mismo, y devuelve como dato los pocos fragmentos que mejor responden. Cada Sesión lo consulta o no según su Ajuste `rag`, que copia del de Main en `/new`, como el Enlace. No decide por el LLM ni le da tools o permisos (ADR-0019).
_Avoid_: memoria (a secas), base de conocimiento, personalidad

**Corpus** (`Corpus`):
Conjunto con nombre de carpetas de documentos que un humano da al RAG de un Perfil.
_Avoid_: colección, biblioteca

**Segmento** (`Segment`):
Parte del RAG que se da o se quita entera a una Sesión: un Corpus, el historial de una Sesión, el historial del Perfil o lo de AllIn sobre sí mismo. Lo que una Sesión ve del RAG es su lista de Segmentos, y solo un humano la amplía.
_Avoid_: índice, scope, colección

**Ficha** (`Card`):
Un fragmento recuperado tal como lo recibe el LLM: su texto con fecha, fuente, Segmento, Origen e id, en formato fijo.
_Avoid_: chunk, resultado, recuerdo

**Pista** (`Hint`):
Notificación de una línea que Main añade antes de un Prompt cuando el RAG encontró algo muy relevante para él: fecha, fuente, inicio e id del fragmento, sin su contenido, que el LLM pide con `leer` si le sirve.
_Avoid_: sugerencia, recuerdo

## Personaje

**Personaje** (`Persona`):
Alguien con memoria y carácter propios que vive en una instancia de fproc con Subperfil, adherida en el Perfil que es su casa. Tiene una sola memoria y varios sentidos: oído, vista y texto. Fuera de su Perfil está apagado: no existe ni piensa.
_Avoid_: bot, asistente, agente de voz

**Charla** (`Talk`):
Sesión del Subperfil del Personaje en la que conversa con una o más personas en un solo lugar, como su Ventana o un canal de Discord. Dura un encuentro: termina cuando el Personaje se va del lugar o tras una pausa larga.
_Avoid_: chat (el Chat es lo que dibuja una Ventana), conversación (la Conversación es lo que ve el LLM)

**turno de palabra** (`floor`):
El derecho a hablar en una Charla: quién lo tiene, quién lo pide y quién lo cede.
_Avoid_: turno, a secas (un Turno es la atención de un Prompt)

**voz interior** (`inner_voice`):
Lo que el Personaje piensa sin decirlo, dentro de su propia Charla, como cuando lo interrumpen y decide si cede, insiste o retoma después.
_Avoid_: subagente, razonamiento

**Reposo** (`Rest`):
Estado del Personaje encendido y sin Charla, en el que reflexiona sobre sí mismo y sobre lo que vivió.
_Avoid_: dormir, sueño, suspensión (un Supervisor dormido es otra cosa)

**Núcleo** (`Core`):
Lo que no cambia del Personaje: su carácter, sus límites y su manera de hablar. Lo escribe el usuario en su Sysprompt.
_Avoid_: personalidad (abarca también el Autorretrato)

**Autorretrato** (`SelfPortrait`):
Lo que el Personaje concluye de sí mismo al reflexionar, con lo que lo sostiene de su Log. Le llega como dato al empezar cada Charla, nunca dentro del Sysprompt.
_Avoid_: perfil, personalidad

**Conocido** (`Acquaintance`):
Persona a la que el Personaje reconoce, con sus Huellas, sus cuentas y su Relación con él. El usuario también es un Conocido, el único que se registra desde la consola; que lo reconozca no le da autoridad.
_Avoid_: identidad (el Perfil ya es una identidad con nombre), usuario (es siempre el humano de AllIn), contacto

**Huella** (`Print`):
Lo que el Personaje guarda de la voz o de la cara de un Conocido para reconocerlo. No es la grabación ni la foto.
_Avoid_: hash biométrico, embedding

**Relación** (`Relationship`):
Lo que el Personaje sabe y siente de un Conocido: su historia en común, la confianza y su ánimo hacia él, que cambian con el tiempo.
_Avoid_: ficha (la Ficha es del RAG)

**Confidencia** (`Confidence`):
Lo que un Conocido le contó al Personaje pidiéndole que no lo repita. El Personaje solo la vuelve a ver a solas con quien se la confió; el usuario la ve en los datos, pero el Personaje no se la cuenta.
_Avoid_: secreto (un Secreto lo da un humano y el LLM nunca lo ve)
