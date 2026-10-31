# Roadmap

**Meta:** un orquestador de terminal para conversar con un LLM local desde varias Ventanas, donde Main ordena los Turnos de todas las Sesiones, el Servidor lo lanza y vigila un Supervisor, y todo lo que se extiende (functions, Avisos, Sysprompts, Perfiles) es modular y se declara con nombres y decoradores legibles para una persona.

Cada fase es un milestone en GitHub (`sayee-444/allin`). Dentro de una fase, los issues van en el orden de la lista; las dependencias están marcadas en GitHub como "blocked by". Vocabulario: `GLOSSARY.md`; decisiones: `docs/adr/`.

## Dónde vamos

Lo actualiza el agente al terminar cada issue (`docs/agents/issue-workflow.md`).

- **Último hecho:** #21 junto con #17 (Servidor en caliente y 503 al cargar el modelo), verificados con el `llama serve` real; evidencia en `evidence/issues/21` (2026-10-30).
- **Siguiente:** #20 Partir el Dispatcher.
- **Decidido en el grilling del tejido (ronda 1):** autoridad por Origen, `-y` y `--devastation` solo humanos, inyección entre Sesiones (ADR-0013); vida del Encargo si falla la coordinadora (ADR-0011); revisión optimista en Procesos adheridos (ADR-0012); Kit compartible y cargado por copia (ADR-0014).
- **Decidido en el grilling del tejido (rondas 2–4):** cuarentena de functions ajenas, fusión de Kits, Servidor sugerido, entrada, Plano e historial inicial (ADR-0014); Restablecer, `/reset --all` y archivos como composición (ADR-0015).
- **Decidido en el grilling del aislamiento:** cada proceso de tool y de Proceso adherido se encierra con Landlock y seccomp según su Alcance (`@reach`), con Carpeta de trabajo por Sesión y la carpeta de datos siempre fuera; llega al comienzo de F2 y F4 desaparece (ADR-0016).
- **Revisión de coherencia (2026-10-30):** corregidas 14 contradicciones entre ADR-0016 y el resto del tejido: descripción de functions sin importarlas en Main, recorte también de la instalación, procedencia frente a Origen, Referencia a archivo, canal de vuelta, muerte de tools con Main, Suscripción no bloqueante, Rewind por Origen, cambio de Perfil con Encargos vivos, Clon y Carpeta de trabajo.
- **Segunda revisión de coherencia (2026-10-30):** encierro de descripción, timeout sin las esperas a Main, `display` como actuar por el usuario, puertos del Servidor fuera de todo Alcance, UDP con lista de puertos, Referencia a archivo como instantánea, ruta de la Carpeta de trabajo fuera de los Ajustes, procedencia, Avisos y Command sessions como parte de Main, plazo vencido que cancela, Ventana según el Origen del Prompt y Encargos del Perfil que los abrió.
- **Tercera revisión (2026-10-30), con el diagrama del tejido:** un proceso encerrado salía por los sockets unix con ruta (D-Bus → `systemd --user`, X11, agente SSH), comprobado en este kernel; lo cierra el filtro seccomp, junto con io_uring y `TIOCSTI`, y el proceso de una tool nace sin la terminal, el entorno ni las rutas de importación de Main. Además: tres puertas (Confirmación, Aprobación, Encierro); Main y el Supervisor nunca tocan rutas ni red por una Sesión más allá de sus tools; package entero y sin `.pyc`; recorte de toda ruta concedida; escribir donde el usuario ejecuta como `display`; caracteres de control y de dirección neutralizados en pantalla; la respuesta de un Prompt al final de su cadena; ADR-0004 con el modelo de Pasos; Carpeta de trabajo como directorio actual, y una propia para cada Proceso adherido; Rewind que cancela lo abierto después; solo el Origen humano vuelve al input; el Perfil como frontera de `/send`; piezas por Perfil; y una batería de fugas antes de la primera tool.
- **En grilling:** gestión del Contexto de Conversaciones largas (compactación manual y automática); su fase sale de ese grilling.

## F1 · Cierre

Ya tienen código; verificados y comentados.

1. ✅ #8 Contexto y velocidad en el borde
2. ✅ #9 Cerrar una Ventana cancela su Turno
3. ✅ #10 Caída de llama.cpp sin perder el Prompt
4. ✅ #11 `/exit`, señales y caída de Main
5. ✅ #12 Restaurar MainState

## Pre-F2 · Servidor

1. ✅ #21 Observar el Servidor en caliente, junto con #17 (503 al cargar el modelo)
2. #20 Partir el Dispatcher
3. #28 Recuadro de Aviso y Confirmación
4. #24 Supervisor 1/4: Supervisor con nombre y Enlace
5. #25 Supervisor 2/4: reinicio ordenado y drenado
6. #26 Supervisor 3/4: `/server …` e investigación de Ajustes del Servidor
7. #27 Supervisor 4/4: cambiar el Enlace en caliente
8. #29 Admisión al pool con `--kv-unified` y Aviso de Contexto

