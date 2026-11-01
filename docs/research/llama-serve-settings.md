# Ajustes de `llama serve`: carga, muestreo por Paso y dónde va cada clave

Investigación de #26 (Supervisor 3/4). Comprobada el 2026-11-01 contra el `llama serve` real (`llama --version`: 0.4.0-dev, build 10909, commit a2878d30d) y el SDK de OpenAI del venv (`openai>=2`, `client.responses.create`). Decisiones relacionadas: ADR-0004, ADR-0008, ADR-0013, ADR-0017, ADR-0022.

## Cómo se comprobó

- **Valores por defecto de carga:** `llama serve --help` de esta build.
- **Muestreo por Paso:** un Servidor de pruebas (`Swift-1.5-Qwen3.8-27B-IQ2_XXS.gguf`, `-np 1 -c 8192`, puerto 9932) arrancado con `--temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.05 --presence-penalty 0 --repeat-penalty 1.0`. Por cada parámetro se mandó un `responses.create` y, mientras el slot procesaba, se leyó `GET /slots`, cuyo `params` dice con qué muestrea ese Paso. Si el valor del Paso aparece ahí en lugar del de la línea de arranque, el Servidor lo aplicó.
- **Razonamiento:** misma técnica, más la longitud del razonamiento devuelto y los tokens de salida (`usage.output_tokens`) con un problema que obliga a razonar.

## Muestreo por Paso en `/v1/responses`

| Parámetro | Cómo se manda | ¿Lo aplica? | Evidencia (`/slots` → `params`) |
|---|---|---|---|
| `temperature` | argumento del SDK | sí | 0.3 (arranque: 0.6) |
| `top_p` | argumento del SDK | sí | 0.5 (arranque: 0.95) |
| `max_output_tokens` | argumento del SDK | sí | `n_predict` 50 y `usage.output_tokens` 50 |
| `top_k` | `extra_body` | sí | 5 (arranque: 20) |
| `min_p` | `extra_body` | sí | 0.2 (arranque: 0.05) |
| `presence_penalty` | `extra_body` | sí | 0.5 (arranque: 0) |
| `repeat_penalty` | `extra_body` | sí | 1.2 (arranque: 1.0) |
| `frequency_penalty` | `extra_body` | sí | 0.3 |
| `seed` | `extra_body` | sí | 7 |
| `typical_p`, `top_n_sigma`, `repeat_last_n`, `dry_multiplier`, `xtc_probability` | `extra_body` | sí | 0.9, 1.5, 128, 0.8, 0.1 |
| `reasoning.effort` | argumento del SDK (`reasoning={"effort": …}`) | sí, pero lo valida la plantilla del modelo | `low` y `medium` corren; `high` da **500**: «Supported types are xhigh (default), medium, and low» |
| `thinking_budget_tokens` | `extra_body` | sí | con 20, el razonamiento se corta (60 caracteres frente a ~2000) y la respuesta llega; con 100, 104 tokens de salida |
| `reasoning_budget_tokens` | `extra_body` | sí, alias del anterior | con 100, 104 tokens de salida y razonamiento cortado |
| `reasoning_budget` | `extra_body` | **no**, se ignora | razonamiento entero (600 tokens) |
| `thinking: {type, budget_tokens}` | `extra_body` | **no**, se ignora | razonamiento entero |
| `chat_template_kwargs: {"enable_thinking": false}` | `extra_body` | sí | sin razonamiento, 4 tokens de salida |
| una clave inventada (`no_such_param`) | `extra_body` | se ignora **sin error** | 200, como si no estuviera |
| un tipo equivocado (`top_k: "abc"`) | `extra_body` | se rechaza | **400** «type must be number» |

Conclusiones:

1. **Todo el muestreo se puede cambiar por Paso.** Los flags de muestreo de la línea de arranque (`--temp`, `--top-k`…) solo son el valor por defecto de cada Paso que no los manda.
2. **Llama ignora sin avisar lo que no conoce.** Un nombre mal escrito (`reasoning_budget` en lugar de `reasoning_budget_tokens`) no da error: el Paso corre con el valor por defecto. Por eso AllIn valida contra una **lista cerrada** propia y nunca reenvía claves libres.
3. **`reasoning.effort` depende de la plantilla.** Qwen3.8-27B solo admite `low`, `medium` y `xhigh`; otro modelo puede admitir otros. Un valor fuera de su lista hace fallar el Paso con 500. Si AllIn lo admite, tiene que validarlo contra lo que admita el modelo, o tratar ese 500 como error del Ajuste y no como caída.
4. **La Responses API no señala el corte.** Ya medido en #51: `status: completed` aunque corte `max_output_tokens`; el Turno cortado se detecta por los tokens (ADR-0004).

### Lista cerrada para la Sobrescritura de parámetro (#14)

Son los parámetros comprobados arriba, que se pueden sobrescribir por Prompt:

