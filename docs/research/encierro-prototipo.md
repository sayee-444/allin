# Prototipo del encierro: lo medido y la recomendación para el lanzador

Investigación de #99. Medida el 2026-11-02 en esta máquina: kernel 7.0.0-38-generic (x86_64), Landlock ABI 8, Python 3.14.4 del sistema con el venv del repo, `dev.tty.legacy_tiocsti = 0`. Decisiones relacionadas: ADR-0016 (encierro), ADR-0005 (un proceso por llamada), ADR-0012 (Procesos adheridos).

El prototipo era desechable y no entró en `src/`: estuvo en `prototypes/encierro/`, fuera de ruff y basedpyright, y se borró con #100, que trae el lanzador definitivo (`src/allin/confinement/`). Sigue en el historial: `git show 3ac8218:prototypes/encierro/<script>`. Cada script se corría con `venv/bin/python -I prototypes/encierro/<script>` y decía al principio qué mide. Todas las cifras de este informe salen de una sola ejecución, cuya salida completa está en la rama `evidence`, en `issues/99/` (`test.log`, `probe.json` y `cost.json`).

| Script | Qué responde |
|---|---|
| `confine.py` | Landlock (3 llamadas al sistema) y el filtro seccomp, con `ctypes` y sin librerías |
| `ports.py`, `ports_memory.py` | 1. El coste de `net=True` |
| `launch.py probe` con `tool_pkg/probe_tool.py`, `shm.py` | 2. Qué partes de `/proc` y `/dev` necesita una tool, y la sonda de fugas |
| `launch.py born`, `launch.py pickle` | 3. Nacer limpio, y qué se fía Main del canal |
| `confine.py`, `tsync.py`, `launch.py tiocsti` | 4. Con qué se aplican Landlock y seccomp |
| `launch.py cost`, `warden.py` | 5. El coste por llamada |
| `inode.py` | 6. Reglas atadas al inodo |
| `reaper.py`, `warden.py` | Matar a los descendientes (no lo preguntaba el issue, pero sale de la 3) |

## Resumen para #100

1. **Lanzar con `subprocess.Popen` y un `socketpair`, no con `multiprocessing`.** El canal lleva JSON por líneas (ADR-0002), parseado con pydantic. Con una `multiprocessing.Queue`, Main desempaqueta con `pickle` lo que mande la tool, y eso ejecuta código en Main (medido; sección 3). Además, con `spawn` el hijo nace con el entorno, el `sys.path` y la carpeta actual de Main, y del entorno no se puede librar después.
2. **Landlock y seccomp con `ctypes`, sin dependencias nuevas.** Landlock son tres llamadas al sistema con estructuras fijas. El filtro seccomp es un programa BPF escrito a mano de unas 25 instrucciones. Los dos se aplican a todos los threads: `LANDLOCK_RESTRICT_SELF_TSYNC` con la ABI 8 y `SECCOMP_FILTER_FLAG_TSYNC`.
3. **Para `net=True`, Main construye una vez la capa de red** (todo puerto TCP menos los de los Servidores, 65 534 reglas, unos 30 ms). La rehace cuando cambian los puertos de los Servidores y se la pasa a cada llamada como descriptor. El proceso aplica primero su capa de archivos y la de red la última, y cierra el descriptor antes de importar la function.
4. **El Alcance mínimo necesita también `/dev/null` (leer y escribir).** Sin él falla git. `/dev/zero`, `/dev/urandom` y `/dev/random` (leer) son inocuos y conviene darlos. No hace falta `/proc`. Con red, hay que leer además la carpeta adonde apunta `/etc/resolv.conf` (aquí `/run/systemd/resolve`). `/dev/shm` hace falta para `multiprocessing`, pero expone memoria compartida de otros programas: queda como pregunta para el usuario (sección 2).
5. **El primer proceso es un vigilante.** Se encierra, crea con `fork` el proceso que corre la function y espera. Al terminar, o al cancelar Main por el canal, hace `kill(-1, SIGKILL)`. Con `LANDLOCK_SCOPE_SIGNAL` eso solo alcanza a los procesos de su propio encierro, también a los nietos que se fueron a otra sesión con `setsid`, y no toca a Main ni a otras llamadas. `killpg` no basta (sección 5).
6. **Coste:** de 17 a 25 ms por llamada, según la red. Queda por debajo de los 50–100 ms que acepta ADR-0005 y del `multiprocessing` spawn con `Queue` de hoy (28 ms).