## Pre-F2 · Perfiles

1. #30 Perfiles 1/3: Perfil con nombre, `--profile`, `/profile new`, piezas por Perfil, migración a `default`
2. #31 Perfiles 2/3: `/profile switch`
3. #32 Perfiles 3/3: compartir Sesiones
4. #39 Perfiles 4/4: exportar un Perfil como copia, y Kits (primera parte)

## Pre-F2 · Pendientes de F1

1. #15 El texto de un Turno fallido con la Ventana cerrada
2. #16 Un Prompt en cola sin el anterior de su Sesión que falló
3. #13 Selección con el ratón, rueda y portapapeles
4. #14 Command prompts: Referencia a archivo y Sobrescritura de parámetro
5. #22 Cursor en el input e historial de Prompts
6. #23 `/help` generado del registro de Command main
7. #19 Lo que quede de la señal durante el arranque
8. #33 Una Ventana desconectada sigue mandando Borradores (Broken pipe)
9. ✅ #34 Los textos que vuelven al input se mezclan con lo escrito

## F2 · Command sessions y fsource

Sin issues todavía. Lo primero, antes de que corra ninguna tool: el encierro (ADR-0016). Empieza con un prototipo desechable que responde en este kernel lo que el diseño aún no sabe: cuánto cuestan las reglas que dejan fuera los puertos del Servidor, qué partes de `/proc` y `/dev` necesita una tool, y cómo nace limpio un proceso de `multiprocessing` spawn con su `Queue`. Después, el lanzador de tools ya encerrado, con el proceso que nace limpio (sin la terminal, el entorno ni las rutas de importación de Main, ADR-0005) y el filtro seccomp (sin sockets unix salvo `socketpair` o con `display`, sin io_uring ni `TIOCSTI`); `@reach` con su Alcance mínimo, la Carpeta de trabajo como directorio actual, el `TMPDIR` por llamada, el recorte de toda ruta concedida, la descripción de functions en el encierro de descripción (Main nunca las importa), los puertos del Servidor fuera de todo Alcance, el canal de vuelta de Command sessions a la tool, el subreaper y `PR_SET_PDEATHSIG`, y el Aviso de kernel sin soporte. Su aceptación es una batería de fugas (D-Bus, X11, io_uring, señales, `ptrace`, terminal, `.pyc`, puertos del Servidor) que tiene que fallar entera antes de que corra ninguna tool, y que queda como test. Al implementarlo, el README explica el Alcance y el recorte con el ejemplo de ADR-0016. Después: Command sessions (`@…`, incluido `@server`), fsource, Notificaciones y Conversation logs, y los decoradores `@confirm` y `@zombie` (ADR-0009). Del grilling del tejido de primitivas (ADR-0011, ADR-0012): Continuaciones con prioridad y candado por Paso (ADR-0004), Suscripción y Encargo con reintentos, Consultar, Moldes, Procesos adheridos (fscreen genérica, archivo compartido) e Informe de recuperación; los Procesos adheridos se encierran como las tools, con su archivo compartido elegido antes de lanzarlos (ADR-0016). Del grilling del tejido, rondas 1–5: Origen en cada Prompt y comando, resultados entre Sesiones como datos y `/send` con adjuntos de la Carpeta de trabajo y parámetros estructurados, solo dentro del Perfil activo (ADR-0013, ADR-0010); cuarentena por hashes aprobados de functions (su package entero, sin `.pyc`), Sysprompts, Moldes y Planos, con la pantalla de aprobación que muestra los caracteres de dirección e invisibles, la Aprobación de todo lo que amplía un encierro, que ni `-y` ni `--devastation` saltan, y Crear como única vía para escribir piezas, que en F2 crea Sysprompts y Moldes (functions con fagent y Planos con Kits, después) (ADR-0013, ADR-0016); corte de ciclos de Suscripciones por la cadena de Origen; vida del Encargo si su coordinadora falla (ADR-0011); revisión con `@writes` en Procesos adheridos (ADR-0012); Rewind y Restablecer como eventos suscribibles, con el id del Prompt visible para las tools (ADR-0015).

## F3 · Submains y Traspaso

Submains, Traspaso de Sesiones dentro del árbol, y #18 (FIFOs huérfanas por Main). Antes, en su grilling: el protocolo entre Mains y las primitivas entre Mains del árbol (ADR-0011).

## Después

Lo que trae código ajeno (fagent del LLM y Kits, segunda parte) ya llega con el encierro de F2: sus functions corren con el Alcance aprobado, y en un kernel sin Landlock no corren (ADR-0016).

Sin fecha ni orden fijo: fagent y packages (con Crear de functions desde una Sesión), encadenar functions, Clon y Goal. Kits, segunda parte: functions con cuarentena, Moldes, Planos, historial inicial y entrada (ADR-0014). Restablecer (`/session reset`) y `/reset --all` (ADR-0015).
