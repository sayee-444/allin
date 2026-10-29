# AllIn

Se pronuncia "Alyn". Antes se llamaba Neva; ese nombre ya no se usa en ningún lado.

Los términos están en español; entre paréntesis va el nombre canónico en código (todo el código, comentarios y Log van en inglés).

Orquestador de terminal para conversar con un LLM local (vía OpenAI SDK) desde varias ventanas, donde un proceso central ordena y atiende las peticiones de cada una.

## Procesos

**Main**:
El proceso orquestador (`main.py`); dueño único del estado de todas las Sesiones y de la cola de Turnos.
_Avoid_: abuelo, padre, proceso principal

**Lanzador** (`Launcher`):
El proceso intermedio que Main crea para abrir una Ventana; solo existe para hacer `setsid` + `exec` de xterm y Main espera su terminación.
_Avoid_: hijo, worker

**Ventana** (`Window`):
El proceso xterm/curses (`start.py`) que muestra una Sesión y recibe lo que escribe el usuario. A lo sumo una Ventana por Sesión.
_Avoid_: nieto, terminal, xterm (como nombre del concepto)

## Sesiones y registros

**Sesión** (`Session`):
Entidad persistente con id corto y carpeta propia (Conversación, Log, ajustes, functions); sobrevive al cierre de su Ventana.
_Avoid_: ventana, chat

**Chat** (`Chat`):
Lo que se dibuja en el pad de chat de una Ventana o de Main: mensajes, etiquetas, errores y salidas de comandos. Se reconstruye a partir de Conversación + Log.
_Avoid_: historial, conversación

**Conversación** (`Conversation`):
La lista de mensajes/ítems que el LLM recibe como `input`; es lo único que el modelo "ve".
_Avoid_: chat, historial

**Log** (`Log`):
Registro con fecha y hora de los eventos de una Sesión o de Main; siempre activo, nunca se apaga. El Log de Main y el de cada Sesión son independientes.
_Avoid_: historial

**Sysprompt** (`Sysprompt`):
Archivo de texto plantilla en `sysprompts/` que, renderizado con los Ajustes de la Sesión, se vuelve el mensaje `developer` inicial de la Conversación. La Sesión guarda el texto renderizado, no la ruta: editar el archivo después no afecta a Sesiones existentes. Solo se puede fijar o cambiar mientras la Conversación no tiene Turnos (al crearla, o tras un Rewind hasta el inicio).
_Avoid_: system prompt (como nombre del archivo)

**Ajustes** (`Settings`):
Parámetros que gobiernan a Main o a una Sesión. Se heredan siempre por copia: lo ya creado no cambia cuando cambia el origen.
_Avoid_: settings, config

**Ajustes de Main** (`MainSettings`):
Los que Main usa para sí mismo (su idioma, sus colores, `llm.parallel`…); no se comparten con las Sesiones.

**Ajustes de Sesión** (`SessionSettings`):
Los que usa una Sesión y su Ventana: parámetros del LLM y de interfaz (idioma, Tema, fuente…). Main guarda unos de plantilla que solo almacena, no usa, y que se copian a la Sesión en `/new`; después se cambian por Sesión con `/session set`, con efecto en su Ventana aunque esté abierta.

**Ajustes actuales** (`current settings`):
La versión de los Ajustes de Main (incluida la plantilla de Sesión) que rige ahora; se cambian con `/set` y son los que copian las Sesiones nuevas y los submains.

**Ajustes por defecto** (`default settings`):
La versión con la que Main arranca tras un `/reset`; se cambia con `/set --default`.

**Tema** (`Theme`):
Conjunto con nombre de Ajustes visuales (fuente, tamaño, geometría, colores por rol) en `themes/<nombre>.toml`; las claves sueltas sobrescritas tienen prioridad sobre él.
_Avoid_: skin, estilo

## Ejecución

**Prompt**:
Una petición al LLM para una Sesión, en espera en la cola de Main; siempre nace del Command main `/send` (lo que el usuario envía con Enter es `/send` a su propia Sesión).
_Avoid_: /run

**Turno** (`Turn`):
La atención completa de un Prompt: empieza cuando Main lo saca de la cola y termina cuando un paso concluye sin llamadas a tools pendientes, o al cancelarse o fallar. Una Sesión tiene a lo sumo un Turno activo; Sesiones distintas pueden tener Turnos en paralelo hasta el número de slots del servidor.
_Avoid_: request, petición (para el ciclo completo)

