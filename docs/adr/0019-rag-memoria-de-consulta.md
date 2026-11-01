# El RAG es la memoria de consulta de un Perfil: guarda todo, devuelve como dato lo poco que responde y no decide por el LLM

Una Sesión solo sabe lo que hay en su Conversación. Le quedan fuera lo que la Compactación resumió, lo que pasó en otras Sesiones, los documentos del usuario y lo que AllIn es: sus comandos, sus functions y dónde viven. Por eso el LLM pregunta de nuevo lo que ya se dijo o recrea una function que ya existe. Para eso cada Perfil tiene un RAG, la memoria de consulta del sistema. Guarda, con fecha y procedencia, los documentos que le da un humano, lo que AllIn sabe de sí mismo y lo que pasa en las Sesiones: texto, imágenes y video, estos dos por su descripción. Cuando una Sesión lo pide, devuelve como dato los pocos fragmentos que mejor responden, buscados por palabras y por embeddings y reordenados por un reranker. Viene con la instalación, se enciende y se apaga con un Ajuste, y cada Sesión ve solo la parte que se le da.

Guarda todo y elige al recuperar. Se descartó que el LLM decidiera qué guardar con tools, como MemGPT, y que un LLM extrajera hechos de cada Turno para fusionarlos, como Mem0. En los dos casos un modelo decide qué se olvida, cada Turno cuesta un Paso más, y lo extraído pierde: en LoCoMo, según el propio artículo de Mem0, darle al modelo el contexto completo puntuó 72,9 y la memoria de Mem0, 66,9. El historial se indexa por Turno, la granularidad que mejor recuperó en LongMemEval.

El RAG no decide por el LLM, no le da tools ni permisos y no define su personalidad, que es del modelo (se trabajará aparte, con un LoRA). Lo que devuelve entra como `user`, nunca como `developer`, con la autoridad de su Origen (ADR-0013). Una memoria es una vía de inyección probada: en AgentPoison, envenenar menos del 0,1 % de la memoria bastó para más del 80 % de éxito, y en MINJA cualquier usuario la envenena solo con consultas. Por eso en esta versión el LLM no escribe en el RAG: las notas del LLM llegarán después, con el Origen de su cadena.

## Qué guarda y quién lo ve

Entran cuatro cosas, cada una en sus Segmentos:
- **Corpus**: carpetas de documentos que añade un humano.
- **El historial de cada Sesión**: cada Turno terminado, sin LLM: el Prompt y la respuesta, sin el razonamiento.
- **AllIn sobre sí mismo**: el manual (README, GLOSSARY, ADR, `/help`), la documentación del código y el catálogo de las functions aprobadas del Perfil (qué hacen y dónde viven), para no recrearlas. Main lo regenera desde la fuente aprobada, así que nunca entra una pieza en cuarentena.
- **Notas del LLM**: después, no en esta versión.

Cada Sesión busca en su propio historial. El historial del Perfil (el de todas sus Sesiones) solo lo da un humano, y nunca se alcanza otro Perfil (ADR-0010).

Lo que ve una Sesión es su lista de Segmentos. Por defecto: su historial, lo de AllIn y los Corpus que el Perfil marque como de serie. Se lleva como sus functions: un Molde la fija, un Clon la copia, y solo un humano la amplía. Los métodos `buscar` y `leer` del RAG se heredan en `/new` como las fsources y se quitan igual (`-sf=off`, o un Molde que no los lleve). En `tools=[...]` se llaman `rag_buscar` y `rag_leer` (ADR-0021). El permiso es de la Sesión, no de su Ventana ni de Main: Main pone la lista en cada llamada y comprueba de qué Segmento es cada fragmento que vuelve. Las Sesiones solo leen: el historial se escribe solo y los Corpus los añade un humano.

## Cómo llega a la Conversación sin llenarla

`buscar(texto, en, desde, hasta)` devuelve Fichas: cada fragmento con su fecha, fuente, Segmento, Origen e id, en formato fijo. En LongMemEval, presentarlo estructurado y pedir al modelo que lo anote antes de responder subió hasta 10 puntos. `en` acota a algunos de los Segmentos de la Sesión y nunca los amplía. `desde` y `hasta` filtran por fecha: el LLM convierte «la semana pasada» en fechas, y filtrar por tiempo subió de 6,8 a 11,3 puntos en LongMemEval. `leer(id, alrededor)` da el fragmento entero con sus vecinos (el Turno de antes y el de después, o los párrafos de alrededor), o la descripción y la ruta de una imagen o un video.

Mejor nada que basura. El reranker da a cada candidato una probabilidad de ser relevante; solo vuelve lo que pasa un umbral, como mucho 5 fragmentos y unos 1 500 tokens. Si nada pasa, la tool dice que no encontró nada relevante, y el LLM puede decir que no lo sabe en lugar de inventar a partir de un fragmento flojo. Nunca vuelven Turnos que siguen en la Conversación de esa Sesión. Las salidas viejas de `buscar` se podan como las de cualquier tool (ADR-0017), y se pueden volver a pedir.

El modelo no sabe lo que no sabe que recuerda. Por eso, antes de cada Prompt, Main busca con su texto. Solo si algo pasa un umbral alto, añade una Pista por resultado, como mucho 3: una Notificación de una línea con la fecha, la fuente, el inicio del fragmento y su id, que remite a `leer`. Cuesta unos 100 tokens cuando hay algo y nada cuando no, y se apaga con `@conversation_logs off rag`. Si el reranker es lento en CPU, la Pista usa solo la búsqueda por palabras y embeddings.

