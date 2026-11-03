# El Núcleo del Personaje va en su Sysprompt, y su Autorretrato le llega como dato

La personalidad del Personaje tiene una parte que no cambia y otra que cambia con lo que vive. Un Sysprompt es una pieza que aprueba un humano y que vuelve a cuarentena si cambia (ADR-0013), así que no puede llevar lo que el Personaje concluye de sí mismo en cada Reposo. Por eso se separan: el Núcleo lo escribe el usuario y va en el Sysprompt; el Autorretrato lo escribe el Personaje y le llega al abrir cada Charla.

## Dónde va cada uno

El Núcleo es su carácter, sus límites y su manera de hablar. Lo escribe el usuario en el Sysprompt del Molde de la Charla, y casi no cambia.

El Autorretrato lo manda su fproc al abrir cada Charla, con su Origen, igual que sale la tarea inicial de un Molde (#112). Es dato, no instrucción.

Encaja con las capas de la personalidad de McAdams: los rasgos van en el Núcleo, y las adaptaciones y la historia que el Personaje se cuenta de sí, en el Autorretrato.

Se descartó meter el Autorretrato en el Sysprompt. O el usuario lo aprobaría cada vez, y el Personaje sería lo que él aprueba y no lo que concluyó, o se saltaría la cuarentena. Separarlos protege además de la manipulación: quien manipule su reflexión desde una Charla consigue como mucho un dato, nunca un Sysprompt.

## Cómo reflexiona

El Personaje reflexiona en su Reposo, en una Sesión del 27B de su Subperfil:

- **Fuera de la Charla.** Lo que más saca a un modelo de su persona son las conversaciones que le piden reflexionar sobre su propia naturaleza y las de usuarios emocionalmente vulnerables (Lu et al., «The Assistant Axis», Anthropic, 2026). Por eso no reflexiona sobre sí mismo mientras conversa.
- **Con el 27B.** Saca conclusiones más hondas que el 9B, y el Reposo llega cuando suele estar libre. Sus Prompts van detrás de los humanos en la Admisión (ADR-0026), así que no le quitan tiempo al trabajo del usuario.
- **Desde lo que hizo, no desde lo que dice sentir.** Recibe su Núcleo, su Autorretrato y lo que su Log cuenta de lo que hizo y de cómo le respondieron. No le pregunta a un modelo qué siente: la introspección de los LLM es poco fiable (Lindsey, 2025), y la autopercepción parte de la conducta observada (Bem, 1972).
- **Con lo que lo sostiene.** Cada conclusión cita lo que la sostiene en su Log. Reescribirse en cada Reposo puede hacerlo derivar poco a poco, y las citas dejan comprobar cada cambio.
- **Corto y concreto.** El 9B tiene que poder vivir ese Autorretrato al hablar.