**Paso** (`Step`):
Una sola llamada a `responses.create` dentro de un Turno; un Turno con tools tiene varios Pasos.
_Avoid_: ronda, iteración

**Contexto** (`Context`):
Tokens totales que ocupa la Conversación en el último Paso (entrada + salida), comparados contra el límite de contexto de la Sesión.
_Avoid_: tokens gastados (ambiguo)

## Comandos

**Command main** (`MainCommand`):
Comando de Main, escrito con `/`. Es la única vía para modificar el estado; se carga al arrancar Main y no se recarga en caliente.

**Command session** (`SessionCommand`):
Comando de una Ventana, escrito con `@` al inicio de un input que no contiene nada más; no lanza Turno y se traduce a exactamente un Command main.
_Avoid_: comsession (salvo como nombre de carpeta)

**Command prompt** (`PromptCommand`):
Directiva escrita con `@` en cualquier posición de un input que lanza un Turno; solo afecta a ese Turno y nunca cambia los Ajustes. Hoy existen dos: Referencia a archivo y Sobrescritura de parámetro.

**Referencia a archivo** (`FileReference`):
Command prompt `@<ruta>` (empieza con `/`, `~` o `./`, precedido de espacio o inicio) que adjunta un archivo al Prompt.
_Avoid_: @path (como nombre del concepto)

**Sobrescritura de parámetro** (`ParamOverride`):
Command prompt `@clave=valor` (p. ej. `@effort=medium`) que cambia un parámetro del LLM solo para ese Turno.

**Notificación** (`Notification`):
Línea que un Command session o fsource deja pendiente para entrar en la Conversación al inicio del siguiente Prompt, bajo la cabecera `[LOGS @COMMANDS]`. Las Notificaciones de acciones que se anulan entre sí antes de enviarse se cancelan.
_Avoid_: log (una Notificación se deriva del Log, pero no es el Log)

**Conversation logs**:
Ajuste de la Sesión que decide, por cada comando o fsource, si sus Notificaciones entran en la Conversación; se controla con `@conversation_logs on|off|list [origen]`. Nunca afecta al Log ni al Chat.
_Avoid_: @logs

**Rewind** (`Rewind`):
Retroceder una Sesión N Prompts del usuario: trunca la Conversación hasta antes de ese Prompt y devuelve su texto al input para reeditarlo; `n=0` no hace nada. Solo existe como Command main.

**Clon** (`Clone`):
Sesión nueva creada como copia de otra en su último estado consistente (Conversación, Ajustes, functions); tiene id y Log propios. Se crea con `/session clone` o `@clone`.
_Avoid_: fork, copia

**Goal** _(experimental)_:
fsource que repite Turnos sobre una meta descrita en un `.md` hasta que el LLM la declara cumplida, se rinde, o se agota `max_rounds`; cada ronda es un Turno separado que vuelve a la cola.
_Avoid_: loop

## Functions

**Function tool** (`FunctionTool`):
Función que solo el LLM puede invocar (va en `tools=[...]`); un módulo = una tool. El usuario nunca la llama directamente.
_Avoid_: skill, comando

**fsource**:
Function tool que se hereda automáticamente en `/new` (salvo `-sf=off`) y que puede ejecutar Command sessions; es la vía por la que el LLM actúa sobre AllIn.
_Avoid_: source, function source

**fagent**:
Function tool escrita por el usuario o por el LLM; se pasa explícitamente en `/new` o se añade después. Puede agruparse en packages fuera de las Sesiones. No es un agente.
_Avoid_: agent, function agent, agente

**fscreen**:
Clase cuyos métodos públicos se exponen como Function tools para controlar un proceso que corre en paralelo adherido a Main, que el usuario también puede manipular directamente. `screen.py` es solo un ejemplo de fscreen, sin estatus especial; su comportamiento lo definen sus métodos, no los Ajustes.
_Avoid_: screen, function screen

**Confirmación** (`Confirmation`):
Pregunta s/n que Main hace al usuario humano antes de una acción marcada como delicada (una Function tool o un comando); `-y` la salta para esa invocación y `--devastation` para todas.
_Avoid_: aprobación, permiso
