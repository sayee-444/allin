# Las Confidencias se protegen en el código, no con el criterio del modelo

Lo que alguien le cuenta al Personaje pidiéndole que no lo repita no puede quedar a criterio del LLM, porque los modelos guardan mal los secretos. En ConfAIde (Mireshghallah et al., ICLR 2024), GPT-4 reveló información privada en el 39 % de los contextos en que un humano no lo haría, y nada indica que un 9B lo haga mejor. Por eso lo confidencial lo filtra Main, y el criterio del modelo se reserva para lo demás.

## Tres niveles

- **Confidencia.** Main nunca se la muestra al Personaje en una Charla donde haya alguien más que quien se la confió, aunque sea el usuario. Ejemplo: Marx le cuenta a solas que lo despidieron y le pide que no lo diga. Al día siguiente el usuario le pregunta por él, y el Personaje ni siquiera ve esa Confidencia.
- **Privado.** Lo que le dijeron a solas le llega marcado así, y lo usa con discreción, a su juicio.
- **Público.** Lo que se dijo en grupo puede comentarlo, como cualquier chisme.

## El usuario ve los datos

El Log nunca se apaga, y el usuario cura las Charlas para entrenar, así que él ve las Confidencias en los datos. El Personaje no se las cuenta en la conversación. El aviso a quien hable con el Personaje, sobre lo que hace y lo que guarda, lo escribe y lo publica el usuario, bajo su responsabilidad.

## Cómo se marca

Solo el Personaje entiende un «no se lo digas a nadie», así que la marca con una tool en el momento. Si no la marca, queda como Privado.

La tool la da Main, no su fproc, y la marca queda en el Turno, donde la lee el RAG, igual que el Origen. Su fproc y sus piezas pueden cambiar (ADR-0027). Si el filtro viviera ahí, cualquier cambio podría apagarlo, incluso uno que alguien le pidiera por Discord.

Su Reposo nunca recibe una Confidencia. Si el 27B la viera, podría escribir «perdió su trabajo» en la Relación con Marx, y esa frase saldría en un grupo. Lo confidencial no puede depender de que el 27B no lo repita. El Personaje la recuerda solo a solas con quien se la confió, por el RAG.

## Quién está presente

Lo dice el código de su fproc, nunca el LLM:
- en un mensaje directo, la otra persona;
- en un canal de texto, todos los que pueden leerlo, así que nunca está a solas;
- en un canal de voz, los conectados;
- en su Ventana, el usuario;
- por el micrófono, el usuario y cualquier otra voz que haya oído en esa Charla.

Esas reglas son código de su fproc, nunca Ajustes que el Personaje pueda cambiar.

Si alguien llega a una Charla que contiene una Confidencia, esa Charla se cierra y empieza otra: la Confidencia ya está en su contexto y no se puede sacar de ahí.

Riesgo aceptado: alguien callado en el cuarto del usuario no cuenta como presente. Solo afecta a las Confidencias del propio usuario, porque las de otros nunca salen con él.

## El entrenamiento

Una Charla con una Confidencia nunca se exporta para entrenar, aunque el usuario la marque. Un LoRA puede memorizar, y ningún filtro llega a los pesos, así que lo impide el código.
