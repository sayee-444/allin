# Las Confidencias se protegen en el código, no con el criterio del modelo

Lo que alguien le cuenta al Personaje pidiéndole que no lo repita no puede quedar a criterio del LLM, porque los modelos guardan mal los secretos. En ConfAIde (Mireshghallah et al., ICLR 2024), GPT-4 reveló información privada en el 39 % de los contextos en que un humano no lo haría, y nada indica que un 9B lo haga mejor. Por eso lo confidencial lo filtra Main, y el criterio del modelo se reserva para lo demás.

## Tres niveles

- **Confidencia.** Main nunca se la muestra al Personaje en una Charla donde haya alguien más que quien se la confió, aunque sea el usuario. Ejemplo: Marx le cuenta a solas que lo despidieron y le pide que no lo diga. Al día siguiente el usuario le pregunta por él, y el Personaje ni siquiera ve esa Confidencia.
- **Privado.** Lo que le dijeron a solas le llega marcado así, y lo usa con discreción, a su juicio.
- **Público.** Lo que se dijo en grupo puede comentarlo, como cualquier chisme.

## El usuario ve los datos

El Log nunca se apaga, y el usuario cura las Charlas para entrenar, así que él ve las Confidencias en los datos. El Personaje no se las cuenta en la conversación. El aviso a quien hable con el Personaje, sobre lo que hace y lo que guarda, lo escribe y lo publica el usuario, bajo su responsabilidad.

Queda para su grilling cómo se marca una Confidencia, cómo sabe Main quién está en cada Charla y qué pasa con las Confidencias al entrenar.
