# Lo que el humano escribe durante un Turno entra en él, y Shift+Enter lo interrumpe

Hasta ahora la Ventana bloqueaba Enter mientras corría el Turno de su Sesión (`turn running: Enter is blocked`). Con las tools de F2 un Turno puede tardar minutos, y la única forma de corregirlo era cancelarlo cerrando la Ventana. Se toma de Claude Code: allí, un mensaje enviado mientras el agente trabaja le llega en la siguiente pausa entre tools, sin que el agente pare ni empiece de nuevo.

Enter nunca se bloquea. Lo que el humano envía mientras corre un Turno de esa Sesión sale en el Chat como «en cola». En F1, donde cada Turno es un solo Paso, corre como el Turno siguiente. Desde F2 entra en el mismo Turno, como un mensaje más antes del siguiente Paso, después de las salidas de las tools. No le sube la autoridad al Turno: el Turno conserva su Origen, y un texto humano que entra en un Turno de otro Origen no lo vuelve humano (ADR-0013). Solo entra lo que se teclea en la Ventana de esa Sesión. Los Prompts de otras Sesiones esperan su propio Turno, porque quien los mandó espera la respuesta a su propio Prompt (ADR-0011).

Mientras algo está en cola, ↑ en la primera línea del input, antes de recorrer el historial de Prompts, trae de vuelta lo último que se envió: sale de la cola y del Chat, y queda en el input como un bloque propio del Borrador. Cuando ya no queda nada en cola, ↑ sigue con el historial. Lo que ya entró en el Turno solo se deshace con Rewind, que devuelve al input, como bloques, todos los textos humanos de ese Turno.

Shift+Enter interrumpe. Corta el Turno en ese momento y lo guarda con lo que llevaba, como un Turno cortado, y cancela las tools que estuvieran corriendo. El texto va como el siguiente Prompt de esa Sesión, antes que los que ya esperaban, y el LLM recibe una Notificación de que lo interrumpieron. xterm ya manda Shift+Enter distinto de Enter gracias a la opción que la Ventana usa para Ctrl+Enter (`modifyOtherKeys`). Las dos teclas son Atajos por defecto, que un humano puede cambiar con `/key`.

Cerrar la Ventana devuelve al Borrador lo que estaba en cola, como el Prompt de un Turno de Origen humano que se cancela; pasar a segundo plano no cambia nada. Se descartó seguir bloqueando Enter, que hace esperar al humano sin necesidad. Se descartó también que, en F2, lo escrito esperara siempre al Turno siguiente, porque un Turno largo no se podría corregir sin cancelarlo.

## Cómo quedó en F2 (#121)

Comprobado con `llama serve` (Qwen3.8-27B): la plantilla de chat pone el mensaje del usuario justo después del `<tool_response>`, y el LLM lo obedece.

- Entra lo que espera en cola cuando el Turno va a empezar su siguiente Paso: al tener otra vez slot tras sus tools, o al seguir tras una caída o una Compactación. Si el Turno ya no tiene otro Paso, lo escrito corre como el Turno siguiente, como en F1.
- Solo entra texto llano. Lo que lleva Command prompts (`@clave=valor`, `@./ruta`) o `-y` vale para un Turno entero, así que espera el suyo, y lo escrito después también, para no cambiar el orden en que el humano lo escribió.
- Tampoco entra lo que no cabe en lo que le queda al slot del Turno, contado a un token por carácter: espera su propio Turno, que se mide y compacta si hace falta.
- Lo que entró se guarda en el Paso tras cuyas tools entró, y el Chat lo muestra en ese punto, en vivo y al reconstruirse. Si el Turno falla, vuelve al input con el Prompt; un Rewind lo devuelve también.
- Si Shift+Enter corta el Turno durante sus tools, se guarda el Paso que las llamó, cada llamada con un error como salida que dice que el humano lo interrumpió y que la tool pudo hacer parte de su trabajo, para que el LLM sepa qué había puesto en marcha.
