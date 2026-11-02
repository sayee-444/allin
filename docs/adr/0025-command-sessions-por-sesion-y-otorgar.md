# Cada Sesión tiene su lista de Command sessions, cada function declara los que ejecuta, y solo un humano da Otorgar

`spec.md` pedía que los Command sessions se heredaran al crear una Sesión (`/new [-comsession='.py']`), por defecto los de la raíz de su carpeta y los de una subcarpeta solo si se nombran, para controlar qué tiene cada Ventana. Ningún ADR lo recogió: toda Sesión podía usarlos todos, y una function, todos los que tuviera quien la llamara. Además, los Command sessions son la única puerta de un proceso encerrado hacia Main (ADR-0016), así que una function que no los necesitaba los podía usar igual. Por ejemplo, un PDF preparado que tomara el RAG mandaría Prompts a otras Sesiones mientras el RAG atendía un `rag_buscar` de una Sesión con `@send`.

## La lista de cada Sesión

Cada Sesión tiene su lista de Command sessions, que se lleva como sus functions: se copia de la plantilla de Sesión en `/new`, un Molde la fija, un Clon la copia y Restablecer vuelve a la de su receta (ADR-0015). Por defecto trae todos los de la raíz de la carpeta de Command sessions; los de una subcarpeta solo entran si se nombran en `/new`. Si un Molde nombra uno que no existe en esta máquina, la Sesión nace sin él y sale un Aviso, como con el Supervisor (ADR-0022). Solo un humano la amplía, con los comandos de functions de F2, salvo con Otorgar (abajo), y una Sesión solo puede pasarles a las que crea lo que ella tiene.

La lista limita a su LLM, es decir, a sus fsources y a los métodos que llama, y no al humano: lo que un humano teclea con `@` en una Ventana lo podría hacer igual desde el Chat de Main, y bloquearlo ahí solo estorbaría. Como manda el eslabón más débil (ADR-0013), lo que llega por una cadena solo usa lo que está en la lista de todas sus Sesiones, así que una Sesión sin `@new` tampoco crea Sesiones mandándole un Prompt a otra que sí lo tiene.

## Lo que declara cada function

El Alcance de una function dice también qué Command sessions ejecuta (`@reach(commands=[…])`), como dice qué carpetas y puertos toca (ADR-0016). No lo fuerza el kernel sino Main, que rechaza cualquier otro por el canal de vuelta. La aprobación lo muestra, se puede reducir al aprobar, y cambiarlo devuelve la function a cuarentena. Si no declara ninguno, no ejecuta ninguno. Lo que ejecuta es lo que declara y además está en la lista de su cadena, y una function que importa el código de otra no gana nada, porque cada una solo ejecuta lo que declara ella. El RAG declara solo `@embed` y `@rerank`: aunque un documento lo tome, no puede hacer más.

Lo que Main pide por su cuenta a un Proceso adherido no viene de un humano ni de una Sesión: por ejemplo, pasarle al RAG cada Turno terminado, o ponerlo al día cuando lo adhiere un Ajuste (ADR-0019). Eso lleva el Origen de Main. No tiene lista, así que solo lo limita lo que declara la fproc, y como pasa por el proceso, nunca cuenta como humano (ADR-0021).

## Otorgar

Otorgar es el Command session que da Command sessions y functions a cualquier Sesión del Perfil, también a la propia, aunque quien lo usa no los tenga. Sirve para que un humano delegue en un LLM, por ejemplo en una coordinadora, el reparto de lo que puede hacer cada Sesión. La fsource que lo declara es la única que no se hereda: solo la da un humano, a una Sesión concreta, y una fproc que lo declare solo la adhiere un humano. No la traen `/new`, un Molde ni un Clon, y Otorgar no da Otorgar, así que el permiso no pasa de una Sesión a otra.

Su aprobación lo dice con esas palabras y nunca va en bloque, venga sola, en un package, en un Kit o con un Perfil copiado, como una pieza con `display` (ADR-0016). Cuando un humano se la da a una Sesión, sale un Aviso en su Ventana, y el borde lo marca mientras la tenga, como `--devastation`. Cada uso pide Confirmación, como Crear una pieza desde una Sesión (ADR-0013), y queda en el Log con la cadena de Origen entera; un `--devastation` que cubra todo lo que toca salta esa Confirmación, como las demás (ADR-0013). Otorgar no da `-y`, `--devastation` ni Exenciones, no aprueba piezas ni amplía encierros, que siguen siendo de un humano, y solo da lo que existe y está aprobado en el Perfil.

Se descartó que la lista limitara también al humano, y que siguieran valiendo todos los Command sessions para todas las Sesiones. Se descartó también que Otorgar tuviera solo un Aviso y el Log, sin Confirmación: el caso peligroso es un LLM engañado por algo que leyó en su Turno (el resultado de otra Sesión, un documento o el evento de un Proceso adherido), y el aviso llegaría cuando ya hubiera repartido.

## Enmienda (#108): cómo quedó en F2

Main trae dos Command sessions propios, `@send <sid> <texto>` y `@server [restart]`, que están siempre y van en la lista por defecto. Los módulos de `<data-dir>/comsession/` añaden los suyos con `@session_command(nombre, usage=…, summary=…)` de `allin.session_commands`: una función que recibe la llamada (la Sesión, lo que sigue al nombre y el Supervisor de su Enlace) y da exactamente un Command main. Uno que repite un nombre ya cargado no lo reemplaza. Un módulo que falla al cargar se cuenta al arrancar, como un Aviso roto (ADR-0016), y los demás se cargan igual.

`/new -cs=<nombres>` nombra los de una Sesión en vez de los de la raíz. Si alguno no existe en esta máquina, la Sesión nace sin él y sale un Aviso en el Chat de Main. Si la crea otra Sesión, solo recibe los que esa tiene. `/func add <sid> @nombre` amplía la lista y solo lo hace un humano; `/func remove <sid> @nombre` la reduce y puede hacerlo cualquiera.

El canal de vuelta es `allin.channel.command("@nombre …")`. Dentro del proceso de la tool escribe una línea en una tubería que solo lee su vigilante. El vigilante la pasa a Main como una petición aparte, nunca como el resultado de la llamada, y le devuelve la respuesta de Main: `{"result": "ok"}` o `{"error": …}`. Una línea que no es un Command session se contesta `malformed command` y no llega a Main. El tiempo que Main tarda en contestar no cuenta para el timeout de la llamada. Main comprueba primero lo que declara la function, luego la lista de cada Sesión de la cadena del Turno, y ejecuta el Command main con esa cadena, así que uno delicado pide Confirmación como si lo hubiera mandado la Sesión. `@server restart` contesta cuando el Servidor vuelve a estar `ready`, o con `failed` y el motivo.

Todavía no hay Moldes (#112), Clon ni Restablecer, así que lo que esta decisión dice de ellos se hará con ellos. Tampoco hay aún Procesos adheridos, así que nada lleva por ahora el Origen de Main.
