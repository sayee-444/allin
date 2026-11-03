# El Personaje vive en su propio Perfil, en una sola fproc que reúne sus sentidos

El usuario quiere una compañera con memoria y personalidad propias, que hable, escuche y vea, y que conviva con AllIn usado como herramienta. Un Personaje es una sola instancia de fproc con Subperfil (ADR-0026), adherida en un Perfil propio que es su casa. Todo lo que oye, ve y lee entra por esa instancia: solo así tiene una memoria y no varias.

## Una instancia, una memoria

Cada instancia adherida de una fproc que declara Subperfil recibe uno, con su propio RAG (ADR-0026). Si el micrófono, Discord y su Ventana fueran fprocs aparte, habría tres memorias y, por tanto, tres personas. Por eso sus sentidos son partes de su fproc: el micrófono de la computadora, la voz y el chat de Discord, su Ventana y, después, la cámara.

Lo que hace de vez en cuando no es parte de su fproc. Tomar una foto, hacer una captura de pantalla o navegar son Function tools que el usuario le da y le quita, con su Alcance, como a cualquier Sesión.

No es la fproc `discord` genérica de «Después», que tiene una instancia por servidor y reparte a las personas entre Sesiones. El Personaje es una sola instancia que entra a los servidores donde el usuario lo presentó, y en su tiempo libre elige a qué canal conectarse.

## Su casa y el interruptor

Al cambiar de Perfil, Main ya cierra los Procesos adheridos del Perfil que se deja, así que apagar al Personaje es salir de su Perfil. Apagado no existe, no piensa y no carga nada.

Antes de apagarse se despide. Main le avisa a su fproc antes de soltarla y la espera hasta un tope, un Ajuste de 10 segundos por defecto. Si hay alguien en una Charla, el Personaje se despide ahí; si no, su fproc solo anota la hora. Después Main suelta su fproc y duerme sus Supervisores. Al volver, su fproc le dice cuánto tiempo estuvo apagado, junto con el Autorretrato.

Mientras el Personaje está encendido, el usuario puede trabajar con agentes en su Perfil. Para usar AllIn como herramienta pura, con el 27B más grande, cambia a un Perfil de trabajo.

Se descartó que su casa fuera el Perfil de trabajo del usuario, con un interruptor que lo soltara: su respaldo arrastraría ese trabajo, y en cualquier otro Perfil estaría apagado igual. ADR-0026 descartó un Perfil aparte para un bot de Discord porque se pararía al cambiar de Perfil. Para el Personaje eso es justo lo que se busca: es su interruptor.

Al entrar a su Perfil se adhiere su fproc y despiertan sus Supervisores: el 9B y el 27B en IQ3_XXS. Si no caben, Main antes drena y duerme los demás, como el Q6: no les deja empezar Pasos nuevos y los duerme cuando acaban los que están en curso. Al salir, drena y duerme los suyos. Lo declara un Ajuste del Perfil que el usuario pone solo en el de ella, así que su Perfil de trabajo no duerme ni despierta nada: el Q6 despierta solo con su primer Prompt (enmienda de ADR-0022). Si su fproc se cae, Main sigue sin relanzarla.

Su Subperfil tiene un tope de 2 Turnos a la vez, uno para la Charla y otro para su Reposo; el de ADR-0026 es 1 por defecto.

## La Charla

Cada encuentro es una Sesión de su Subperfil, la Charla. Empieza cuando alguien toma la palabra. Termina cuando el Personaje la da por terminada, por ejemplo porque el usuario se despidió, cuando se va del lugar o tras una pausa sin que nadie más hable (un Ajuste, 30 minutos por defecto). La cierra con un método de su fproc, y su Reposo empieza enseguida. La pausa cuenta desde lo último que dijo otra persona, así que sus propios mensajes no la alargan. La continuidad entre Charlas viene de su memoria, con las Pistas y sus Relaciones: si el usuario le habla a las 10:00 y vuelve a las 11:00, son dos Charlas, y en la segunda una Pista le recuerda la primera. Se descartó una Sesión eterna, que perdería detalle en cada Compactación (ADR-0017).

Su Ventana es la de la Charla en curso. Las Sesiones de su Subperfil nacen en segundo plano (ADR-0026): el usuario abre la Charla en curso desde el Organizador y escribe con Origen humano, así que su teclado conserva la autoridad. Su fproc abre y cierra las Charlas con su Iniciativa. Cuando se cierra una, deja abierta la siguiente; si nadie ha escrito en ella cuando termina su reflexión, la cambia por una con el Autorretrato nuevo. Para eso Main le avisa a su fproc cuando termina cada Turno de las Sesiones de su Subperfil, también los que escribe el usuario: hoy las Suscripciones solo despiertan Sesiones. Se descartó que el usuario le escribiera a su fproc como un Externo, porque perdería la autoridad del teclado en su Ventana.

