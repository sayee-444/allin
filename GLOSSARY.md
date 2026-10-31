# AllIn

Se pronuncia "Alyn". Antes se llamaba Neva; ese nombre ya no se usa en ningún lado.

Los términos están en español; entre paréntesis va el nombre canónico en código (todo el código, comentarios y Log van en inglés).

Orquestador de terminal para conversar con un LLM local (vía OpenAI SDK) desde varias ventanas, donde un proceso central ordena y atiende las peticiones de cada una.

## Procesos

**Main**:
El proceso orquestador (`python -m allin`, paquete `allin.main`); dueño único del estado de las Sesiones que orquesta y de su cola de Turnos. Por carpeta de datos corre un solo Main raíz, con un Perfil a la vez; sus submains corren aparte, cada uno con su id (ADR-0007).
_Avoid_: abuelo, padre, proceso principal, servidor (el Servidor es `llama serve`)

**Perfil** (`Profile`):
Identidad con nombre de un Main raíz, guardada como archivos modulares en `profiles/<nombre>/`: sus Ajustes de Main (con el Enlace), las Sesiones que le pertenecen, sus submains, su Estado de Main y sus piezas (functions, Sysprompts, Moldes, Planos y Temas). Su nombre lo elige el usuario y solo `/profile new` lo crea: nombrar uno que no existe es un error, nunca lo crea. Arrancar un Perfil es arrancar Main con ese nombre como id; trabajar en otro proyecto es cambiar de Perfil, sin perder nada del anterior. Una Sesión puede pertenecer a varios Perfiles a la vez (compartida): como solo corre un Main raíz, eso no cambia quién la orquesta. Es privado: lo que se comparte con otros usuarios es un Kit, y desde una Sesión, `/send` y Consultar solo alcanzan las del Perfil activo.
_Avoid_: perfil de servidor, perfil de main, perfil de sesión, workspace

**Kit** (`Kit`):
Receta portable y versionada de un entorno de trabajo (Sysprompts, Moldes, functions, Goals y composiciones, Temas, parte de los Ajustes de Main y Ajustes del Servidor sugeridos) que se exporta desde un Perfil y se carga en otro, de esta máquina o de otro usuario. Nunca lleva Sesiones ni nada de ejecución, y solo fuentes que se puedan leer: uno con `.pyc` o binarios se rechaza. Cargarlo copia sus piezas en la carpeta del Perfil, y cada una recuerda de qué Kit y versión vino; un Perfil puede cargar varios. Cargarlo no ejecuta nada con efectos, y sus piezas (functions, Sysprompts, Moldes, Planos e historiales iniciales) esperan en cuarentena hasta que un humano las aprueba.
_Avoid_: plantilla, paquete, perfil (para lo que se comparte)

**Traspaso** (`Handover`):
Pasar una Sesión de un Main a otro dentro del mismo árbol (main → submain, submain → main, submain → submain): desde ese momento sus Prompts entran en la cola del nuevo Main y él ordena sus Turnos. No se prohíbe: se avisa. Compartir una Sesión entre Perfiles no es un Traspaso.
_Avoid_: mover, migrar

**Lanzador** (`Launcher`):
El componente de Main que abre una Ventana: lanza xterm con `Popen` en una sesión nueva (`setsid`) y un thread vigía espera su terminación (ADR-0003). No hay proceso intermedio.
_Avoid_: hijo, worker

**Ventana** (`Window`):
El proceso xterm/curses (`python -m allin.window`) que muestra una Sesión y recibe lo que escribe el usuario. A lo sumo una Ventana por Sesión; una Sesión trabaja igual sin ella. Cerrarla cancela el Turno de su Sesión solo si el Prompt de ese Turno tiene Origen humano; para cualquier otro Turno es una mirilla, y cerrarla no detiene el trabajo.
_Avoid_: nieto, terminal, xterm (como nombre del concepto)

**Servidor** (`Server`):
El proceso `llama serve` que atiende los Pasos de los Turnos de los Mains que apuntan a él. Lo lanza un Supervisor, o el usuario a mano; en ese caso Main solo lo observa.
_Avoid_: deploy, LLM (como nombre del proceso)

