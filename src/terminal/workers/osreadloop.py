import os
import sys


def read_loop(master: int) -> None:
    out = b""
    while True:
        try:
            chunk = os.read(master, 1024)
        except OSError as e:  # EIO en Linux: todos los slaves cerrados
            print(e)
            break
        if not chunk:  # EOF en otros sistemas
            break
        out += chunk
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
