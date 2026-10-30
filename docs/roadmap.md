# Roadmap

**Meta:** un orquestador de terminal para conversar con un LLM local desde varias Ventanas, donde Main ordena los Turnos de todas las Sesiones, el Servidor lo lanza y vigila un Supervisor, y todo lo que se extiende (functions, Avisos, Sysprompts, Perfiles) es modular y se declara con nombres y decoradores legibles para una persona.

Cada fase es un milestone en GitHub (`sayee-444/allin`). Dentro de una fase, los issues van en el orden de la lista; las dependencias están marcadas en GitHub como "blocked by". Vocabulario: `GLOSSARY.md`; decisiones: `docs/adr/`.

## Dónde vamos

Lo actualiza el agente al terminar cada issue (`docs/agents/issue-workflow.md`).

- **Último hecho:** #21 junto con #17 (Servidor en caliente y 503 al cargar el modelo), verificados con el `llama serve` real; evidencia en `evidence/issues/21` (2026-10-30).
- **Siguiente:** #20 Partir el Dispatcher (tiene la etiqueta `needs-triage`: revisar su alcance antes de empezar).
- **Pendiente de decidir con el usuario:** nada. Bajar la capacidad con Turnos en curso solo se probó con tests: hoy solo baja reiniciando el Servidor, y `parallel` aún no cambia en caliente.

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

1. #30 Perfiles 1/3: Perfil con nombre, `--profile`, `/profile new`, migración a `default`
2. #31 Perfiles 2/3: `/profile switch`
3. #32 Perfiles 3/3: compartir Sesiones

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

Sin issues todavía: Command sessions (`@…`, incluido `@server`), fsource, Notificaciones y Conversation logs, y los decoradores `@confirm` y `@zombie` (ADR-0009).

## F3 · Submains y Traspaso

Submains, Traspaso de Sesiones dentro del árbol, y #18 (FIFOs huérfanas por Main).

## Después

Sin fecha ni orden fijo: fagent y packages, fscreen, encadenar functions, Clon y Goal.
