# El Lanzador usa subprocess.Popen, no multiprocessing.Process + pty

La Ventana se abre con `subprocess.Popen(["xterm", …], start_new_session=True)` y un thread vigía que hace `wait()`, en lugar del `Process` que abría un pty con `posix_openpt` y hacía `execvp`. El pty de Main solo capturaba la salida propia de xterm (que ahora va a un archivo de Log), toda la comunicación real va por FIFOs (ADR-0002), y hacer `fork` desde un Main que ya tiene threads vivos (UI, lectores) arriesga deadlocks; `Popen` hace fork+exec de forma segura.