Esto cambia dos frases de los ADR, que #100 tendrá que enmendar al adoptarlo. ADR-0005 dice «un proceso `multiprocessing` en modo spawn» y que las Command sessions viajan «por una `multiprocessing.Queue`». ADR-0016 descarta bubblewrap, entre otras razones, porque obliga a un `exec` en vez de un `multiprocessing` spawn; ese motivo deja de contar, pero el principal (los user namespaces que restringe AppArmor) sigue en pie.

## 1. Puertos del Servidor: el coste de `net=True`

Landlock solo sabe permitir puertos, así que «todo menos los Servidores» son 65 534 reglas (`ports.py`, 15 repeticiones cada caso, mediana):

| Forma | Coste dentro del proceso de la tool |
|---|---|
| Construir las reglas y aplicarlas en cada llamada | **34,6 ms** (28,8 construir + 5,7 aplicar; máximo 37,7) |
| Main las construye una vez (30,5 ms) y el proceso solo aplica el descriptor heredado | **7,1 ms** |

Comprobado con la capa compartida y otra de archivos encima: conectar o hacer `bind` a 9931 y 9932 da `EACCES` de Landlock, mientras que 9933 y 443 pasan (dan `ConnectionRefused` porque nada escucha). Conectar cuesta lo mismo con 65 534 reglas que sin ellas: 3,8 µs por intento en los dos casos.

- **El orden de las capas importa.** Cada capa nueva copia las reglas de las anteriores. Con la de red debajo y la de archivos encima, el proceso tarda 13,6 ms en encerrarse; con la de archivos primero y la de red la última, 7,4 ms (`launch.py cost`).
- **Memoria:** cada proceso con esa capa retiene unos **4,5 MiB** de memoria del kernel que no se libera hasta que muere (`ports_memory.py`, 40 procesos: 4514 KiB por proceso). Para tools cortas no importa; para un Proceso adherido con `net=True` es memoria fija mientras viva. Los hijos de la tool heredan el mismo dominio sin copiarlo.
- **Descriptor heredado.** ADR-0005 dice «sin más descriptores que su canal». El de la capa de red se cierra antes de importar la function, así que se cumple cuando importa. Hay que escribirlo así en la enmienda.
- **Solo TCP.** La red de Landlock no cubre UDP. Con `net=[443]` o `net=True`, el filtro deja abierto todo UDP, como ya dice ADR-0016 («TCP 443 · UDP abierto»). Solo `net=False` lo cierra, porque entonces seccomp no deja crear sockets de IP.

Alternativas que miré:

- **Servidores en un socket unix.** `llama serve --help` dice que `--host` acepta una ruta acabada en `.sock`. Si los Servidores que lanza AllIn escucharan ahí, el filtro seccomp ya los dejaría fuera de toda tool, y `net=True` no necesitaría ninguna regla de puerto. Pero un Servidor lanzado a mano en TCP, que Main observa, sigue necesitando la exclusión. No ahorra la capa de red, solo la haría innecesaria en el caso común. Sin medir: ni el ahorro ni que Main hable HTTP por un socket unix (httpx lo admite).
- **Un espacio de nombres de red** (`unshare(CLONE_NEWUSER | CLONE_NEWNET)`). Aquí funciona sin privilegios, aunque `apparmor_restrict_unprivileged_userns = 1`, pero deja al proceso sin ninguna red. Solo serviría para `net=False`, que seccomp ya cubre.