Los umbrales, los topes y si el modelo usa `buscar` y hace caso a las Pistas los mide un prototipo desechable antes de escribir el definitivo.

## Dónde vive

Es un Proceso adherido (ADR-0012) que Main lanza desde la fscreen `rag`, que trae la instalación. El Ajuste de Main `rag`, encendido por defecto, hace que Main lo adhiera al arrancar y al entrar en cada Perfil. Apagarlo lo suelta y quita sus métodos a todas las Sesiones. Si se cae, sale un Aviso y Main no lo relanza: lo vuelve a adherir un humano. Se descartó que fuera parte de Main: Main crecería con cada idea (ADR-0011) y leería documentos sin encierro.

Va encerrado (ADR-0016). Lee en solo lectura las carpetas de los Corpus y la instalación, de donde saca el manual de AllIn, y escribe sus índices en su Carpeta de trabajo, que es del Perfil. Un PDF hecho para atacar al lector se queda dentro del encierro. Lo que vive en la carpeta de datos, que no alcanza, se lo pasa Main: cada Turno terminado, las marcas de deshecho y el catálogo de functions. Hay un índice por Segmento.

Hay un solo RAG por Perfil, y lo adhiere el Main raíz. Nunca hay uno por submain, porque dos procesos no pueden escribir en el mismo índice. Las Sesiones de un submain llegan a él por el protocolo entre Mains, que se decide en F3.

## Los modelos

El modelo de embeddings (Qwen3-Embedding-0.6B) y el reranker (Qwen3-Reranker-0.6B, que convertimos nosotros desde los pesos oficiales porque Qwen no publica su GGUF) corren cada uno en un Servidor con su Supervisor (ADR-0008). Si el RAG está encendido, Main los despierta además del de su Enlace. El RAG llega a ellos a través de Main, con los Command sessions `@embed` y `@rerank` por el canal de vuelta, porque ningún proceso encerrado habla con un Servidor (ADR-0016). La instalación deja esos Supervisores dormidos con el modelo sugerido, porque el archivo del modelo solo lo nombra un humano. Sin ellos el RAG funciona igual, solo por palabras (FTS5), y suma embeddings y reranker cuando sus Supervisores están listos. Por defecto corren en CPU. Medido con build 10909: el 0.6B de embeddings indexa 1 051 tokens/s en CPU, con unos 33 ms por consulta, y 5 061 tokens/s en GPU. Cada vector recuerda el modelo que lo calculó y el texto original se conserva, así que cambiar de modelo es volver a calcular, sin perder nada.

## Documentos, imágenes y video

Un humano añade una carpeta a un Corpus con `/rag add <corpus> <carpeta>`. Como amplía lo que lee un proceso encerrado, pasa por la Aprobación, y ni `-y` ni `--devastation` la saltan. Entran texto, Markdown, código y PDF con capa de texto. Un PDF escaneado se trata como imagen, y lo demás se ignora con una línea en el Informe. Los documentos se parten por encabezados y párrafos, en fragmentos de unos 400 tokens con el título y la ruta de encabezados delante. Un Turno largo se parte igual. El RAG reindexa comparando el hash de cada archivo al adherirse y con `/rag reindex`; no vigila las carpetas en vivo.

Las imágenes y el video entran por su descripción, que escribe el modelo del Enlace en un Paso de Main con el Sysprompt pieza `describir`. Como el de compactación, vive en el Perfil, pasa por cuarentena y lo trae la instalación (ADR-0017). Va con la prioridad más baja: solo toma un slot cuando ningún Turno espera. El video se describe por tramos de un minuto (unos 28k tokens a 4 fps, medido), y el audio no se entiende. Antes de describir, Main muestra una estimación («40 videos, unas 3 h de Servidor») y pide Confirmación. La descripción es dato con el Origen del documento, y se guarda con la ruta y el hash del original. Es la misma que usa la Compactación al convertir imágenes viejas en una línea. Los embeddings multimodales (Qwen3-VL-Embedding) llegarán cuando la build de llama.cpp en uso los soporte (la 10909 no) y haya GGUF.

## El tiempo

El historial nunca se edita. Rewind y Restablecer marcan los Turnos que deshacen, sin borrarlos, y quedan fuera de la búsqueda normal (ADR-0015). Compactar no lo toca: es justo lo que permite recuperar lo resumido. Un Clon tiene el historial de su original hasta el punto de la copia. Un documento que cambia se reindexa, y sus fragmentos viejos quedan marcados como sustituidos, con fecha, fuera de la búsqueda normal. Solo el historial envejece: baja en el orden con la edad, y los documentos y lo de AllIn no. Borrar de verdad un Corpus o el historial de una Sesión solo lo hace un humano, con un Command main.

Si el RAG estuvo apagado o caído, al adherirse dice cuál es el último Turno que tiene de cada Sesión. Main le manda los que faltan, incluidos los archivados por la Compactación, y las marcas de deshecho, leídas del Log.

## Perfiles y Kits

El historial de una Sesión es un Segmento con su id. Una Sesión compartida entre Perfiles (ADR-0010) aparece en el historial de los dos, y el RAG de cada uno lo indexa en su propia Carpeta de trabajo, porque ningún proceso encerrado alcanza la carpeta de la Sesión. Los Corpus son del Perfil. Cambiar de Perfil cierra su RAG, como los demás Procesos adheridos, y adhiere el del Perfil que entra. Ni un Kit ni la copia de un Perfil llevan índices: son datos derivados que dependen del modelo y de las rutas de una máquina, y se rehacen al adherirse.