**Supervisor** (`Supervisor`):
Entidad con nombre y sus propios Ajustes del Servidor que, cuando está **activo**, es un proceso de AllIn que lanza su Servidor, lo relanza si cae y lo reinicia con otros Ajustes cuando se le pide; **dormido**, solo existen sus Ajustes. No muere con Main. Varios Mains pueden compartir un Supervisor, y se reparten los slots de su Servidor.
_Avoid_: singleton, el script que ejecuta llama, servidor por defecto

**Enlace** (`server` link):
Ajuste de Main que nombra el único Supervisor al que manda sus Turnos. Al arrancar, Main despierta a ese Supervisor (o se adhiere a él si ya está activo) y a ningún otro. Un submain lo hereda por copia.

## Sesiones y registros

**Sesión** (`Session`):
Entidad persistente con id corto y carpeta propia (Conversación, Log, ajustes, functions); sobrevive al cierre de su Ventana.
_Avoid_: ventana, chat

**Carpeta de trabajo** (`workspace`):
La carpeta donde las tools de una Sesión pueden escribir sin declarar nada más, y su directorio actual: `<carpeta de datos>/workspaces/<id>/`, aparte de la carpeta de la Sesión, que guarda su estado. La Conversación nombra sus archivos en relativo. Solo un humano la puede apuntar a otra ruta, nunca dentro de AllIn; esa ruta no es un Ajuste y nunca viaja en plantillas, Moldes ni Kits. Entre Sesiones no se comparte por defecto: los archivos viajan como adjuntos de `/send`, que Main copia a `.allin/adjuntos/<prompt>/` (ADR-0016). Un Proceso adherido tiene la suya propia.
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
Lo que Main guarda de sí mismo tras cada cambio, en `state/<main_id>.json` (el del Main raíz pasa a la carpeta de su Perfil en #30), para que al reiniciarse (tras `/exit`, una señal o un `kill -9`) el usuario vuelva a estar donde estaba: Ventanas abiertas (con el pid de su xterm, para cerrarlas si Main muere y quedan huérfanas, ADR-0007), Borradores, cola de Prompts y Prompts de los Turnos en curso. Lo de cada Sesión (Conversación, Log, Ajustes) no va aquí.
_Avoid_: snapshot, sesión de Main

**Borrador** (`draft`):
Lo que hay en el input de una Ventana sin enviar; la Ventana se lo manda a Main cuando el usuario deja de teclear un momento, y vuelve al input al reabrirla o al reiniciar Main. Es una lista de bloques: el que se ve en el input y los que esperan detrás (el borde dice `+N waiting`). Un Prompt de Origen humano que vuelve al input (Turno fallido o interrumpido) se muestra como bloque propio y lo que había pasa a esperar; nunca se mezclan ni se repiten. Enter envía solo el bloque que se ve y muestra el siguiente.

**Sysprompt** (`Sysprompt`):
Archivo de texto plantilla en `sysprompts/`, dentro de la carpeta de su Perfil (hoy en la de instalación; cambia en #30), que, rellenado con los Ajustes de la Sesión, se vuelve el mensaje `developer` inicial de la Conversación. Es dato: Main lo rellena y nunca lo ejecuta. La Sesión guarda el texto renderizado, no la ruta: editar el archivo después no afecta a Sesiones existentes. Solo se puede fijar o cambiar mientras la Conversación no tiene Turnos (al crearla, o tras un Rewind hasta el inicio).
_Avoid_: system prompt (como nombre del archivo)

**Sesión coordinadora** (`coordinator`):
Papel de una Sesión que reparte trabajo a otras con Encargos y decide con sus resultados; se lo dan su Sysprompt o su Molde, no es un tipo de Sesión aparte.
_Avoid_: orquestador (es Main), maestro

**Sesión trabajadora** (`worker`):
Papel de una Sesión que recibe trabajo de una Sesión coordinadora; suele nacer de un Molde, sin Ventana.
_Avoid_: esclava, subventana

**Molde** (`Mold`):
Receta con nombre para crear una Sesión especializada: su Sysprompt, sus Ajustes, sus functions y, si se quiere, un historial inicial (mensajes que cuentan como inicio de la Conversación, no como Turnos) y una tarea inicial. La usan el usuario, las fsources y los submains para crear Sesiones que a su vez pueden crear otras. No es un Perfil.
_Avoid_: perfil, plantilla, preset

**Plano** (`Blueprint`):
Receta de un árbol de Mains: qué submains, qué Sesiones crea cada uno desde qué Moldes, cuáles abren Ventana y cuál coordina. Viaja en un Kit; crear desde un Plano solo añade, nunca toca lo que ya existe.
_Avoid_: topología, red, estructura (como nombre del concepto)

**Informe de recuperación** (`RecoveryReport`):
Lo que Main reúne al arrancar cuando su apagado anterior no fue limpio: lo que quedó a medias (Turnos, Ventanas, Procesos adheridos, Encargos). Queda en el Log, se muestra al usuario como Aviso y llega a cada Sesión afectada como Notificación; dice qué quedó a medias, no por qué murió Main.
_Avoid_: crash report, volcado

**Ajustes** (`Settings`):
Parámetros que gobiernan a Main o a una Sesión. Se heredan siempre por copia: lo ya creado no cambia cuando cambia el origen.
_Avoid_: settings, config

**Ajustes de Main** (`MainSettings`):
Los que Main usa para sí mismo (su idioma, sus colores, el Enlace, `parallel`…); no se comparten con las Sesiones.

**Ajustes de Sesión** (`SessionSettings`):
Los que usa una Sesión y su Ventana: parámetros del LLM y de interfaz (idioma, Tema, fuente…). Main guarda unos de plantilla que solo almacena, no usa, y que se copian a la Sesión en `/new`; después se cambian por Sesión con `/session set`, con efecto en su Ventana aunque esté abierta.

**Ajustes actuales** (`current settings`):
La versión de los Ajustes de Main (incluida la plantilla de Sesión) que rige ahora; se cambian con `/set` y son los que copian las Sesiones nuevas y los submains.

**Ajustes por defecto** (`default settings`):
La versión con la que Main arranca tras un `/reset`; se cambia con `/set --default`.

**Ajustes del Servidor** (`ServerSettings`):
Los parámetros de carga con los que arranca el Servidor (modelo, contexto, slots, caché KV, GPU…); solo cambian reiniciándolo. No son los parámetros de muestreo, que viajan en cada Paso desde los Ajustes de Sesión.
Los **en curso** son con los que corre el Servidor ahora (los informa el Supervisor); los **pendientes** son los que se aplicarán en su próximo arranque, sea por reinicio pedido o por caída.

**Tema** (`Theme`):
Conjunto con nombre de Ajustes visuales (fuente, tamaño, geometría, colores por rol) en `themes/<nombre>.toml`, dentro de la carpeta de su Perfil; las claves sueltas sobrescritas tienen prioridad sobre él. Es dato y no pasa por cuarentena.
_Avoid_: skin, estilo

## Ejecución

**Prompt**:
Una petición al LLM para una Sesión, en espera en la cola de Main, siempre con su Origen. Nace del Command main `/send` (lo que el usuario envía con Enter es `/send` a su propia Sesión) o de una Suscripción que despierta. Si su Turno falla o se cancela, vuelve al input solo si su Origen es humano; si no, falla hacia quien lo envió y queda en el Log. Su respuesta es la del último Turno de su cadena: un Turno que deja abierto un Encargo, un `--reply-to` o un Goal todavía no responde (ADR-0011).
_Avoid_: /run

**Turno** (`Turn`):
La atención completa de un Prompt: empieza cuando Main lo saca de la cola y termina cuando un paso concluye sin llamadas a tools pendientes, o al cancelarse o fallar. Una Sesión tiene a lo sumo un Turno activo. Sesiones distintas pueden tener Turnos activos en paralelo, y sus Pasos corren a la vez hasta el número de slots del Servidor, que se reparten entre los Mains que lo comparten; un Turno cuyas tools corren no ocupa slot.
_Avoid_: request, petición (para el ciclo completo)

**Turno suspendido** (`suspended turn`):
Turno que, al caer o reiniciarse el Servidor, no falla porque ya llamó a una Zombie: conserva su Conversación hasta la salida de la última Zombie terminada y continúa con un Paso nuevo cuando el Servidor vuelve a estar listo, como Continuación.

**Continuación** (`Continuation`):
Trabajo que retoma algo ya empezado: un Turno suspendido, el Paso siguiente de un Turno cuyas tools terminaron, o una Sesión que despierta de una Suscripción. Dentro de su Main va antes que los Prompts nuevos, salvo cuando ya pasaron varias seguidas y toca dejar pasar uno nuevo.

**Suscripción** (`Subscription`):
Petición de una Sesión de que un evento la despierte: cuando ocurre (responden otras Sesiones, un Proceso adherido dice algo que le interesa, pasa un tiempo…), Main le encola un Prompt con lo ocurrido. Mientras espera no tiene Turno activo ni ocupa slot, y no bloquea a su Sesión: esta atiende sus Prompts con normalidad, y el del evento entra como Continuación en cuanto la Sesión no tenga Turno activo.
_Avoid_: listener, hook, trigger

**Encargo** (`Errand`):
Suscripción a las respuestas de los Prompts que una Sesión repartió a otras Sesiones de su árbol de Mains: la despierta con la lista de resultados cuando todas terminan, sea bien, fallando o sin respuesta al vencer su plazo. Los Encargos forman un árbol: ninguno puede esperar a la Sesión que lo abrió ni a sus ancestros. Pertenece a la Sesión, no a su Turno: cerrar su Ventana no lo cancela; solo lo cancelan un Rewind a antes de abrirlo o una cancelación explícita, que alcanza a lo que sus trabajadoras hacen para él. Vencer su plazo cancela igual la parte vencida, y ese plazo solo corre mientras su Perfil está activo en un Main vivo. Es del Perfil en que se abrió.
_Avoid_: job, barrera, tarea

**Paso** (`Step`):
Una sola llamada a `responses.create` dentro de un Turno; un Turno con tools tiene varios Pasos.
_Avoid_: ronda, iteración

**Contexto** (`Context`):
Tokens totales que ocupa la Conversación en el último Paso (entrada + salida), comparados contra el límite que el Servidor da a cada slot (no un Ajuste). Con caché KV unificada, ese límite lo comparten las Sesiones con Turno activo. Cuando una Sesión se acerca a su límite, se compacta.
_Avoid_: tokens gastados (ambiguo)

**Compactación** (`Compaction`):
Reemplazar la parte vieja de la Conversación de una Sesión por un resumen que escribe el LLM, archivando lo reemplazado, para que siga cabiendo en su Contexto. Se hace a mano (`/session compact`, `@compact`) o sola, al pasar un umbral del límite del slot (`compact.auto`, encendida por defecto). Quedan literales el Sysprompt, el historial inicial, el Prompt actual y los Turnos recientes. El resumen entra como `user`, nunca como `developer`, y un Rewind a antes del corte recupera lo archivado. Nunca se trunca en silencio (ADR-0017).
_Avoid_: truncar, context shift, resumen (es lo que produce)

## Comandos

**Command main** (`MainCommand`):
Comando de Main, escrito con `/`. Es la única vía para modificar el estado; se carga al arrancar Main y no se recarga en caliente.

**Command session** (`SessionCommand`):
Comando de una Ventana, escrito con `@` al inicio de un input que no contiene nada más; no lanza Turno y se traduce a exactamente un Command main. Sus módulos son parte de Main, comunes a todos los Perfiles, como los de Avisos (ADR-0016).
_Avoid_: comsession (salvo como nombre de carpeta)

**Command prompt** (`PromptCommand`):
Directiva escrita con `@` en cualquier posición de un input que lanza un Turno; solo afecta a ese Turno y nunca cambia los Ajustes. Solo se reconoce en lo que teclea un humano (ADR-0013). Hoy existen dos: Referencia a archivo y Sobrescritura de parámetro.

**Referencia a archivo** (`FileReference`):
Command prompt `@<ruta>` (empieza con `/`, `~` o `./`, precedido de espacio o inicio) que adjunta un archivo al Prompt; Main lo copia, como instantánea, a `.allin/adjuntos/<prompt>/` en la Carpeta de trabajo de la Sesión, y la Conversación lo nombra en relativo para que sus tools lo lean; si ya está dentro, no se copia (ADR-0016).
_Avoid_: @path (como nombre del concepto)

**Sobrescritura de parámetro** (`ParamOverride`):
Command prompt `@clave=valor` (p. ej. `@effort=medium`) que cambia un parámetro del LLM solo para ese Turno. Solo admite la lista cerrada de parámetros de muestreo que viajan en cada Paso; nunca functions, Sysprompt ni Alcance.

**Origen** (`origin`):
De dónde viene un Prompt o un comando: un humano (lo tecleado en una Ventana o en el Chat de Main) o una Sesión, con la cadena por la que llegó. Una acción nunca tiene más autoridad que el eslabón más débil de su Origen. Lo que llega de otra Sesión es dato: Main nunca lo trata como comando ni le da más autoridad, y el LLM puede seguirlo, pero con la autoridad de su cadena. Una pieza (function, Sysprompt, Molde, Plano) tiene Origen humano solo cuando un humano aprobó su versión; hasta entonces está en cuarentena.

**Notificación** (`Notification`):
Línea que Main deja pendiente, por un comando, una fsource o un evento suyo (el avance de un Encargo, el Informe de recuperación, un ciclo de Suscripciones cortado), para entrar en la Conversación al inicio del siguiente Prompt, bajo la cabecera `[LOGS @COMMANDS]`, como mensaje `user` y nunca `developer`: lo que cuenta de otras Sesiones es dato (ADR-0013). Las Notificaciones de acciones que se anulan entre sí antes de enviarse se cancelan.
_Avoid_: log (una Notificación se deriva del Log, pero no es el Log)

**Conversation logs**:
Ajuste de la Sesión que decide, por cada comando o fsource, si sus Notificaciones entran en la Conversación; se controla con `@conversation_logs on|off|list [fuente]`. Nunca afecta al Log ni al Chat.
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
Función que solo el LLM puede invocar (va en `tools=[...]`); un módulo = una tool, salvo una fscreen, que expone una por método. El usuario nunca la llama directamente.
_Avoid_: skill, comando

**fsource**:
Function tool que se hereda automáticamente en `/new` (salvo `-sf=off`) y que puede ejecutar Command sessions; es la vía por la que el LLM actúa sobre AllIn.
_Avoid_: source, function source

**fagent**:
Function tool escrita por el usuario o por el LLM; se pasa explícitamente en `/new` o se añade después. Puede agruparse en packages fuera de las Sesiones. No es un agente.
_Avoid_: agent, function agent, agente

**fscreen**:
Clase cuyos métodos públicos se exponen como Function tools para controlar un Proceso adherido, que el usuario también puede manipular directamente. `screen.py` es solo un ejemplo de fscreen, sin estatus especial; su comportamiento lo definen sus métodos, no los Ajustes.
_Avoid_: screen, function screen

**Proceso adherido** (`Attachment`):
Proceso vivo y con estado que Main lanza a partir de una fscreen y que sus Sesiones controlan llamando a los métodos de esta (un lienzo, un archivo compartido con su cursor…). Main solo lo adhiere, lo vigila y lo suelta; lo que hace lo define su fscreen. Muere con Main, y al cambiar de Perfil se cierran los del saliente. Al adherirlo recibe un nombre y una Carpeta de trabajo propia que lo sobrevive; Main nunca lo relanza solo, y adherirlo de nuevo con el mismo nombre le devuelve esa carpeta. Main rechaza la escritura de una Sesión que no ha visto su último cambio, para que nadie escriba sobre una lectura obsoleta.
_Avoid_: screen, plugin, servicio

**Alcance** (`reach`):
Lo que el proceso de una Function tool o de un Proceso adherido puede tocar, declarado con el decorador `@reach` (qué carpetas lee y escribe, qué puertos de red, si abre una ventana gráfica) y forzado por el Encierro. Sin decorador es el mínimo: su Carpeta de trabajo, leer el sistema y sin red. Nunca incluye nada de AllIn, ni dentro de un Alcance amplio como `home`: ni la carpeta de datos (salvo la Carpeta de trabajo, el `TMPDIR` de la llamada y su propio package), ni la instalación (solo lectura), ni los puertos de sus Servidores; tampoco las terminales. `display`, o escribir donde el usuario ejecuta, equivale a actuar como él. Es parte del código aprobado: ampliarlo devuelve la function a cuarentena, y al aprobarla se puede reducir (ADR-0016).
_Avoid_: permiso, sandbox (como nombre del concepto), capacidades

**Encierro** (`confinement`):
Lo que el kernel impone al proceso de una Function tool o de un Proceso adherido según su Alcance: Landlock para archivos y TCP, y un filtro seccomp para lo que Landlock no ve (sockets unix, UDP sin red, io_uring, meter teclas en una terminal). El proceso se lo pone a sí mismo antes de importar su function, y no hereda de Main ni su terminal, ni su entorno, ni sus rutas de importación. Nada lo salta, ni `--devastation` (ADR-0016).
_Avoid_: sandbox, jaula

**Zombie** (`zombie`):
Marca (decorador) de una fsource o fagent cuya llamada sobrevive a una caída o reinicio del Servidor: su Turno queda suspendido en lugar de fallar. Las fscreen no la admiten. Solo cubre al Servidor, no a una caída de Main. No es un cuarto tipo de function.

**Confirmación** (`Confirmation`):
Pregunta s/n que Main hace al usuario humano antes de una acción marcada como delicada (una Function tool con el decorador `@confirm`, o un comando), en un recuadro superpuesto en la Ventana de la Sesión y en el Chat de Main; si la rechaza, el LLM recibe el rechazo como salida de la tool. Mientras espera, el Turno queda pausado sin ocupar slot y conserva su lugar: al responderse sigue como Continuación, antes que los Prompts nuevos de su Main. `-y` la salta para esa invocación y `--devastation` para todas, pero solo con Origen humano; no se apaga desde los Ajustes. Crear o modificar una pieza desde una Sesión siempre la pide, y además deja la pieza en cuarentena.
_Avoid_: aprobación (es otra puerta), permiso

**Aprobación** (`approval`):
Lo que un humano da, a propósito, a todo lo que amplía un Encierro: cada versión de una pieza con su Alcance, la ruta de una Carpeta de trabajo, un archivo de fuera que pida una Sesión y los Ajustes del Servidor que nombran un archivo o abren la red. Ni `-y` ni `--devastation` la saltan. Da Origen humano a la pieza, pero no cambia su procedencia, y lo que se ajusta al aprobar (`@confirm`, un Alcance menor) se guarda con ella, sin tocar el código (ADR-0013).
_Avoid_: Confirmación, permiso

**Cuarentena** (`quarantine`):
Estado de una pieza nueva o cambiada (function, Sysprompt, Molde, Plano, historial inicial) hasta su Aprobación: Main la muestra, pero no la ofrece al LLM ni la usa. De una function cuenta su package entero: cambiar cualquier archivo devuelve a cuarentena todas sus functions (ADR-0013).
_Avoid_: pendiente, bloqueo

**Aviso** (`Notice`):
Recuadro superpuesto en una Ventana (y en el Chat de Main) que informa algo sin pedir decisión: se cierra con una tecla y nunca bloquea un Turno. Cada tipo de Aviso es un módulo de `notices/`, parte de Main y común a todos los Perfiles, que se declara con un decorador y tiene sus propios Ajustes: encendido, título y texto en todos, y los que su tipo necesite (el de Contexto, un umbral), así que su lectura no tiene que ser de alarma; el de Contexto sale al cruzar un umbral del límite. No es una Notificación: no entra en la Conversación.
_Avoid_: advertencia (como nombre del concepto), alerta, popup
