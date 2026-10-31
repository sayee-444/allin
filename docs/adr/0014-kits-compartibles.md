# Lo que se comparte entre usuarios es un Kit, no un Perfil, y se carga por copia

Se quiere pasar a otra persona, o a otra máquina, un entorno ya armado: un Sysprompt, un Goal o una composición propia ya configurada, functions escritas por otro usuario, Moldes, Temas. El Perfil no sirve para eso: es la identidad de un Main raíz con sus Sesiones, su Estado de Main y sus submains (ADR-0010), cosas privadas y atadas a una carpeta de datos. Por eso se separan:

- El **Perfil** se exporta como copia para respaldarlo o mudarlo de máquina, y no se mezcla con otros.
- El **Kit** es una receta portable y versionada que se exporta desde un Perfil y se carga en otro: Sysprompts, Moldes, functions (fsource, fagent y fscreen, también de otros autores), Goals y composiciones, Temas, parte de los Ajustes de Main y Ajustes del Servidor sugeridos. Nunca lleva Conversaciones, Logs ni nada de ejecución (Estado de Main, pids, FIFOs, candados). Combinar entornos es cargar varios Kits en un Perfil.

Cargar un Kit copia sus piezas en el Perfil, como toda herencia de Ajustes: si su autor publica otra versión, al Perfil no le cambia nada hasta que la cargue de nuevo. Cada pieza recuerda de qué Kit y versión vino, para poder actualizarlo o quitarlo después. Se descartó enlazar el Perfil al Kit vivo porque un cambio del autor alteraría el entorno de otro sin que este lo supiera.