Puede escribirle primero al usuario. Al terminar un Reposo, si concluyó algo que quiere contarle, le escribe en la Charla que dejó lista. Si escribe o no lo decide el Personaje, con un tope que es un Ajuste de su fproc: una vez por Reposo, por defecto. Su fproc muestra entonces un Aviso «te escribió», sin el texto, que sale en el Chat de Main si su Ventana está cerrada. No lo interrumpe: que le hable en el momento, sin que le hablen, queda para el tramo de la voz.

En una Charla abierta siente los silencios. Cuando el usuario lleva un rato sin escribir, su fproc se lo dice («lleva 5 minutos sin escribir»). El Personaje decide si le vuelve a escribir o lo deja tranquilo, porque piensa que está concentrado o que se fue. Los momentos son Ajustes de su fproc, a los 5 y a los 15 minutos por defecto. En su Ventana, el usuario ve ese aviso con el rótulo de su fproc, porque es lo que el Personaje percibe.

Cada Charla es también la unidad que el usuario marca, o no, para entrenar al modelo de voz. La marca o la desmarca un comando de Main, solo de un humano. Se exporta como JSONL, una Charla por línea, en mensajes `system`, `user` y `assistant`, que leen Unsloth y LLaMA-Factory. Cada una va con su Sysprompt, su Autorretrato y sus líneas de voz interior, tal como las vio el 9B, para que aprenda a hablar con ellos delante. Una Charla con una Confidencia nunca se exporta (ADR-0029).

Está en un solo lugar a la vez. De otro lugar solo le llega una línea, sin el texto («Marx te escribió por mensaje directo»), y el Personaje decide si va. Lo que escribe un Externo no entra en su contexto hasta que el Personaje lo elige.

Su voz interior va dentro de la propia Charla. Cuando lo interrumpen, su fproc corta lo que estaba diciendo y le pasa lo que oye. El Personaje anota en una línea, sin decirla, si cede, insiste o retoma después. Se descartó una Sesión aparte que pensara en paralelo, por lo medido en «La GPU».

Esa línea va marcada al principio de su respuesta, como «(pienso: cedo; lo del viaje lo retomo después)», y sale en la misma generación. Se descartó una tool `pensar`, que costaría una vuelta más por cada pensamiento, y en AllIn cada llamada lanza un proceso (ADR-0005). También se descartó el razonamiento propio del modelo, que piensa demasiado antes de hablar. El formato vive en su Sysprompt y en su fproc, no en Main. En su Ventana el usuario la ve; en Discord y en la voz, su fproc la quita.

## Tomar la palabra en la voz

En la voz, el código detecta los momentos y el Personaje decide qué hace en cada uno. El código detecta el fin de cada frase, que alguien empieza a hablar mientras él habla y los silencios. Al final de cada frase que oye, el Personaje decide si cede, espera, dice «ajá» o interrumpe. Así puede cortar a alguien cuando algo le molesta o le importa. Cada decisión es un Turno corto del 9B, y seguir en el mismo slot cuesta 0,07 s.

De pipecat y LiveKit se toman tres cosas:
- para cortarlo hacen falta unas palabras, así que un «ajá» o una tos no lo cortan;
- si lo cortó un ruido sin palabras, sigue donde iba;
- su TTS genera por frases, para saber qué alcanzó a decir antes del corte.

Los umbrales y un tope de interrupciones son Ajustes de su fproc. La latencia, los grupos y a quién le habla quedan para el grilling del tramo de la voz.

## La voz del usuario no da autoridad

Lo que trae una fproc nunca cuenta como humano (ADR-0013, ADR-0021, ADR-0026), y eso no cambia. Que el Personaje reconozca la voz del usuario es una marca para él, no autoridad: lo trata como a quien es, pero las Confirmaciones, `-y` y `--devastation` siguen siendo solo de teclado. Otra persona que hable en el cuarto del usuario, por su micrófono, es un Externo, igual que en Discord. Se consideró enmendar ADR-0013 para que su voz verificada contara como humana, con el riesgo asumido por el usuario, y se descartó por ahora: una grabación o un clon la falsifican más fácil que el teclado.

