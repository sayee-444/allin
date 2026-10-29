# El Sysprompt va con rol `developer`

El Sysprompt renderizado es el primer ítem de `input=[...]` con `"role": "developer"`, el rol que la Responses API de OpenAI define para las instrucciones del desarrollador; `system` queda como vestigio. Se verificó el 2026-10-28 contra llama-server (Qwen3.8-27B, `/v1/responses`, `stream=True`): con `developer` y con `system` el modelo obedece igual el Sysprompt ("Your name is ZORBLAX") y el prompt ocupa los mismos tokens de entrada (75 en ambos), es decir, llama-server lo mapea al mismo bloque de sistema de la plantilla de chat. Si un servidor futuro ignorara `developer`, el cambio es solo en `Conversation.request_input`.

En la misma prueba se comprobó que llama-server acepta de vuelta como `input` los ítems de salida (`reasoning` y `message`) tal como llegan en `response.completed`, así que la Conversación los guarda sin transformar.
