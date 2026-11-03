# Una fproc puede traer Externos y actuar por su cuenta sobre sus Sesiones, dentro de su Subperfil

El ejemplo guía es un bot de Discord. Una fproc mantiene la conexión con un servidor de Discord, y cada persona que escribe ahí, un Externo, conversa con una Sesión que crea la fproc. Hasta ahora una fproc solo actuaba mientras atendía una llamada. El Origen solo conocía al humano y a las Sesiones, había un solo RAG por Perfil y no había dónde guardar el token de un bot. La fproc `discord` concreta queda para «Después». Aquí se fijan las piezas generales, para que F2 no les cierre la puerta.

## Iniciativa entre llamadas

Entre llamadas, una fproc puede ejecutar por su cuenta los Command sessions que declara en su Alcance, pero solo sobre las Sesiones que creó ella: crearlas desde sus Moldes, mandarles Prompts, cerrarlas y volver a abrirlas. Cómo reparte a los Externos entre Sesiones lo decide su código; una Sesión por persona es solo el ejemplo. Se descartó que todo pasara por una Sesión administradora suscrita a sus eventos (ADR-0021). Cada mensaje costaría dos Turnos, y repartir mensajes por persona es código, no razonamiento: con dos slots, gastar un Turno en lo que decide un `if` es caro. Quien quiera una administradora puede seguir usándola.

## Externos

El texto de un Externo entra como Prompt con el Origen de la fproc, que nunca cuenta como humano (ADR-0021). Su identidad (`discord:juan`) se anota solo para el Log y la memoria. Nunca da autoridad, ni `-y` ni comandos escritos en el texto: es la defensa contra el «ignora tus instrucciones» de un desconocido.

## Las Sesiones que crea una fproc

Solo se crean desde Moldes que viajan en el package de la fproc, se aprueban con ella y figuran en su Alcance. Reciben las functions de su Molde, pero nunca más que las de la Sesión que adhirió la fproc; si la adhirió un humano desde el Chat de Main, el único límite es el Molde. Nunca reciben Otorgar. Es el eslabón más débil de ADR-0025, aplicado también a las functions.

Nacen en segundo plano: sin Ventana y sin contar para el Máximo de Ventanas (ADR-0020). Un humano les abre una desde el Organizador. La fproc las cierra y las vuelve a abrir cuando quiere. Borrarlas, con su historial, solo lo hace un humano, también cuando un Externo pide que se le olvide.

## El Subperfil

La fproc que lo declara recibe un Subperfil por cada instancia adherida, dentro del Perfil en que se adhirió, y la aprobación lo muestra. Tiene sus Sesiones, su propio RAG y su Carpeta de trabajo. Una fproc solo crea Sesiones dentro de su Subperfil, así que la que no lo declara no crea ninguna. Sus Sesiones no aparecen en la lista del Perfil; en el Organizador salen plegadas bajo la instancia. Es una frontera como la del Perfil: `/send` no la cruza en ningún sentido, y solo un humano pasa de un lado a otro. Existe mientras su Perfil está activo, y al cambiar de Perfil se congela como lo demás (ADR-0010).

Su RAG es otra instancia de la fproc `rag` (ADR-0019). Indexa el historial de las Sesiones del Subperfil y nunca el del Perfil, y esas Sesiones nunca reciben el RAG del Perfil. La fproc puede añadirle lo que traiga, como los mensajes del canal que no van dirigidos al bot, con su procedencia en cada fragmento y siempre como dato. Si alguien envenena esa memoria escribiendo en ella (MINJA, ADR-0019), el daño queda dentro del Subperfil.

Se descartó usar el RAG del Perfil con un Segmento por Externo: un solo error de permisos expondría la memoria del usuario. También se descartó un Perfil aparte para el bot: un Main tiene un solo Perfil activo, y el bot se pararía al cambiar de Perfil. Cuando existan los submains (F3), un Subperfil podrá vivir en uno.

## Confirmación y Exención

Lo delicado que piden las Sesiones de un Subperfil le pregunta al humano. `--devastation` no lo contesta, porque la fproc está en su cadena y nunca tiene Exención (ADR-0013). Si la tuviera, una Sesión sin Exención o un Externo escribiría en la fproc, y la fproc ejecutaría lo delicado con la autoridad de `--devastation`.

En cambio, un humano puede ponerle la Exención a un Subperfil. Entonces sus Confirmaciones suben a `--devastation` solo si la acción no toca nada fuera de él: ni otras Sesiones, ni el Perfil, ni archivos fuera de las Carpetas de trabajo del Subperfil. La fproc sigue sin tenerla, y la marca se ve en el borde y en el Organizador. Así un bot puede trabajar solo dentro de su caja, y lo de fuera sigue pasando por el humano.

## La GPU

En la Admisión, un Prompt que trae una fproc por su cuenta va detrás de los de Origen humano (ADR-0004). Un Ajuste del Subperfil dice cuántas de sus Sesiones pueden tener a la vez un Turno en marcha o en cola. Pasado ese tope, la fproc recibe «ocupado» y decide con su código: esperar, o contestar al Externo que espere.

## Secretos

Un humano da cada Secreto con `/secret set <instancia> <nombre>`, y Main lo guarda en el Perfil a nombre de la instancia, aunque esta no tenga Subperfil. Main se lo entrega a la fproc al lanzarla por su canal, nunca por el entorno (ADR-0005), y vale en cada adhesión hasta que un humano lo quita. Nunca va en su Carpeta de trabajo. No lo ven el LLM, la Conversación, el Log ni el RAG, y nunca viaja en Moldes, Kits ni copias del Perfil.

Al implementarlo (#124), el valor se pide en un recuadro del Chat de Main que solo muestra puntos, nunca en la línea del comando, que guarda el Log; si alguien lo escribe ahí por error, el Log y el Chat solo guardan `/secret set <instancia> <nombre> …`. No hay `@secret`: tecleado en una Ventana, o pedido por una herramienta, se rechaza con un error que remite a `/secret` en el Chat de Main, y el Log, la Ventana y su Borrador guardan solo sus cuatro primeras palabras, o nada (#155). Lo mismo vale para el `@secret` que manda un `/send`, un `@send` o la tarea de un Encargo: se rechaza y no vuelve a ninguna entrada ni Borrador (#178, #195). Y en cualquier Log, quien sea que la escriba, una línea de comando se corta tres palabras después de su primer `@secret` o `/secret` (#195). Las tres cosas, el corte, el rechazo y la vuelta a la entrada, salen de una sola lectura de la línea, que no se deja engañar por opciones delante (también entre comillas), saltos de línea, tabs ni escapes de JSON (`\u0040secret`), y vale también para la Iniciativa de una fproc (#209). Un `/send` humano con algo que empieza por `--` delante del `@secret` se lee así también y se rechaza: aunque su texto iría tal cual, empieza como la forma estructurada de una Sesión. La fproc lo lee en claro con `self.secret("<nombre>")`. Lo que devuelve uno de sus métodos lo lee el LLM, así que no pasárselo depende de su código, igual que todo lo que hace con su red: lo frena la Aprobación.

## La red

Landlock limita la red por puerto, no por host (ADR-0016). Una fproc con red hacia Discord puede conectar al puerto 443 de cualquier sitio. El único freno es la Aprobación de su código.
