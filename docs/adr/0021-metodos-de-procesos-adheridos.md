# Los métodos de un Proceso adherido también los llama un humano, y los hereda quien lo adhiere

Una fproc expone sus métodos al LLM (ADR-0012), pero hasta ahora el humano solo podía manipular el Proceso adherido por fuera, y no estaba dicho qué Sesiones reciben sus métodos. Se pidió que un humano llame a esos métodos con un comando, que la Sesión que adhiere el proceso los reciba sola y que un método pueda actuar sobre AllIn como una fsource.

## Un solo Command main

Una pieza no puede añadir Command main. Un Command main es código de Main, corre sin encierro y es la única vía que cambia el estado (ADR-0001). Por eso hay un solo Command main genérico, `/call <nombre> <método> [clave=valor…]`, o `@call` desde una Ventana. Lleva la llamada de un humano al método, que corre en su proceso encerrado, igual que cuando lo llama el LLM. `/call <nombre>` a secas lista los métodos con su descripción y sus parámetros. Adherir y Soltar son `/attach <fproc> [nombre] [--to <sid,…>]` y `/detach <nombre>`. Se descartó `/fsource` como nombre porque nombra otro tipo de function.

## Quién llama a qué

- Todos los métodos públicos los llaman el LLM y el humano. `@human` marca los que solo llama un humano, y esos nunca llegan a `tools=[...]`.
- `@confirm` pregunta a los dos, y `-y` la salta solo al humano.
- Cuando un humano llama a un método `@writes`, la revisión sube como con la manipulación directa. La Sesión que leyó antes tiene que volver a leer antes de escribir.
- Lo que amplía el encierro nunca es un método: sigue siendo un Command main con Aprobación, como `/rag add` (ADR-0019).

## Quién recibe los métodos

La Sesión que adhiere el proceso recibe sus métodos como recibe sus functions. Si lo adhiere Main por un Ajuste, como el RAG, cada Sesión los tiene según su propio Ajuste, copiado del de Main en `/new` (ADR-0019). Desde el Chat de Main, `/attach` solo da los métodos a las Sesiones de `--to`; con `@attach`, los recibe la Sesión de esa Ventana.

La Sesión que adhirió puede pasar los métodos a las Sesiones que ella crea, al crearlas o después, pero nunca a otras. Un humano se los da o se los quita a cualquiera, con los comandos de functions de F2. Se dan y se quitan juntos, como un bloque con el nombre del Proceso adherido. En `tools=[...]`, cada método se llama `<nombre>_<método>`, porque una Sesión puede tener dos Procesos adheridos de la misma fproc y los nombres de tool no admiten puntos.

Soltar el proceso les quita los métodos a todas las Sesiones. Si el proceso se cae, sus métodos dan error y sale un Aviso (ADR-0012). Al deshacer o copiar una Sesión:
- un Rewind a antes del Prompt en que la Sesión recibió los métodos se los quita, igual que los Encargos abiertos después (ADR-0015), pero no suelta el proceso;
- Restablecer la devuelve a los métodos de su receta;
- un Clon copia los métodos y comparte el proceso, con la última revisión que vio el original;
- Compactar no los toca.

## Actuar sobre AllIn

Un método puede ejecutar Command sessions, pero solo mientras atiende una llamada. Entre llamadas, lo que pasa solo se avisa como evento a quien esté suscrito (ADR-0011).

Lo que manda lleva el Origen de quien lo llamó, más el Proceso adherido como eslabón de la cadena. Ese eslabón nunca cuenta como humano, porque el estado del proceso mezcla lo que escribieron varias Sesiones (ADR-0013). Así, aunque lo llame un humano, no pasan ni `-y` ni `--devastation`, y lo delicado pide Confirmación. Si no, un humano que llama a un método ejecutaría con su autoridad algo que dejó escrito un LLM.

## Lo que devuelve una llamada humana

Lo que devuelve sale en el Chat donde se tecleó la llamada y queda en el Log de Main. Con `@call`, queda también en el Log de la Sesión y le deja una Notificación de una línea, para que su LLM no siga trabajando sobre un estado que ya cambió; se apaga con `@conversation_logs off call`. Con `/call` desde el Chat de Main no deja Notificación a nadie, porque a las Sesiones que usan el proceso ya las protege la revisión.

**Enmienda (2026-11-01, ADR-0012).** Un Proceso adherido puede atender varias llamadas a la vez, si su fproc lo declara. Entonces cada Command session que ejecuta dice de qué llamada viene: lleva el Origen de esa llamada y se rechaza si esa llamada ya terminó. No abre un riesgo nuevo, porque el estado del proceso ya mezcla lo que escribieron varias Sesiones, y por eso su eslabón nunca cuenta como humano. La revisión la lleva ahora el propio proceso, y un `@writes` de un humano la sube igual que antes.

**Enmienda (2026-11-01, ADR-0025).** Un método solo ejecuta los Command sessions que declara su fproc en su Alcance y que además están en la lista de todas las Sesiones de la cadena de su llamada. Lo que Main pide por su cuenta lleva el Origen de Main. Una fproc que declara Otorgar solo la adhiere un humano.

**Enmienda (2026-11-02, ADR-0026).** Entre llamadas, una fproc ya no solo avisa con eventos. Por su cuenta, puede ejecutar los Command sessions de su Alcance sobre las Sesiones que creó ella, dentro de su Subperfil, con su propio Origen, que nunca cuenta como humano.

**Enmienda (2026-11-02, #114).** Al implementar los métodos se fijó lo que el texto de arriba dejaba abierto:
- **La orden.** `/call <nombre> [<método> [clave=valor…]]`. Un valor que se lee como JSON es JSON (`n=3`, `ok=true`); si no, es el texto tal cual, y puede ir entre comillas (`text='dos palabras'`). `@call` desde una Ventana es `/call … --in=<sid>`, y no viene en la lista por defecto, como `@attach`. Una Sesión solo llama a los métodos que tiene, y solo para sí misma.
- **Dar y quitar.** Un humano da o quita los métodos con `/func add|remove <sid> attached:<nombre>`. Una Sesión puede quitárselos, pero no darlos. Pasarlos a las Sesiones que crea queda pendiente, porque AllIn aún no guarda qué Sesión creó a otra (llega con el árbol, F3). También quedan pendientes el Rewind, Restablecer y el Clon de los métodos. Soltar el proceso quita los métodos a todas las Sesiones del Perfil, también cuando Main se cierra o se cambia de Perfil. Uno caído los deja, y sus llamadas dan error.
- **Desde el Chat de Main no hay Sesión.** Ahí, los Command sessions que pide el método se rechazan, porque un Command session corre en una Sesión. Con `@call`, corren en la Sesión de la Ventana, con el Origen `human > process:<nombre>`.
- **Lo que ve la Sesión con `@call`.** El resultado sale en su Ventana y en su Log, y además le queda una Notificación de una línea (fuente `call`). Un `@call` que ejecuta una tool recibe solo `ok`, no el valor.
- **Un Turno cancelado durante una llamada.** El método sigue hasta acabar. La llamada siguiente salta su respuesta, y los Command sessions que pida mientras tanto reciben un error. Esperar a que acabe o cortarlo de verdad es #115.
- **`@reach` en un método.** Un `@reach` puesto en un método, y no en la clase, hace fallar la descripción con `invalid_reach` en lugar de ignorarse, porque el Alcance es el de todo el proceso.
