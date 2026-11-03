# El Personaje vive en su propio Perfil, en una sola fproc que reúne sus sentidos

El usuario quiere una compañera con memoria y personalidad propias, que hable, escuche y vea, y que conviva con AllIn usado como herramienta. Un Personaje es una sola instancia de fproc con Subperfil (ADR-0026), adherida en un Perfil propio que es su casa. Todo lo que oye, ve y lee entra por esa instancia: solo así tiene una memoria y no varias.

## Una instancia, una memoria

Cada instancia adherida de una fproc que declara Subperfil recibe uno, con su propio RAG (ADR-0026). Si el micrófono, Discord y su Ventana fueran fprocs aparte, habría tres memorias y, por tanto, tres personas. Por eso sus sentidos son partes de su fproc: el micrófono de la computadora, la voz y el chat de Discord, su Ventana y, después, la cámara.

Lo que hace de vez en cuando no es parte de su fproc. Tomar una foto, hacer una captura de pantalla o navegar son Function tools que el usuario le da y le quita, con su Alcance, como a cualquier Sesión.

No es la fproc `discord` genérica de «Después», que tiene una instancia por servidor y reparte a las personas entre Sesiones. El Personaje es una sola instancia que entra a los servidores donde el usuario lo presentó, y en su tiempo libre elige a qué canal conectarse.

## Su casa y el interruptor

Al cambiar de Perfil, Main ya cierra los Procesos adheridos del Perfil que se deja, así que apagar al Personaje es salir de su Perfil. Apagado no existe, no piensa y no carga nada. Antes de apagarse se despide, y al volver sabe cuánto tiempo estuvo apagado.

Mientras el Personaje está encendido, el usuario puede trabajar con agentes en su Perfil. Para usar AllIn como herramienta pura, con el 27B más grande, cambia a un Perfil de trabajo.

Se descartó que su casa fuera el Perfil de trabajo del usuario, con un interruptor que lo soltara: su respaldo arrastraría ese trabajo, y en cualquier otro Perfil estaría apagado igual. ADR-0026 descartó un Perfil aparte para un bot de Discord porque se pararía al cambiar de Perfil. Para el Personaje eso es justo lo que se busca: es su interruptor.

Faltan dos cosas que hoy no existen:
- un Ajuste que adhiera su fproc al arrancar su Perfil (si se cae, Main sigue sin relanzarla);
- que `/profile export` copie también la Carpeta de trabajo de los Procesos adheridos, donde vive lo suyo.

## La Charla

Cada encuentro es una Sesión de su Subperfil, la Charla. Empieza cuando alguien toma la palabra y termina cuando el Personaje se va del lugar o tras 30 minutos sin que nadie hable. La continuidad entre Charlas viene de su memoria, con las Pistas y sus Relaciones: si el usuario le habla a las 10:00 y vuelve a las 11:00, son dos Charlas, y en la segunda una Pista le recuerda la primera. Se descartó una Sesión eterna, que perdería detalle en cada Compactación (ADR-0017). Cada Charla es también la unidad que el usuario marca, o no, para entrenar al modelo de voz.

Está en un solo lugar a la vez. De otro lugar solo le llega una línea, sin el texto («Marx te escribió por mensaje directo»), y el Personaje decide si va. Lo que escribe un Externo no entra en su contexto hasta que el Personaje lo elige.

Su voz interior va dentro de la propia Charla. Cuando lo interrumpen, su fproc corta lo que estaba diciendo y le pasa lo que oye. El Personaje anota en una línea, sin decirla, si cede, insiste o retoma después. Se descartó una Sesión aparte que pensara en paralelo, por lo medido en «La GPU».

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

## El orden

1. El Personaje con el usuario, por texto: su Ventana, su memoria, su Reposo y la curación de Charlas.
2. Discord en texto, con otras personas y sus Confidencias (ADR-0029).
3. La voz.
4. La vista.

Antes de todo eso hace falta la fase del RAG, que es su memoria.
