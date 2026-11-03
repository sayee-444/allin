# El Núcleo del Personaje va en su Sysprompt, y su Autorretrato le llega como dato

La personalidad del Personaje tiene una parte que no cambia y otra que cambia con lo que vive. Un Sysprompt es una pieza que aprueba un humano y que vuelve a cuarentena si cambia (ADR-0013), así que no puede llevar lo que el Personaje concluye de sí mismo en cada Reposo. Por eso se separan: el Núcleo lo escribe el usuario y va en el Sysprompt; el Autorretrato lo escribe el Personaje y le llega al abrir cada Charla.

## Dónde va cada uno

El Núcleo es su carácter, sus límites y su manera de hablar. Lo escribe el usuario en el Sysprompt del Molde de la Charla, y casi no cambia.

Al abrir cada Charla, su fproc le manda el Autorretrato y la Relación con cada Conocido presente, con su Origen, igual que sale la tarea inicial de un Molde (#112). Son dato, no instrucción.

Encaja con las capas de la personalidad de McAdams: los rasgos van en el Núcleo, y las adaptaciones y la historia que el Personaje se cuenta de sí, en el Autorretrato.

Se descartó meter el Autorretrato en el Sysprompt. O el usuario lo aprobaría cada vez, y el Personaje sería lo que él aprueba y no lo que concluyó, o se saltaría la cuarentena. Separarlos protege además de la manipulación: quien manipule su reflexión desde una Charla consigue como mucho un dato, nunca un Sysprompt.

## Cómo reflexiona

El Personaje reflexiona en su Reposo, en una Sesión del 27B de su Subperfil, cada vez que se cierra una Charla y sobre esa Charla:

- **Fuera de la Charla.** Lo que más saca a un modelo de su persona son las conversaciones que le piden reflexionar sobre su propia naturaleza y las de usuarios emocionalmente vulnerables (Lu et al., «The Assistant Axis», Anthropic, 2026). Por eso no reflexiona sobre sí mismo mientras conversa.
- **Con el 27B.** Saca conclusiones más hondas que el 9B, y el Reposo llega cuando suele estar libre. Sus Prompts van detrás de los humanos en la Admisión (ADR-0026), así que no le quitan tiempo al trabajo del usuario.
- **Desde lo que hizo, no desde lo que dice sentir.** Recibe su Núcleo, su Autorretrato, sus Relaciones y lo que su Log cuenta de lo que hizo y de cómo le respondieron, nunca las Confidencias (ADR-0029). No le pregunta a un modelo qué siente: la introspección de los LLM es poco fiable (Lindsey, 2025), y la autopercepción parte de la conducta observada (Bem, 1972).

Una Charla nueva no corta la reflexión, que sigue en el proceso del 27B: el 9B mantiene ~43 tok/s durante un prefill del 27B (ADR-0027). El Autorretrato nuevo entra en la Charla siguiente. Mientras dura la pausa que cierra una Charla no reflexiona, porque la Charla sigue abierta.

## Cómo se guarda

- **Corto y concreto.** El Autorretrato tiene hasta ~300 palabras, y cada Relación, hasta ~150, dos Ajustes. El 9B tiene que poder vivirlos al hablar.
- **Con citas.** Cada frase cita los Turnos de su Log que la sostienen. Reescribirse en cada Reposo puede hacerlo derivar poco a poco, y las citas dejan comprobar cada cambio.
- **Con versiones.** Cada Reposo guarda una versión nueva con su fecha. El usuario ve los cambios y puede volver a una versión anterior, pero no los edita a mano: si los escribiera él, el Personaje sería lo que él aprueba y no lo que concluyó.