- `temperature`, `top_p`, `max_output_tokens` (argumentos del SDK; ya son Ajustes de Sesión en `session.sampling`).
- `top_k`, `min_p`, `presence_penalty`, `repeat_penalty`, `frequency_penalty`, `seed` (por `extra_body`).
- `reasoning_budget_tokens` (por `extra_body`; `-1` sin límite, `0` sin razonamiento).
- Fuera de la lista por ahora: `reasoning.effort` (depende de la plantilla, ver 3), `chat_template_kwargs` (es un objeto libre que entra en la plantilla) y los muestreadores exóticos (`typical_p`, `dry_*`, `xtc_*`, `top_n_sigma`, `mirostat`), que funcionan pero nadie ha pedido.

## Flags de carga

Los relevantes para AllIn, con su valor por defecto en esta build, el recomendado y el sitio que tienen en los Ajustes del Servidor (`servers/<nombre>.toml`). «Línea del usuario» es la de `models/como llamar llama serve con mis diferentes modelos.txt`.

| Flag | Por defecto | Qwen3.8-27B (Q6_K, una GPU de 32 GB) | Q2 de pruebas (IQ2_XXS) | Clave |
|---|---|---|---|---|
| `-m` | — | el `.gguf` | el `.gguf` | `model` (archivo) |
| `--mmproj` | sin proyector | `mmproj-…-f16.gguf` | sin él si hacen falta tres Servidores | `mmproj` (archivo) |
| `-c` | `0` (el del modelo) | `128000` | pequeño (`8192`–`32000`) | `context` |
| `-np` | `-1` (auto, y con auto `--kv-unified` se enciende) | `2` | `1`–`2` | `parallel` |
| `--kv-unified` | encendido solo si `-np` es auto | encendido con `-np ≥ 2` (cada slot ve todo el pool) | igual | `kv_unified` |
| `-ngl` | `auto` | `all` | `all` | `gpu_layers` |
| `-fa` | `auto` | `on` | `on` | `flash_attn` |
| `-ctk` / `-ctv` | `f16` | `q8_0` | `q4_0` | `cache_type_k` / `cache_type_v` |
| `--spec-type` | `none` | `draft-mtp` | `draft-mtp` | `spec_type` |
| `--port` | `8080` | uno libre (9931 suele ser el del usuario) | 9932+ | `port` (red) |
| `--fit` | `on`: ajusta lo no fijado para que quepa | dejarlo | dejarlo | en `extra` |
| `--jinja` | encendido | (la línea del usuario lo repite) | — | en `extra` |
| `--temp`, `--top-p`, `--top-k`, `--min-p`, `--presence-penalty`, `--repeat-penalty` | 0.80, 0.95, 40, 0.05, 0, 1.0 | 0.6, 0.95, 20, 0.05, 0, 1.0 (la línea del usuario) | igual | en `extra`: son el valor por defecto de cada Paso, y la Sesión los cambia por Paso |
| `--reasoning-budget` | `-1` | sin límite | — | en `extra` |
| `--context-shift` | apagado | **no se admite** (ADR-0017) | **no se admite** | rechazado |

Una clave sin valor no se pasa, así que vale el valor por defecto de llama. Así, `/server adopt` reproduce una línea sin añadir flags que cambien lo que hace.

### Lo que no se admite, o solo un humano

- **`--context-shift`** truncaría la Conversación en silencio (ADR-0017). `/server new` y `/server set` lo rechazan dentro de `extra`, y `/server adopt` se niega a adoptar una línea que lo lleve. Llama también lo lee de la variable de entorno `LLAMA_ARG_CONTEXT_SHIFT`, así que `adopt` mira además el entorno del proceso (`/proc/<pid>/environ`), y el Supervisor lanza el Servidor sin esa variable.
- **Claves que nombran un archivo o abren la red** (ADR-0008, ADR-0013): `model`, `mmproj`, `cwd`, `command` (ejecuta un programa) y `port`. También `extra`, que puede llevar cualquier flag de los dos tipos: `--host`, `--log-file`, `--chat-template-file`, `--slot-save-path`, `--media-path`, `--api-key-file`, `-hf` (descarga de la red), `--props` (deja cambiar propiedades globales por HTTP)… Solo las fija un humano.
- **`--sleep-idle-seconds`** (apagado por defecto): con él, llama se duerme solo (`/props` → `is_sleeping`). Se confundiría con el dormir del Supervisor (ADR-0022). No se rechaza todavía, pero no se recomienda.

## Ajustes del Servidor frente a Ajustes de Sesión

- **Ajustes del Servidor** (`servers/<nombre>.toml`, los aplica un reinicio): todo lo que fija la carga, es decir, la tabla de flags de carga. También los flags de muestreo de la línea, en `extra`, como valor por defecto de los Pasos que no mandan el suyo.
- **Ajustes de Sesión** (`session.sampling.*`, por Paso): hoy `temperature`, `top_p` y `max_output_tokens`. Los de la lista cerrada de arriba (`top_k`, `min_p`, `presence_penalty`, `repeat_penalty`, `frequency_penalty`, `seed`, `reasoning_budget_tokens`) pueden sumarse, porque llama los aplica por Paso. Llegan con la Sobrescritura de parámetro (#14), que usa esa misma lista.
