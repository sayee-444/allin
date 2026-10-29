# Main es el único escritor del estado de las Sesiones

Las Ventanas no modifican por sí mismas el estado de su Sesión: todo Command session se traduce a exactamente un Command main y Main lo ejecuta en orden dentro de su cola. Se eligió así en vez de permitir comandos "locales" en la Ventana para que haya un solo punto de serialización (sin carreras entre la Ventana, Main y otras Sesiones que actúan vía fsource) y para que todo cambio quede en el Log de Main.
