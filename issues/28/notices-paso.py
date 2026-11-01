from allin.notices import notice


@notice("paso", title="Paso [\x9b] terminado", text="Contexto: {tokens} tokens. Control: [\x1b[2J] [\x9b] [\x07]")
def paso(event, params):
    return {"tokens": event.context}
