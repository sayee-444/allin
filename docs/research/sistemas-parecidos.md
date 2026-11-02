# Investigación: ¿existe ya un sistema parecido a AllIn?

Fecha: 2026-11-01. Dos agentes de investigación en paralelo, con solo fuentes primarias (README y código de cada repositorio, documentación oficial, especificaciones, arXiv). Lo no comprobado va como «sin verificar»; lo que salió de un agregador o un blog va marcado como secundario. Las versiones y fechas son las que daba cada fuente ese día.

## Resumen

**La combinación que es el núcleo de AllIn no aparece en ningún proyecto revisado:** varias Ventanas de terminal, un solo LLM local compartido, un Main que ordena los Turnos sobre los slots de la GPU y una autoridad por Origen sobre todo lo que se ejecuta. Casi todas las piezas existen sueltas, en proyectos distintos y sin relación entre sí.

| Pieza de AllIn | ¿Existe fuera? | Lo más cercano |
|---|---|---|
| Main: un planificador de Turnos sobre los slots, con admisión, reserva de tokens y espera entre Mains | No en un producto | AIOS (paper, COLM 2025): planificador central de llamadas al LLM con FIFO y Round Robin. llama.cpp, Ollama y llama-swap solo tienen colas FIFO sin Sesiones ni Origen |
| Supervisor: lanzar, dormir, despertar y relanzar el Servidor | En parte | llama.cpp (`--sleep-idle-seconds`, modo router), llama-swap (TTL y salud), Ollama (`keep_alive`), LocalAI (watchdog). Ninguno documenta relanzar un servidor caído |
| Varias Ventanas y Organizador | Sí, sin un LLM compartido | claude-squad, WaitAgent (alfa): sesiones en tmux con el estado de cada una |
| Compactación con resumen archivado | Como idea | MemGPT/Letta |
| Rewind | Sí | LangGraph (checkpoints, fork) |
| Confirmación humana por tool | Sí | llm `--ta`, LangGraph `interrupt`, AIOS §3.8, MCP («explicit user consent») |
| Autoridad por Origen con el eslabón más débil, sobre órdenes | **No** | CaMeL y FIDES la aplican a los datos; CAPMAS, HDP y SentinelAgent (papers de 2026) atenúan permisos por salto de delegación, sin un producto |
| Encierro por llamada con Landlock y seccomp según un Alcance | Casi | **nono**: Landlock, un encierro nuevo por llamada y política según quién llama. Codex, Claude Code y Gemini CLI usan bubblewrap + seccomp (en Codex, Landlock quedó como legacy) |
| Aprobación por hash y cuarentena | Sí, fuera del mundo de agentes | direnv (`sha256` de ruta y contenido), pip `--require-hashes`; nono con firmas Sigstore |
| Procesos adheridos (fproc) | **No** | Servidores MCP y kernels de Jupyter tienen el ciclo de vida de un proceso largo, pero sus tools son solo para el modelo. La revisión 2026-07-28 de MCP es sin estado a propósito y depreca sampling y roots |
| RAG por Perfil con reranker local | En parte | Open WebUI (knowledge), AnythingLLM (workspaces), mem0. Ninguno encerrado como proceso con Supervisores propios |

## Lo que conviene llevarse para el diseño

- **nono** merece una lectura antes del prototipo de F2. Sus notas de Landlock coinciden con ADR-0016 (ABI 1, 4 y 6; UDP sin cubrir) y añaden un detalle útil: las reglas se atan al inodo, así que reemplazar un archivo de forma atómica deja la concesión «stale». Para los sockets unix usa seccomp con user-notify en lugar de prohibirlos.
- **MCP 2026-07-28** se aleja de lo que es una fproc: sin estado, con handles explícitos y sin sampling. Adherir un servidor MCP como fproc sigue siendo posible, porque en stdio sigue siendo un proceso largo que el cliente lanza y reinicia, pero su estado vivo no se podrá dar por supuesto.
- **«The Chain Is Signed, the Handoff Is Prose»** (NeurIPS 2026): cuando la delegación se escribe en prosa, la identidad de quien la originó sobrevive solo el 35,8 % de las veces. Respalda que el Origen lo lleve Main fuera del texto, como dice ADR-0013.
- **llama.cpp** ya trae `--sleep-idle-seconds` (descarga el modelo en reposo y lo recarga con la siguiente tarea) y un modo router con `/models/load` y `/models/unload`. Son candidatos para el Supervisor y para el prototipo del RAG, que quería saber si el modo router sirve con dos modelos.

Las dos partes siguientes son los informes de cada agente, tal como los devolvieron.

## Parte 1 · Orquestación, planificación y UI local