Las grabaciones con las que se registra al usuario, de su voz y de su cara, se guardan fuera del repo y fuera de todo Encierro. Sirven para probar el reconocimiento con su voz real y para registrarlo de nuevo cuando cambie el modelo. Él las borra cuando quiera. Las Huellas y el registro se deciden con el tramo de la voz.

## La GPU

Medido el 2026-11-03 con `llama` b10909 en la RTX 5090. El modelo de voz es el Qwen3.5-9B «Defiant Fable Uncensored Heretic» de DavidAU, en Q5_K_S (el IQ4_XS queda de reserva):

- **Sin MTP.** Charlando solo acepta un 24 % de lo que adivina, y con MTP genera más lento que sin él: 122 frente a 160 tok/s. Al razonar o al llamar tools acierta un 86–96 %.
- **Sin razonamiento en la Charla.** Por defecto razona, y por la API Responses llegó a gastar todo el tope de salida sin decir nada.
- **Un slot.** Un slot no reutiliza lo que leyó otro, así que una voz interior en otra Sesión releería la Charla entera (1,7 s por 16K tokens) y, mientras tanto, con el batch por defecto la frenaría a 5–6 tok/s. Dentro de la Charla, seguir escribiendo cuesta 0,07 s, y volver a otra rama del final, 0,13 s.
- **El 27B no la congela.** Un prefill de 28K tokens del 27B en IQ3_XXS, en su propio proceso, deja al 9B a ~43 tok/s y sin cortes. Ese prefill puede venir de un Encargo o de su Reposo. Para hablar en voz alta bastan unos 4–5 tok/s. En su Perfil corre ese 27B; el Q6 queda para el Perfil de trabajo.

Solo se entrena el modelo de voz, nunca el de razonamiento, con un LoRA sobre esos mismos pesos: DavidAU publica los safetensors. Cambiar de modelo base obliga a entrenar de nuevo.

## Lo menos posible fijo en el código

El usuario pidió que el código fije lo menos posible y que sea el Personaje, el LLM o el conjunto de modelos que forma AllIn en modo diálogo, quien le dé forma. Main pone primitivas genéricas, como el Ajuste de los Supervisores del Perfil o la exportación de Charlas. Lo propio del Personaje vive en su fproc y en sus piezas: su Sysprompt, sus Moldes y sus functions.

Lo que hace lo decide el LLM: si cede la palabra, qué concluye en su Reposo y qué marca como Confidencia. Los números son Ajustes con un valor por defecto, como la pausa que cierra una Charla o el largo del Autorretrato. Solo queda fijo en el código lo que protege: el Origen, el filtro de las Confidencias y el Encierro.

El Personaje cambia solo los Ajustes de su fproc, como la pausa o el largo del Autorretrato, pero nunca lo que protege. Sus piezas (su Sysprompt, sus Moldes y sus functions) viajan en el package de su fproc (ADR-0026). Propone versiones nuevas, que quedan en cuarentena hasta que el usuario las aprueba (ADR-0013). Eso llega con los packages de «Después»; hasta entonces solo cambia sus Ajustes. Su Iniciativa sigue dentro de su Subperfil.

Se descartó que maneje AllIn por el usuario desde la Charla, creando Sesiones o functions en su Perfil. Cruzaría la frontera del Subperfil, que solo cruza un humano, y le daría poder sobre el Perfil a lo que entra por voz y por Discord, también a un clon de la voz del usuario. Podría venir más adelante, si el usuario confirma en el teclado cada acción fuera de su Subperfil.

## Lo que falta en Main

Son primitivas genéricas, que puede usar cualquier fproc:
- un Ajuste del Perfil que adhiera fprocs y despierte y duerma sus Supervisores al entrar y al salir (enmienda de ADR-0022);
- que Main le avise a una fproc cuando termina cada Turno de las Sesiones de su Subperfil;
- que Main le avise a una fproc antes de soltarla y la espere hasta un tope;
- que una fproc muestre un Aviso;
- que una fproc declare sus Ajustes y que las Sesiones de su Subperfil puedan cambiarlos;
- marcar Sesiones para entrenar y exportarlas en JSONL, nunca las que tengan una Confidencia;
- la tool que marca una Confidencia, con su filtro (ADR-0029);
- que `/profile export` copie los Subperfiles y las Carpetas de trabajo (#217).

## El orden

1. El Personaje con el usuario, por texto: su Ventana, su memoria, su Reposo y la curación de Charlas.
2. Discord en texto, con otras personas y sus Confidencias (ADR-0029).
3. La voz.
4. La vista.

Cada tramo es un milestone. Van después de la fase del RAG, que es su memoria, y antes de F3, que no necesita.
