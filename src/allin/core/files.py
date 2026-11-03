import os
from pathlib import Path


def write_atomically(path: Path, text: str, mode: int = 0o666) -> None:
    """Write aside and rename, so a crash never leaves half a file behind. ``mode`` is the
    file's from its first byte, as the umask leaves it: ``0o600`` for one only the user reads."""
    partial = path.with_name(f"{path.name}.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        if mode != 0o666:  # a partial file left by a crash keeps the mode it was made with
            os.fchmod(descriptor, mode)
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    partial.replace(path)