### llama.cpp `llama-server`: slots, modo router y suspensión en reposo
**Qué es.** El servidor HTTP de llama.cpp. Ofrece rutas compatibles con OpenAI para chat, responses y embeddings. Según la API de GitHub, la última release es la etiqueta `v0.8.0`, del 2026-10-28 (https://api.github.com/repos/ggml-org/llama.cpp/releases/latest). Es raro que use semver; antes las etiquetas tenían la forma bNNNN, y el motivo está sin verificar.
- **Slots.** `-np, --parallel N` fija el «number of server slots (default: -1, -1 = auto)». `--kv-unified` hace que todas las secuencias compartan un buffer KV, y viene activo por defecto si el número de slots es auto. `--kv-unified-per-slot N` limita el contexto de cada slot (https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).
- **Asignación a un slot.** `id_slot=-1` manda la tarea a un slot libre. `-sps, --slot-prompt-similarity` (0.10 por defecto) elige el slot cuyo prompt más se parece al de la petición (misma URL).
- **Cuando todos los slots están ocupados.** La tarea pasa a `queue_tasks_deferred` con `push_back`. Al liberarse un slot, `pop_deferred_task(id_slot)` saca primero una tarea diferida que apunte a ese slot y, si no hay ninguna, la más antigua («just pop the first deferred task (default behavior)») (https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/server-queue.cpp). La métrica `llamacpp:requests_deferred` cuenta estas tareas (README). No hay prioridades ni Origen en esta cola.
- **Persistencia.** `--slot-save-path` guarda la caché KV de cada slot. `--cache-reuse N` reutiliza la caché mediante desplazamiento del KV. `GET /slots?fail_on_no_slot=1` devuelve 503 si no queda ningún slot libre (README).
- **Suspensión en reposo.** `--sleep-idle-seconds` descarga el modelo y la caché KV tras ese tiempo sin tareas, y «Any new incoming task will automatically trigger the model to reload». Las peticiones a `/health`, `/props`, `/models` y `/metrics` ni despiertan el servidor ni reinician el contador. `/props` expone `is_sleeping` (https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md, desde el carácter 100000).
- **Modo router.** Si se arranca sin modelo, `llama-server` expone una API para cargar y descargar modelos (`POST /models/load` y `POST /models/unload`). Cada instancia hereda los argumentos y el entorno del router. Los estados posibles son `unloaded|loading|loaded|sleeping|downloading|incomplete`. Otras opciones: presets en un `.ini` (`--models-preset`), `--models-max` (4 por defecto) y `--models-memory-margin`, que descarga por LRU hasta que el modelo nuevo quepa (misma URL).
- **`/v1/responses`.** «works by converting Responses request into Chat Completions request» (misma URL).

**Qué cubre de AllIn.** Es el sustrato de los Turnos: los slots, la cola diferida y la caché por slot. También hace parte de lo que hace el Supervisor: dormir y despertar el modelo (`--sleep-idle-seconds`) y cargar y descargar modelos (modo router).
**Qué no cubre.** No relanza el servidor si muere, y eso sigue siendo trabajo del Supervisor. No hay admisión a un pool, reserva de tokens de salida, prioridad por Origen ni equidad entre Sesiones; la cola es FIFO con preferencia por slot. Tampoco hay lista de espera entre varios procesos clientes.

### llama-swap
**Qué es.** Un proxy en Go que intercambia modelos en caliente según el campo `model` de cada petición (https://github.com/mostlygeek/llama-swap). Última release: v269, del 2026-11-01 (https://api.github.com/repos/mostlygeek/llama-swap/releases/latest).
- **Grupos.** `swap`, `exclusive` (cargar un miembro descarga los demás grupos) y `persistent`. Hay además una `matrix` de combinaciones que pueden estar cargadas a la vez, y «the solver makes room for it by evicting as few running models as possible» (https://raw.githubusercontent.com/mostlygeek/llama-swap/main/docs/config.example.yaml).
- **Descarga por inactividad.** Con `ttl`; `unloadTimeout` da margen para una descarga ordenada.
- **Salud.** `checkEndpoint`: «all requests wait until the endpoint is ready or fails». `healthCheckTimeout` se reinicia cada vez que cambia el progreso de la carga (misma URL).
- **Concurrencia y cola.** `concurrencyLimit` por modelo devuelve 429 al superarlo. `globalConcurrencyLimit` devuelve 429 «immediately, rather than being queued». `scheduler`: «higher priority requests are serviced first in the queue», y el único valor válido es `"fifo"`. La prioridad se fija por modelo, no por petición (misma URL).
- **Otros.** `hooks.on_startup.preload` precarga modelos. `sendLoadingState` inyecta el estado de carga en el campo de razonamiento para que la UI lo muestre (misma URL).

**Qué cubre.** Una parte del Supervisor: arranca procesos upstream, comprueba su salud, los descarga por TTL y avisa de la carga. Tiene un esbozo de cola con prioridad.
**Qué no cubre.** El README no dice que reinicie un upstream caído (sin verificar). La prioridad va por modelo y no por Sesión ni por Origen. No hay reserva de tokens ni noción de Sesión.

### Paddler
**Qué es.** «open-source LLM load balancer and serving platform» construido sobre llama.cpp. Tiene su propia implementación de slots, cada uno con su contexto y su caché KV, y agentes que se añaden dinámicamente. Una de sus funciones es «Request buffering, enabling scaling from zero hosts» (https://github.com/intentee/paddler). Última release: v4.1.0, del 2026-07-19 (https://api.github.com/repos/intentee/paddler/releases/latest).
**Qué cubre.** Un búfer de peticiones mientras no hay capacidad, que se parece a la lista de espera, y el equilibrio de carga entre varios hosts o slots.
**Qué no cubre.** Está pensado para un clúster, no para una sola GPU con varias Sesiones. Sin Origen, sin reserva de tokens y sin Sesiones. El README no detalla el algoritmo de equilibrio ni qué pasa cuando no hay slot libre (sin verificar).

### Ollama
**Qué es.** Un servidor local de modelos. Última release: v0.40.13, del 2026-10-28 (https://api.github.com/repos/ollama/ollama/releases/latest).
- `OLLAMA_NUM_PARALLEL` fija las peticiones simultáneas por modelo (1 por defecto). `OLLAMA_MAX_LOADED_MODELS` limita los modelos cargados. `OLLAMA_MAX_QUEUE` (512 por defecto) devuelve 503 si la cola se llena. Un modelo se mantiene cargado 5 minutos tras usarse, con `keep_alive` por petición u `OLLAMA_KEEP_ALIVE` global, y `0` lo descarga en cuanto responde. Si el modelo nuevo no cabe, «all new requests will be queued until the new model can be loaded», y las peticiones en cola se atienden en orden (https://github.com/ollama/ollama/blob/main/docs/faq.mdx).
- **Código del planificador.** `pendingReqCh` tiene el tamaño de `MaxQueue`. `processPending` lleva el comentario «Block other requests until we get this pending request running». Para desalojar un modelo pone `sessionDuration=0` y espera a `unloadedCh`; solo descarga cuando `refCount` es 0. Un comentario dice «llama-server auto-detects layers…», lo que sugiere que Ollama lanza runners de `llama-server`; no lo he comprobado más allá de ese comentario (https://github.com/ollama/ollama/blob/main/server/sched.go).

**Qué cubre.** Cola de admisión con límite, carga y descarga por inactividad, y desalojo para hacer sitio. Equivale a una parte del Supervisor y a una cola de Turnos muy simple.
**Qué no cubre.** La cola es FIFO global, sin prioridad, sin Sesión, sin Origen y sin reserva de tokens. No hay coordinación entre varios clientes más allá de esa cola.

### LocalAI
**Qué es.** Un servidor local compatible con OpenAI con varios backends. Última release: v4.12.1, del 2026-10-23 (https://api.github.com/repos/mudler/LocalAI/releases/latest).
- `--max-active-backends` desaloja por LRU (con 1 equivale al modo de un solo backend), y «By default, LocalAI will skip evicting models that have active API calls».
- **Watchdog.** Uno de inactividad descarga los modelos sin uso. Otro de ocupación vigila backends que llevan demasiado tiempo procesando; con peticiones en paralelo, el tiempo cuenta desde la petición en vuelo más antigua. Al descargar espera «up to 30 seconds for in-flight requests». Si todos están ocupados, reintenta el desalojo (https://localai.io/docs/advanced/vram-management/).

**Qué cubre.** Es lo más parecido al Supervisor de los servidores revisados: detecta backends colgados y descarga por inactividad.
**Qué no cubre.** No documenta una cola de peticiones (sin verificar). Sin Sesiones, sin Origen y sin Turnos.

### LM Studio
**Qué es.** Una aplicación de escritorio con un servidor local. «Max Concurrent Predictions» vale 4 por defecto y se apoya en continuous batching; requiere el runtime llama.cpp v2.0.0 (https://lmstudio.ai/docs/app/advanced/parallel-requests). Tiene carga JIT, un TTL de reposo de 60 minutos por defecto y Auto-Evict, que deja un solo modelo cargado por JIT (https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict). No he comprobado la versión (sin verificar).
**Qué cubre.** Slots en paralelo y carga y descarga automáticas, como una versión mínima del Supervisor.
**Qué no cubre.** Cola con prioridad, Sesiones con Rewind, Origen y Encierro.

### Open WebUI
**Qué es.** Una interfaz web autoalojada sobre Ollama o cualquier API compatible con OpenAI. Tiene roles, grupos y permisos por usuario, acceso a modelos por usuario o grupo, RAG con 9 bases vectoriales y reranking, «Channels» compartidos, conversaciones con varios modelos a la vez y una cola de mensajes mientras el modelo responde (https://github.com/open-webui/open-webui). Última release: v0.12.5, del 2026-10-29 (https://api.github.com/repos/open-webui/open-webui/releases/latest).
- **Tools.** «The Tools feature is designed to execute user-provided Python code on the server». Conceder `workspace.tools` «is documented as equivalent to giving the user shell access» (https://docs.openwebui.com/security/vendor-dispositions/cve-2026-0766.txt). Para ejecutar código ofrece «Terminals», con «per-user isolated containers» (README).

**Qué cubre.** Perfiles (usuarios y grupos), RAG por usuario y un parecido lejano a las Command sessions en los Channels.
**Qué no cubre.** Las tools corren en el servidor sin Encierro por llamada. No hay Origen por Prompt, Aprobación por hash, Turnos entre Sesiones ni Supervisor. No tiene ventanas de terminal.

### Jan
**Qué es.** Una alternativa offline a ChatGPT con llama.cpp integrado, un servidor local en `localhost:1337`, asistentes y MCP (https://github.com/janhq/jan). Última release: v0.8.8, del 2026-10-28 (https://api.github.com/repos/janhq/jan/releases/latest).
**Qué cubre.** Algo parecido a los Sysprompts por asistente.
**Qué no cubre.** El README no habla de planificación, varias Sesiones simultáneas con cola, Origen ni Encierro.

### AnythingLLM
**Qué es.** Una aplicación de escritorio o Docker con workspaces, varios usuarios con permisos (solo en Docker), agentes, un constructor de flujos sin código, tareas programadas con cron, MCP y RAG con LanceDB por defecto. Funciona con Ollama, LM Studio y otros (https://github.com/Mintplex-Labs/anything-llm). Última release: v1.19.0, del 2026-10-26 (https://api.github.com/repos/Mintplex-Labs/anything-llm/releases/latest).
**Qué cubre.** RAG por workspace, que se acerca a RAG por Perfil. Las tareas programadas se parecen a trabajar en segundo plano.
**Qué no cubre.** Turnos, Supervisor, Origen y Encierro. Cómo aísla los workspaces está sin verificar.

### aichat (más llm-functions)
**Qué es.** Una CLI y REPL con sesiones, roles (prompt más configuración de modelo), agentes («Instructions + Tools + Documents»), RAG, un servidor `--serve` y la web Arena (https://github.com/sigoden/aichat). Última release: v0.30.0, del 2025-07-06, así que la actividad parece haber bajado (https://api.github.com/repos/sigoden/aichat/releases/latest).
- **Tools.** Los ejemplos de llm-functions ejecutan lo que manda el modelo con `eval` y `exec`, y el README no describe ningún sandbox (https://raw.githubusercontent.com/sigoden/llm-functions/main/README.md). Si existen guardas de confirmación en el código, está sin verificar.

**Qué cubre.** Sesiones y roles, parecidos a Sesión más Sysprompt, y agentes empaquetados, parecidos a un Kit.
**Qué no cubre.** Encierro, Origen, Turnos y Supervisor.

### oterm
**Qué es.** «The terminal client for Ollama, OpenAI, Anthropic, and any pydantic-ai-supported provider» (https://raw.githubusercontent.com/ggozad/oterm/main/README.md). Última release: 0.26.1, del 2026-10-21 (https://api.github.com/repos/ggozad/oterm/releases/latest). No he podido comprobar en fuente primaria si tiene varias sesiones persistentes en SQLite, ni el detalle de su soporte MCP (sin verificar; la documentación enlaza una sección «Model Context Protocol»: https://ggozad.github.io/oterm/).
**Qué cubre.** Una UI de terminal para conversar, que es la parte de Ventana.
**Qué no cubre.** Todo lo de orquestación.

### llm (Simon Willison)
**Qué es.** Una CLI con conversaciones (`-c`, `--cid <id>`) y log en SQLite. Las tools se definen como funciones Python (`--functions`), y `--ta/--tools-approve` sirve para «approve each tool call interactively before it is executed» (https://llm.datasette.io/en/stable/usage.html). Sobre seguridad, avisa de la «lethal trifecta» y de la inyección de prompt (https://llm.datasette.io/en/stable/tools.html). Última release: 0.36, del 2026-09-22 (https://api.github.com/repos/simonw/llm/releases/latest).
**Qué cubre.** Una Confirmación humana por cada llamada a una tool. Un historial que permite continuar una conversación, que es una forma pobre de Rewind.
**Qué no cubre.** La documentación de tools no menciona sandbox, así que no hay Encierro. Tampoco Origen ni Turnos.

### AIOS (arXiv 2403.16971)
**Qué es.** Un «LLM Agent Operating System». La v5 es del 12 de agosto de 2025 y fue aceptado en COLM 2025 (https://arxiv.org/abs/2403.16971, https://github.com/agiresearch/AIOS). Según la sección 3 del PDF v5 (https://arxiv.org/pdf/2403.16971v5):
- **§3.2.** Cada LLM es un «core», como un núcleo de CPU, detrás de syscalls unificadas.
- **§3.3.** «We centralize all queues within the scheduler module», con FIFO y Round Robin. Round Robin funciona gracias a la interrupción de contexto.
- **§3.4.** El gestor de contexto hace un snapshot de la generación suspendida y la restaura; puede guardar texto o logits.
- **§3.5.** La memoria pasa de RAM a disco con LRU-K al llegar al 80 % del bloque.
- **§3.7.** Conflictos de tools: un hashmap cuenta las instancias de cada tool y, si hay conflicto, se pasa a la siguiente petición de la cola.
- **§3.8.** Grupos de privilegio por agente, y «user intervention interface» antes de operaciones irreversibles (borrar, sobrescribir, cambiar privilegios).

**Qué cubre.** Es lo más cercano a Main: un planificador central de llamadas al LLM con colas por módulo. También se acerca a la Confirmación humana y a una autoridad por grupo.
**Qué no cubre.** No hay Origen por eslabón ni regla del eslabón más débil, ni reserva de tokens de salida, ni lista de espera entre varios Mains. Su gestor de acceso es un hashmap, no un Encierro del sistema operativo con Landlock o seccomp. No hay Supervisor del servidor de inferencia ni Ventanas.

### MemGPT / Letta
**Qué es.** «MemGPT: Towards LLMs as Operating Systems»; la v2 es del 12 de febrero de 2024 (https://arxiv.org/abs/2310.08560). Según el PDF v2 (https://arxiv.org/pdf/2310.08560v2):
- El contexto se divide en instrucciones de sistema, contexto de trabajo y una cola FIFO cuyo primer elemento es un «recursive summary».
- Cuando el contexto llega al 70 %, el gestor de cola inserta un aviso de «memory pressure». Al 100 % expulsa unos mensajes (por ejemplo, la mitad del contexto) y genera un nuevo resumen recursivo. Los mensajes expulsados «are stored indefinitely in recall storage».
- Las inferencias se disparan con eventos: mensajes del usuario o del sistema, interacciones y eventos programados. `request heartbeat` encadena llamadas a funciones (§2.2–2.4).

El desarrollo activo de Letta se ha trasladado a `letta-ai/letta-code`, con subagentes en segundo plano, modos de permisos y `letta backend local` (https://github.com/letta-ai/letta, https://github.com/letta-ai/letta-code). La función «Dreaming» usa subagentes en segundo plano para consolidar memoria, y se dispara tras N pasos o cuando el contexto se compacta (https://docs.letta.com/guides/agents/architectures/sleeptime). No he visto la versión de Letta (sin verificar).
**Qué cubre.** Una compactación con resumen archivado y sin truncar en silencio, muy parecida a la de AllIn. Eventos programados, parecidos a Suscripciones o a trabajar en segundo plano.
**Qué no cubre.** Planificación de slots en la GPU, Supervisor, Origen y Encierro del sistema operativo.

### claude-squad
**Qué es.** Una TUI (`cs`) que lanza Claude Code, Codex, Gemini o Aider, cada uno en su sesión de tmux y su git worktree. Tiene `-y/--autoyes` para aceptar automáticamente y tareas en segundo plano con revisión antes de aplicar los cambios (https://github.com/smtg-ai/claude-squad). Última release: v1.0.20, del 2026-08-20 (https://api.github.com/repos/smtg-ai/claude-squad/releases/latest).
**Qué cubre.** Varias Ventanas o Sesiones, segundo plano, una Carpeta de trabajo por Sesión (el worktree) y algo parecido al Organizador.
**Qué no cubre.** Cada agente usa su propio LLM, normalmente en la nube. No hay un LLM local compartido, ni Turnos, ni Main, ni Encierro; `autoyes` es justo lo contrario de una Aprobación.

### WaitAgent (y otros gestores basados en tmux)
**Qué es.** Un espacio de trabajo de terminal en Rust/ratatui, en alfa pública, con un «Main Slot»: «Only the active main-slot target receives normal keystrokes». En una barra lateral muestra el estado de cada sesión: `I` espera entrada, `R` ejecutando, `C` espera confirmación, `U` desconocido. Puede abrir sesiones remotas por gRPC/SSH (https://github.com/kikakkz/wait-agent). No he visto la versión (sin verificar). Otros proyectos parecidos solo aparecieron en un agregador, una fuente secundaria: agent-session-manager, TUICommander y Sequence-LLM (https://awesome.ecosyste.ms/projects/github.com%2Fkikakkz%2Fwait-agent, https://pypi.org/project/sequence-llm/); su contenido está sin verificar.
**Qué cubre.** El Organizador de Ventanas y un estado «espera confirmación» por Sesión.
**Qué no cubre.** No planifica el LLM.

### Olla
**Qué es.** Un proxy y equilibrador de carga delante de Ollama, llama.cpp, LM Studio y otros, con «Priority-based routing with automatic failover», comprobaciones de salud con circuit breakers y un catálogo de modelos unificado por proveedor (https://github.com/thushan/olla). Versión sin verificar.
**Qué cubre.** Vigilancia de salud de backends, que es una parte del Supervisor.
**Qué no cubre.** El README no dice si encola peticiones (sin verificar). Sin Sesiones y sin Origen.

### vLLM (referencia de planificación con prioridad)
`--scheduling-policy`: `fcfs` por defecto. Con `priority`, «requests are handled based on given priority (lower value means earlier handling) and time of arrival deciding any ties» (https://docs.vllm.ai/en/latest/configuration/engine_args/).
**Qué cubre.** Prioridad por petición en el motor, que podría servir para priorizar por Origen o por Sesión.
**Qué no cubre.** No es llama.cpp, y la prioridad no reserva capacidad.

### AutoGen, CrewAI y LangGraph (solo planificación y delegación)
- **AutoGen.** «Project archived and no longer maintained»; recomiendan Microsoft Agent Framework. Ofrece chats en grupo y `AgentTool`, que envuelve a especialistas como tools de un agente principal (https://github.com/microsoft/autogen).
- **CrewAI.**
  - El proceso jerárquico necesita `manager_llm` o `manager_agent`, que hace «planning, delegation, and validation», y «Tasks are not pre-assigned» (https://docs.crewai.com/en/concepts/processes).
  - `allow_delegation` vale False por defecto, `max_rpm` limita las peticiones por minuto y `max_iter` vale 20.
  - `code_execution_mode` puede ser «safe» (Docker) o «unsafe», y está obsoleto en favor de sandboxes externos (https://docs.crewai.com/en/concepts/agents).
- **LangGraph.**
  - `interrupt()` con un checkpointer y un `thread_id` permite poner la interrupción dentro de una tool para «human review and editing of the tool call before it is executed», y se reanuda con `Command(resume=...)` (https://docs.langchain.com/oss/python/langgraph/interrupts).
  - «Replay» y «Fork» vuelven a ejecutar o ramifican desde un checkpoint anterior (https://docs.langchain.com/oss/python/langgraph/use-time-travel).

**Qué cubren.** Delegación tipo manager y encargos, parecidos a las Command sessions. Confirmación humana a nivel de tool. Rewind y bifurcación con los checkpoints de LangGraph. Un límite de RPM como control de carga muy tosco.
**Qué no cubren.** Ninguno propaga el Origen por la cadena ni aplica el eslabón más débil. Ninguno encierra las tools a nivel del sistema operativo según un Alcance declarado. Ninguno planifica slots de GPU ni supervisa el servidor.

### Conclusión parcial
- **Ya existen fuera, sueltas:**
  - Slots, cola diferida y caché KV por slot (llama.cpp).
  - Dormir y despertar el modelo, y modo router con descarga LRU (llama.cpp, Ollama, LM Studio, llama-swap).
  - Watchdog de backends colgados (LocalAI) y salud con circuit breakers (Olla).
  - Cola FIFO con prioridad por modelo (llama-swap) o por petición (vLLM).
- **Como idea académica:** un planificador central de llamadas con snapshot de contexto (AIOS) y compactación con resumen recursivo archivado (MemGPT).
- **También existen:** Confirmación humana por tool (llm `--ta`, LangGraph `interrupt`, AIOS §3.8), Rewind y fork (LangGraph), varias Ventanas con worktree por Sesión (claude-squad, WaitAgent) y Perfiles con RAG por usuario o workspace (Open WebUI, AnythingLLM).
- **No lo encontré en ningún sitio:**
  - Un Main único que reparta los slots de una GPU entre varias Sesiones de terminal con admisión a un pool, reserva de tokens de salida y lista de espera compartida entre varios Mains.
  - Un Supervisor por Enlace de Sesión que, además de dormir y despertar, relance el servidor si muere (ninguno lo documenta de forma explícita).
  - Autoridad por Origen propagada por la cadena con la regla del eslabón más débil.
  - Aprobación por hash de código ajeno con cuarentena.
  - Encierro de cada llamada a una tool en un proceso spawn con Landlock y seccomp según un Alcance declarado. Lo más cercano son los contenedores por usuario de Open WebUI y el Docker de CrewAI.
  - Procesos adheridos que exponen métodos al LLM y al humano a la vez.
- **Conclusión:** la combinación de varias Ventanas, un solo LLM local compartido y una cola de Turnos con autoridad, que es el núcleo de AllIn, no aparece en ningún proyecto revisado.

## Parte 2 · Autoridad, encierro, Procesos adheridos y memoria

### OpenAI Codex CLI (Encierro)

**Qué es.** Agente de programación de terminal de OpenAI, escrito en Rust. La última versión estable es la 0.166.0, del 29-10-2026; la 0.167.0-alpha.3 salió el 1-11-2026 (https://github.com/openai/codex/releases).

**Corrección a la premisa del encargo.** En Linux, Codex ya no se encierra con Landlock. Bubblewrap (bwrap) es la capa por defecto para el sistema de archivos y los namespaces. Encima de eso aplica `PR_SET_NO_NEW_PRIVS` y un filtro seccomp dentro del proceso. Landlock queda solo como implementación «legacy» y se rechaza con las políticas que restringen el sistema de archivos, porque «cannot isolate app-server Unix sockets» (https://raw.githubusercontent.com/openai/codex/main/codex-rs/linux-sandbox/README.md).

**Qué cubre de AllIn.**
- **Encierro del sistema de archivos.** La raíz se monta de solo lectura (`--ro-bind / /`) y las raíces escribibles se añaden con `--bind`. Dentro de ellas, `.git`, el `gitdir:` resuelto y `.codex` se vuelven a poner de solo lectura. Los symlinks y las rutas protegidas que no existen se tapan montando `/dev/null` (mismo README).
- **Namespaces.** Siempre usa `--unshare-user`, `--unshare-pid` por defecto y `--unshare-net` cuando la red está restringida (mismo README).
- **seccomp.** Bloquea `io_uring` en todos los modos y `AF_VSOCK`. Los sockets `AF_UNIX` nuevos se bloquean salvo que se conceda `dangerously_allow_all_unix_sockets`. En modo proxy gestionado, la red solo llega a los endpoints del proxy mediante un puente TCP→UDS→TCP (mismo README).
- **Carpeta de trabajo por Sesión.** El modo `workspace-write` permite leer y editar dentro del workspace. Los otros modos son `read-only` y `danger-full-access` (https://learn.chatgpt.com/docs/sandboxing).
- **Confirmación.** Las políticas de aprobación son `on-request` y `never`; `untrusted` ya no se puede elegir. `approvals_reviewer` puede valer `auto_review`, un agente revisor, que «doesn't change the sandbox boundary» (misma URL).
- **Encierro por proceso lanzado.** Según la documentación, «The sandbox applies to spawned commands, not just to built-in file operations» (misma URL).

**Qué no cubre.**
- No hay Alcance declarado por tool: la política es por sesión o por modo.
- No hay Origen: no se distingue si un comando lo pidió un humano o el LLM.
- No hay Aprobación por hash. La «confianza de proyecto», que deja fuera `.codex/` y sus servidores MCP si el proyecto no es de confianza, solo la encontré en blogs de terceros: **sin verificar** en fuentes oficiales (secundario: https://codex.danielvaughan.com/2026/08/17/pre-prompt-code-execution-attack-surface-codex-cli-project-trust-mcp-server-hooks-scanning-defence/).
- Si los servidores MCP corren dentro del sandbox: **sin verificar**. La página de sandboxing no lo dice.

**Fuentes.**
- https://raw.githubusercontent.com/openai/codex/main/codex-rs/linux-sandbox/README.md
- https://learn.chatgpt.com/docs/sandboxing
- https://github.com/openai/codex/releases

### Claude Code y sandbox-runtime (Encierro)

**Qué es.** CLI de Anthropic. En Linux encierra Bash con bubblewrap, un namespace de red sin salida y un proxy en el host que aplica una lista de dominios permitidos. Viene desactivado por defecto (https://code.claude.com/docs/en/sandboxing). La base es `@anthropic-ai/sandbox-runtime` (`srt`), con licencia Apache-2.0 y versiones 0.0.7x (https://github.com/anthropics/sandbox-runtime).

**Qué cubre de AllIn.**
- **Encierro del sistema de archivos y de la red.**
  - Por defecto se escribe en el directorio de trabajo, en los directorios añadidos con `--add-dir` y en el temporal del usuario.
  - Se lee todo el disco, salvo lo denegado. La documentación advierte: «This default still allows reading credential files».
  - Las escrituras a `.bashrc`, `.gitconfig`, `.git/hooks` y la configuración de `.claude` se deniegan aunque estén dentro de un directorio escribible.
  - seccomp es opcional y solo bloquea sockets Unix.
  - (https://code.claude.com/docs/en/sandboxing)
- **Proceso adherido encerrado.** `srt` puede envolver un servidor MCP stdio poniendo el comando detrás de `srt` en `.mcp.json`. El proxy se niega a conectar a loopback, link-local y direcciones de metadatos (https://github.com/anthropics/sandbox-runtime).
- **Aprobación por hash.**
  - Las fuentes de plugins `github`, `url` y `git-subdir` se pueden fijar a un `sha` de commit de 40 caracteres.
  - La fuente `archive` acepta `sha256`, y Claude Code «refuses a download that doesn't match».
  - Las fuentes `npm` se descargan sin ejecutar scripts de instalación.
  - (https://code.claude.com/docs/en/plugins/marketplace-reference.md)
- **Acciones solo humanas.** Los servidores de un `.mcp.json` de proyecto piden aprobación en sesiones interactivas. Además, «A cloned repository can't approve its own servers»: `enableAllProjectMcpServers` se ignora en una carpeta que no es de confianza (https://code.claude.com/docs/en/mcp).
- **Roots de MCP.** Claude Code responde a `roots/list` con el directorio de arranque más los directorios añadidos (misma URL).

**Qué no cubre.**
- No hay Encierro por tool ni por llamada con Alcance propio.
- Los hooks, los servidores MCP locales y LSP corren «with your full access».
- Existe una vía de escape, `dangerouslyDisableSandbox`, que se puede desactivar con `allowUnsandboxedCommands: false`.
- Si falta bwrap, por defecto los comandos corren sin encierro, salvo con `failIfUnavailable` (https://code.claude.com/docs/en/sandboxing).
- La aprobación de MCP de proyecto no es por hash. Si un cambio en la definición obliga a reaprobar: **sin verificar**.
- No hay Origen humano/LLM en la autoridad.

**Fuentes.**
- https://code.claude.com/docs/en/sandboxing
- https://github.com/anthropics/sandbox-runtime
- https://code.claude.com/docs/en/mcp
- https://code.claude.com/docs/en/plugins/marketplace-reference.md

### Gemini CLI (Encierro)

**Qué es.** CLI de Google. Desde el 18-06-2026, las cuentas personales y los planes AI Pro/Ultra pasaron a «Antigravity CLI» (`agy`). Gemini CLI queda para Code Assist Standard/Enterprise (https://github.com/google-gemini/gemini-cli).

**Qué cubre de AllIn.**
- **Encierro por llamada de tool.** El «tool sandboxing» viene activado por defecto y da «granular isolation for individual tool executions», por ejemplo `shell_exec` y `write_file`, en vez de encerrar todo el CLI (https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/sandbox.md).
- **Mecanismo en Linux, según el código.** Usa bwrap con un filtro seccomp BPF que se pasa por un descriptor de archivo. Se prepara un fichero de argumentos de bwrap por cada petición. El filtro solo bloquea `ptrace` (EPERM) y mata el proceso si la arquitectura no coincide (https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/sandbox/linux/LinuxSandboxManager.ts).
- **Otros métodos.** Docker/Podman, gVisor (runsc) y LXC (https://geminicli.com/docs/cli/sandbox/).
- **Confirmación por comando.** La «sandbox expansion» pide permisos extra solo para esa ejecución (misma URL).
- **Carpetas de confianza.** En una carpeta que no es de confianza, `.gemini/settings.json` se ignora, «MCP servers do not connect» y no se pueden instalar extensiones. La decisión se pregunta una sola vez por carpeta (https://geminicli.com/docs/cli/trusted-folders/).

**Qué no cubre.**
- No usa Landlock.
- No hay Alcance declarado por la propia tool.
- No hay Origen.
- La confianza de carpeta no va por hash: no encontré que un cambio obligue a reaprobar.

**Fuentes.**
- https://github.com/google-gemini/gemini-cli
- https://geminicli.com/docs/cli/sandbox/
- https://geminicli.com/docs/cli/trusted-folders/
- https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/sandbox/linux/LinuxSandboxManager.ts

### nono (Encierro con Landlock, por llamada; Aprobación firmada)

**Qué es.** CLI y SDK de encierro «kernel-enforced» para agentes y servidores MCP, con licencia Apache-2.0 (https://github.com/always-further/nono, que muestra el repo nolabs-ai/nono). Versión: **sin verificar**, porque la página no la muestra.

**Qué cubre de AllIn.** Es lo más parecido al Encierro de AllIn que encontré.
- **Landlock.** Detecta el ABI más alto disponible:
  - v1 (5.13): sistema de archivos.
  - v4 (6.7): `bind`/`connect` TCP.
  - v6 (6.12): señales y sockets UNIX abstractos.
  - Las reglas se atan al inodo, así que una sustitución atómica deja la concesión «stale».
  - «DNS resolution (UDP) is not blocked by Landlock».
  - seccomp se usa en modo proxy y con user-notify para mediar `connect`/`bind` de `AF_UNIX`.
  - (https://nono.sh/docs/cli/internals/landlock.md)
- **Encierro nuevo por llamada.** El supervisor de nono «launches each tool into a fresh command sandbox» con la política de esa invocación. Ese sandbox no hereda las concesiones de la sesión. Cada comando tiene su política en `command_policies.commands.<name>` (https://nono.sh/docs/cli/features/tool-sandbox.md). Esto equivale a spawn por llamada con Alcance por tool.
- **Política según quién llama.** Una tool invocada por otra usa `from.<caller>.sandbox`. Encadenar requiere a la vez `can_use` en el llamante y `from.<caller>` en el llamado; si falta el eslabón, «does not fall back» (misma URL). Es una forma de Origen por proceso, aunque no distingue humano de LLM.
- **Aprobación de código ajeno.**
  - Calcula SHA-256 de los archivos que cubre la política (skills, `CLAUDE*`, `*.py`…) y los compara con una lista de bloqueo.
  - Verifica una firma Sigstore y la identidad de quien firmó.
  - Modos `deny`, `warn` y `audit`.
  - En Linux, en modo supervisado, verifica cada `openat()` con seccomp-notify, también los archivos que aparecen a mitad de sesión.
  - (https://nono.sh/docs/cli/features/trust.md)

**Qué no cubre.**
- Distinguir Origen humano frente a LLM.
- Confirmación humana por acción.
- Cuarentena por hash aprobada localmente sin firma: usa firmas, no aprobaciones por hash.
- Turnos, Sesiones y Procesos adheridos.
- Ojo con el nombre: nono también usa «Supervisor», pero para el proceso padre de confianza, no para un servidor LLM.

**Fuentes.**
- https://github.com/always-further/nono
- https://nono.sh/docs/llms.txt
- https://nono.sh/docs/cli/internals/landlock.md
- https://nono.sh/docs/cli/features/tool-sandbox.md
- https://nono.sh/docs/cli/features/trust.md

### Open Interpreter

**Qué es.** Hoy es «A coding agent optimized for low-cost models», una reescritura en Rust basada en Codex. El proyecto Python original sigue como fork comunitario en endolith/open-interpreter (https://github.com/openinterpreter/open-interpreter).

**Qué cubre de AllIn.**
- **Encierro.** En Linux usa «Bubblewrap, seccomp, and related kernel sandboxing where available» y falla cerrado si no puede aplicar la política. Tiene los modos `read-only`, `workspace-write` (red desactivada salvo que se active) y `danger-full-access`.
- **Confirmación.** Aprobaciones `untrusted`, `on-request` y `never`.
- (https://github.com/openinterpreter/open-interpreter/blob/main/docs/sandbox.md)

**Qué no cubre.** Origen, Alcance por tool, Aprobación por hash y Procesos adheridos. Versión y fecha: **sin verificar**.

**Fuentes.**
- https://github.com/openinterpreter/open-interpreter
- https://github.com/openinterpreter/open-interpreter/blob/main/docs/sandbox.md

### OpenHands

**Qué es.** Plataforma y SDK de agentes. El agent-server corre dentro de un contenedor Docker, que es el proveedor por defecto (https://docs.openhands.dev/openhands/usage/runtimes/docker).

**Qué cubre de AllIn.**
- **Encierro por contenedor y Carpeta de trabajo.** El workspace se monta en `/workspace`, y «Anything mounted read-write into `/workspace` can be modified by the agent» (misma URL). La documentación del SDK habla de «isolation between different agent executions» (https://docs.openhands.dev/sdk/guides/agent-server/overview).
- **Confirmación.**
  - Políticas `AlwaysConfirm`, `NeverConfirm` y `ConfirmRisky`.
  - Niveles de riesgo LOW, MEDIUM, HIGH y UNKNOWN, que pone un `LLMSecurityAnalyzer`.
  - Ojo: «`conversation.execute_tool()` bypasses the analyzer and confirmation policy».
  - (https://docs.openhands.dev/sdk/guides/security)

**Qué no cubre.**
- No hay Encierro por llamada: el contenedor dura toda la conversación.
- El riesgo lo estima un LLM: no hay Origen ni procedencia determinista.
- No hay Aprobación por hash.

**Fuentes.**
- https://docs.openhands.dev/openhands/usage/runtimes/docker
- https://docs.openhands.dev/sdk/guides/agent-server/overview
- https://docs.openhands.dev/sdk/guides/security

### goose

**Qué es.** Agente general (escritorio, CLI y API) con más de 70 extensiones MCP. Ahora vive en github.com/aaif-goose/goose, bajo la Agentic AI Foundation de la Linux Foundation (https://github.com/block/goose, que sirve aaif-goose/goose).

**Qué cubre de AllIn.**
- **Confirmación.** Modos Autonomous (el de defecto), Manual Approval, Smart Approval y Chat Only.
- Pide confirmación para las tools de «escritura», pero esa clasificación la hace el LLM «best-effort».
- (https://goose-docs.ai/docs/guides/managing-tools/goose-permissions)

**Qué no cubre.**
- Encierro en Linux: un sandbox solo para macOS con sandbox-exec aparece en resultados de búsqueda, pero la página dio 404. **Sin verificar**.
- Origen y Aprobación por hash: no encontrados.
- La página de «Extension Allowlist» no la leí.

**Fuentes.**
- https://github.com/block/goose
- https://goose-docs.ai/docs/guides/managing-tools/goose-permissions

### CaMeL: «Defeating Prompt Injections by Design» (Origen y procedencia)

**Qué es.** Paper de seguridad (arXiv:2503.18813). La v1 es del 24-03-2025 y la v2 del 24-06-2025, ya con enlace al código (https://arxiv.org/abs/2503.18813).

**Qué cubre de AllIn.**
- **Origen.**
  - El P-LLM genera Python a partir de la consulta del usuario y «only interacts with the user query».
  - El Q-LLM procesa los datos no confiables y no tiene tools.
  - Cada valor lleva capacidades: procedencia (User si es un literal del código del P-LLM, CaMeL si es una transformación, o el id de la tool con una fuente interna, como el remitente de un email) y lectores permitidos.
  - (https://arxiv.org/html/2503.18813v2, §5.1 y §5.3)
- **Eslabón más débil.** El intérprete lleva un grafo de dependencias. La política de `send_money` exige que destinatario e importe vengan del usuario, sin fuentes no confiables en el grafo (§5.4 y §6.2.2). Es la regla de eslabón más débil aplicada a datos.
- **Confirmación.** Una ejecución que viola la política no se bloquea, sino que pide confirmación al usuario (§5.4). Los autores admiten la fatiga que eso causa (§9.2).

**Qué no cubre.**
- Cadenas de delegación entre Sesiones o agentes.
- Encierro de procesos.
- Hay canales laterales: «CaMeL is vulnerable to side-channel attacks» (§7).
- Los autores lo dicen claro: «No, prompt injection attacks are not fully solved» (§9.3).

**Fuentes.**
- https://arxiv.org/abs/2503.18813
- https://arxiv.org/html/2503.18813v2

### Patrón Dual LLM (Simon Willison)

**Qué es.** El patrón original, publicado el 25-04-2023 en el blog de su autor. Es fuente primaria porque el término lo acuñó él (https://simonwillison.net/2023/Apr/25/dual-llm-pattern/).

**Qué cubre de AllIn.**
- El LLM privilegiado tiene tools y entrada del usuario.
- El LLM en cuarentena procesa contenido no confiable y no tiene tools.
- Un controlador, que no es un LLM, maneja variables simbólicas (`$VAR1`) para que el LLM privilegiado nunca vea el texto contaminado.
- Esto equivale a separar el Origen y a que Main sea código determinista, no LLM.

**Qué no cubre.** La aprobación humana no forma parte del patrón: el autor la considera condenada por la fatiga. No trata delegación, Encierro ni hash.

**Fuentes.** https://simonwillison.net/2023/Apr/25/dual-llm-pattern/

### FIDES: «Securing AI Agents with Information-Flow Control» (Microsoft)

**Qué es.** Paper de control de flujo de información (IFC) para planificadores de agentes (arXiv:2505.23643; v1 del 29-05-2025, v2 del 03-09-2025). Viene con un notebook de tutorial con licencia MIT (https://arxiv.org/abs/2505.23643, https://github.com/microsoft/fides).

**Qué cubre de AllIn.**
- **Origen como etiqueta de integridad.** El planificador «tracks confidentiality and integrity labels» y aplica políticas de forma determinista.
- Tiene primitivas para ocultar y revelar información al LLM de forma selectiva.
- Define un modelo formal de qué propiedades puede garantizar el taint-tracking dinámico.

**Qué no cubre.** Delegación entre agentes, Encierro y Procesos adheridos. El repo es solo un notebook, con 5 commits.

**Fuentes.**
- https://arxiv.org/abs/2505.23643
- https://github.com/microsoft/fides

### Progent (privilegio mínimo para tools)

**Qué es.** Paper (arXiv:2504.11703; v3 del 14-05-2026).

**Qué cubre de AllIn.**
- **Alcance por tool.** La política son reglas simbólicas sobre nombres de tool y argumentos, y se comprueba de forma determinista en cada llamada.
- La política inicial la genera un LLM a partir de la tarea.
- **Confirmación.** Un solver SMT clasifica cada actualización: si estrecha la política se aplica sola; si la amplía, requiere aprobación.
- Esto se parece a que la autoridad solo baje sin un humano.
- (https://arxiv.org/abs/2504.11703v3; los detalles vienen del resumen del abstract, no del texto completo)

**Qué no cubre.** Origen humano frente a LLM en cadenas, Encierro de SO y hash.

**Fuentes.** https://arxiv.org/abs/2504.11703v3

### HDP, CAPMAS, SentinelAgent y «The Chain Is Signed…» (Origen en delegaciones)

**HDP (Human Delegation Provenance).** arXiv:2604.04522, del 06-04-2026, más el borrador IETF `draft-helixar-hdp-agentic-delegation-00`. Un token ata un evento de autorización humana a una sesión, y cada delegación entre agentes queda como un salto firmado (Ed25519) en una cadena de solo-añadir que se verifica offline.
- Es lo más cercano a «Origen humano frente a LLM en delegaciones» que encontré.
- Si el alcance se estrecha en cada salto: **sin verificar**, porque solo leí el abstract.
- (https://arxiv.org/abs/2604.04522)

**CAPMAS.** arXiv:2609.06500, del 06-09-2026. Usa tokens Macaroon cuyos privilegios solo pueden estrecharse en cada traspaso, con un alcance semántico inferido de la petición. Es un eslabón más débil por atenuación (https://arxiv.org/abs/2609.06500).

**SentinelAgent.** arXiv:2604.02767, del 03-04-2026. Propone un cálculo de cadenas de delegación con siete propiedades. Un servicio de autoridad que no es un LLM las aplica en tiempo de ejecución, y lo verifican con TLA+. La verificación de intención cae al 13% frente a paráfrasis sofisticadas (https://arxiv.org/abs/2604.02767).

**«The Chain Is Signed, the Handoff Is Prose».** NeurIPS 2026. En cadenas sin instrucción explícita, la identidad del principal sobrevive solo el 35,8% de las veces cuando la delegación se escribe en prosa. Es un argumento a favor de que el Origen lo lleve Main fuera del texto (https://neurips.cc/virtual/2026/168175, https://openreview.net/forum?id=jo8LG8LlaI).

**Qué no cubre ninguno.** Encierro, Turnos ni Procesos adheridos. Son propuestas académicas sin un sistema de usuario final.

### A2A (Agent2Agent)

**Qué es.** Protocolo entre agentes opacos; la última versión publicada es la 1.0.0 (https://a2a-protocol.org/latest/specification/).

**Qué cubre de AllIn.**
- **Command sessions entre agentes, en parte.**
  - La autenticación se declara en `AgentCard.securitySchemes` (§3.1.11).
  - El servidor autoriza «based on the authenticated identity and its own policies» (§7.5).
  - La Agent Card se puede firmar, y los clientes «SHOULD verify at least one signature» (§8.4).
- **Delegación de autorización al cliente** (§7.6), con la cautela de que `TASK_STATE_AUTH_REQUIRED` por sí solo no es autorización (§7.6.4).

**Qué no cubre.**
- No distingue humano de agente: `ROLE_USER` solo indica la dirección cliente→servidor (§4.1.5), y un cliente es «An application or agent» (§2.2).
- No hay eslabón más débil ni procedencia por mensaje.
- La identidad se delega a la capa de transporte (§7).

**Fuentes.** https://a2a-protocol.org/latest/specification/

### MCP (Model Context Protocol), comparado con los Procesos adheridos

**Qué es.** Protocolo JSON-RPC entre host/cliente y servidores. La revisión vigente es la **2026-07-28**, que ya no tiene handshake: cada petición lleva en `_meta` su versión y sus capacidades (https://modelcontextprotocol.io/specification/latest, https://modelcontextprotocol.io/specification/latest/basic/lifecycle).

**Qué cubre de AllIn.**
- **Ciclo de vida de un proceso de larga vida.**
  - En stdio, el cliente lanza el servidor como subproceso.
  - Para apagarlo cierra stdin, espera y escala de SIGTERM a SIGKILL.
  - Si el servidor muere de forma inesperada, el cliente «SHOULD restart it», y como el protocolo es sin estado, las peticiones en curso se pierden y se reintentan.
  - (https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)
  - Esto se parece a un Supervisor para el Proceso adherido.
- **Suscripciones a eventos.** Mediante `subscriptions/listen` (misma URL).
- **Tareas largas.** La extensión Tasks permite operaciones asíncronas largas (https://modelcontextprotocol.io/specification/latest).
- **Confirmación.** Principios de la especificación:
  - «Hosts must obtain explicit user consent before invoking any tool».
  - Las anotaciones de tools son «untrusted, unless obtained from a trusted server».
  - (https://modelcontextprotocol.io/specification/latest)
- **Elicitation.** Sigue vigente. El modo form no puede pedir secretos; el modo URL saca la interacción fuera de banda. El cliente debe mostrar qué servidor pregunta y ofrecer rechazar o cancelar (https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation). Es una vía solo humana.
- **Sampling.** **Deprecado** en la 2026-07-28 (SEP-2577). Mientras exista, «SHOULD always be a human in the loop» (https://modelcontextprotocol.io/specification/2026-07-28/client/sampling).
- **Roots.** **Deprecado** también. Ya eran «informational guidance rather than an access-control mechanism» (https://modelcontextprotocol.io/specification/2026-07-28/client/roots).
- **Encierro.** Para servidores locales, la guía de seguridad dice que el cliente «SHOULD» ejecutarlos «in a sandboxed environment with minimal default privileges». Si hay configuración en un clic, el cliente «MUST» mostrar el comando exacto antes de lanzarlo (https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices).

**Qué no cubre.**
- El servidor MCP no está atado a una Sesión: lo está al host.
- Las tools son «for the AI model»: no está pensado para que un humano llame los mismos métodos con su propio Origen.
- La 2026-07-28 es sin estado a propósito. El estado se guarda en handles explícitos, y «MUST NOT treat possession of a state handle as authentication» (misma guía). Es lo contrario de un Proceso adherido con estado vivo.
- No hay Origen por llamada ni Aprobación por hash del binario del servidor.

**Fuentes.** Las URLs de modelcontextprotocol.io citadas arriba.

### Kernels de Jupyter usados por agentes

**Qué es.** El protocolo de mensajería de Jupyter va por la versión 5.5 (https://jupyter-client.readthedocs.io/en/latest/messaging.html).

**Qué cubre de AllIn.**
- **Proceso adherido con estado.**
  - Un kernel atiende a varios frontends por el canal shell, y IOPub difunde la salida a todos. Así, un humano y un agente pueden llamar al mismo proceso.
  - El canal control (apagado, reinicio, debug) va aparte para no esperar en la cola de ejecución.
  - Hay un heartbeat.
  - `parent_header` permite saber qué petición causó cada mensaje, y al reiniciar cambia el id de sesión.
  - Los mensajes se firman con HMAC-SHA256.
  - (misma URL)
- **Uso por agentes.** jupyter-mcp-server (Datalayer) expone notebooks y kernels como tools MCP (https://github.com/datalayer/jupyter-mcp-server).

**Qué no cubre.**
- Origen: la firma HMAC autentica la conexión, no quién la origina.
- Encierro del kernel.
- Aprobación de código.

**Fuentes.**
- https://jupyter-client.readthedocs.io/en/latest/messaging.html
- https://github.com/datalayer/jupyter-mcp-server

### Letta (antes MemGPT)

**Qué es.** Plataforma de agentes con estado. El código actual está en `letta-ai/letta-code`; el servidor de la API V1 está retirado en la rama `archive` (https://github.com/letta-ai/letta).

**Qué cubre de AllIn.**
- **Memoria por agente.** Bloques de memoria editables por el propio agente con tools, y bloques compartidos entre agentes (https://docs.letta.com/guides/agents/memory).
- **Memoria archivada.** Una base vectorial con `archival_memory_insert` y `archival_memory_search` (https://docs.letta.com/guides/agents/archival-memory).
- **Mensajes compactados.** Los mensajes antiguos siguen recuperables tras la compactación, lo que se parece al resumen archivado de AllIn (https://docs.letta.com/guides/agents/memory).

**Qué no cubre.**
- No es RAG por Perfil encerrado.
- Reranker local y uso con llama.cpp: **sin verificar**.

**Fuentes.**
- https://github.com/letta-ai/letta
- https://docs.letta.com/guides/agents/memory
- https://docs.letta.com/guides/agents/archival-memory

### mem0

**Qué es.** Capa de memoria; el algoritmo nuevo es de abril de 2026. Se usa como librería (`pip install mem0ai`) o como servidor propio en Docker (https://github.com/mem0ai/mem0, https://docs.mem0.ai/open-source/overview).

**Qué cubre de AllIn.**
- **Memoria por usuario.** Delimitada por `user_id`.
- **Reranker local.** Admite Sentence Transformers («on-device cross-encoder models») y Hugging Face self-hosted, además de Cohere, LLM y Zero Entropy (https://docs.mem0.ai/components/rerankers/overview).
- **LLM local.** Funciona con Ollama (https://docs.mem0.ai/components/llms/models/ollama).

**Qué no cubre.**
- Por defecto usa embedder y LLM de OpenAI. Una configuración 100% local: **sin verificar**, porque el ejemplo de Ollama sigue usando la clave de OpenAI para el embedder.
- No hay Encierro ni Supervisor.

**Fuentes.**
- https://github.com/mem0ai/mem0
- https://docs.mem0.ai/open-source/overview
- https://docs.mem0.ai/components/rerankers/overview
- https://docs.mem0.ai/components/llms/models/ollama

### AnythingLLM (workspaces)

**Qué es.** Aplicación de RAG y agentes. La versión referenciada es la v1.11.2, sin fecha visible. Usa LanceDB y un embedder nativo por defecto, y tiene multiusuario solo en Docker (https://github.com/Mintplex-Labs/anything-llm).

**Qué cubre de AllIn.**
- **RAG con ámbito delimitado.** «The LLM can only "see" documents embedded in this workspace».
- Umbral de similitud ajustable.
- (https://docs.anythingllm.com/llm-not-using-my-docs)

**Qué no cubre.**
- Reranker local: **sin verificar**. Hay un issue que pide sección de reranking por workspace (https://github.com/Mintplex-Labs/anything-llm/issues/4012).
- No hay Encierro del RAG.

**Fuentes.**
- https://github.com/Mintplex-Labs/anything-llm
- https://docs.anythingllm.com/llm-not-using-my-docs

### Open WebUI (knowledge)

**Qué es.** Interfaz web para LLM. La última versión es la v0.12.5, del 29 de octubre (el año no aparece en la página) (https://github.com/open-webui/open-webui/releases).

**Qué cubre de AllIn.**
- **RAG por usuario o grupo.**
  - Control de acceso por knowledge base.
  - Al adjuntar una KB a un modelo, «scopes the model to only those documents».
  - Con function calling nativo, el modelo tiene que llamar a las tools de knowledge.
  - (https://docs.openwebui.com/features/workspace/knowledge)
- **Búsqueda híbrida.** BM25 más vectores más reranking, con `ENABLE_RAG_HYBRID_SEARCH` (misma URL).
- **Lo que dice el código.**
  - El embedder por defecto es `sentence-transformers/all-MiniLM-L6-v2`.
  - Existen `RAG_RERANKING_ENGINE` (con valor `'external'` en la UI) y `RAG_EXTERNAL_RERANKER_URL`.
  - `RAG_RERANKING_MODEL_TRUST_REMOTE_CODE` vale True por defecto.
  - (https://raw.githubusercontent.com/open-webui/open-webui/main/backend/open_webui/config.py)

**Qué no cubre.** No hay Encierro ni Supervisor del RAG, y no hay Origen.

**Fuentes.**
- https://docs.openwebui.com/features/workspace/knowledge
- https://raw.githubusercontent.com/open-webui/open-webui/main/backend/open_webui/config.py
- https://github.com/open-webui/open-webui/releases

### Aprobación y cuarentena por hash

**direnv.** La aprobación vale `sha256(ruta_absoluta + "\n" + contenido)`. Si se edita `.envrc`, el hash cambia y el archivo vuelve a «NotAllowed» hasta que se aprueba otra vez. También hay un fichero de denegación por hash (https://raw.githubusercontent.com/direnv/direnv/master/internal/cmd/rc.go). Es el análogo más limpio de la Aprobación por hash de AllIn, aunque fuera del mundo de agentes.

**pip.** `--require-hashes` exige hash en todas las dependencias y rechaza md5, sha1 y sha224 (https://pip.pypa.io/en/stable/topics/secure-installs/).

**Claude Code.** Plugins fijados por `sha` de commit o `sha256` de archivo, como se describió arriba.

**nono.** SHA-256, lista de bloqueo y firma Sigstore, con verificación al abrir cada archivo, como se describió arriba.

**mcp-scan, hoy Snyk Agent Scan.** Que fije las tools por hash: **sin verificar**. El README actual no lo dice (https://github.com/invariantlabs-ai/mcp-scan). La idea de «rug pull detectado por hash de la descripción» solo aparece en blogs (secundario).

### Conclusión parcial

**Piezas de AllIn que ya existen fuera.**
- **Encierro del SO por proceso.** Lo tienen Codex, Claude Code/srt, Gemini CLI y Open Interpreter, pero los cuatro usan bubblewrap + seccomp, no Landlock (en Codex, Landlock quedó como legacy).
- **Spawn por llamada con Alcance propio por tool y política según el llamante.** Existe en nono, con Landlock. Es lo más parecido al Encierro y al @reach de AllIn.
- **Procedencia y eslabón más débil sobre datos.** Existe en CaMeL y FIDES.
- **Atenuación en delegaciones y Origen humano firmado por salto.** Existe en papers de 2026 (CAPMAS, HDP, SentinelAgent), sin un producto que lo use.
- **Ciclo de vida de procesos de larga vida.** MCP stdio y los kernels de Jupyter: reinicio, cierre por stdin y SIGTERM, canal de control.
- **Aprobación por hash.** direnv, pip, fuentes de plugins de Claude Code y nono (firmas).
- **RAG por usuario con reranker local.** mem0 (Sentence Transformers) y Open WebUI.

**Lo que no encontré en ningún sitio.**
- Un sistema que lleve el Origen (humano, LLM, otra Sesión, proceso) en cada Prompt o comando y aplique el eslabón más débil a las órdenes, no solo a los datos.
- Acciones solo humanas definidas por Origen y no por modo de permisos.
- Procesos adheridos a una Sesión que llamen a la vez el humano y el LLM con autoridad distinta y que estén encerrados. MCP va en sentido contrario: sin estado, tools solo «for the AI model», y sampling y roots deprecados.
- Un RAG encerrado como proceso con Supervisores propios para embeddings y reranker.
- Un Main único que ordene Turnos sobre slots de GPU.