**Recomendación:** la capa compartida de Main, aplicada la última. Cuesta 7,4 ms por llamada, y unos 30 ms cada vez que cambian los puertos de los Servidores.

## 2. `/proc` y `/dev`: lo que necesita una tool normal

La sonda (`tool_pkg/probe_tool.py`) intenta lo que hace una tool corriente. Se encierra con el Alcance mínimo de ADR-0016: leer `/usr`, `/etc`, el venv, la instalación recortada y su package; escribir la Carpeta de trabajo y el `TMPDIR` de la llamada; sin red. Luego se le van añadiendo cosas, cada columna con todo lo de la anterior (`launch.py probe`):

| Prueba | Mínimo | + `/dev/null` rw | + `/dev/zero`, `/dev/urandom`, `/dev/random` r | + `/dev/shm` rw | + `/proc` r |
|---|---|---|---|---|---|
| importar `json`, `sqlite3`, `ssl`, `pydantic` | ok | ok | ok | ok | ok |
| escribir en la Carpeta de trabajo, con renombrado atómico | ok | ok | ok | ok | ok |
| `tempfile` en `TMPDIR` | ok | ok | ok | ok | ok |
| threads, `asyncio` (usa `socketpair`), `os.urandom`, `os.cpu_count` | ok | ok | ok | ok | ok |
| `subprocess` de `ls` y de `python -c pass` | ok | ok | ok | ok | ok |
| `subprocess` de `git --version` | **falla** (128) | ok | ok | ok | ok |
| leer `/dev/zero`, `/dev/urandom`, `/dev/random` | `EACCES` | `EACCES` | ok | ok | ok |
| `multiprocessing` spawn y fork con `Queue`, `Pool` | `EACCES` | `EACCES` | `EACCES` | ok | ok |
| `multiprocessing` forkserver (el método por defecto en 3.14) | `EACCES` | `EACCES` | `EACCES` | **`EPERM`** | `EPERM` |
| leer `/proc/self/status`, `/proc/cpuinfo` | `EACCES` | `EACCES` | `EACCES` | `EACCES` | ok |

Con red, la capa compartida y todo lo de la penúltima columna, `getaddrinfo("example.com")` falla («Temporary failure in name resolution»). Al añadir la lectura de `/run/systemd/resolve`, funciona.

Qué significa:

