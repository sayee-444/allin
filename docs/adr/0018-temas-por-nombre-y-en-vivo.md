# Cada Ventana y el Chat de Main se ven según un Tema, que se elige por nombre y se aplica en vivo

Con varias Ventanas abiertas, todas se ven igual: el Lanzador abre cada xterm con la misma letra, tamaño y geometría, y curses dibuja los mismos cinco colores por tipo de línea. Distinguir una Sesión de otra, una coordinadora de sus trabajadoras o las Sesiones de un submain de las del raíz obliga a leer títulos. Por eso cada Ventana, y el Chat de Main, se ven según un Tema.

Un Tema es un archivo `themes/<nombre>.toml` en la carpeta del Perfil (ADR-0010) con la letra (familia y tamaño), los colores (fondo, texto, uno por tipo de línea del Chat, el del texto del borde y el del borde), el estilo del borde (`single`, `double`, `rounded`, `heavy` o `ascii`), el tamaño de la Ventana en columnas × filas, y el juego de caracteres especiales (`nerd`, `unicode` o `ascii`), con un ícono opcional por tipo de línea. Su nombre es de letras, números y guiones, y siempre se busca dentro de `themes/`. La instalación trae `default`, que reproduce el aspecto de antes salvo el borde, y tres o cuatro Temas bien distintos; si `default` falta o no es válido, se usa el aspecto integrado en AllIn.

Se hereda como los demás Ajustes. Main tiene el suyo (`theme`), que usa el Chat de Main; la plantilla de Sesión lleva otro (`session.theme`), que cada Sesión copia en `/new`; un Molde puede traer el suyo, para que sus trabajadoras se vean iguales y distintas de su coordinadora; y cada Sesión lo cambia con `/session set theme=<nombre>`, donde una clave suelta (`font.size=13`) pisa la del Tema solo en esa Sesión. Un submain hereda la plantilla por copia y puede tener la suya, así que se ve qué Main orquesta cada Ventana.

Sin elegir nada, `default` pone `auto` como color del borde: cada Sesión recibe al crearse el color de la paleta que menos usan las demás Sesiones y lo conserva siempre, como su id. Así la misma Sesión sale siempre del mismo color, y las que se crean juntas salen de colores distintos. Se descartó calcularlo del id, que es aleatorio: con cuatro Ventanas y doce colores, dos coincidirían el 43 % de las veces.

Lo que se copia es el nombre, no los colores: Main vuelve a leer el Tema cuando su archivo cambia, y editar `oceano.toml` cambia al momento todas las Ventanas que lo usan. Es la excepción a que los Ajustes se hereden por copia, y vale porque un Tema solo cambia cómo se ve una Sesión, nunca lo que hace. Si el nombre no existe (se borró, o la Sesión está compartida con un Perfil que no lo tiene), se usa `default` con un Aviso; si una edición deja el Tema inválido, quien lo usa se queda con su última versión válida, y el Aviso dice qué falló. Main lee y valida los Temas y manda a cada Ventana los valores ya resueltos, como un mensaje más (ADR-0001, ADR-0002); la Ventana solo los aplica.

Cambiar el Tema de una Sesión se ve al momento y no cierra su Ventana, porque cerrarla cancelaría un Turno de Origen humano (ADR-0013). Los colores y el borde los redibuja curses; la letra, el tamaño y el fondo los cambia AllIn con secuencias de control de xterm, y solo lo que cambió: cambiar los colores no deshace el tamaño que el humano le dio a la Ventana a mano. Esas secuencias las manda siempre AllIn, nunca el texto, que el saneado neutraliza (ADR-0013). Y xterm solo acepta las que cambian el tamaño, la letra o los colores, ninguna que pregunte algo, porque cada respuesta de xterm llega a la Ventana como si el humano la tecleara. Desde F1-01 las Ventanas se lanzaban con `allowWindowOps` encendido, que deja que un texto le pida a xterm su título o el portapapeles y los haga pasar por algo tecleado; #28 lo apaga.

Los recuadros de Confirmación, Aprobación y Aviso no toman colores del Tema: siempre se ven igual y legibles, para que ningún Tema, tampoco uno que llegue en un Kit, esconda lo que piden.

El Chat de Main corre en la terminal desde la que se lanza Main, que AllIn no abre. Siempre aplica lo que dibuja curses (los colores de cada línea y el borde), con el color más cercano de los que ya tiene esa terminal y sin redefinir su paleta, que otros programas siguen usando después de Main. El fondo, el texto y la letra los cambia solo si la terminal deja hacerlo con secuencias de control; al salir devuelve los colores que había, y lo que no se puede aplicar se dice en un Aviso al elegir el Tema, no en cada arranque. Se descartó abrir Main en su propio xterm, porque cambiaría cómo arranca y se apaga Main por algo solo visual.

Un Tema nombra su letra por su nombre, y la resuelve fontconfig con `monospace` de respaldo: si no está instalada, se usa otra de ancho fijo (sin el respaldo, fontconfig pone una proporcional) y un Aviso dice cuál falta y cuál se usa; al instalarla, la próxima Ventana la usa sola. Si ninguna letra instalada tiene los símbolos del juego elegido, AllIn baja al siguiente (`nerd`, luego `unicode`, luego `ascii`) y lo dice en el mismo Aviso.

Los Temas viajan en Kits de otros autores y no pasan por cuarentena (ADR-0014). Pero el nombre de la letra llega a xterm dentro de una secuencia de control, donde un carácter de control podría cerrarla y meter otra, y a fontconfig como patrón, donde un `:` le añade propiedades, una `,` otra letra y un `-` el tamaño. Por eso un Tema solo admite valores con forma fija:
- colores `#rrggbb`, uno de los 16 nombres de la terminal (`cyan`, `bright-red`…, que siguen su paleta) o `auto` en el borde;
- estilos y juegos de su lista;
- tamaños dentro de un rango;
- un nombre de letra solo con letras, números, espacios y `- _ . +`, con el `-` escapado;
- íconos de un solo carácter visible.

Una clave suelta pasa por la misma validación. Un Tema que no cumple no se carga, con un Aviso.

Llega con #47, al final de los pendientes de F1, cuando ya existen los Avisos, el saneado y las piezas por Perfil; ese mismo issue añade los Temas a la exportación y la carga de Kits.
