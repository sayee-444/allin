import os
from pathlib import Path


def write_atomically(path: Path, text: str) -> None:
    """Write aside and rename, so a crash never leaves half a file behind."""
    partial = path.with_name(f"{path.name}.partial")
    with open(partial, "w", encoding="utf-8") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(partial, path)