- **`/dev/null`** lo necesitan git y cualquier programa que redirija ahí. `/dev/zero`, `/dev/urandom` y `/dev/random` no los necesitó nada de la sonda, salvo leerlos a propósito (`os.urandom` usa `getrandom`). Son inocuos, y muchos programas los abren. Recomiendo los cuatro en el mínimo.
- **`/proc` no hace falta** para importar, ni para `multiprocessing`, ni para subprocesos. `subprocess` cierra descriptores con `close_range` y `os.cpu_count` usa `sched_getaffinity`. Lo necesitarán las herramientas que miran procesos (`ps`, psutil) y quizá librerías que leen `/proc/cpuinfo` (sin medir). Abrir `/proc` mostraría la línea de comandos de los demás procesos del usuario, Main y `llama serve` incluidos (sin medir aquí; Landlock sí impide depurar procesos de fuera del encierro). Recomiendo dejarlo fuera del mínimo.
- **`multiprocessing` necesita `/dev/shm`** para sus semáforos (`sem_open`). Con permiso de crear y borrar, pero sin el de listar la carpeta, los semáforos funcionan y no se puede ver qué hay. Aun así, un segmento de otro programa cuyo nombre se conozca sigue siendo legible (`shm.py`: lee `b'secret'` de un segmento plantado). **Pregunta para el usuario (#101):** ¿`/dev/shm` va en el mínimo, con crear y borrar sin listar, o solo cuando la function lo declara en `@reach`? Me inclino por lo segundo, como raíz simbólica (`shm`) que la Aprobación muestra.
- **`forkserver` no puede funcionar encerrado.** Crea un socket unix con ruta y el filtro lo niega. El arranque debe fijar `spawn` como método por defecto antes de importar la function. Una tool que pida `forkserver` a propósito recibirá el error.
- **DNS.** `/etc/resolv.conf` es un enlace a `/run/systemd/resolve/stub-resolv.conf`, y Landlock comprueba el destino. Con red, Main debe añadir a la lectura la carpeta del destino real de `/etc/resolv.conf`, resuelto en cada llamada. Sin medir: qué hace el módulo `mdns4_minimal` de `nsswitch.conf`, que habla con avahi por un socket unix que el filtro niega. Con la carpeta añadida, la resolución funcionó, así que al menos no la rompe.

**Sonda de fugas** (la batería de #103, en pequeño). Main escucha en un socket unix abstracto, para que un rechazo signifique encierro y no «no hay nadie». Con `display=True` el filtro deja pasar `AF_UNIX`, así que esa columna muestra lo que hace Landlock por su cuenta.

| Salida | `net=False` | `net=True` | `display=True` | Sin encierro |
|---|---|---|---|---|
| bus de D-Bus de la sesión (socket unix con ruta) | `EPERM` (seccomp) | `EPERM` (seccomp) | **conecta** | conecta |
| socket unix abstracto en el que escucha Main | `EPERM` (seccomp) | `EPERM` (seccomp) | `EPERM` (scope de Landlock) | conecta |
| `io_uring_setup` | `EPERM` | `EPERM` | `EPERM` | funciona |
| `TIOCSTI` en 0–2 | `EPERM` | `EPERM` | `EPERM` | `ENOTTY` (la entrada ya es `/dev/null`) |
| señal a Main (`kill(ppid, 0)`) | `EPERM` (scope de señales) | `EPERM` | `EPERM` | funciona |
| conectar al 9931 | `EPERM` (seccomp) | `EACCES` (Landlock) | `EPERM` | permitido |
| listar `home` | `EACCES` | `EACCES` | `EACCES` | funciona |
| listar la carpeta de datos (`var/`), dentro de la instalación que sí se lee | `EACCES` (recorte de ADR-0016) | `EACCES` | `EACCES` | funciona |
| un nieto con `setsid` sigue vivo al acabar la llamada | no (`kill(-1)`) | no | no | **sí** |

Con `display=True` se ve lo que ya dice ADR-0016: el bus de D-Bus queda al alcance, y `display` equivale a actuar como el usuario.

`TIOCSTI` con una terminal de verdad (`launch.py tiocsti`): este kernel ya lo niega por el sysctl `legacy_tiocsti = 0` (`EIO`), y el filtro da `EPERM` antes. El filtro sigue haciendo falta en los kernels que tengan ese sysctl en 1.

## 3. Nacer limpio

`launch.py born` ensucia a propósito un proceso que hace de Main: la terminal en 0–2, un token y `DBUS_SESSION_BUS_ADDRESS` en el entorno, un descriptor heredable de más y un `json.py` en su carpeta actual. Luego compara lo que hereda el hijo de cada lanzador, leído antes de encerrarse:

| | `multiprocessing` spawn | `subprocess` + `socketpair` |
|---|---|---|
| sesión y grupo propios | no | sí (`start_new_session=True`, sin `preexec_fn`) |
| descriptores 0–2 | **la terminal de Main** | `/dev/null`, tubería, tubería |
| otros descriptores | 5 tuberías (el canal y el rastreador de recursos), `/dev/null` y el de `ctypes` | el canal y el que abre su propio `ctypes` |
| entorno | **las 80 variables de Main**, el token, D-Bus y `SSH_AUTH_SOCK` incluidos | 4: `PATH`, `LANG`, `TMPDIR`, `HOME` |
| `sys.path` | **el de Main**, copiado al hijo | el de `-I`: biblioteca y venv (con `src/`, por la instalación editable) |
| carpeta actual | **la de Main** | la Carpeta de trabajo |

Algunas de esas diferencias el hijo de `spawn` sí podría corregirlas al arrancar. No lidera su grupo, así que puede llamar a `setsid()`, y puede poner `/dev/null` en 0–2 con `dup2`. Otras, no:

- **El entorno no se puede limpiar después.** El bloque original sigue en la memoria del proceso y en `/proc/self/environ`, y la tool corre en ese mismo proceso.
- **`sys.path` y la carpeta actual de Main llegan antes que cualquier código nuestro**, porque `spawn` los aplica al preparar el hijo. Con `multiprocessing` no hay forma de darle a una llamada otro entorno: `set_executable` cambia el ejecutable de todo `multiprocessing`, y `preexec_fn` está descartado (ADR-0003).

En esta prueba, el `json.py` plantado no distingue entre lanzadores: el propio Main corre con `-I`, así que la carpeta actual no estaba en su `sys.path` y ninguno de los dos hijos lo importa. Con un Main sin `-I`/`-P`, `spawn` heredaría esa ruta.

**Lo que Main se fía del canal** (`launch.py pickle`). Una `multiprocessing.Queue` desempaqueta con `pickle`. Una tool que escriba en la cola unos bytes preparados (también controla el semáforo de la cola) hace que **Main** ejecute lo que ella quiera al llamar a `get()`. En la prueba, Main crea un archivo vacío que nombra la tool. Esa sola razón basta para que el canal no sea `multiprocessing`: con ese canal, encerrar la tool no protege a Main. Con un `socketpair`, Main lee líneas JSON y las valida con pydantic, como en la frontera de los Avisos.

**Recomendación:** `subprocess.Popen([venv/bin/python, "-I", "-m", <arranque>], stdin=DEVNULL, stdout=PIPE, stderr=PIPE, env=<limpio>, cwd=<Carpeta de trabajo>, start_new_session=True, close_fds=True, pass_fds=(<canal>, <capa de red>))`. El arranque pide `PR_SET_PDEATHSIG`, comprueba que `getppid()` es Main y se declara `PR_SET_CHILD_SUBREAPER`. Luego lee la llamada del canal, se encierra, fija `sys.path` y `spawn`, e importa la function. Abierto para #100: qué `HOME` recibe la tool (en el prototipo, la Carpeta de trabajo, para que git y similares no busquen el `~` real).

## 4. Con qué se aplican Landlock y seccomp

- **Landlock con `ctypes`** (`confine.py`): `landlock_create_ruleset`, `landlock_add_rule` y `landlock_restrict_self`, con estructuras fijas de 12 a 24 bytes. La ABI se lee con el flag `LANDLOCK_CREATE_RULESET_VERSION`; aquí es 8. Detalles que el código definitivo tiene que cuidar:
  - Una regla sobre un archivo no admite derechos de carpeta (`READ_DIR`, `MAKE_*`, `REMOVE_*`, `REFER`), o da `EINVAL`.
  - `landlock_restrict_self` sin flags solo encierra al thread que la llama. Con la ABI 8, el bit 3 es `LANDLOCK_RESTRICT_SELF_TSYNC` (en `/usr/include/linux/landlock.h`), que encierra a todos. El kernel lo acepta y el bit 4 no (`tsync.py`). El prototipo lo pasa cuando la ABI es 8 o más. Con una ABI menor, el arranque tiene que asegurarse de no tener threads al encerrarse.
  - Las reglas de red incluyen el puerto 0. La cabecera dice que eso permite hacer `bind` a un puerto efímero.
  - No busqué una librería de Landlock en PyPI que cubra la ABI 6 (red y scopes). Con tres llamadas, `ctypes` basta y no añade una dependencia que fijar y revisar.
- **seccomp con un BPF escrito a mano** (`build_filter` en `confine.py`). Comprueba que la arquitectura es x86_64 y mata el proceso si no, igual que con las llamadas x32. Niega con `EPERM`:
  - `io_uring_setup`, `io_uring_enter` e `io_uring_register`;
  - `ioctl` con `TIOCSTI` o `TIOCLINUX`, comparando los 32 bits bajos, que es lo que mira el kernel;
  - `socket` con `AF_UNIX` (salvo con `display`), y con `AF_INET` y `AF_INET6` sin red.

  `socketpair` es otra llamada y sigue permitida. Landlock y seccomp juntos se aplican en 0,2 ms sin red y con pocos puertos (`launch.py cost`), con `seccomp(SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_TSYNC)`. libseccomp está instalada en el sistema (`libseccomp.so.2`) y se podría llamar con `ctypes`. No hace falta para un filtro de este tamaño, y traería sus propias versiones; la contrapartida es que el filtro solo sirve en x86_64, y otra arquitectura necesitaría sus propios números de llamada.
- **Para #100, mejor una lista de permitidos.** La lista de familias de socket debería decir qué se permite, no qué se niega: con red, `AF_INET`, `AF_INET6` y `AF_NETLINK` (glibc lo usa en `getaddrinfo`); sin red, ninguna o solo `AF_NETLINK`. Hoy, el filtro del prototipo deja pasar familias raras (`AF_VSOCK`, `AF_BLUETOOTH`…). Sin medir: qué rompe esa lista.

**Recomendación:** `ctypes` para los dos, en un módulo del lanzador con sus tests, y la batería de fugas (#103) como red de seguridad.

## 5. Coste por llamada

`launch.py cost`, 30 llamadas por caso, de lanzar a tener el resultado y el proceso recogido. El p90 es el noveno decil. La function es `noop`, así que no cuenta lo que tarde en importarse la de verdad:

| Caso | Mediana | p90 | Encerrarse (dentro del hijo) |
|---|---|---|---|
| `multiprocessing` spawn sin encierro, sin devolver nada | 26,6 ms | 27,3 ms | — |
| `multiprocessing` spawn sin encierro, con el resultado por una `Queue` (lo de hoy, ADR-0005) | 28,3 ms | 29,7 ms | — |
| `subprocess` + `socketpair` sin encierro | 16,6 ms | 17,6 ms | — |
| + encierro, `net=False` | 16,8 ms | 17,3 ms | 0,2 ms |
| + encierro, `net=[443]` | 16,8 ms | 17,2 ms | 0,2 ms |
| + encierro, `net=True` con las reglas construidas en el hijo | 54,8 ms | 59,1 ms | 37,7 ms |
| + encierro, `net=True` con la capa de Main debajo de la de archivos | 30,4 ms | 34,5 ms | 13,6 ms |
| + encierro, **`net=True` con la capa de Main la última** | **24,7 ms** | **27,7 ms** | 7,4 ms |

Del arranque del intérprete con `-I` hasta empezar a encerrarse pasan 5,9 ms en todos los casos. ADR-0005 acepta 50–100 ms por llamada. Con la recomendación, el peor caso (`net=True`) queda en la mitad del límite bajo y por debajo de lo de hoy. Construir las reglas en cada llamada (54,8 ms) ya se pasaría.

**Matar a los descendientes** (`reaper.py`, `warden.py`). Lo encontré al medir. Un nieto que se va a otra sesión con `setsid` sobrevive a la tool (última fila de la sonda de fugas). Además, si se queda con las tuberías de salida de la tool, un `communicate()` de Main espera hasta que muera: en una prueba anterior, que no está en la evidencia, 40 s por llamada. `killpg`, que es lo que pide #100 para el timeout, no lo alcanza. Lo que sí funciona:

- Desde dentro del encierro, con `LANDLOCK_SCOPE_SIGNAL`, `kill(-1, SIGKILL)` llega solo a los procesos del mismo dominio. Mata al nieto escapado de la llamada y deja vivos a Main, a una llamada hermana y al nieto de esta (`reaper.py`). Sin encierro, `kill(-1)` mataría todos los procesos del usuario. Por eso el código solo puede llamarlo después de comprobar que el encierro está puesto.
- Para que eso también sirva en un timeout, con la function colgada y quizá sin soltar el GIL, el proceso que se encierra hace de vigilante. Crea con `fork` el proceso que corre la function (**0,21 ms**) y espera, con `select`, al canal y a ese proceso. Al terminar, o cuando Main manda cancelar, hace `kill(-1, SIGKILL)` y lo recoge (**0,40 ms**). No queda ningún nieto vivo (`warden.py`, 10 repeticiones). `killpg` desde Main queda como último recurso, si el vigilante no contesta. Sin medir: el vigilante dentro del lanzador completo. El `boot.py` del prototipo todavía corre la function en el mismo proceso y hace `kill(-1)` al terminar; los 0,21 ms del `fork` se sumarían a la tabla.
- Main no debe esperar a que se cierren las tuberías de salida para dar por acabada la llamada. Primero el resultado por el canal, luego que muera el vigilante.

## 6. Reglas atadas al inodo

`inode.py`: un proceso encerrado de larga vida, como un Proceso adherido, recibe sus reglas. Alguien de fuera cambia luego los archivos como lo hacen los editores, y el proceso lo intenta otra vez:

| Escenario | Antes | Después |
|---|---|---|
| 1. Regla sobre un archivo; se reescribe en el sitio | lee `v1` | lee `v2` |
| 2. Regla sobre un archivo; se reemplaza de forma atómica (temporal + `rename`) | lee `v1` | **`EACCES`** |
| 3. Regla sobre una carpeta; un archivo de dentro se reemplaza de forma atómica | lee `v1` | lee `v2` |
| 4. Regla sobre una carpeta; se mueve a otro sitio y se crea otra con el mismo nombre | lee | la nueva: **`EACCES`**; la vieja, en su nueva ruta: **lee** |

Esto confirma lo que avisa nono, el sistema de encierro con Landlock que reseña `docs/research/sistemas-parecidos.md`: la regla vale para el inodo que había al añadirla. Para AllIn:

- **Tools:** no les afecta. Main recalcula las reglas en cada llamada y una llamada dura poco.
- **El archivo compartido de un Proceso adherido** (ADR-0016) se pierde en cuanto un editor lo guarda con renombrado atómico, que es lo normal (escenario 2). El Proceso adherido vería `EACCES` en un archivo que su Alcance dice que tiene. Opciones para #113: (a) Main vigila el inodo de ese archivo y relanza el Proceso adherido cuando cambia; (b) la regla va sobre la carpeta del archivo, que es dar más de lo pedido; (c) se documenta y el error llega como dato. Me inclino por (a), porque ya existe el relanzamiento al cambiar el Alcance.
- **Una carpeta movida conserva su permiso en la ruta nueva** (escenario 4). La regla sigue a la carpeta, no a su nombre. Para el recorte de ADR-0016 no importa: la carpeta de datos solo la mueve el usuario, no una tool, porque una tool no puede tocar las entradas de los niveles intermedios. Pero confirma que el recorte tiene que recalcularse en cada llamada y no guardarse.
- **Mover algo debajo de una carpeta concedida lo pone al alcance**, porque Landlock mira el camino en el momento del acceso. Es lo contrario del escenario 4. Para una tool vale mientras dura la llamada; para un Proceso adherido, mientras viva. Sin medir aparte; es la misma regla vista desde el otro lado.

## Preguntas abiertas para el usuario

1. **`/dev/shm`:** ¿en el Alcance mínimo, con crear y borrar sin listar, o solo declarado en `@reach`? Sin él, `multiprocessing` no funciona dentro de una tool (sección 2).
2. **Canal y lanzador:** la recomendación cambia ADR-0005 (`subprocess` + `socketpair` con JSON por líneas en vez de `multiprocessing` y su `Queue`). #100 lo enmendaría al adoptarlo, salvo que prefieras otra cosa.
3. **`HOME` de la tool:** ¿la Carpeta de trabajo, como en el prototipo, o ninguno?
