def leer(capitulo: int) -> str:
    """Lee un capitulo del libro de pruebas (unas 2 000 palabras). Su primera palabra es
    siempre el nombre de una fruta, distinta en cada capitulo."""
    frutas = ["mango", "pera", "kiwi", "uva", "higo", "lima", "coco", "fresa", "cereza", "melon"]
    fruta = frutas[capitulo % len(frutas)]
    relleno = " ".join(f"linea{capitulo}-{i} del capitulo, texto de relleno para ocupar contexto" for i in range(220))
    return f"{fruta}. Capitulo {capitulo}. {relleno}"
