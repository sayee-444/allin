# Segundo plano sin xterm, y el Organizador como parte de Main

Se quieren muchas Sesiones abiertas sin llenar la pantalla: un máximo de Ventanas a la vista, y las demás en segundo plano, donde siguen trabajando. El segundo plano es un estado de la Sesión, no un tipo de Ventana. Main cierra su xterm, pero la recuerda como abierta. La Sesión sigue exactamente igual que con Ventana: recibe Prompts, corre sus Turnos y sus tools, y la alcanzan otras Sesiones y submains. Al traerla de vuelta, Main abre un xterm nuevo con el Chat reconstruido y lo que el Turno en curso lleva generado, como ya hacía `/open`. El precio es que al volver se pierden el scroll y la selección.

Se descartó minimizar el xterm vivo. En Wayland, donde xterm corre en XWayland, mover ventanas desde fuera depende del gestor de ventanas de GNOME y de herramientas que no están instaladas. Que xterm se minimice solo exige `allowWindowOps`, que #28 apaga porque deja que un texto se haga pasar por algo tecleado (ADR-0018).

## Segundo plano no es cerrar

Pasar a segundo plano no cancela nada, aunque el Turno sea de Origen humano:
- el Borrador se guarda;
- los Avisos y Confirmaciones salen en el Chat de Main, y en la Ventana cuando vuelve;
- si Main se reinicia, la Sesión vuelve en segundo plano.

Cerrar sigue como en ADR-0013: la X del gestor de ventanas o `/close` cancela el Turno de Origen humano, también el de una Sesión en segundo plano. Se descartó que la X mandara a segundo plano: protegería de cierres accidentales, pero cambiaría lo que cualquiera espera de una X.

## El máximo

El Máximo de Ventanas (`windows.max`, por defecto 6, `0` sin tope) es un Ajuste de Main. Lo rigen estas reglas:
- Lo que abre un humano siempre queda a la vista. Si no hay sitio, pasa a segundo plano la Sesión que el humano lleva más tiempo sin usar, y nunca una que espera una Confirmación mientras haya otra.
- Lo que abre una Sesión nunca desplaza a otra: si no hay sitio, nace en segundo plano.
- Nada sale solo del segundo plano: si se libera un hueco, no se abre ninguna Ventana por su cuenta.
- `--bg` en `/new` y `/open` abre directamente en segundo plano. Un Molde o un Plano también pueden fijarlo.
- Si el máximo baja, las Sesiones que sobran pasan a segundo plano. Al reiniciar Main, vuelven a la vista hasta el máximo.

Antes de F3 hay un solo Main. El tope compartido por un árbol de submains, y un Organizador de todo el árbol, se deciden con el protocolo entre Mains.

## El Organizador

El Organizador es una pantalla temporal que abre `/windows`. Lista en vivo las Sesiones abiertas del Perfil, y debajo, plegadas, las cerradas. De cada una muestra su título, si está a la vista o en segundo plano, su Turno o su cola, sus Confirmaciones y Avisos, su Contexto y su Supervisor. Con un clic o una tecla ejecuta `/open`, `/bg` o `/close`, y `/windows list` escribe la misma lista en el Chat de Main. No cuenta para el máximo, hay uno por Main y no vuelve tras un reinicio. Usa el Tema (ADR-0018). No tiene animaciones.

Es parte de Main, como las Ventanas y el Chat de Main, no una pieza: lo que el humano hace en él tiene Origen humano. Se descartó hacerlo con una fproc y su Proceso adherido. Ese proceso está encerrado, y lo que sale de él lleva un eslabón que nunca cuenta como humano (ADR-0021). Un clic que pasara por él ya no sería humano, y un «cerrar» podría cancelar el Turno de un humano sin que ningún humano lo pidiera.

## El LLM

El LLM tiene la fsource `windows`, que se hereda en `/new` y solo alcanza Sesiones del Perfil activo. Con ella puede:
- listar las Sesiones, como `/windows list`;
- mandar a segundo plano a su propia Sesión y a las que ella creó, nunca a otras, para no quitarle de delante a un humano la Ventana que está usando;
- abrir o traer a la vista una Sesión sin desplazar a nadie. Si no hay sitio, la Sesión queda marcada como que pide atención, en el Organizador y en el Chat de Main, hasta que el humano la trae o descarta la marca.

Nunca cierra una Ventana, porque cerrarla puede cancelar el Turno de un humano.
